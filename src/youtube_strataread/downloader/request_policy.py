"""Shared, durable pacing and cooldown for the workbench's YouTube traffic."""

from __future__ import annotations

import fcntl
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path

REQUEST_INTERVAL = 2.0
VIDEO_INTERVAL = 15.0
INITIAL_COOLDOWN = 15 * 60
MAX_COOLDOWN = 6 * 60 * 60
MAX_RATE_LIMIT_ATTEMPTS = 4
_STATE_KEY = "youtube_request_policy"


class YouTubeError(RuntimeError):
    """Public acquisition error shared by the CLI and the workbench."""


class ConfirmedCaptionAbsence(YouTubeError):
    """Accessible, identity-matched source metadata proves captions are absent."""


class YouTubeRateLimited(YouTubeError):
    def __init__(
        self,
        retry_at: float | None = None,
        *,
        recorded: bool = False,
        response_received: bool = True,
    ) -> None:
        super().__init__("YouTube 请求限流，正在冷却，稍后自动重试。")
        self.retry_at = retry_at
        self.recorded = recorded
        self.response_received = response_received


class YouTubeRequestsStopped(RuntimeError):
    pass


def rate_limit_error(
    error: BaseException, *, now: float | None = None
) -> YouTubeRateLimited | None:
    """Preserve Retry-After through urllib/yt-dlp exception wrappers."""
    now = time.time() if now is None else now
    pending, seen = [error], set()
    found, retry_at = False, None
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, YouTubeRateLimited):
            return item
        response = getattr(item, "response", None)
        status = (
            getattr(item, "status", None)
            or getattr(item, "status_code", None)
            or getattr(item, "code", None)
            or getattr(response, "status", None)
            or getattr(response, "status_code", None)
        )
        if status == 429 or re.search(
            r"HTTP(?: Error)?[ :]+429\b|Too Many Requests", str(item), re.I
        ):
            found = True
            headers = getattr(item, "headers", None) or getattr(response, "headers", None)
            value = headers.get("Retry-After") if headers else None
            if value:
                try:
                    deadline = now + max(0, int(value.strip()))
                except (ValueError, AttributeError):
                    try:
                        deadline = parsedate_to_datetime(value).timestamp()
                    except (ValueError, TypeError, OverflowError):
                        deadline = now
                retry_at = max(retry_at or now, deadline)
        pending.extend(cause for cause in (item.__cause__, item.__context__) if cause is not None)
        exc_info = getattr(item, "exc_info", None)
        if (
            isinstance(exc_info, tuple)
            and len(exc_info) > 1
            and isinstance(exc_info[1], BaseException)
        ):
            pending.append(exc_info[1])
    return YouTubeRateLimited(retry_at) if found else None


class YouTubeRequestPolicy:
    """SQLite serializes reservations across workers and survives application restarts.

    These conservative intervals are product defaults, not published YouTube limits.
    Only successful caption acquisition resets the exponential cooldown streak;
    a successful metadata or watch page must not undo repeated subtitle rate limits.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.now = time.time
        self.stopped = threading.Event()
        self._acquisition_local = threading.local()

    @staticmethod
    def _read(connection) -> dict:
        row = connection.execute(
            "SELECT value FROM workspace_meta WHERE key = ?", (_STATE_KEY,)
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "cooldown_until": 0,
                "strikes": 0,
                "next_request_at": 0,
                "next_video_at": 0,
            }
        )

    @staticmethod
    def _write(connection, state: dict) -> None:
        connection.execute(
            "INSERT INTO workspace_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_STATE_KEY, json.dumps(state)),
        )

    def snapshot(self) -> dict:
        with sqlite3.connect(self.database_path) as connection:
            return self._read(connection)

    def cooling_down(self) -> bool:
        return self.snapshot()["cooldown_until"] > self.now()

    @contextmanager
    def acquisition(self):
        """One recovering acquisition owns the capability until its result is recorded.

        Advisory ownership is released by the OS on normal exit or process death;
        there is no expiring lease that can overlap a slow live probe. Nested
        downloader calls on the same worker reuse their caller's ownership.
        """
        if getattr(self._acquisition_local, "active", False):
            yield
            return
        state = self.snapshot()
        if state["cooldown_until"] > self.now():
            raise YouTubeRateLimited(state["cooldown_until"], recorded=True, response_received=False)
        lock = None
        try:
            if state.get("failure_kind") or state.get("strikes"):
                lock = self.database_path.with_suffix(".acquisition-lock").open("a+b")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise YouTubeRateLimited(self.now() + REQUEST_INTERVAL,
                                             recorded=True, response_received=False) from None
                # The previous owner may have imposed a new deadline while we
                # opened the lock file. Never reserve over that later failure.
                state = self.snapshot()
                if state["cooldown_until"] > self.now():
                    raise YouTubeRateLimited(state["cooldown_until"], recorded=True, response_received=False)
            self._acquisition_local.active = True
            yield
        finally:
            self._acquisition_local.active = False
            if lock is not None:
                lock.close()

    def probe_unproven(self) -> None:
        """An absent/denied video is not successful capability recovery."""
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read(connection)
            if state.get("failure_kind") and state["cooldown_until"] <= self.now():
                state["cooldown_until"] = self.now() + MAX_COOLDOWN
                self._write(connection, state)

    def _take_slot(self, field: str, interval: float) -> None:
        while True:
            if self.stopped.is_set():
                raise YouTubeRequestsStopped("YouTube requests stopped")
            with sqlite3.connect(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                state, now = self._read(connection), self.now()
                if state["cooldown_until"] > now:
                    raise YouTubeRateLimited(
                        state["cooldown_until"], recorded=True, response_received=False
                    )
                delay = state[field] - now
                if delay <= 0:
                    state[field] = now + interval
                    self._write(connection, state)
                    return
            # Never hold the database while waiting, and remain interruptible on quit.
            self.stopped.wait(min(delay, 0.5))

    def before_request(self) -> None:
        self._take_slot("next_request_at", REQUEST_INTERVAL)

    def before_video(self) -> None:
        self._take_slot("next_video_at", VIDEO_INTERVAL)

    def limit(self, error: YouTubeRateLimited) -> YouTubeRateLimited:
        if error.recorded:
            return error
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state, now = self._read(connection), self.now()
            state["strikes"] += 1
            state["failure_sequence"] = state.get("failure_sequence", 0) + 1
            delay = INITIAL_COOLDOWN * 2 ** (state["strikes"] - 1) if state["strikes"] <= 3 else MAX_COOLDOWN
            state["failure_kind"] = "rate_limited"
            state.setdefault("first_failure_at", now)
            state["last_failure_at"] = now
            state["reason"] = "YouTube 请求限流，等待共享能力探测。"
            state["cooldown_until"] = max(state["cooldown_until"], now + delay, error.retry_at or 0)
            self._write(connection, state)
        return YouTubeRateLimited(state["cooldown_until"], recorded=True)

    def network_failed(self, reason: str, retry_at: float | None = None) -> float:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state, now = self._read(connection), self.now()
            state["failure_sequence"] = state.get("failure_sequence", 0) + 1
            count = state.get("network_failures", 0) + 1
            state["network_failures"] = count
            delay = 600 * 2 ** (count - 1) if count <= 3 else MAX_COOLDOWN
            state["cooldown_until"] = max(state["cooldown_until"], now + delay, retry_at or 0)
            state["failure_kind"] = "network"
            state.setdefault("first_failure_at", now)
            state["last_failure_at"] = now
            state["reason"] = reason
            self._write(connection, state)
        return state["cooldown_until"]

    def caption_succeeded(self, *, observed_sequence: int | None = None) -> None:
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read(connection)
            # Another in-flight request may have just imposed a cooldown.
            if (state["cooldown_until"] <= self.now()
                    and (observed_sequence is None or observed_sequence == state.get("failure_sequence", 0))):
                state["strikes"] = 0
                state["network_failures"] = 0
                for key in ("failure_kind", "first_failure_at", "last_failure_at", "reason"):
                    state.pop(key, None)
                self._write(connection, state)

"""Which failures wait, which retry, and which need a person.

One vocabulary for both external capabilities of preparation: subtitle
acquisition and the host model. Account-level restrictions pause every request
to the same account. Acquisition failures share capability recovery; model
transient failures retain a task budget. Unrecoverable failures stop automatically.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

from youtube_strataread.downloader.request_policy import (
    MAX_RATE_LIMIT_ATTEMPTS,
    ConfirmedCaptionAbsence,
)

# Model transient errors retain an initial attempt plus three retries.
# Caption availability and shared acquisition recovery have independent clocks.
MAX_AUTOMATIC_ATTEMPTS = MAX_RATE_LIMIT_ATTEMPTS
RETRY_BASE = 10 * 60
RETRY_MAX = 6 * 60 * 60
MODEL_GATE_BASE = 60
MODEL_GATE_MAX = 60 * 60
_GATE_KEY = "youtube_model_gate"

Kind = Literal["caption_pending", "transient", "account", "fatal"]


@dataclass(frozen=True)
class Failure:
    kind: Kind
    reason: str
    retry_at: float | None = None
    category: str | None = None


def retry_delay(attempts: int) -> float:
    return min(RETRY_MAX, RETRY_BASE * 2 ** max(0, attempts - 1))


def _chain(error: BaseException) -> Iterator[BaseException]:
    pending, seen = [error], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        pending.extend(c for c in (item.__cause__, item.__context__) if c is not None)
        exc_info = getattr(item, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1 and isinstance(exc_info[1], BaseException):
            pending.append(exc_info[1])


def _status(item: BaseException) -> int | None:
    response = getattr(item, "response", None)
    for value in (getattr(item, "status_code", None), getattr(item, "status", None),
                  getattr(item, "code", None), getattr(response, "status_code", None),
                  getattr(response, "status", None)):
        if isinstance(value, int) and 100 <= value < 600:
            return value
    return None


def _retry_after(item: BaseException, now: float) -> float | None:
    response = getattr(item, "response", None)
    headers = getattr(item, "headers", None) or getattr(response, "headers", None)
    try:
        value = headers.get("Retry-After") if headers else None
    except AttributeError:
        return None
    if not value:
        return None
    try:
        return now + max(0, int(str(value).strip()))
    except ValueError:
        try:
            return parsedate_to_datetime(str(value)).timestamp()
        except (ValueError, TypeError, OverflowError):
            return None


def _facts(error: BaseException, now: float) -> tuple[set[int], float | None, str]:
    statuses, deadline, text = set(), None, []
    for item in _chain(error):
        if (status := _status(item)) is not None:
            statuses.add(status)
        if (value := _retry_after(item, now)) is not None:
            deadline = max(deadline or value, value)
        text.append(f"{type(item).__name__}: {item}")
    return statuses, deadline, "\n".join(text)


_NETWORK = re.compile(
    r"timed? ?out|connection (?:reset|refused|aborted)|temporary failure|network is unreachable|"
    r"remote end closed|name resolution|HTTP Error 5\d\d|EOF occurred|ConnectError|ReadTimeout|"
    r"APIConnectionError|APITimeoutError", re.I)


def _transient(statuses: set[int], text: str, error: BaseException) -> bool:
    return (bool(statuses & {408, 425, 500, 502, 503, 504})
            or any(isinstance(item, (TimeoutError, ConnectionError)) for item in _chain(error))
            or (not statuses - {408, 425, 500, 502, 503, 504} and bool(_NETWORK.search(text))))


def classify_caption(error: BaseException, now: float) -> Failure:
    """Subtitle 429 is handled by the shared request cooldown before this point."""
    statuses, deadline, text = _facts(error, now)
    reason = str(error).strip() or type(error).__name__
    if statuses & {401, 403}:
        return Failure("fatal", reason, category="access_denied")
    if _transient(statuses, text, error):
        return Failure("transient", "字幕获取暂时失败，等待共享能力探测。", deadline)
    if isinstance(error, ConfirmedCaptionAbsence) and not statuses:
        return Failure("caption_pending", "已确认视频可访问，但字幕尚未提供。", deadline)
    if re.search(r"private video", text, re.I):
        return Failure("fatal", reason, category="private")
    if re.search(r"video (?:has been )?(?:removed|deleted)", text, re.I):
        return Failure("fatal", reason, category="deleted")
    if statuses & {401, 403} or re.search(r"do not have permission|access denied|members.only", text, re.I):
        return Failure("fatal", reason, category="access_denied")
    return Failure("fatal", reason, category="unknown")


def classify_model(error: BaseException, now: float) -> Failure:
    statuses, deadline, text = _facts(error, now)
    if statuses & {401, 403}:
        return Failure("account", "模型服务拒绝了当前凭据，请检查模型设置；同账号请求暂停。", deadline)
    if 402 in statuses or re.search(r"insufficient_quota|billing|quota", text, re.I):
        return Failure("account", "模型服务额度或计费受限；同账号请求暂停。", deadline)
    if 429 in statuses:
        return Failure("account", "模型服务限流；同账号请求暂停。", deadline)
    if _transient(statuses, text, error):
        return Failure("transient", "模型服务暂时不可用，稍后自动再试。", deadline)
    return Failure("fatal", str(error).strip() or type(error).__name__)


class ModelAccountGate:
    """Durable pause of new model requests for one provider account.

    Changing to another video cannot bypass it; subtitle work is unaffected.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _read(self, connection) -> dict:
        row = connection.execute("SELECT value FROM workspace_meta WHERE key = ?", (_GATE_KEY,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _write(self, connection, state: dict) -> None:
        connection.execute(
            "INSERT INTO workspace_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (_GATE_KEY, json.dumps(state)))

    def active(self, account: str | None, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with sqlite3.connect(self.database_path) as connection:
            gate = self._read(connection).get(account or "")
        return gate if gate and gate["until"] > now else None

    def snapshot(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with sqlite3.connect(self.database_path) as connection:
            state = self._read(connection)
        return [{"account": key, **value} for key, value in state.items() if value["until"] > now]

    def limit(self, account: str | None, failure: Failure, now: float | None = None) -> float:
        now = time.time() if now is None else now
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read(connection)
            gate = state.get(account or "", {"until": 0, "strikes": 0})
            strikes = gate["strikes"] + 1
            delay = min(MODEL_GATE_MAX, MODEL_GATE_BASE * 2 ** min(strikes - 1, 10))
            until = max(gate["until"], now + delay, failure.retry_at or 0)
            state[account or ""] = {"until": until, "strikes": strikes, "reason": failure.reason}
            self._write(connection, state)
        return until

    def succeeded(self, account: str | None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read(connection)
            gate = state.get(account or "")
            if gate and gate["until"] <= now:
                del state[account or ""]
                self._write(connection, state)

"""Durable admission barrier for a bounded YouTube cutover attempt.

A hold never snapshots execution permissions. In-flight work retains its lease
until its caller returns after saving results; withdrawal remains admissible.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager

from youtube_strataread.workbench.youtube_api import ConnectionError


class CutoverBlocked(ConnectionError):
    pass


class CutoverHold:
    def __init__(self, workspace):
        self.workspace = workspace
        workspace.cutover = self
        self._lock = threading.RLock()
        self._local = threading.local()
        self._condition = threading.Condition(self._lock)
        self._closing = False
        self._active = 0
        self._drain_closing = False
        self._drain_failed = False
        self._generation = 0
        self._monotonic_deadline = None
        self._state = json.loads(workspace.meta("youtube_cutover") or '{"held":false,"state":"idle"}')
        if self._state.get("held"):
            self._state.update(state="aborted", reason="服务重启已中止切换；保留最新资料，采集继续暂停。", drained=False)
            self._save()

    def _save(self):
        self.workspace.set_meta("youtube_cutover", json.dumps(self._state, ensure_ascii=False))

    @property
    def held(self):
        with self._lock:
            return bool(self._state.get("held"))

    @contextmanager
    def work(self, *, withdrawal=False):
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            if not depth and self._closing:
                raise CutoverBlocked("This server has retired; refresh the page.")
            if not depth and self.held and not withdrawal:
                raise CutoverBlocked("YouTube 切换暂停中；新处理与连接变更暂不可用，已有资料仍可阅读。")
            if not depth:
                if self.held and self._state.get("drained"):
                    previous = self._state.copy()
                    self._state["drained"] = False
                    if self._state["state"] != "aborted":
                        self._state.update(state="draining", reason="正在保存用户最新操作，等待写者结束。")
                    try:
                        self._save()
                    except Exception:
                        self._state = previous
                        raise
                self._active += 1
                self._generation += 1
            self._local.depth = depth + 1
        try:
            yield
        finally:
            with self._lock:
                self._local.depth -= 1
                if not depth:
                    self._active -= 1
                    self._condition.notify_all()

    def seal(self):
        """Normal process exit blocks new work without changing durable intent."""
        with self._lock:
            self._closing = True

    def wait_idle(self):
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0 and not self._drain_closing)

    def begin(self, stop):
        with self._lock:
            if not self.held:
                now = self.workspace.now()
                previous = self._state
                self._state = dict(held=True, state="draining", started_at=now,
                                   deadline_at=now + 1800, drained=False,
                                   reason="等待在途工作保存成果和所有连接写者退出。")
                try:
                    self._save()
                except Exception:
                    self._state = previous
                    raise
                self._monotonic_deadline = time.monotonic() + 1800
            # Persist before stopping schedulers; never toggle Auto Update.
            stop.set()
            return self.status()

    def abort(self):
        with self._lock:
            if self.held and self._state["state"] != "aborted":
                self._state.update(state="aborted", reason="维护者已中止切换；保留最新资料，采集继续暂停。")
                self._save()
            return self.status()

    def status(self):
        with self._lock:
            expired = self.held and (self.workspace.now() >= self._state["deadline_at"]
                or (self._monotonic_deadline is not None and time.monotonic() >= self._monotonic_deadline))
            if expired and self._state["state"] != "aborted":
                self._state.update(state="aborted", reason="30 分钟内未完成安全收尾与正式验证，已中止切换；在途工作继续保存，采集保持暂停。")
                self._save()
            return {**self._state, "active_requests": self._active,
                    "drained": bool(self._state.get("drained")) and self._active == 0}

    def observe_drain(self, threads, close_browser):
        with self._lock:
            state = self.status()
            if (not self.held or self._active or self._drain_closing or self._drain_failed
                    or self._state.get("drained") or any(thread.is_alive() for thread in threads)):
                return state
            self._drain_closing = True
            generation = self._generation
            threading.Thread(target=self._finish_drain, args=(close_browser, generation),
                             name="edison-cutover-browser-close", daemon=True).start()
            return state

    def _finish_drain(self, close_browser, generation):
        # A browser/profile flush must not hold the admission lock: deadline
        # observation and newer cancellation/disconnection remain responsive.
        failed = False
        try:
            close_browser()
        except Exception:
            failed = True
        with self._lock:
            self._drain_closing = False
            self.status()  # Closing cannot extend the original attempt deadline.
            if failed:
                self._drain_failed = True
                self._state.update(state="aborted", reason="浏览器写者尚未确认退出；采集保持暂停，请检查服务。")
            elif self._active == 0 and self._generation == generation:
                self._state["drained"] = True
                if self._state["state"] != "aborted":
                    self._state.update(state="ready", reason="在途成果已保存，采集线程与连接写者已结束；等待正式验证，采集保持暂停。")
            self._save()
            self._condition.notify_all()


@contextmanager
def admitted(workspace):
    """Core/legacy entry points share the server lease or the durable barrier."""
    hold = getattr(workspace, "cutover", None)
    if hold is not None:
        with hold.work():
            yield
    else:
        if json.loads(workspace.meta("youtube_cutover") or "{}").get("held"):
            raise CutoverBlocked("YouTube 切换暂停中；新处理与连接变更暂不可用。")
        yield

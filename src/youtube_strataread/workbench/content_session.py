"""One server browser writes the profile; consumers receive disposable leases.

Credentials live outside the reading library. The owner lock spans the host's
lifetime, and the browser also holds it if the host dies before its child exits.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from youtube_strataread.downloader.srt import load_cues
from youtube_strataread.utils.process_lock import acquire_process_lock


class ContentSessionError(RuntimeError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def content_failure(error):
    """Only safe domain messages leave provider or filesystem boundaries."""
    from youtube_strataread.downloader.request_policy import (
        ConfirmedCaptionAbsence,
        YouTubeRateLimited,
    )

    if isinstance(error, ContentSessionError):
        return error
    if isinstance(error, YouTubeRateLimited):
        return ContentSessionError("rate_limit", "YouTube 正在冷却，请等待下一次检查。")
    if isinstance(error, ConfirmedCaptionAbsence):
        return ContentSessionError(
            "no_captions", "此视频已确认暂无字幕，请使用有字幕的视频检查连接。"
        )
    if isinstance(error, OSError):
        return ContentSessionError(
            "maintenance", "内容会话存储或浏览器运行环境不可用，请检查服务权限。"
        )
    return ContentSessionError("unknown", "内容获取失败，原因尚未确认。")


@dataclass(frozen=True)
class Snapshot:
    path: Path
    generation: int


class ContentSession:
    def __init__(self, root: Path, *, browser=None, now=time.time, resources=None):
        self.root = root.resolve()
        self.now = now
        self._lock = threading.RLock()
        self._owner = None
        self._retired = False
        self._verified = False
        self._running = False
        self._last_input = 0.0
        self._browser = browser
        self._state = {}
        self.resources = resources
        self._open_pending = False

    def _claim(self):
        if self._retired:
            raise ContentSessionError("maintenance", "内容会话已停止，请刷新页面。")
        if self._owner:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.getuid() == 0 or self.root.stat().st_uid != os.getuid():
                raise OSError("owner")
            os.chmod(self.root, 0o700)
            self._owner = acquire_process_lock(self.root / "owner.lock")
            owner = self.root / "service-uid"
            if owner.exists() and owner.read_text() != str(os.getuid()):
                raise OSError("identity changed")
            self._atomic(owner, str(os.getuid()))
            state = self.root / "state.json"
            self._state = json.loads(state.read_text()) if state.exists() else {"generation": 0}
            # Orphaned disposable copies never become a durable session source.
            shutil.rmtree(self.root / "consumers", ignore_errors=True)
            (self.root / "consumers").mkdir(mode=0o700)
        except (OSError, ValueError):
            if self._owner:
                self._owner.close()
                self._owner = None
            raise ContentSessionError(
                "maintenance", "内容会话存储或服务身份不可用，或另一服务已持有此会话。"
            ) from None

    @staticmethod
    def _atomic(path, text):
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _save(self):
        self._atomic(self.root / "state.json", json.dumps(self._state))

    def status(self):
        with self._lock:
            self._claim()
            if self._running and hasattr(self._browser, "alive") and not self._browser.alive:
                released = getattr(self._browser, "resource_released", False)
                self._browser.close()
                self._running = False
                if released and self.resources:
                    self.resources.browser_released()
                    self._open_pending = True
                self.record_failure(ContentSessionError(
                    "resource" if released else "maintenance",
                    "维护浏览器已因资源压力关闭，稍后再检查资源。" if released
                    else "服务器浏览器意外退出，请检查运行环境。"))
            error = self._state.get("failure_kind")
            return {
                "state": (
                    "waiting_resources"
                    if error == "resource"
                    else "needs_user"
                    if error == "user"
                    else "needs_maintenance"
                    if error == "maintenance"
                    else "interrupted"
                    if error
                    else "available"
                    if self._verified
                    else "unverified"
                ),
                "browser_running": self._running,
                "browser_pending": self._open_pending,
                "resources": self.resources.status() if self.resources else None,
                "snapshot_present": (self.root / "current.cookies").exists(),
                "account_consistency": self._state.get("account_consistency"),
                "last_success_at": self._state.get("last_success_at"),
                "last_failure_at": self._state.get("last_failure_at"),
                "failure_kind": error,
                "reason": self._state.get("reason"),
                "generation": self._state.get("generation", 0),
                "idle_closes_at": self._last_input + 900 if self._running else None,
            }

    @contextmanager
    def _control(self):
        """Publish browser/control failures before releasing ownership coordination."""
        with self._lock:
            try:
                self._claim()
                yield
            except (ContentSessionError, OSError) as error:
                safe = content_failure(error)
                self.record_failure(safe)
                raise safe from None

    def open(self):
        with self._control():
            self._last_input = self.now()
            self._open_pending = True
            if not self._running:
                if self.resources:
                    self.resources.require_browser_start()
                if self._browser is None:
                    from youtube_strataread.workbench.session_browser import ServerBrowser

                    self._browser = ServerBrowser(owner_fd=self._owner.fileno())
                try:
                    self._browser.start(self.root / "profile")
                except ContentSessionError as error:
                    if error.kind == "resource" and self.resources:
                        self.resources.browser_released()
                    raise
                self._running = True
            self._open_pending = False
            if self._state.get("failure_kind") == "resource":
                self._state.update(failure_kind=None, reason=None)
                self._save()
            self._last_input = self.now()
            return self.status()

    def browser_command(self, command, arguments=None):
        with self._control():
            if not self._running:
                raise ContentSessionError("user", "服务器浏览器已关闭，请重新打开。")
            try:
                result = self._browser.command(command, arguments or {})
            except (ContentSessionError, OSError) as error:
                if not isinstance(error, ContentSessionError) or error.kind in {"maintenance", "resource"}:
                    self._browser.close()
                    self._running = False
                    if isinstance(error, ContentSessionError) and error.kind == "resource" and self.resources:
                        self.resources.browser_released()
                        self._open_pending = True
                raise
            if command != "screen":
                self._last_input = self.now()
            return result

    def tick(self):
        with self._lock:
            if (self._running or self._open_pending) and self.now() >= self._last_input + 900:
                self.close_browser()
                return
            if not self.resources:
                return
            if self._running and hasattr(self._browser, "alive") and not self._browser.alive:
                self.status()
            state = self.resources.status()
            if self._running and state["release_browser"]:
                self._browser.close()
                self._running = False
                self._open_pending = True
                self.resources.browser_released()
                self.record_failure(ContentSessionError("resource", state["reason"]))
            elif self._open_pending and not state["browser_waiting"]:
                last_input = self._last_input
                try:
                    self.open()
                except (ContentSessionError, OSError):
                    pass
                finally:
                    self._last_input = last_input
            elif not self._open_pending and not state["captions_waiting"] and self._state.get("failure_kind") == "resource":
                self._state.update(failure_kind=None, reason=None)
                self._save()

    def close_browser(self):
        with self._control():
            self._open_pending = False
            if self._running:
                self._browser.close()
                self._running = False
            return self.status()

    def confirm(self, video_id, *, same_account, fetch):
        if same_account is not True:
            raise ContentSessionError(
                "user", "请确认内容登录与 Google 官方授权使用同一 Google 账号。"
            )
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ContentSessionError("unknown", "请输入有效的 11 位字幕检查视频 ID。")
        with self._lock:
            self._claim()
            if not self._running:
                raise ContentSessionError("user", "请先打开服务器浏览器并完成 YouTube 登录。")
            self._last_input = self.now()
            try:
                try:
                    cookies = self._browser.snapshot()
                finally:
                    # Native snapshot reads only after orderly profile flush. All
                    # adapters end handoff here, including failed extraction.
                    self._browser.close()
                    self._running = False
                self._atomic(self.root / "current.cookies", cookies)
                self._state.update(
                    generation=self._state.get("generation", 0) + 1,
                    account_consistency="user_confirmed_not_verified",
                )
                self._verified = False
                self._save()
            except (ContentSessionError, OSError) as error:
                safe = content_failure(error)
                self.record_failure(safe)
                raise safe from None
        return self.verify(video_id, fetch=fetch)

    def verify(self, video_id, *, fetch):
        try:
            self.acquire(video_id, fetch=fetch)
        except Exception as error:
            raise content_failure(error) from None
        return self.status()

    def acquire(self, video_id, *, fetch):
        """One result-publication boundary for Web checks and queued captions.

        Keep original typed errors for the scheduler, while recording only safe
        messages. Both success and failure publication check the generation under
        the same lock as their state write; callers must not publish them again.
        """
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ContentSessionError("unknown", "请输入有效的 11 位字幕检查视频 ID。")
        with self._lock:
            self._claim()
            lease = Snapshot(self.root / "current.cookies", self._state["generation"])
        try:
            if self.resources:
                self.resources.require_captions()
            with self.snapshot() as lease:
                result = fetch(f"https://www.youtube.com/watch?v={video_id}", lease.path)
                self.accept(lease, result, video_id)
                return result
        except Exception as error:
            self.record_failure(content_failure(error), lease=lease)
            raise

    @contextmanager
    def snapshot(self):
        if self.resources:
            self.resources.require_captions()
        with self._lock:
            self._claim()
            current = self.root / "current.cookies"
            if not current.exists():
                raise ContentSessionError("user", "尚未完成服务器 YouTube 内容登录。")
            directory = Path(tempfile.mkdtemp(dir=self.root / "consumers"))
            path = directory / "cookies.txt"
            self._atomic(path, current.read_text())
            lease = Snapshot(path, self._state["generation"])
        try:
            yield lease
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def accept(self, lease, result, video_id):
        with self._lock:
            self.assert_current(lease)
            try:
                valid = (
                    result.video_id == video_id
                    and result.language != "live_chat"
                    and bool(load_cues(result.srt_text))
                )
            except (ValueError, AttributeError):
                valid = False
            if not valid:
                raise ContentSessionError(
                    "unknown", "字幕结果为空、不可解析或视频身份不匹配，内容能力尚未验证。"
                )
            self._verified = True
            self._state.update(last_success_at=self.now(), failure_kind=None, reason=None)
            self._save()

    def assert_current(self, lease):
        if self._retired or lease.generation != self._state.get("generation"):
            raise ContentSessionError("invalidated", "内容会话已更新或断开；旧请求结果已丢弃。")

    def record_failure(self, error, *, lease=None):
        with self._lock:
            if lease is not None:
                self.assert_current(lease)
            if self._retired or error.kind == "invalidated":
                return
            if error.kind != "resource":
                self._verified = False
            self._state.update(
                last_failure_at=self.now(), failure_kind=error.kind, reason=str(error)
            )
            if self._owner:
                try:
                    self._save()
                except OSError:
                    # Reporting a storage failure must not itself need storage.
                    self._state.update(
                        failure_kind="maintenance", reason="内容会话状态无法保存，请修复存储权限。"
                    )

    def clear(self):
        """Disconnect boundary: stop writer first, invalidate leases, remove secrets."""
        with self._lock:
            self._claim()
            self.close_browser()
            self._state = {"generation": self._state.get("generation", 0) + 1}
            self._verified = False
            self._save()
            for name in ("profile", "consumers"):
                shutil.rmtree(self.root / name, ignore_errors=False) if (
                    self.root / name
                ).exists() else None
            (self.root / "current.cookies").unlink(missing_ok=True)
            (self.root / "consumers").mkdir(mode=0o700)
            return self.status()

    def shutdown(self):
        with self._lock:
            if self._owner:
                self.close_browser()
                self._owner.close()
                self._owner = None
            self._retired = True

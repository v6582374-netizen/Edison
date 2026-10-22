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
from dataclasses import dataclass, field
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
    source: str = field(default="", repr=False, compare=False)


HANDOFF_IDLE = 15 * 60
MAINTENANCE_LIMIT = 3 * 60
# The profile's own page needs time to refresh its session before the orderly
# close; this is an operating choice, not a measured Google rotation interval.
MAINTENANCE_SETTLE = 45
MAINTENANCE_BUDGET = 2
MAINTENANCE_WINDOW = 24 * 60 * 60
MAINTENANCE_RECHECK = 60
# Identity cookies whose replacement in a consumer's response means Google moved
# the session on without the profile. Rapidly churning *CC values are excluded.
_IDENTITY_COOKIES = re.compile(r"^(?:__Secure-[13]P|S|H)?(?:SID|SIDTS|APISID)$|^LOGIN_INFO$")
MAINTENANCE_REASON = "内容会话需要维护，后台将自动打开服务器浏览器（每次最多 3 分钟），完成后继续获取字幕。"
HANDOFF_REASON = (
    "Google 要求你亲自完成登录或安全验证。请打开服务器浏览器，在 Google 页面输入密码或验证码；"
    "后台已停止相关自动尝试。"
)


def _identity_cookies(text):
    values = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 7 and not line.startswith("#") and _IDENTITY_COOKIES.match(fields[5]):
            values[(fields[0], fields[5])] = fields[6]
    return values


class ContentSession:
    def __init__(self, root: Path, *, browser=None, now=time.time, resources=None,
                 requests=None, unattended=False):
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
        self._leases: dict[Path, int] = {}
        self.resources = resources
        self.requests = requests
        self.unattended = unattended
        self._open_pending = False
        self._maintaining = False
        self._maintenance_wait = {}

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
            if self._state.pop("maintenance_started_at", None) is not None:
                # A previous host ended mid-maintenance; its attempt stays spent.
                self._state.pop("maintenance_generation", None)
                self._save()
            if self._state.get("disconnected"):
                if (self.root / "profile").exists():
                    shutil.rmtree(self.root / "profile")
                (self.root / "current.cookies").unlink(missing_ok=True)
                self._state["cleanup_pending"] = False
                self._save()
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
                if released:
                    self._browser.release_resources()
                else:
                    self._browser.close()
                self._running = False
                if released and self.resources:
                    self.resources.browser_released()
                    self._open_pending = not self._state.get("disconnected", False) and not self._maintaining
                failure = ContentSessionError(
                    "resource" if released else "maintenance",
                    "维护浏览器已因资源压力关闭，稍后再检查资源。" if released
                    else "服务器浏览器意外退出，请检查运行环境。")
                if self._maintaining:
                    self._end_maintenance(failure)
                else:
                    self.record_failure(failure)
            error = self._state.get("failure_kind")
            return {
                "state": (
                    "disconnected"
                    if self._state.get("disconnected")
                    else "waiting_resources"
                    if error == "resource"
                    else "needs_user"
                    if error == "user"
                    else "maintaining"
                    if error == "session"
                    else "needs_maintenance"
                    if error == "maintenance"
                    else "interrupted"
                    if error
                    else "available"
                    if self._verified
                    else "unverified"
                ),
                "maintenance": self._maintenance_status(),
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
                "idle_closes_at": (self._last_input + HANDOFF_IDLE
                                   if self._running and not self._maintaining else None),
            }

    def _recent_starts(self, now):
        return sorted(t for t in self._state.get("maintenance_starts", []) if t > now - MAINTENANCE_WINDOW)

    def _maintenance_status(self):
        now = self.now()
        starts = self._recent_starts(now)
        started = self._state.get("maintenance_started_at")
        due = self._state.get("maintenance_due")
        state, reason, next_at = "idle", self._state.get("maintenance_reason"), None
        if self._state.get("handoff"):
            state, reason = "handoff", HANDOFF_REASON
        elif started is not None:
            state, reason = "running", "后台正在自动维护内容会话，最多 3 分钟后关闭浏览器。"
        elif due and len(starts) >= MAINTENANCE_BUDGET:
            state, next_at = "waiting_budget", starts[0] + MAINTENANCE_WINDOW
            reason = "自动维护在滚动 24 小时内已用完 2 次，等待下一窗口；需要时可亲自打开服务器浏览器。"
        elif due and self._maintenance_wait.get("until", 0) > now:
            state = self._maintenance_wait["state"]
            reason, next_at = self._maintenance_wait["reason"], self._maintenance_wait["until"]
        elif due:
            state = "due"
        return {
            "state": state,
            "basis": due,
            "reason": reason,
            "next_at": next_at,
            "deadline_at": started + MAINTENANCE_LIMIT if started is not None else None,
            "used": len(starts),
            "limit": MAINTENANCE_BUDGET,
        }

    def acquisition_ready(self):
        """New captions wait while the current snapshot is known to be refused."""
        with self._lock:
            if self.unattended and not self._retired:
                try:
                    self._claim()
                except ContentSessionError:
                    return True  # acquisition itself reports the storage failure
            return not (self._state.get("handoff") or self._state.get("maintenance_due") == "signed_out")

    def _end_maintenance(self, error=None):
        self._maintaining = False
        self._state.pop("maintenance_started_at", None)
        self._state.pop("maintenance_generation", None)
        if error is not None:
            if error.kind == "user":
                self._hand_off()
            elif error.kind == "resource":
                self._maintenance_wait = {"state": "waiting_resources", "until": self.now() + MAINTENANCE_RECHECK,
                                          "reason": str(error)}
            else:
                self._state["maintenance_reason"] = str(error)
                if error.kind == "maintenance":
                    self.record_failure(error)
        self._save()

    def _hand_off(self):
        self._verified = False
        self._state.pop("maintenance_due", None)
        self._state.update(handoff=True, failure_kind="user", reason=HANDOFF_REASON, last_failure_at=self.now())

    def _session_lost(self, lease):
        """A once-working snapshot was refused; initial connection keeps its own path."""
        with self._lock:
            self.assert_current(lease)
            if self._state.get("disconnected") or not self._state.get("last_success_at"):
                return None
            if self._state.get("handoff") or lease.generation == self._state.get("maintained_generation"):
                self._hand_off()
            else:
                self._verified = False
                self._state.update(maintenance_due="signed_out", failure_kind="session",
                                   reason=MAINTENANCE_REASON, last_failure_at=self.now())
            self._save()
            return ContentSessionError("session", self._state["reason"])

    def _observe_response(self, lease):
        try:
            returned = _identity_cookies(lease.path.read_text())
        except OSError:
            return
        original = _identity_cookies(lease.source)
        if not any(original.get(key, value) != value for key, value in returned.items()):
            return
        with self._lock:
            if (not self._retired and lease.generation == self._state.get("generation")
                    and not self._state.get("disconnected") and not self._state.get("handoff")
                    and self._state.get("last_success_at") and not self._state.get("maintenance_due")):
                self._state["maintenance_due"] = "rotation"
                self._save()

    def _begin_maintenance(self):
        now = self.now()
        if (not self._state.get("maintenance_due") or self._state.get("handoff") or self._running
                or self._state.get("cleanup_pending") or self._state.get("disconnected")):
            return
        starts = self._recent_starts(now)
        if len(starts) >= MAINTENANCE_BUDGET or self._maintenance_wait.get("until", 0) > now:
            return
        if self.requests is not None:
            until = self.requests.snapshot()["cooldown_until"]
            if until > now:
                self._maintenance_wait = {"state": "waiting_cooldown", "until": until,
                                          "reason": "YouTube 请求正在冷却，冷却结束后再维护内容会话。"}
                return
        if self.resources:
            state = self.resources.status()
            if state["browser_waiting"]:
                self._maintenance_wait = {"state": "waiting_resources", "until": now + MAINTENANCE_RECHECK,
                                          "reason": state["reason"]}
                return
        self._maintenance_wait = {}
        # Spend the attempt durably before any browser exists, so neither a
        # restart nor a crash during the run can refund it.
        self._state.update(maintenance_starts=starts + [now], maintenance_started_at=now,
                           maintenance_generation=self._state.get("generation", 0))
        self._state.pop("maintenance_reason", None)
        self._save()
        self._maintaining = True
        self._last_input = now
        try:
            if self._browser is None:
                from youtube_strataread.workbench.session_browser import ServerBrowser

                self._browser = ServerBrowser(owner_fd=self._owner.fileno())
            self._browser.start(self.root / "profile")
        except (ContentSessionError, OSError) as error:
            self._end_maintenance(content_failure(error))
            return
        self._running = True

    def _advance_maintenance(self):
        started = self._state.get("maintenance_started_at")
        alive = getattr(self._browser, "alive", self._running)
        if (started is None or self._state.get("disconnected") or not self._running or not alive
                or self._state.get("maintenance_generation") != self._state.get("generation")):
            if self._running:
                self._browser.close()
                self._running = False
            self._end_maintenance()
            return
        if self.now() >= started + MAINTENANCE_LIMIT:
            self._browser.close()
            self._running = False
            self._end_maintenance(ContentSessionError(
                "timeout", "自动维护 3 分钟内未完成，已关闭浏览器；等待下一次维护机会。"))
            return
        if self.resources and (state := self.resources.status())["release_browser"]:
            self._browser.release_resources(state)
            self._running = False
            self.resources.browser_released()
            self._end_maintenance(ContentSessionError("resource", state["reason"]))
            return
        if self.now() < started + MAINTENANCE_SETTLE:
            return
        try:
            try:
                cookies = self._browser.snapshot()
            finally:
                self._browser.close()
                self._running = False
            self._atomic(self.root / "current.cookies", cookies)
        except (ContentSessionError, OSError) as error:
            self._end_maintenance(content_failure(error))
            return
        generation = self._state.get("generation", 0) + 1
        self._verified = False
        self._state.pop("maintenance_due", None)
        self._state.update(generation=generation, maintained_generation=generation, failure_kind=None, reason=None)
        self._end_maintenance()

    @contextmanager
    def _control(self):
        """Publish browser/control failures before releasing ownership coordination."""
        observed = self._state.get("generation") if self._owner else None
        with self._lock:
            try:
                self._claim()
                if observed is not None and observed != self._state.get("generation"):
                    raise ContentSessionError("invalidated", "内容会话已更新或断开；旧请求结果已丢弃。")
                yield
            except (ContentSessionError, OSError) as error:
                safe = content_failure(error)
                if safe.kind != "resource":
                    self._open_pending = False
                self.record_failure(safe)
                raise safe from None

    def open(self):
        with self._control():
            if self._state.get("cleanup_pending"):
                raise ContentSessionError("maintenance", "内容会话清理尚未完成，请再次断开后再重新连接。")
            if self._state.get("disconnected"):
                self._state["disconnected"] = False
                self._save()
            if self._maintaining:
                # The person takes over the one profile browser; the spent attempt stays spent.
                self._end_maintenance()
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
                except BaseException as error:
                    if isinstance(error, ContentSessionError) and error.kind == "resource":
                        if self.resources:
                            self.resources.browser_released()
                    else:
                        self._open_pending = False
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
            if self._state.get("disconnected"):
                raise ContentSessionError("invalidated", "内容会话已断开，请完成清理后重新连接。")
            if not self._running:
                raise ContentSessionError("user", "服务器浏览器已关闭，请重新打开。")
            try:
                result = self._browser.command(command, arguments or {})
            except (ContentSessionError, OSError) as error:
                if not isinstance(error, ContentSessionError) or error.kind in {"maintenance", "resource"}:
                    if isinstance(error, ContentSessionError) and error.kind == "resource":
                        self._browser.release_resources()
                    else:
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
            if self.unattended and not self._retired:
                try:
                    self._claim()
                except ContentSessionError:
                    return
            if self._state.get("disconnected"):
                self._open_pending = False
                return
            if self._maintaining:
                self._advance_maintenance()
                return
            if self.unattended and not self._open_pending:
                self._begin_maintenance()
                if self._maintaining:
                    return
            if (self._running or self._open_pending) and self.now() >= self._last_input + HANDOFF_IDLE:
                self.close_browser()
                return
            if not self.resources:
                return
            if self._running and hasattr(self._browser, "alive") and not self._browser.alive:
                self.status()
            state = self.resources.status()
            if self._running and state["release_browser"]:
                self._browser.release_resources(state)
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
            if self._maintaining:
                self._end_maintenance()
            return self.status()

    def confirm(self, video_id, *, same_account, fetch):
        if same_account is not True:
            raise ContentSessionError(
                "user", "请确认内容登录与 Google 官方授权使用同一 Google 账号。"
            )
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ContentSessionError("unknown", "请输入有效的 11 位字幕检查视频 ID。")
        observed = self._state.get("generation") if self._owner else None
        with self._lock:
            self._claim()
            if observed is not None and observed != self._state.get("generation"):
                raise ContentSessionError("invalidated", "内容会话已更新或断开；旧请求结果已丢弃。")
            if self._state.get("disconnected") or not self._running:
                raise ContentSessionError("user", "请先打开服务器浏览器并完成 YouTube 登录。")
            if self._maintaining:
                self._end_maintenance()
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
            self.acquire(video_id, fetch=fetch, maintain=False)
        except Exception as error:
            raise content_failure(error) from None
        return self.status()

    def acquire(self, video_id, *, fetch, maintain=True):
        """One result-publication boundary for Web checks and queued captions.

        Keep original typed errors for the scheduler, while recording only safe
        messages. Both success and failure publication check the generation under
        the same lock as their state write; callers must not publish them again.
        A refused background snapshot becomes a "session" wait, never a task failure.
        """
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ContentSessionError("unknown", "请输入有效的 11 位字幕检查视频 ID。")
        with self._lock:
            self._claim()
            lease = Snapshot(self.root / "current.cookies", self._state["generation"])
        try:
            if self.resources:
                self.resources.require_captions()
            with self.snapshot(expected_generation=lease.generation) as lease:
                try:
                    result = fetch(f"https://www.youtube.com/watch?v={video_id}", lease.path)
                finally:
                    if self.unattended:
                        self._observe_response(lease)
                self.accept(lease, result, video_id)
                return result
        except Exception as error:
            safe = content_failure(error)
            if maintain and self.unattended and safe.kind == "user":
                lost = self._session_lost(lease)
                if lost is not None:
                    raise lost from None
            self.record_failure(safe, lease=lease)
            raise

    @contextmanager
    def snapshot(self, *, expected_generation=None):
        observed = expected_generation if expected_generation is not None else self._state.get("generation") if self._owner else None
        if self.resources:
            self.resources.require_captions()
        with self._lock:
            self._claim()
            if observed is not None and observed != self._state.get("generation"):
                raise ContentSessionError("invalidated", "内容会话已更新或断开；旧请求结果已丢弃。")
            current = self.root / "current.cookies"
            if self._state.get("disconnected") or not current.exists():
                raise ContentSessionError("user", "尚未完成服务器 YouTube 内容登录。")
            directory = Path(tempfile.mkdtemp(dir=self.root / "consumers"))
            path = directory / "cookies.txt"
            source = current.read_text()
            self._atomic(path, source)
            lease = Snapshot(path, self._state["generation"], source)
            self._leases[path] = lease.generation
        try:
            yield lease
        finally:
            with self._lock:
                self._leases.pop(path, None)
            shutil.rmtree(directory, ignore_errors=True)

    def snapshot_active(self, path):
        """Permission is independent of successful credential-file deletion."""
        with self._lock:
            return (not self._retired and not self._state.get("disconnected")
                    and path in self._leases
                    and self._leases[path] == self._state.get("generation"))

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
            for proven in ("handoff", "maintained_generation", "maintenance_reason"):
                self._state.pop(proven, None)
            if self._state.get("maintenance_due") == "signed_out":
                self._state.pop("maintenance_due")
            self._save()

    def assert_current(self, lease):
        if self._retired or lease.generation != self._state.get("generation"):
            raise ContentSessionError("invalidated", "内容会话已更新或断开；旧请求结果已丢弃。")

    def record_failure(self, error, *, lease=None):
        with self._lock:
            if lease is not None:
                self.assert_current(lease)
            if self._retired or self._state.get("disconnected") or error.kind == "invalidated":
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
        """Withdraw leases durably, stop the writer, then remove its profile."""
        with self._lock:
            self._claim()
            # The maintenance budget is not a credential; reconnecting cannot refund it.
            self._state = {"generation": self._state.get("generation", 0) + 1, "disconnected": True, "cleanup_pending": True,
                           "maintenance_starts": self._state.get("maintenance_starts", [])}
            self._verified = False
            try:
                self._save()
                if (self.root / "consumers").exists():
                    shutil.rmtree(self.root / "consumers", ignore_errors=False)
                (self.root / "current.cookies").unlink(missing_ok=True)
                (self.root / "consumers").mkdir(mode=0o700)
            finally:
                # Failure to erase secrets must never keep their writer alive.
                # Readers use the in-memory generation fence, not file absence.
                self.close_browser()
            for name in ("profile", "consumers"):
                shutil.rmtree(self.root / name, ignore_errors=False) if (
                    self.root / name
                ).exists() else None
            (self.root / "current.cookies").unlink(missing_ok=True)
            (self.root / "consumers").mkdir(mode=0o700)
            self._state["cleanup_pending"] = False
            self._save()
            return self.status()

    def shutdown(self):
        with self._lock:
            if self._owner:
                self.close_browser()
                self._owner.close()
                self._owner = None
            self._retired = True

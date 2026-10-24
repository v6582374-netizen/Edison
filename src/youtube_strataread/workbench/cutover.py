"""Durable admission barrier for a bounded YouTube cutover attempt.

A hold never snapshots execution permissions. In-flight work retains its lease
until its caller returns after saving results; withdrawal remains admissible.
A library written by a newer program holds this program the same way without
a durable record: reading continues and acquisition stays stopped.
"""
from __future__ import annotations

import json
import copy
import math
import threading
import time
from contextlib import contextmanager

from youtube_strataread.workbench.youtube_api import ConnectionError


class CutoverBlocked(ConnectionError):
    pass


class CutoverHold:
    def __init__(self, workspace, *, context=None, bootstrap_started_at=None):
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
        self._incompatible = _incompatible_reason(workspace)
        saved = workspace.meta("youtube_cutover")
        self._state = json.loads(saved or '{"held":false,"state":"idle"}')
        if not saved and bootstrap_started_at is not None and not self._incompatible:
            started = float(bootstrap_started_at)
            if not math.isfinite(started) or started > workspace.now():
                raise ValueError("维护起点必须是已发生的有限时间，不能延后维护窗口。")
            self._state = dict(held=True, state="draining", started_at=started,
                               deadline_at=started + 1800, drained=False,
                               reason="维护启动已暂停采集，等待证明全部写者结束。")
            self._save()
        if self._state.get("held") and not self._incompatible:
            validation = self._state.get("validation") or {}
            if (validation.get("restart_clean") and validation.get("context") == context
                    and self._state.get("state") == "validating"
                    and workspace.now() < self._state["deadline_at"]):
                validation.update(restart_clean=False, restarted=True, restart_requested=False)
                validation["proofs"].pop("official", None)
                validation["proofs"].pop("restarted", None)
                self._state.update(reason="计划重启已完成；请重新实际验证订阅与字幕，采集继续暂停。")
            elif saved:
                self._state.update(state="aborted", drained=False)
                if not self._state.get("restored_from"):  # A restore notice outlives restarts.
                    self._state["reason"] = "服务重启已中止切换；保留最新资料，采集继续暂停。"
            self._monotonic_deadline = time.monotonic() + max(0, self._state["deadline_at"] - workspace.now())
            self._save()

    def _save(self):
        if self._incompatible:
            return  # Never reinterpret a newer program's durable cutover record.
        self.workspace.set_meta("youtube_cutover", json.dumps(self._state, ensure_ascii=False))

    @property
    def held(self):
        with self._lock:
            return bool(self._state.get("held")) or self._incompatible is not None

    @property
    def incompatible(self):
        return self._incompatible

    def quiescent(self):
        """The admission generation while a durable hold has proven every writer stopped."""
        with self._lock:
            status = self.status()
            if (self._state.get("held") and status["drained"] and not self._drain_closing
                    and not self._drain_failed and not self._incompatible):
                return self._generation
            return None

    @contextmanager
    def work(self, *, withdrawal=False):
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            if not depth and self._closing:
                raise CutoverBlocked("This server has retired; refresh the page.")
            if not depth and self._incompatible and not withdrawal:
                raise CutoverBlocked(self._incompatible)
            if not depth and self.held and not withdrawal:
                raise CutoverBlocked("YouTube 切换暂停中；新处理与连接变更暂不可用，已有资料仍可阅读。")
            if not depth:
                if self._state.get("held") and self._state.get("drained") and not self.validating:
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

    @property
    def validating(self):
        with self._lock:
            return self.status().get("state") == "validating"

    def begin_validation(self, context):
        with self._lock:
            if self.status().get("state") != "ready" or self.quiescent() is None:
                raise CutoverBlocked("必须先等待所有写者结束，再开始正式验证。")
            self._state.update(state="validating", reason="正式连接验证中；自动与手动处理继续暂停。",
                               validation={"context": context, "proofs": {}, "revision": 0})
            self._save()
            return self.status()

    @contextmanager
    def validation_work(self):
        """Explicit operator scope; nested connection operations share its lease."""
        with self._lock:
            if (not self.validating or self._closing or self._active or self._drain_closing
                    or self._state["validation"].get("restart_requested")):
                raise CutoverBlocked("正式验证未开始、已中止，或另一操作尚未结束。")
            self._active += 1
            self._generation += 1
            self._local.depth = 1
            revision = self._state["validation"]["revision"]
        try:
            yield revision
        finally:
            with self._lock:
                self._local.depth = 0
                self._active -= 1
                self._condition.notify_all()

    def validation_proof(self, revision, key, value):
        with self._lock:
            if not self.validating or self._state["validation"]["revision"] != revision:
                raise CutoverBlocked("连接已变更或切换已中止，旧验证结果已丢弃。")
            self._state["validation"]["proofs"][key] = value
            self._save()

    def reset_validation(self, revision, *, keys=("initial", "closed", "restarted")):
        with self._lock:
            if not self.validating or self._state["validation"]["revision"] != revision:
                raise CutoverBlocked("正式验证已失效。")
            proofs = self._state["validation"]["proofs"]
            if "initial" in keys:
                self._state["validation"]["restarted"] = False
            for key in keys:
                proofs.pop(key, None)
            self._save()

    def prepare_restart(self):
        with self._lock:
            self._require_validation_idle()
            validation = self._state["validation"]
            if not {"official", "initial", "closed"} <= validation["proofs"].keys():
                raise CutoverBlocked("必须先实际验证订阅、同账号内容登录和关闭浏览器后的新字幕进程。")
            validation.update(restart_requested=True, restart_clean=False)
            self._state["reason"] = "等待维护者正常重启服务；原维护截止时间保持不变。"
            self._save()
            return self.status()

    def closed_normally(self):
        """Receipt is written only after every worker, lease and profile is closed."""
        with self._lock:
            if self.validating and self._state["validation"].get("restart_requested") and not self._active:
                self._state["validation"]["restart_clean"] = True
                self._save()

    def _require_validation_idle(self):
        if not self.validating or self._active or self._drain_closing or self._closing:
            raise CutoverBlocked("正式验证未开始、已中止，或操作尚未结束。")

    def complete_validation(self, verify_current, prepare_workers):
        with self._lock:
            self._require_validation_idle()
            validation = self._state["validation"]
            proofs = validation["proofs"]
            if not validation.get("restarted") or not {"official", "initial", "closed", "restarted"} <= proofs.keys():
                raise CutoverBlocked("缺少重启后的真实订阅与字幕获取证据；采集继续暂停。")
            verify_current(proofs)
            # Start replacement threads behind a gate before making the durable
            # release. Thread allocation or storage failure leaves the hold set.
            release, cancel = prepare_workers()
            previous = copy.deepcopy(self._state)
            try:
                self._state.update(held=False, state="complete", completed_at=self.workspace.now(),
                                   reason="正式验证已通过；按当前用户许可恢复处理，等待持续观察验收。")
                self._save()
            except Exception:
                self._state = previous
                cancel()
                raise
            release()
            return self.status()

    def invalidate_validation(self):
        with self._lock:
            if self._state.get("validation"):
                self._state["validation"]["revision"] += 1
                self._state["validation"]["proofs"] = {}
                self.abort()

    def wait_idle(self):
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0 and not self._drain_closing)

    def begin(self, stop):
        with self._lock:
            if not self._state.get("held"):
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
            if self._state.get("held") and self._state["state"] != "aborted":
                self._state.update(state="aborted", reason="维护者已中止切换；保留最新资料，采集继续暂停。")
                self._save()
            return self.status()

    def status(self):
        with self._lock:
            expired = not self._incompatible and self._state.get("held") and (self.workspace.now() >= self._state["deadline_at"]
                or (self._monotonic_deadline is not None and time.monotonic() >= self._monotonic_deadline))
            if expired and self._state["state"] != "aborted":
                self._state.update(state="aborted", reason="30 分钟内未完成安全收尾与正式验证，已中止切换；在途工作继续保存，采集保持暂停。")
                self._save()
            status = {**self._state, "active_requests": self._active,
                      "drained": bool(self._state.get("drained")) and self._active == 0}
            if self._incompatible:
                library, program = self.workspace.newer_data_contract
                status.update(held=True, state="incompatible", reason=self._incompatible, drained=False,
                              library_contract=library, program_contract=program)
            return status

    def observe_drain(self, threads, close_browser):
        with self._lock:
            state = self.status()
            if (not self._state.get("held") or self._active or self._drain_closing or self._drain_failed
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
            try:
                self.status()  # Closing cannot extend the original attempt deadline.
                if failed:
                    self._drain_failed = True
                    self._state.update(state="aborted", reason="浏览器写者尚未确认退出；采集保持暂停，请检查服务。")
                elif self._active == 0 and self._generation == generation:
                    self._state["drained"] = True
                    if self._state["state"] != "aborted":
                        self._state.update(state="ready", reason="在途成果已保存，采集线程与连接写者已结束；等待正式验证，采集保持暂停。")
                self._save()
            except Exception:
                # The original durable hold remains authoritative on restart.
                # Report the storage fault without requiring another write to
                # the same failing storage or exposing a false ready state.
                self._drain_failed = True
                self._state.update(held=True, state="aborted", drained=False,
                                   reason="切换收尾状态无法保存；采集保持暂停，请修复存储后检查服务。")
            finally:
                # Completion notification is a lifecycle obligation even when
                # deadline publication or the final durable write fails.
                self._drain_closing = False
                self._condition.notify_all()


@contextmanager
def admitted(workspace):
    """Core/legacy entry points share the server lease or the durable barrier."""
    hold = getattr(workspace, "cutover", None)
    if hold is not None:
        with hold.work():
            yield
    else:
        if reason := _incompatible_reason(workspace):
            raise CutoverBlocked(reason)
        if json.loads(workspace.meta("youtube_cutover") or "{}").get("held"):
            raise CutoverBlocked("YouTube 切换暂停中；新处理与连接变更暂不可用。")
        yield


def _incompatible_reason(workspace):
    if not getattr(workspace, "newer_data_contract", None):
        return None
    return "资料库由更新的程序写入；本程序只提供阅读，采集与连接变更保持暂停。"

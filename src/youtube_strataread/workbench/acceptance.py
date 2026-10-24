"""Durable evidence for continuous YouTube availability and graded notices.

Thread heartbeats are not capability: this record keeps each capability's
outcomes, fault categories, next checks, maintenance windows, real task stages,
human interventions, resources and reading comparisons for whole observation
periods, independently of the short console. It never stores credentials,
cookies or provider bodies; details are built here from safe domain fields.

A calendar alone never passes acceptance. Controlled events (fault drills, or a
deployment whose external services are substituted) are accounted separately
and never stand in for natural coverage or count against natural limits.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path

DAY = 24 * 60 * 60
OBSERVATION_DAYS = 14
ESCALATE_AFTER = DAY
SECURITY_LIMIT = 3
SUBSCRIPTION_RETRY = 300  # the host's subscription watcher retries after five minutes
TASK_STAGES = frozenset({"commenced", "transcript_ready", "ready", "failed", "unavailable"})
CONTROLLED_ENV = "EDISON_ACCEPTANCE_CONTROLLED"
_STATE = "youtube_acceptance"
_OBSERVED = "youtube_acceptance_observed"
_DRILL = "youtube_acceptance_drill"

SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_evidence (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at REAL NOT NULL,
    kind TEXT NOT NULL,
    capability TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    controlled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS youtube_evidence_time ON youtube_evidence(occurred_at);
"""

# Categories that need a person or a maintainer immediately.
ATTENTION = frozenset({"user", "reauthorization", "authorization", "maintenance", "storage",
                       "configuration", "permission"})
# Per-video results and withdrawn requests are not capability faults.
IGNORED = frozenset({"", "no_captions", "private", "deleted", "access_denied", "invalidated",
                     "disconnect_pending", "channel"})
REAUTHORIZATION = frozenset({"reauthorization", "authorization"})

COVERAGE = {
    "daily_records": "每日订阅能力与字幕获取记录",
    "subscription_chain": "真实订阅到字幕再到成稿的完整链路",
    "token_refresh": "正常令牌刷新",
    "session_update": "内容会话更新",
    "natural_rotation": "自然 Cookie 轮换后的会话更新",
    "service_restart": "计划服务重启",
    "host_restart": "主机重启",
    "fault_recovery": "故障后无人值守恢复",
    "resources": "资源记录",
    "reading": "阅读耗时对照",
}
_SECRET = re.compile(r"(ya29\.|1//|\bSID\w*=|__Secure-|\t(?:TRUE|FALSE)\t|refresh_token|access_token|client_secret)", re.I)


def boot_id() -> str | None:
    """Kernel boot identity; another value after start means the host restarted."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


def build_id() -> str:
    """Deployment identity used to recognise a return to an earlier program."""
    try:
        import hashlib
        from importlib.metadata import version
        from importlib.util import find_spec

        host = find_spec("coworker")
        roots = [Path(__file__).resolve().parents[1]]
        if host and host.origin:
            roots.append(Path(host.origin).resolve().parent)
        digest = hashlib.sha256()
        count = 0
        for root in roots:
            for source in sorted(root.rglob("*.py")):
                digest.update(f"{root.name}/{source.relative_to(root)}\0".encode())
                digest.update(source.read_bytes())
                count += 1
        if not count:
            return "unknown"
        label = os.environ.get("EDISON_BUILD_ID", f"edison {version('edison')}")[:100]
        return f"{label}@{digest.hexdigest()[:24]}"
    except Exception:
        return "unknown"


def policy_fingerprint(managed_content: bool) -> str:
    """Identity, recovery and resource parameters; a change opens a new period."""
    import hashlib

    from youtube_strataread.downloader import request_policy as requests
    from youtube_strataread.workbench import content_session as session
    from youtube_strataread.workbench import resource_governor as resources

    values = [managed_content, requests.REQUEST_INTERVAL, requests.VIDEO_INTERVAL, requests.INITIAL_COOLDOWN,
              requests.NETWORK_COOLDOWN, requests.MAX_COOLDOWN, session.MAINTENANCE_LIMIT,
              session.MAINTENANCE_BUDGET, session.MAINTENANCE_WINDOW, session.HANDOFF_IDLE,
              resources.BROWSER_LIMIT, resources.BROWSER_HIGH, resources.START_AVAILABLE,
              resources.RUN_AVAILABLE, resources.BROWSER_RELEASE, resources.CPU_QUOTA, resources.CPU_PERIOD]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:16]


def _clean(value):
    if isinstance(value, str):
        return "（已隐藏可能的凭据内容）" if _SECRET.search(value) else value[:500]
    if isinstance(value, bool) or value is None or isinstance(value, int | float):
        return value
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value[:50]]
    return str(value)[:200]


def _controlled(connection) -> bool:
    if os.environ.get(CONTROLLED_ENV) == "1":
        return True
    row = connection.execute("SELECT value FROM workspace_meta WHERE key = ?", (_DRILL,)).fetchone()
    return bool(row and row[0] == "1")


def _insert(connection, at, kind, capability="", detail=None) -> int:
    cursor = connection.execute(
        "INSERT INTO youtube_evidence (occurred_at, kind, capability, detail, controlled) VALUES (?, ?, ?, ?, ?)",
        (at, kind, capability, json.dumps(_clean(detail or {}), ensure_ascii=False), int(_controlled(connection))))
    return int(cursor.lastrowid)


def record_task(connection, video_id, stage, at) -> None:
    """Called inside the workspace's own stage transaction."""
    if stage not in TASK_STAGES:
        return
    row = connection.execute("SELECT request_kind FROM candidates WHERE video_id = ?", (video_id,)).fetchone()
    _insert(connection, at, "task", "tasks",
            {"video_id": video_id, "stage": stage, "request_kind": row[0] if row else None})


def _kind(value) -> str | None:
    return None if value is None or value in IGNORED else str(value)


def captions_capability(content, requests, now) -> dict:
    """Caption capability from the content session and the shared request policy."""
    request_kind = _kind(requests.get("failure_kind"))
    content_kind = _kind(content.get("failure_kind")) if content else None
    disconnected = bool(content and content.get("state") == "disconnected")
    if disconnected:
        fault = reason = None
    elif content_kind in ATTENTION or not request_kind:
        fault = content_kind or request_kind
        reason = content.get("reason") if content_kind else requests.get("reason")
    else:
        fault, reason = request_kind, requests.get("reason")
    maintenance = (content or {}).get("maintenance") or {}
    next_check = None
    if fault and fault != "user":
        cooldown = requests.get("cooldown_until") or 0
        next_check = cooldown if cooldown > now else maintenance.get("next_at") or maintenance.get("deadline_at")
    failures = [value for value in ((content or {}).get("last_failure_at"), requests.get("last_failure_at")) if value]
    since = requests.get("first_failure_at") if fault == request_kind and request_kind else None
    return {
        "state": "disconnected" if disconnected else ("attention" if fault in ATTENTION else "waiting") if fault
        else (content or {}).get("state") or "unverified",
        "fault": fault, "reason": reason, "next_check_at": next_check,
        "last_success_at": (content or {}).get("last_success_at"),
        "last_failure_at": max(failures) if failures else None,
        "last_failure_kind": (content or {}).get("failure_kind") or requests.get("failure_kind"),
        "first_failure_at": since,
    }


def subscriptions_capability(subscription, gate, now) -> dict:
    fault = _kind(subscription.get("failure_kind")) if subscription.get("state") == "interrupted" else None
    next_check = None
    if fault and fault not in REAUTHORIZATION:
        next_check = (subscription.get("last_failure_at") or now) + SUBSCRIPTION_RETRY
        if gate.get("kind") and gate.get("retry_at"):
            next_check = max(next_check, gate["retry_at"])
    return {
        "state": ("attention" if fault in ATTENTION else "waiting") if fault else subscription.get("state") or "unverified",
        "fault": fault, "reason": subscription.get("reason") if fault else None, "next_check_at": next_check,
        "last_success_at": subscription.get("last_success_at"),
        "last_failure_at": subscription.get("last_failure_at"),
        "last_failure_kind": subscription.get("last_failure_kind"),
        "first_failure_at": None,
    }


class AcceptanceRecord:
    def __init__(self, workspace) -> None:
        self.workspace = workspace
        # The host samples on its heartbeat and whenever the page reads health.
        self._observing = threading.Lock()

    def _now(self) -> float:
        return float(self.workspace.now())

    def _connect(self):
        return sqlite3.connect(self.workspace.database_path)

    def _meta(self, key, default):
        value = self.workspace.meta(key)
        return json.loads(value) if value else default

    def _state(self) -> dict:
        return self._meta(_STATE, {"periods": [], "builds": []})

    def _save(self, state) -> None:
        self.workspace.set_meta(_STATE, json.dumps(state, ensure_ascii=False))

    def record(self, kind, capability="", *, at=None, **detail) -> int:
        with self._connect() as connection:
            return _insert(connection, self._now() if at is None else at, kind, capability, detail)

    # Host lifecycle -----------------------------------------------------
    def host_started(self, *, build: str, policy: str, boot: str | None) -> None:
        state = self._state()
        mode = "first" if "running" not in state else "crash" if state["running"] else "planned"
        reboot = bool(boot and state.get("boot") and boot != state["boot"])
        self.record("host", "service", mode=mode, reboot=reboot, build=build)
        reasons = []
        if state.get("build") and build != state["build"]:
            if build in state["builds"]:
                reasons.append("Edison 程序回退")
            self.record("build", "service", previous=state["build"], current=build,
                        rollback=build in state["builds"])
        if state.get("policy") and policy != state["policy"]:
            reasons.append("身份、恢复或资源策略变化")
        if build not in state["builds"]:
            state["builds"].append(build)
        state.update(build=build, policy=policy, boot=boot or state.get("boot"), running=True)
        self._save(state)
        if reasons:
            self.restart_period("；".join(reasons))

    def host_stopped(self) -> None:
        state = self._state()
        state["running"] = False
        self._save(state)

    # Observation periods ---------------------------------------------------
    def begin(self, reason="维护者在正式路径检查通过后开始持续验收") -> dict:
        state = self._state()
        if not state.get("build") or state["build"] == "unknown":
            raise ValueError("无法确认正在运行的程序版本，持续验收尚未验证；请恢复可识别的部署源码后开始。")
        if not state["periods"] or state["periods"][-1].get("ended_at") is not None:
            self._open_period(state, reason)
        return self.status()

    def restart_period(self, reason) -> None:
        """Only an active observation restarts; earlier evidence is kept as is."""
        state = self._state()
        if state["periods"] and state["periods"][-1].get("ended_at") is None:
            self._open_period(state, reason)

    def _open_period(self, state, reason) -> None:
        now = self._now()
        if state["periods"] and state["periods"][-1].get("ended_at") is None:
            previous = state["periods"][-1]
            previous.update(ended_at=now, end_reason=reason)
            previous["final"] = self._evaluate(previous, None, now)
            if previous["final"]["state"] == "observing":
                # Cut short before its days and events: neither passed nor failed.
                previous["final"]["state"] = "unverified"
            self.record("period", "acceptance", phase="ended", number=previous["number"], reason=reason,
                        verdict=previous["final"]["state"])
        number = len(state["periods"]) + 1
        sequence = self.record("period", "acceptance", phase="started", number=number, reason=reason)
        state["periods"].append({"number": number, "started_at": now, "start_sequence": sequence,
                                 "reason": reason, "ended_at": None})
        self._save(state)

    def drill(self, active: bool, reason: str = "") -> dict:
        if active and self.workspace.meta(_DRILL) != "1":
            self.workspace.set_meta(_DRILL, "1")
            self.record("drill", "acceptance", phase="started", reason=reason or "主动故障演练")
        elif not active and self.workspace.meta(_DRILL) == "1":
            self.record("drill", "acceptance", phase="ended", reason=reason or "演练结束")
            self.workspace.set_meta(_DRILL, "")
        return self.status()

    def intervention(self, reason: str, *, ordinary: bool = False, **detail) -> None:
        self.record("intervention", "person", reason=reason, ordinary=ordinary, **detail)

    def connected(self, identity: str) -> str:
        """A completed sign-in or authorization; an active observation restarts."""
        state = self._state()
        seen = state.setdefault("connected", [])
        reason = "reconnect" if identity in seen else "first_connection"
        if identity not in seen:
            seen.append(identity)
            self._save(state)
        self.intervention(reason, identity=identity)
        label = "Google 官方授权" if identity == "official" else "YouTube 内容会话"
        self.restart_period(f"{label}{'重新连接' if reason == 'reconnect' else '首次连接'}")
        return reason

    def connection_verified(self, identity: str) -> None:
        """A reused grant can establish first connection; checking is no reconnect."""
        if identity not in self._state().get("connected", []):
            self.connected(identity)

    def record_reading(self, arguments: dict) -> dict:
        try:
            baseline = float(arguments["baseline_p95_ms"])
            maintenance = float(arguments["maintenance_p95_ms"])
            samples = int(arguments["samples"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("请提供基线 P95、维护期 P95（毫秒）和每个条件的样本量。") from None
        if not (0 < baseline < 600000 and 0 < maintenance < 600000 and 1 <= samples <= 100000):
            raise ValueError("阅读对照数值超出有效范围。")
        allowed = max(baseline * 0.2, 200.0)
        self.record("reading", "reading", baseline_p95_ms=baseline, maintenance_p95_ms=maintenance,
                    increase_ms=round(maintenance - baseline, 1), allowed_ms=round(allowed, 1), samples=samples,
                    load=str(arguments.get("load") or "")[:200], passed=maintenance - baseline <= allowed)
        return self.status()

    # Capability observation ---------------------------------------------
    def capabilities(self, *, subscription, content, requests, gate, now=None) -> dict:
        now = self._now() if now is None else now
        observed = self._meta(_OBSERVED, {})
        result = {"subscriptions": subscriptions_capability(subscription, gate, now),
                  "captions": captions_capability(content, requests, now)}
        for name, item in result.items():
            previous = observed.get(name, {})
            if item["fault"]:
                item["since"] = previous.get("since") if previous.get("fault") else (
                    item["first_failure_at"] or item["last_failure_at"] or now)
            else:
                item["since"] = None
        return result

    def observe(self, *, subscription, content, requests, gate, resources=None, refreshed_at=None) -> None:
        with self._observing:
            self._observe(subscription, content, requests, gate, resources, refreshed_at)

    def _observe(self, subscription, content, requests, gate, resources, refreshed_at) -> None:
        now = self._now()
        observed = self._meta(_OBSERVED, {})
        before = json.dumps(observed, sort_keys=True)
        capabilities = self.capabilities(subscription=subscription, content=content, requests=requests,
                                         gate=gate, now=now)
        for name, item in capabilities.items():
            previous = observed.get(name, {})
            unresolved = previous.get("unresolved")
            if not unresolved and previous.get("fault"):
                # Preserve an episode sampled before this accounting field was
                # introduced, including its last known successful acquisition.
                unresolved = {"category": previous["fault"], "since": previous.get("since"),
                              "success_before": previous.get("success"), "failed_at": previous.get("failure")}
            if item["fault"]:
                unresolved = {"category": item["fault"], "since": item["since"],
                              "success_before": item["last_success_at"], "failed_at": item["last_failure_at"]}
            if item["last_success_at"] and item["last_success_at"] != previous.get("success"):
                self.record("capability", name, at=item["last_success_at"], outcome="success")
            if item["last_failure_at"] and item["last_failure_at"] != previous.get("failure"):
                self.record("capability", name, at=item["last_failure_at"], outcome="failure",
                            category=item["fault"] or item["last_failure_kind"] or "unknown", reason=item["reason"],
                            next_check_at=item["next_check_at"])
            if item["fault"] and item["fault"] != previous.get("fault"):
                self.record("fault", name, outcome="started" if not previous.get("fault") else "changed",
                            category=item["fault"], reason=item["reason"], since=item["since"],
                            next_check_at=item["next_check_at"])
            elif unresolved:
                success = item["last_success_at"]
                if item["state"] == "disconnected":
                    self.record("fault", name, outcome="withdrawn", category=unresolved["category"])
                    unresolved = None
                elif (success is not None and success > (unresolved.get("success_before") or 0)
                      and success >= (unresolved.get("failed_at") or unresolved["since"] or 0)):
                    since = unresolved["since"]
                    self.record("fault", name, outcome="recovered", category=unresolved["category"],
                                since=since, duration=now - (since if since is not None else now))
                    unresolved = None
            observed[name] = {"fault": item["fault"], "since": item["since"],
                              "success": item["last_success_at"], "failure": item["last_failure_at"],
                              "unresolved": unresolved}
        if content is not None:
            self._observe_session(observed, content)
        notice = self.notice(capabilities, now)
        if notice["level"] != observed.get("notice"):
            self.record("notice", notice.get("capability") or "", level=notice["level"],
                        category=notice.get("category"), reason=notice.get("reason"))
            observed["notice"] = notice["level"]
        if refreshed_at and refreshed_at != observed.get("refreshed_at"):
            self.record("token_refresh", "subscriptions", at=refreshed_at)
            observed["refreshed_at"] = refreshed_at
        if resources is not None:
            self._observe_resources(observed, resources, now)
        if json.dumps(observed, sort_keys=True) != before:
            self.workspace.set_meta(_OBSERVED, json.dumps(observed))

    def _observe_session(self, observed, content) -> None:
        maintenance = content.get("maintenance") or {}
        state = maintenance.get("state")
        previous = observed.get("maintenance", {})
        if state == "running" and previous.get("state") != "running":
            started = (maintenance.get("deadline_at") or self._now()) - 180
            self.record("maintenance", "captions", at=started, phase="started", basis=maintenance.get("basis"),
                        used=maintenance.get("used"), limit=maintenance.get("limit"))
        elif previous.get("state") == "running" and state != "running":
            self.record("maintenance", "captions", phase="ended", basis=previous.get("basis"), result=state,
                        reason=maintenance.get("reason") or content.get("reason"))
        if state == "handoff" and previous.get("state") != "handoff":
            self.record("maintenance", "captions", phase="handoff", reason=maintenance.get("reason"))
        elif previous.get("state") == "handoff" and state != "handoff":
            self.record("maintenance", "captions", phase="handoff_ended", result=content.get("state"))
        generation = content.get("generation")
        if (previous.get("state") == "running" and generation is not None
                and previous.get("generation") is not None and generation > previous["generation"]
                and content.get("state") != "disconnected"):
            self.record("session_updated", "captions", basis=previous.get("basis"))
        # The basis is cleared when maintenance publishes; keep the one it ran for.
        basis = maintenance.get("basis") or (previous.get("basis") if state == "running" else None)
        observed["maintenance"] = {"state": state, "basis": basis, "generation": generation}

    def _observe_resources(self, observed, resources, now) -> None:
        hour = int(now // 3600)
        periods = self._state()["periods"]
        period = periods[-1]["number"] if periods else None
        with self._connect() as connection:
            controlled = _controlled(connection)
        sample = observed.get("resources") or {}
        # One durable aggregate per hour and accounting context. Update it as
        # soon as peaks change: a crash must not lose the current hour's OOM.
        if (sample.get("hour"), sample.get("period"), sample.get("controlled")) != (hour, period, controlled):
            sample = {"hour": hour, "period": period, "controlled": controlled}
        available = resources.get("available_bytes") or 0
        sample["available_min"] = min(sample.get("available_min", available), available)
        for key, field in (("unreclaimable_peak", "browser_unreclaimable_bytes"),
                           ("swap_peak", "browser_swap_bytes")):
            sample[key] = max(sample.get(key, 0), resources.get(field) or 0)
        counter = resources.get("browser_oom_kills")
        if counter is not None:
            previous = observed.get("oom_counter", 0)
            delta = max(0, counter - previous) if counter >= previous else counter
            sample["oom_kills"] = sample.get("oom_kills", 0) + delta
            sample["oom_counter"] = counter
            observed["oom_counter"] = counter
        sample["constrained"] = sample.get("constrained", True) and resources.get("constrained") is True
        sample["oom_observed"] = sample.get("oom_observed", True) and resources.get("browser_oom_kills") is not None
        sample["contract"] = resources.get("contract")
        detail = {key: value for key, value in sample.items() if key not in {"hour", "period", "controlled", "sequence"}}
        if sample.get("sequence"):
            with self._connect() as connection:
                connection.execute("UPDATE youtube_evidence SET occurred_at = ?, detail = ? WHERE sequence = ?",
                                   (now, json.dumps(detail), sample["sequence"]))
        else:
            sample["sequence"] = self.record("resources", "resources", at=now, **detail)
        observed["resources"] = sample
        waiting = bool(resources.get("browser_waiting") or resources.get("captions_waiting"))
        if waiting != bool(observed.get("resource_wait")):
            self.record("resource_wait", "resources", waiting=waiting, reason=resources.get("reason"))
            observed["resource_wait"] = waiting

    @staticmethod
    def notice(capabilities, now) -> dict:
        """One persistent in-site notice; ordinary waits escalate after 24 hours."""
        best = {"level": "none"}
        rank = {"none": 0, "waiting": 1, "escalated": 2, "attention": 3}
        for name, item in capabilities.items():
            if not item["fault"]:
                continue
            level = "attention" if item["fault"] in ATTENTION else (
                "escalated" if item.get("since") is not None and now - item["since"] >= ESCALATE_AFTER else "waiting")
            if rank[level] > rank[best["level"]]:
                best = {"level": level, "capability": name, "category": item["fault"], "reason": item["reason"],
                        "since": item.get("since"), "next_check_at": item["next_check_at"]}
        return best

    def health(self, *, subscription, content, requests, gate) -> dict:
        now = self._now()
        capabilities = self.capabilities(subscription=subscription, content=content, requests=requests,
                                         gate=gate, now=now)
        last_stage = last_ready = None
        maintenance = []
        if not self.workspace.newer_data_contract:
            try:
                with self._connect() as connection:
                    last_stage, last_ready = connection.execute(
                        "SELECT MAX(occurred_at), MAX(CASE WHEN stage = 'ready' THEN occurred_at END) FROM preparation_events"
                    ).fetchone()
                    maintenance = connection.execute(
                        "SELECT occurred_at, detail FROM youtube_evidence WHERE kind = 'maintenance' "
                        "AND json_extract(detail, '$.phase') IN ('started', 'ended') ORDER BY sequence DESC LIMIT 2"
                    ).fetchall()
            except sqlite3.OperationalError:
                # A rollback reader must not create old evidence tables in a
                # newer library, or break document reading when they are absent.
                pass
        windows = [dict(json.loads(detail), at=at) for at, detail in maintenance]
        last = next((item for item in windows if item["phase"] == "started"), None)
        ended = next((item for item in windows if item["phase"] == "ended"), None)
        return {
            "capabilities": capabilities,
            "notice": self.notice(capabilities, now),
            "maintenance": {**((content or {}).get("maintenance") or {}),
                            "last_started_at": last["at"] if last else None,
                            "last_ended_at": ended["at"] if ended and last and ended["at"] >= last["at"] else None,
                            "last_basis": last.get("basis") if last else None,
                            "last_result": ended.get("reason") if ended and last and ended["at"] >= last["at"] else None},
            "progress": {"last_stage_at": last_stage, "last_ready_at": last_ready},
        }

    # Verdict ------------------------------------------------------------
    def _events(self, start_sequence, end_sequence=None):
        query = "SELECT sequence, occurred_at, kind, capability, detail, controlled FROM youtube_evidence WHERE sequence >= ?"
        values: list = [start_sequence]
        if end_sequence is not None:
            query += " AND sequence < ?"
            values.append(end_sequence)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY sequence", values).fetchall()
        return [{"sequence": row[0], "at": row[1], "kind": row[2], "capability": row[3],
                 "detail": json.loads(row[4]), "controlled": bool(row[5])} for row in rows]

    def _evaluate(self, period, end_sequence, now) -> dict:
        events = self._events(period["start_sequence"], end_sequence)
        natural = [event for event in events if not event["controlled"]]
        controlled = [event for event in events if event["controlled"]]

        def phase(items, kind, value):
            return [event for event in items if event["kind"] == kind and event["detail"].get("phase") == value]

        security = phase(natural, "maintenance", "handoff")
        interventions = [event for event in natural if event["kind"] == "intervention"]
        ordinary = [event for event in interventions if event["detail"].get("ordinary")]
        reauthorization = [event for event in natural if event["kind"] == "fault"
                           and event["detail"].get("outcome") in {"started", "changed"}
                           and event["detail"].get("category") in REAUTHORIZATION]
        resources = [event for event in natural if event["kind"] == "resources"]
        readings = [event for event in natural if event["kind"] == "reading"]
        failures = []
        if len(security) > SECURITY_LIMIT:
            failures.append(f"非主动 Google 安全验证 {len(security)} 次，超过 {SECURITY_LIMIT} 次上限，维护负担不达标。")
        if ordinary:
            failures.append(f"普通到期、轮换或网络/限流恢复出现 {len(ordinary)} 次人工搬运凭据或点击重试。")
        if reauthorization:
            failures.append(f"官方授权 {len(reauthorization)} 次需要人工重新授权（未知 invalid_grant、普通刷新失败或应用配置失效），不计入安全验证例外。")
        if any(event["detail"].get("swap_peak") or event["detail"].get("oom_kills") for event in resources):
            failures.append("浏览器组出现 swap 或 OOM，资源不达标。")
        if any(event["detail"].get("unreclaimable_peak", 0) >
               (event["detail"].get("contract") or {}).get("browser_limit", float("inf")) for event in resources):
            failures.append("浏览器组内存超过资源预算。")
        if any(event["detail"].get("constrained") is False for event in resources):
            failures.append("浏览器资源隔离不可用，无法保证内存、swap 与 CPU 上限。")
        if any(not event["detail"].get("passed") for event in readings):
            failures.append("维护期间阅读 P95 增量超过 max(基线×20%, 200 ms)。")

        started = period["started_at"]
        end = period.get("ended_at") or now
        elapsed = max(0.0, end - started)
        complete_days = int(elapsed // DAY)
        missing_days = []
        for day in range(complete_days):
            low, high = started + day * DAY, started + (day + 1) * DAY
            seen = {event["capability"] for event in natural if event["kind"] == "capability" and low <= event["at"] < high}
            if not {"subscriptions", "captions"} <= seen:
                missing_days.append(day + 1)
        tasks = [event for event in natural if event["kind"] == "task"]
        transcribed = {event["detail"].get("video_id") for event in tasks if event["detail"].get("stage") == "transcript_ready"}
        hosts = [event for event in natural if event["kind"] == "host"]
        updates = [event for event in natural if event["kind"] == "session_updated"]
        covered = {
            "daily_records": complete_days >= OBSERVATION_DAYS and not missing_days,
            "subscription_chain": any(event["detail"].get("stage") == "ready" and event["detail"].get("request_kind") == "automatic"
                                      and event["detail"].get("video_id") in transcribed for event in tasks),
            "token_refresh": any(event["kind"] == "token_refresh" for event in natural),
            "session_update": bool(updates),
            "natural_rotation": any(event["detail"].get("basis") == "rotation" for event in updates),
            "service_restart": any(event["detail"].get("mode") == "planned" for event in hosts),
            "host_restart": any(event["detail"].get("reboot") for event in hosts),
            "fault_recovery": any(event["kind"] == "fault" and event["detail"].get("outcome") == "recovered" for event in natural),
            "resources": bool(resources) and all(event["detail"].get("constrained") is True
                                                  and event["detail"].get("oom_observed") is True
                                                  and event["detail"].get("contract") for event in resources),
            "reading": bool(readings),
        }
        missing = [label for key, label in COVERAGE.items() if not covered[key]]
        if failures:
            verdict = "failed"
        elif elapsed < OBSERVATION_DAYS * DAY:
            verdict = "observing"
        elif missing:
            verdict = "unverified"
        else:
            verdict = "passed"
        reasons: dict[str, int] = {}
        for event in interventions:
            reason = str(event["detail"].get("reason"))
            reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "state": verdict, "number": period["number"], "started_at": started, "ended_at": period.get("ended_at"),
            "reason": period["reason"], "elapsed_seconds": elapsed, "required_days": OBSERVATION_DAYS,
            "complete_days": complete_days, "missing_days": missing_days,
            "coverage": [{"key": key, "label": label, "covered": covered[key]} for key, label in COVERAGE.items()],
            "missing": missing, "failures": failures,
            "security_verifications": {"natural": len(security), "limit": SECURITY_LIMIT,
                                       "controlled": len(phase(controlled, "maintenance", "handoff"))},
            "interventions": {"total": len(interventions), "ordinary": len(ordinary), "by_reason": reasons,
                              "controlled": sum(event["kind"] == "intervention" for event in controlled)},
            "controlled_events": len(controlled), "natural_events": len(natural),
        }

    def status(self) -> dict:
        if self.workspace.newer_data_contract:
            return {"state": "unverified", "current": None, "history": [], "drill": False,
                    "controlled_boundary": os.environ.get(CONTROLLED_ENV) == "1",
                    "reason": "资料库由更新的程序写入；持续验收证据仅由兼容程序解读，当前尚未验证。"}
        state = self._state()
        periods = state["periods"]
        drill = self.workspace.meta(_DRILL) == "1"
        controlled = os.environ.get(CONTROLLED_ENV) == "1"
        if not periods:
            return {"state": "not_started", "current": None, "history": [], "drill": drill,
                    "controlled_boundary": controlled}
        now = self._now()
        current = periods[-1]
        history = [period["final"] for period in periods[:-1] if period.get("final")]
        if current.get("ended_at") is not None:
            return {"state": current["final"]["state"], "current": current["final"], "history": history,
                    "drill": drill, "controlled_boundary": controlled}
        evaluation = self._evaluate(current, None, now)
        return {"state": evaluation["state"], "current": evaluation, "history": history,
                "drill": drill, "controlled_boundary": controlled}

    def export(self) -> dict:
        if self.workspace.newer_data_contract:
            return {"generated_at": self._now(), "status": self.status(), "periods": [], "events": []}
        return {"generated_at": self._now(), "status": self.status(), "periods": self._state()["periods"],
                "events": self._events(0)}

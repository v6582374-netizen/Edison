"""Verdict accounting that the controlled E2E cannot reach with natural events.

The Web E2E (surfaces/gui/e2e/youtube-acceptance.spec.ts) proves notices,
escalation, persistence and period timing through the real host. Its external
boundary is substituted, so every event there is controlled. These contracts
cover how natural evidence is counted: the 3/4 security limit, ordinary manual
work, reauthorization, resources, reading and complete coverage.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from youtube_strataread.workbench import acceptance as module
from youtube_strataread.workbench import workspace as workspace_module
from youtube_strataread.workbench.acceptance import DAY, AcceptanceRecord
from youtube_strataread.workbench.workspace import LocalWorkspace

START = 1_900_000_000.0
IDLE = {"state": "idle", "basis": None, "reason": None, "next_at": None, "deadline_at": None, "used": 0, "limit": 2}


@pytest.fixture
def clock(monkeypatch):
    value = [START]
    monkeypatch.setattr(workspace_module, "time", SimpleNamespace(time=lambda: value[0]))
    monkeypatch.delenv(module.CONTROLLED_ENV, raising=False)
    return value


@pytest.fixture
def record(tmp_path, clock):
    workspace = LocalWorkspace.open(tmp_path / "library")
    workspace.now = lambda: clock[0]
    record = AcceptanceRecord(workspace)
    record.host_started(build="edison 1", policy="p1", boot="boot-1")
    return record


def content(state="available", failure=None, reason=None, maintenance=None, generation=1, success=START):
    return {"state": state, "failure_kind": failure, "reason": reason, "last_success_at": success,
            "last_failure_at": None, "maintenance": maintenance or dict(IDLE), "generation": generation}


def observe(record, **overrides):
    values = {"subscription": {"state": "available", "last_success_at": None, "last_failure_at": None},
              "content": content(), "requests": {"cooldown_until": 0}, "gate": {}}
    values.update(overrides)
    record.observe(**values)


def test_ordinary_waits_escalate_after_a_day_while_attention_is_immediate(record, clock):
    waiting = {"cooldown_until": START + 600, "failure_kind": "network", "first_failure_at": START,
               "last_failure_at": START, "reason": "无法连接 YouTube"}
    observe(record, requests=waiting)
    health = record.health(subscription={"state": "unverified"}, content=content(), requests=waiting, gate={})
    assert health["notice"] == {"level": "waiting", "capability": "captions", "category": "network",
                                "reason": "无法连接 YouTube", "since": START, "next_check_at": START + 600}
    clock[0] = START + DAY - 1
    assert record.health(subscription={}, content=content(), requests=waiting, gate={})["notice"]["level"] == "waiting"
    clock[0] = START + DAY
    escalated = record.health(subscription={}, content=content(), requests=waiting, gate={})["notice"]
    assert escalated["level"] == "escalated" and escalated["since"] == START
    for failure in ("user", "maintenance"):
        notice = record.health(subscription={}, content=content(failure=failure, reason="x"), requests={}, gate={})["notice"]
        assert notice["level"] == "attention" and notice["category"] == failure
    lost = {"state": "interrupted", "failure_kind": "reauthorization", "last_failure_at": START, "reason": "invalid_grant"}
    notice = record.health(subscription=lost, content=content(), requests={}, gate={})["notice"]
    assert (notice["level"], notice["capability"], notice["next_check_at"]) == ("attention", "subscriptions", None)
    # One captionless video and a withdrawn connection are not capability faults.
    assert record.health(subscription={}, content=content(failure="no_captions"), requests={}, gate={})["notice"] == {"level": "none"}
    assert record.health(subscription={}, content=content(state="disconnected", failure="user"), requests={}, gate={})["notice"] == {"level": "none"}


def test_restarts_and_recovered_faults_keep_the_period_while_identity_or_policy_changes_restart_it(record, clock):
    record.begin()
    observe(record, requests={"cooldown_until": START + 600, "failure_kind": "rate_limited", "first_failure_at": START,
                              "last_failure_at": START, "reason": "429"})
    clock[0] += 3600
    observe(record, content=content(success=clock[0]))
    record.host_stopped()
    record.host_started(build="edison 1", policy="p1", boot="boot-1")
    record.host_started(build="edison 1", policy="p1", boot="boot-2")  # crash, then a new kernel boot
    status = record.status()
    assert status["current"]["number"] == 1 and status["state"] == "observing"
    covered = {item["key"]: item["covered"] for item in status["current"]["coverage"]}
    assert covered["fault_recovery"] and covered["service_restart"] and covered["host_restart"]

    record.host_started(build="edison 2", policy="p1", boot="boot-2")  # an upgrade is not a rollback
    assert record.status()["current"]["number"] == 1
    record.host_started(build="edison 1", policy="p1", boot="boot-2")
    assert record.status()["current"]["reason"] == "Edison 程序回退"
    record.host_started(build="edison 1", policy="p2", boot="boot-2")
    assert record.status()["current"]["reason"] == "身份、恢复或资源策略变化"
    assert record.connected("official") == "first_connection"
    assert record.connected("official") == "reconnect"
    status = record.status()
    assert status["current"]["number"] == 5 and status["current"]["reason"] == "Google 官方授权重新连接"
    # Earlier periods keep their evidence and final assessment.
    assert [period["number"] for period in status["history"]] == [1, 2, 3, 4]
    assert status["history"][0]["security_verifications"]["natural"] == 0


def test_three_natural_security_verifications_are_tolerated_and_the_fourth_fails(record, clock):
    record.begin()
    generation = 1
    for count in range(1, 5):
        observe(record, content=content(state="needs_user", failure="user", maintenance={**IDLE, "state": "handoff"},
                                        generation=generation))
        clock[0] += 60
        generation += 1
        observe(record, content=content(generation=generation, success=clock[0]))
        clock[0] += 60
        expected = "observing" if count <= 3 else "failed"
        assert record.status()["state"] == expected, count
    assert record.status()["current"]["security_verifications"] == {"natural": 4, "limit": 3, "controlled": 0}
    assert "超过 3 次上限" in record.status()["current"]["failures"][0]


def test_drills_are_accounted_separately_and_never_count_as_natural(record, clock, monkeypatch):
    record.begin()
    record.drill(True, "受控交接演练")
    for generation in range(1, 6):
        observe(record, content=content(state="needs_user", failure="user", maintenance={**IDLE, "state": "handoff"},
                                        generation=generation))
        observe(record, content=content(generation=generation + 1))
    record.intervention("security_verification")
    record.drill(False)
    status = record.status()["current"]
    assert status["security_verifications"]["natural"] == 0 and status["security_verifications"]["controlled"] == 5
    assert status["interventions"]["total"] == 0 and status["interventions"]["controlled"] == 1
    assert record.status()["state"] == "observing"
    monkeypatch.setenv(module.CONTROLLED_ENV, "1")
    record.record("session_updated", "captions", basis="rotation")
    covered = {item["key"]: item["covered"] for item in record.status()["current"]["coverage"]}
    assert not covered["natural_rotation"] and record.status()["controlled_boundary"]


@pytest.mark.parametrize(("action", "failure"), [
    (lambda record: record.intervention("manual_retry", ordinary=True), "人工搬运凭据或点击重试"),
    (lambda record: observe(record, subscription={"state": "interrupted", "failure_kind": "reauthorization",
                                                  "last_failure_at": START, "reason": "invalid_grant"}), "不计入安全验证例外"),
    (lambda record: record.record("resources", "resources", swap_peak=4096, oom_kills=0), "swap 或 OOM"),
    (lambda record: record.record_reading({"baseline_p95_ms": 1000, "maintenance_p95_ms": 1201, "samples": 100}), "阅读 P95"),
])
def test_ordinary_manual_work_reauthorization_resources_and_reading_fail_the_period(record, action, failure):
    record.begin()
    record.intervention("manual_retry", ordinary=False)  # retrying an unexplained result is investigation
    record.record_reading({"baseline_p95_ms": 1000, "maintenance_p95_ms": 1200, "samples": 100})
    assert record.status()["state"] == "observing"
    action(record)
    status = record.status()
    assert status["state"] == "failed" and any(failure in item for item in status["current"]["failures"])


def test_fourteen_days_without_key_events_are_unverified_and_full_natural_coverage_passes(record, clock):
    record.begin()
    clock[0] = START + 14 * DAY
    status = record.status()
    assert status["state"] == "unverified"
    assert "自然 Cookie 轮换后的会话更新" in status["current"]["missing"]
    assert status["current"]["missing_days"] == list(range(1, 15))

    clock[0] = START
    record.begin()  # idempotent while observing
    workspace = record.workspace
    from test_workbench_library import candidate
    workspace.add_candidate(candidate("chain000001"))
    for day in range(14):
        clock[0] = START + day * DAY + 10
        record.record("capability", "subscriptions", outcome="success")
        record.record("capability", "captions", outcome="success")
    workspace.store_transcript("chain000001", language="en", srt_text="1\n00:00:00,000 --> 00:00:01,000\nx\n")
    workspace.set_preparation_state("chain000001", "ready")
    record.record("token_refresh", "subscriptions")
    record.record("session_updated", "captions", basis="rotation")
    record.record("fault", "captions", outcome="recovered", category="network")
    from youtube_strataread.workbench.resource_governor import ResourceGovernor, ResourceObservation
    observe(record, resources=ResourceGovernor(observe=lambda: ResourceObservation(2_000_000_000, 1, 0, True, 0)).status())
    record.record_reading({"baseline_p95_ms": 1000, "maintenance_p95_ms": 1100, "samples": 100})
    record.host_stopped()
    record.host_started(build="edison 1", policy="p1", boot="boot-1")
    record.host_started(build="edison 1", policy="p1", boot="boot-2")
    clock[0] = START + 14 * DAY - 1
    assert record.status()["state"] == "observing"
    clock[0] = START + 14 * DAY
    status = record.status()
    assert status["state"] == "passed", status["current"]["missing"]
    # Late critical coverage never excuses a gap after the initial fortnight.
    clock[0] = START + 30 * DAY
    status = record.status()
    assert status["state"] == "unverified"
    assert status["current"]["missing_days"] == list(range(15, 31))


@pytest.mark.parametrize("resources", [
    {"swap_peak": 0, "oom_kills": 0, "unreclaimable_peak": 1},
    {"swap_peak": 0, "oom_kills": 0, "unreclaimable_peak": 1, "constrained": False},
])
def test_resource_evidence_without_enforced_limits_cannot_establish_coverage(record, resources):
    record.begin()
    record.record("resources", "resources", **resources)
    covered = {item["key"]: item["covered"] for item in record.status()["current"]["coverage"]}
    assert not covered["resources"]


def test_exceeding_the_memory_budget_fails_without_swap_or_oom(record):
    from youtube_strataread.workbench.resource_governor import BROWSER_LIMIT, ResourceGovernor, ResourceObservation
    record.begin()
    observe(record, resources=ResourceGovernor(observe=lambda: ResourceObservation(2_000_000_000, BROWSER_LIMIT + 1, 0, True)).status())
    assert record.status()["state"] == "failed"


def test_missing_kernel_oom_counter_is_unknown_instead_of_zero(record):
    from youtube_strataread.workbench.resource_governor import ResourceGovernor, ResourceObservation
    record.begin()
    observe(record, resources=ResourceGovernor(observe=lambda: ResourceObservation(2_000_000_000, 1, 0, True, None)).status())
    covered = {item["key"]: item["covered"] for item in record.status()["current"]["coverage"]}
    assert not covered["resources"]


def test_a_new_period_keeps_prior_oom_failure_without_counting_the_same_kernel_counter_twice(record, clock):
    from youtube_strataread.workbench.resource_governor import ResourceGovernor, ResourceObservation
    resources = ResourceGovernor(observe=lambda: ResourceObservation(2_000_000_000, 1, 0, True, 1)).status()
    record.begin()
    observe(record, resources=resources)
    assert record.status()["state"] == "failed"
    clock[0] += 60
    record.restart_period("身份、恢复或资源策略变化")
    observe(record, resources=resources)
    status = record.status()
    assert status["history"][0]["state"] == "failed"
    assert status["state"] == "observing"


def test_same_package_version_builds_have_distinct_source_identities_and_rollback_restarts(record, tmp_path, monkeypatch):
    package = tmp_path / "installed" / "youtube_strataread"
    source = package / "workbench" / "acceptance.py"
    source.parent.mkdir(parents=True)
    source.write_text("# release one\n")
    monkeypatch.setattr(module, "__file__", str(source))
    monkeypatch.setenv("EDISON_BUILD_ID", "same-version-release")
    first = module.build_id()
    source.write_text("# release two\n")
    second = module.build_id()
    assert first != second
    record.host_started(build=first, policy="p1", boot="boot-1")
    record.begin()
    record.host_started(build=second, policy="p1", boot="boot-1")
    assert record.status()["current"]["number"] == 1
    record.host_started(build=first, policy="p1", boot="boot-1")
    assert record.status()["current"]["number"] == 2
    assert record.status()["current"]["reason"] == "Edison 程序回退"


def test_newer_library_without_the_old_evidence_table_keeps_health_and_export_readable(record):
    import sqlite3
    record.begin()
    # A fixture representing a future program's durable schema. An older
    # reader may neither recreate this removed table nor interpret its ledger.
    record.workspace.set_meta("youtube_data_contract", str(workspace_module.YOUTUBE_DATA_CONTRACT + 1))
    with sqlite3.connect(record.workspace.database_path) as connection:
        connection.execute("DROP TABLE youtube_evidence")
    older = AcceptanceRecord(LocalWorkspace.open(record.workspace.root))
    assert older.status()["state"] == "unverified"
    assert "兼容程序" in older.status()["reason"]
    assert older.health(subscription={}, content=None, requests={}, gate={})["progress"] == {
        "last_stage_at": None, "last_ready_at": None}
    assert older.export()["events"] == []


def test_evidence_outlives_the_console_and_asset_deletion_without_secrets(record, clock):
    from test_workbench_library import candidate
    workspace = record.workspace
    workspace.add_candidate(candidate("kept0000001"))
    workspace.set_preparation_state("kept0000001", "ready")
    for index in range(600):
        workspace.report_console("kept0000001", f"line {index}")
    workspace.delete_asset("kept0000001")
    record.record("capability", "captions", outcome="failure",
                  reason=".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret-session", token="ya29.secret")
    exported = record.export()
    assert any(event["kind"] == "task" and event["detail"]["video_id"] == "kept0000001" for event in exported["events"])
    text = str(exported)
    assert "secret-session" not in text and "ya29.secret" not in text


def test_resource_peaks_are_visible_immediately_and_durable_before_an_hour_ends(record, clock):
    record.begin()
    observe(record, resources={"available_bytes": 2_000_000_000, "browser_swap_bytes": 4096,
                               "browser_unreclaimable_bytes": 700_000_000, "browser_oom_kills": 1})
    assert record.status()["state"] == "failed"
    reopened = AcceptanceRecord(LocalWorkspace.open(record.workspace.root))
    exported = reopened.export()
    sample = next(event for event in exported["events"] if event["kind"] == "resources")
    assert sample["detail"]["swap_peak"] == 4096 and sample["detail"]["oom_kills"] == 1
    clock[0] += 60
    observe(reopened, resources={"available_bytes": 2_100_000_000, "browser_swap_bytes": 0,
                                 "browser_unreclaimable_bytes": 0, "browser_oom_kills": 0})
    assert reopened.status()["state"] == "failed"

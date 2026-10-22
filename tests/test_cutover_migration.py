"""Historical cutover migration through the maintainer entry, not the scheduler.

Seams: HistoricalMigration.run/status (stage record, credential file, library
facts) supplements the real Web host test. These tests do not call the queue.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from youtube_strataread.workbench.connection import SubscriptionSource
from youtube_strataread.workbench.cutover import CutoverBlocked, CutoverHold
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.keychain import NativeKeychainVault, ServiceAccountFile
from youtube_strataread.workbench.migration import HistoricalMigration
from youtube_strataread.workbench.workspace import LocalWorkspace

CLIENT = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID"
SECRET = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET"
REFRESH = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
ACCESS = "YOUTUBE_WORKBENCH_OAUTH_ACCESS_TOKEN"
EVIDENCE = 1_700_000_000
NOW = 1_800_000_000.0
SUBTITLE_429 = "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"


def candidate(video_id, title, channel="channel"):
    return Candidate(
        video_id=video_id, channel_id=channel, channel_title=title, title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=datetime.fromtimestamp(EVIDENCE, timezone.utc).isoformat(),
        published_ts=float(EVIDENCE), source_observed_at=float(EVIDENCE),
    )


def harness(tmp_path, *, held=True):
    workspace = LocalWorkspace.open(tmp_path / "library")
    workspace.now = lambda: NOW
    workspace.youtube_requests.now = workspace.now
    hold = CutoverHold(workspace)
    if held:
        hold.begin(threading.Event())
    backend = ServiceAccountFile(tmp_path / "credentials.json")
    vault = NativeKeychainVault(namespace=str(workspace.root.resolve()), backend=backend)
    return workspace, HistoricalMigration(workspace, vault), vault, backend


def plant(workspace, video_id, *, state, reason, kind=None, attempts=0, commenced=None, retry_at=None, request_kind="automatic"):
    workspace.add_candidate(candidate(video_id, video_id))
    if state in {"unavailable", "failed"}:
        workspace.set_preparation_state(video_id, state, reason)
    with sqlite3.connect(workspace.database_path) as db:
        db.execute(
            """UPDATE candidates SET preparation_state=?, failure_reason=?, acquisition_failure_kind=?,
               rate_limit_attempts=?, commenced_at=?, retry_at=?, request_kind=?,
               preparation_completed_at=?, stage_updated_at=? WHERE video_id=?""",
            (state, reason, kind, attempts, commenced, retry_at, request_kind, EVIDENCE, EVIDENCE, video_id),
        )


def test_migration_requires_the_hold_and_records_repeatable_stages_without_scanning(tmp_path):
    workspace, migration, vault, backend = harness(tmp_path, held=False)
    decoy = NativeKeychainVault(namespace=str(tmp_path / "other-account"), backend=backend)
    decoy.save_many({CLIENT: "other-client", SECRET: "other-secret", REFRESH: "synthetic-decoy-refresh"})
    with pytest.raises(CutoverBlocked, match="切换暂停"):
        migration.run({})
    CutoverHold(workspace).begin(threading.Event())
    first = migration.run({})
    second = migration.run({})
    assert first["stage"] == "complete" and second["finished"] is True
    assert second["runs"] == 2 and second["completed"] == ["credentials", "paths", "failures"]
    assert second["canonical_namespace_kept"] is True and second["canonical_paths_kept"] is True
    assert decoy.load(REFRESH) == "synthetic-decoy-refresh"
    assert "synthetic-decoy-refresh" not in json.dumps(second)
    stored = workspace.meta("youtube_historical_migration")
    assert stored and "synthetic-decoy-refresh" not in stored
    assert migration.run({"credential_namespace": str(tmp_path / "missing-account"), "expected_client_id": "fixture-client"})["source_missing"] is True
    assert decoy.load(CLIENT) == "other-client"


def test_matching_client_rotation_is_kept_and_fields_do_not_make_oauth_valid(tmp_path):
    workspace, migration, vault, backend = harness(tmp_path)
    source = NativeKeychainVault(namespace=str(tmp_path / "old-library"), backend=backend)
    source.save_many({CLIENT: "fixture-client", SECRET: "fixture-secret", REFRESH: "synthetic-source-refresh", ACCESS: "synthetic-access"})
    plan = {"credential_namespace": str(tmp_path / "old-library"), "expected_client_id": "fixture-client"}
    moved = migration.run(plan)
    assert moved["client_match"] is True and moved["credential_check"] == {"read": True, "atomic_replace": True, "owner_only": True}
    assert vault.load(CLIENT) == "fixture-client"
    assert vault.load(REFRESH) == "synthetic-source-refresh"
    assert vault.load(ACCESS) is None
    assert workspace.meta("youtube_oauth_authorized") != "1"
    assert moved["oauth_authorized"] is False
    assert "synthetic-source-refresh" not in json.dumps(moved)
    assert (tmp_path / "credentials.json").stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".edison-credentials-*"))
    vault.save_many({REFRESH: "synthetic-canonical-refresh"})
    source.save_many({REFRESH: "synthetic-older-refresh"})
    again = migration.run(plan)
    assert again["rotation_kept"] == "canonical"
    assert vault.load(REFRESH) == "synthetic-canonical-refresh"


def test_client_mismatch_and_permission_error_leave_storage_unchanged(tmp_path):
    workspace, migration, vault, backend = harness(tmp_path)
    vault.save_many({CLIENT: "fixture-client", SECRET: "fixture-secret", REFRESH: "synthetic-canonical-refresh"})
    other = NativeKeychainVault(namespace=str(tmp_path / "foreign-library"), backend=backend)
    other.save_many({CLIENT: "foreign-client", SECRET: "foreign-secret", REFRESH: "synthetic-foreign-refresh"})
    mismatch = migration.run({"credential_namespace": str(tmp_path / "foreign-library"), "expected_client_id": "fixture-client"})
    assert mismatch["client_match"] is False
    assert mismatch["stage_label"] == "OAuth client 不匹配，已保留原 client。"
    assert vault.load(CLIENT) == "fixture-client" and vault.load(REFRESH) == "synthetic-canonical-refresh"
    assert workspace.meta("youtube_oauth_authorized") != "1"
    backend.path.chmod(0o644)
    denied = migration.run({})
    assert denied["permission_error"] is True
    assert denied["credential_check"]["read"] is False
    assert denied["oauth_authorized"] is False
    assert denied["stage_label"] == "凭据存储权限不足，未把字段存在当作授权有效。"
    backend.path.chmod(0o600)
    reread = NativeKeychainVault(namespace=str(workspace.root.resolve()), backend=backend)
    assert reread.load(REFRESH) == "synthetic-canonical-refresh"


def test_explicit_path_map_is_idempotent_and_other_paths_stay(tmp_path):
    workspace, migration, vault, _backend = harness(tmp_path)
    workspace.add_candidate(candidate("mappedvideo", "Mapped"))
    old = tmp_path / "old-assets"
    old.mkdir()
    source = old / "transcript.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nkept line\n", encoding="utf-8")
    outside = tmp_path / "elsewhere" / "note.srt"
    outside.parent.mkdir()
    outside.write_text("outside\n", encoding="utf-8")
    with sqlite3.connect(workspace.database_path) as db:
        db.execute("INSERT INTO transcripts (video_id, language, path, content, created_at) VALUES ('mappedvideo', 'en', ?, 'kept', ?)", (str(source), EVIDENCE))
        db.execute("UPDATE candidates SET manuscript_path=? WHERE video_id='mappedvideo'", (str(outside),))
    destination = tmp_path / "library" / "assets"
    plan = {"path_map": [{"from": str(old), "to": str(destination)}]}
    mapped = migration.run(plan)
    moved = destination / "transcript.srt"
    assert mapped["canonical_paths_kept"] is False
    assert workspace.asset("mappedvideo")["transcript_path"] == str(moved)
    assert moved.read_text(encoding="utf-8").startswith("1\n")
    assert not source.exists()
    assert workspace.asset("mappedvideo")["manuscript_path"] == str(outside)
    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert json.loads(workspace.meta("youtube_asset_path_migration"))["map"] == plan["path_map"]
    migration.run(plan)
    assert moved.read_text(encoding="utf-8").startswith("1\n")
    assert workspace.asset("mappedvideo")["transcript_path"] == str(moved)


def test_only_evidenced_acquisition_failures_join_shared_wait(tmp_path):
    workspace, migration, _vault, _backend = harness(tmp_path)
    plant(workspace, "legacy429vid", state="unavailable", reason=SUBTITLE_429, attempts=3, retry_at=EVIDENCE + 500)
    plant(workspace, "legacynetvid", state="waiting_retry", reason="字幕获取暂时失败，等待共享能力探测。", kind="transient", attempts=2, commenced=EVIDENCE - 50)
    plant(workspace, "legacyunkvid", state="unavailable", reason="player response was empty", attempts=4)
    plant(workspace, "legacymodel", state="waiting_retry", reason="模型服务限流；同账号请求暂停。", attempts=2, retry_at=EVIDENCE + 50)
    plant(workspace, "legacypriv8", state="failed", reason="private video", kind="private")
    workspace.add_candidate(candidate("keptreading", "Kept", channel="kept-channel"))
    workspace.set_preparation_state("keptreading", "ready")
    workspace.save_manuscript("keptreading", "完整译文保留。\n", generator="fixture", transcript_characters=4)
    workspace.set_reading_state("keptreading", "read")
    with sqlite3.connect(workspace.database_path) as db:
        db.execute("UPDATE candidates SET request_kind='manual', commenced_at=? WHERE video_id='keptreading'", (EVIDENCE - 10,))
    workspace.set_excluded_channels(["excluded-ch"])
    workspace.add_candidate(candidate("unsubvideo1", "Unsub", channel="old-channel"))
    workspace.replace_subscription_sources([SubscriptionSource("old-channel", "Old")])
    workspace.replace_subscription_sources([])
    status = migration.run({})
    limited = {item["video_id"]: item for item in workspace.activity_items("rate_limited")["items"]}
    assert set(limited) == {"legacy429vid", "legacynetvid"}
    assert limited["legacy429vid"]["automatic_attempts"] == 3
    assert limited["legacy429vid"]["commenced_at"] is None
    assert limited["legacy429vid"]["retry_at"] == EVIDENCE + 500
    assert limited["legacynetvid"]["automatic_attempts"] == 2
    assert limited["legacynetvid"]["commenced_at"] == EVIDENCE - 50
    policy = workspace.youtube_requests.snapshot()
    assert policy["cooldown_until"] == EVIDENCE + 15 * 60
    assert policy["first_failure_at"] == EVIDENCE
    assert policy["failure_kind"] == "rate_limited"
    assert policy["strikes"] == 1 and policy["network_failures"] == 1
    unknown = workspace.activity_items("unavailable")["items"]
    assert unknown[0]["video_id"] == "legacyunkvid"
    assert unknown[0]["acquisition_failure_kind"] == "pending_review"
    assert unknown[0]["caption_checks_ended_at"] is None
    assert unknown[0]["automatic_attempts"] == 4
    assert [item["video_id"] for item in status["unknown"]] == ["legacyunkvid"]
    model = workspace.activity_items("waiting_retry")["items"][0]
    assert model["video_id"] == "legacymodel" and model["automatic_attempts"] == 2
    assert workspace.activity_items("failed")["items"][0]["acquisition_failure_kind"] == "private"
    assert workspace.activity_items("queued")["total"] == 0
    kept = workspace.asset("keptreading")
    assert kept["reading_state"] == "read" and kept["request_kind"] == "manual" and kept["manuscript_version"] == 1
    assert workspace.document("keptreading")["markdown"] == "完整译文保留。\n"
    assert workspace.asset("unsubvideo1")["preparation_state"] == "cancelled"
    assert workspace.excluded_channels() == ["excluded-ch"]
    assert workspace.add_candidate(candidate("anotherunsu", "Again", channel="old-channel")) is False
    migration.run({})
    assert workspace.youtube_requests.snapshot()["cooldown_until"] == EVIDENCE + 15 * 60
    assert workspace.activity_items("rate_limited")["items"][0]["automatic_attempts"] in {2, 3}


def test_rerun_keeps_a_newer_cancellation_and_a_disconnected_identity(tmp_path):
    workspace, migration, vault, backend = harness(tmp_path)
    source = NativeKeychainVault(namespace=str(tmp_path / "old-library"), backend=backend)
    source.save_many({CLIENT: "fixture-client", SECRET: "fixture-secret", REFRESH: "synthetic-source-refresh"})
    plant(workspace, "legacy429vid", state="unavailable", reason=SUBTITLE_429, attempts=3)
    plan = {"credential_namespace": str(tmp_path / "old-library"), "expected_client_id": "fixture-client"}
    migration.run(plan)
    workspace.change_queue(["legacy429vid"])
    workspace.set_meta("youtube_disconnect_local", "cleared")
    workspace.set_meta("youtube_oauth_authorized", "0")
    vault.save_many({CLIENT: "", SECRET: "", REFRESH: "", ACCESS: ""})
    status = migration.run(plan)
    assert workspace.asset("legacy429vid")["preparation_state"] == "cancelled"
    assert status["identity_withdrawn"] is True
    assert vault.load(REFRESH) is None
    assert workspace.meta("youtube_oauth_authorized") == "0"
    assert "synthetic-source-refresh" not in json.dumps(status)


def test_an_interrupted_stage_can_rerun_without_reconnecting(tmp_path, monkeypatch):
    workspace, migration, vault, backend = harness(tmp_path)
    source = NativeKeychainVault(namespace=str(tmp_path / "old-library"), backend=backend)
    source.save_many({CLIENT: "fixture-client", SECRET: "fixture-secret", REFRESH: "synthetic-source-refresh"})
    plant(workspace, "legacy429vid", state="unavailable", reason=SUBTITLE_429, attempts=1)

    def interrupted(self, path_map):
        raise OSError("injected interruption between durable stages")

    monkeypatch.setattr(HistoricalMigration, "_map_paths", interrupted)
    plan = {"credential_namespace": str(tmp_path / "old-library"), "expected_client_id": "fixture-client",
            "path_map": [{"from": str(tmp_path / "old-assets"), "to": str(tmp_path / "library" / "assets")}]}
    with pytest.raises(OSError, match="injected interruption"):
        migration.run(plan)
    partial = migration.status()
    assert partial["completed"] == ["credentials"] and partial["finished"] is False
    assert vault.load(REFRESH) == "synthetic-source-refresh"
    workspace.set_meta("youtube_disconnect_local", "cleared")
    workspace.set_meta("youtube_oauth_authorized", "0")
    vault.save_many({CLIENT: "", SECRET: "", REFRESH: ""})
    monkeypatch.undo()
    finished = migration.run(plan)
    assert finished["finished"] is True and finished["identity_withdrawn"] is True
    assert vault.load(REFRESH) is None
    assert workspace.meta("youtube_oauth_authorized") == "0"
    assert workspace.activity_items("rate_limited")["total"] == 1
    assert workspace.activity_items("queued")["total"] == 0

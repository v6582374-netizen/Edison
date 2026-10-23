"""Hard-to-stage edges of backup and rollback; the Web drill covers the flow."""
import json
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from youtube_strataread.workbench import workspace as workspace_module
from youtube_strataread.workbench.cutover import CutoverBlocked, CutoverHold, admitted
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.recovery import (
    LibraryBackup,
    check_program,
    library_facts,
    restore,
    verify,
)
from youtube_strataread.workbench.workspace import YOUTUBE_DATA_CONTRACT, LocalWorkspace

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("cleanup", ["pending", "failed"])
@pytest.mark.parametrize("managed_content", [True, False])
def test_incompatible_web_start_and_status_reads_preserve_connection_storage(
    tmp_path, monkeypatch, cleanup, managed_content,
):
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    library = LocalWorkspace.open(tmp_path / "library")
    library.set_meta("youtube_data_contract", str(YOUTUBE_DATA_CONTRACT + 1))
    library.set_meta("youtube_disconnect_local", cleanup)
    library.set_meta("youtube_disconnect_google", "pending")
    library.set_meta("youtube_cutover", json.dumps({"held": True, "state": "draining", "drained": False}))
    with sqlite3.connect(library.database_path) as database:
        database.execute("DROP TABLE IF EXISTS youtube_api_usage")
    before = library_facts(library.database_path, library.root)
    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"preserved":"synthetic-current-credential"}')
    credentials.chmod(0o600)
    content = tmp_path / "content" if managed_content else library.root.parent / "youtube-content-session"
    if cleanup == "failed":
        content.mkdir()
        (content / "state.json").write_text('{"generation":7,"disconnected":true,"maintenance_started_at":123}')
        (content / "current.cookies").write_text("synthetic-current-session")
    content_before = {str(path.relative_to(content)): path.read_bytes() if path.is_file() else None
                      for path in content.rglob("*")}
    content_mode = content.stat().st_mode if content.exists() else None
    monkeypatch.setenv("YOUTUBE_WORKBENCH_WORKSPACE", str(library.root))
    if managed_content:
        monkeypatch.setenv("EDISON_CONTENT_SESSION_DIR", str(content))
    else:
        monkeypatch.delenv("EDISON_CONTENT_SESSION_DIR", raising=False)
    monkeypatch.setenv("EDISON_CREDENTIAL_FILE", str(credentials))
    monkeypatch.setenv("COWORKER_API_TOKEN", "incompatible-test")
    app = create_app(SessionManager(data_dir=tmp_path / "host"))
    try:
        with TestClient(app, headers={"X-OpenWorker-Token": "incompatible-test"}) as client:
            for capability in ("connection.status", "activity.snapshot", "content_session.status", "acceptance.status"):
                response = client.post("/v1/youtube/capability", json={"capability": capability}).json()
                assert response["ok"], response
                if capability == "content_session.status":
                    assert response["result"]["state"] == "unverified"
                    assert response["result"]["generation"] == (7 if cleanup == "failed" else None)
            assert library_facts(library.database_path, library.root) == before
            assert credentials.read_text() == '{"preserved":"synthetic-current-credential"}'
            assert {str(path.relative_to(content)): path.read_bytes() if path.is_file() else None
                    for path in content.rglob("*")} == content_before
            assert (content.stat().st_mode if content.exists() else None) == content_mode
            if cleanup == "pending":
                assert not content.exists()
    finally:
        app.state.youtube.close()


def _candidate(video_id: str) -> Candidate:
    now = datetime.now(timezone.utc)
    return Candidate(video_id=video_id, channel_id="channel", channel_title="Channel", title=video_id,
                     url=f"https://www.youtube.com/watch?v={video_id}", published_at=now.isoformat(),
                     published_ts=now.timestamp())


def _library(root: Path) -> LocalWorkspace:
    library = LocalWorkspace.open(root / "library")
    library.add_candidate(_candidate("kept0000001"))
    library.store_transcript("kept0000001", language="en", srt_text="1\n00:00:00,000 --> 00:00:01,000\nkept line\n")
    library.save_manuscript("kept0000001", "# 文稿\n正文\n", generator="fixture", transcript_characters=9,
                            translation="完整译文\n")
    library.set_reading_state("kept0000001", "read")
    return library


def _drained(library: LocalWorkspace) -> CutoverHold:
    hold = CutoverHold(library)
    hold.begin(threading.Event())
    hold.observe_drain((), lambda: None)
    hold.wait_idle()
    assert hold.quiescent() is not None
    return hold


def _backup(tmp_path: Path, library: LocalWorkspace, hold: CutoverHold) -> LibraryBackup:
    return LibraryBackup(library, hold, tmp_path / "backups",
                         separately_managed=[tmp_path / "content", tmp_path / "credentials.json"])


def test_backup_requires_proven_writer_stop(tmp_path):
    library = _library(tmp_path)
    hold = CutoverHold(library)
    with pytest.raises(CutoverBlocked, match="写者确认结束"):
        _backup(tmp_path, library, hold).run()
    hold.begin(threading.Event())
    with pytest.raises(CutoverBlocked, match="写者确认结束"):
        _backup(tmp_path, library, hold).run()  # Held but not yet drained.
    assert not (tmp_path / "backups").exists() or not any((tmp_path / "backups").iterdir())


def test_backup_is_void_when_work_is_admitted_during_restore_verification(tmp_path):
    library = _library(tmp_path)
    # A real retained document gives the concurrent operation a measurable copy
    # window. No internal backup collaborator is replaced.
    library.save_manuscript("kept0000001", "正文\n" * 2_000_000, generator="fixture", transcript_characters=9)
    hold = _drained(library)
    changed = threading.Event()
    def withdraw_during_verification():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if list((tmp_path / "backups").glob(".verify-*")):
                with hold.work(withdrawal=True):
                    library.set_reading_state("kept0000001", "inbox")
                changed.set()
                return
            time.sleep(.001)
    writer = threading.Thread(target=withdraw_during_verification)
    writer.start()
    try:
        with pytest.raises(CutoverBlocked, match="已作废"):
            _backup(tmp_path, library, hold).run()
    finally:
        writer.join(timeout=11)
    assert changed.is_set()
    assert list((tmp_path / "backups").iterdir()) == []
    assert library.meta("youtube_backup") is None


def test_backup_excludes_credentials_and_restores_identical_facts(tmp_path):
    library = _library(tmp_path)
    (library.root / "stray.cookies").write_text("SID\tsecret-session\n")
    (library.root / "assets" / "link").symlink_to(tmp_path / "credentials.json")
    (tmp_path / "credentials.json").write_text('{"refresh":"secret-refresh"}')
    record = _backup(tmp_path, library, _drained(library)).run()
    assert record["verified"] is True and all(record["checks"].values())
    backup = Path(record["path"])
    assert backup.stat().st_mode & 0o077 == 0
    names = {path.relative_to(backup).parts[0] for path in backup.rglob("*")}
    assert names == {"workspace.sqlite3", "manifest.json", "assets"}
    for path in backup.rglob("*"):
        if path.is_file():
            assert b"secret-" not in path.read_bytes()
    manifest = json.loads((backup / "manifest.json").read_text())
    assert {"stray.cookies", "assets/link"} <= set(manifest["not_copied"])
    assert record["summary"]["transcripts"] == 1 and record["translations"] == 1
    target = tmp_path / "restored"
    result = restore(backup, target)
    assert result["restored"] is True
    # The restore marks the hold afterwards, so only the summary is unchanged.
    assert library_facts(target / "workspace.sqlite3", target)["summary"] == manifest["facts"]["summary"]
    restored = LocalWorkspace(target)
    assert restored.document("kept0000001")["markdown"] == "# 文稿\n正文\n"
    assert restored.retained_transcript("kept0000001")["content"].endswith("kept line\n")
    assert restored.asset("kept0000001")["reading_state"] == "read"
    held = json.loads(restored.meta("youtube_cutover"))
    assert held["held"] is True and "一致性备份恢复" in held["reason"]


def test_restore_never_overwrites_and_detects_a_damaged_backup(tmp_path):
    library = _library(tmp_path)
    backup = Path(_backup(tmp_path, library, _drained(library)).run()["path"])
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "workspace.sqlite3").write_text("latest")
    with pytest.raises(FileExistsError, match="不会覆盖"):
        restore(backup, occupied)
    assert (occupied / "workspace.sqlite3").read_text() == "latest"
    manuscript = next(backup.rglob("manuscript.md"))
    manuscript.write_text("tampered\n")
    assert verify(backup)["checks"]["files_match"] is False
    target = tmp_path / "damaged"
    assert restore(backup, target)["restored"] is False
    assert list(target.iterdir()) == []


def test_reference_outside_the_library_fails_verification(tmp_path):
    library = _library(tmp_path)
    outside = tmp_path / "elsewhere.md"
    outside.write_text("outside\n")
    with sqlite3.connect(library.database_path) as connection:
        connection.execute("UPDATE candidates SET manuscript_path=?", (str(outside),))
    record = _backup(tmp_path, library, _drained(library)).run()
    assert record["verified"] is False and record["outside_library"] == 1


def test_backup_destination_cannot_hold_or_be_held_by_the_library(tmp_path):
    library = _library(tmp_path)
    hold = _drained(library)
    with pytest.raises(ValueError, match="资料库之外"):
        LibraryBackup(library, hold, library.root / "backups", separately_managed=[]).run()
    with pytest.raises(ValueError, match="资料库之外"):
        LibraryBackup(library, hold, tmp_path / "b", separately_managed=[library.root / "content"]).run()


def test_backup_rejects_a_symlinked_assets_root_without_copying_credentials(tmp_path):
    library = _library(tmp_path)
    assets = library.root / "assets"
    retained = tmp_path / "retained-assets"
    assets.rename(retained)
    (retained / "credentials.json").write_text("secret-refresh")
    assets.symlink_to(retained, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        _backup(tmp_path, library, _drained(library)).run()
    assert list((tmp_path / "backups").iterdir()) == []


def test_restore_checks_database_bytes_before_relocating_paths(tmp_path):
    library = _library(tmp_path)
    backup = Path(_backup(tmp_path, library, _drained(library)).run()["path"])
    # Schema bytes differ while the stored rows still match the facts digest.
    with sqlite3.connect(backup / "workspace.sqlite3") as connection:
        connection.execute("CREATE INDEX altered_backup ON candidates(title)")
    with pytest.raises(ValueError, match="数据库校验失败"):
        restore(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_newer_library_contract_holds_an_older_program_read_only(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setattr(workspace_module, "YOUTUBE_DATA_CONTRACT", YOUTUBE_DATA_CONTRACT + 1)
    newer = LocalWorkspace.open(root)
    newer.add_candidate(_candidate("queued00001"))
    newer.set_meta("drain_paused", "0")
    monkeypatch.setattr(workspace_module, "YOUTUBE_DATA_CONTRACT", YOUTUBE_DATA_CONTRACT)
    older = LocalWorkspace.open(root)
    assert older.newer_data_contract == (YOUTUBE_DATA_CONTRACT + 1, YOUTUBE_DATA_CONTRACT)
    assert older.meta("youtube_data_contract") == str(YOUTUBE_DATA_CONTRACT + 1)  # Never lowered.
    assert older.claim_next_queued_asset() is None
    with pytest.raises(CutoverBlocked, match="更新的程序"), admitted(older):
        pass
    hold = CutoverHold(older)
    assert hold.held and hold.status()["state"] == "incompatible"
    with pytest.raises(CutoverBlocked, match="只提供阅读"), hold.work():
        pass
    with hold.work(withdrawal=True):
        older.set_reading_state("queued00001", "read")
    hold.observe_drain((), lambda: None)  # No durable hold is invented.
    hold.wait_idle()
    assert older.meta("youtube_cutover") is None


def test_program_gate_refuses_undeclared_and_older_contracts(tmp_path):
    library = LocalWorkspace.open(tmp_path / "library")
    assert check_program(REPOSITORY, library.root)["compatible"] is True
    old = tmp_path / "old" / "src" / "youtube_strataread" / "workbench"
    old.mkdir(parents=True)
    (old / "workspace.py").write_text("class LocalWorkspace: ...\n")
    refused = check_program(tmp_path / "old", library.root)
    assert refused["compatible"] is False and refused["program_contract"] is None
    assert "promote_web_release" in refused["note"]
    library.set_meta("youtube_data_contract", str(YOUTUBE_DATA_CONTRACT + 1))
    assert check_program(REPOSITORY, library.root)["compatible"] is False
    command = [sys.executable, "-m", "youtube_strataread.workbench.recovery", "check-program", str(REPOSITORY), str(library.root)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 1

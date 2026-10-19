import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from coworker.curriculum import CourseArchive
from coworker.curriculum_sync import (
    AuthenticationRequired,
    collect,
    export_documents,
    generate_transcript,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
RAW = {
    "code": 0,
    "list": [
        {
            "trans_type": "ainew",
            "all_content": [
                {"BeginSec": 10, "EndSec": 20, "Text": "先创建草图，然后标注尺寸，不生成特征。"},
                {"BeginSec": 100, "EndSec": 110, "Text": "尺寸是三十毫米。"},
            ],
        }
    ],
}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "coworker.curriculum_sync.SOURCES", {"design": ("三维造型与工业设计", "53450")}
    )
    store = CourseArchive(tmp_path / "archive")
    item = {
        "sub_id": "123",
        "course_id": "53450",
        "title": "2026-10-09第6-7节",
        "status": 6,
        "content": json.dumps({"api_pass": "NEVER_EXPORT", "playback": {"url": "SECRET_MEDIA"}}),
    }
    return store, item


def model():
    calls = []

    def complete(_model, messages, *args, **kwargs):
        blocks = json.loads(messages[1]["content"])
        calls.append(blocks)
        return SimpleNamespace(
            text=json.dumps(blocks, ensure_ascii=False), finish_reason="stop", tool_calls=[]
        )

    return SimpleNamespace(model="configured-host", provider_complete=complete), calls


def test_late_captions_updates_history_and_identical_export(monkeypatch, tmp_path):
    store, item = setup(monkeypatch, tmp_path)
    raw = {"code": 10002, "list": []}

    def fetch(path):
        return (
            {"success": True, "result": {"code": 200, "data": [item]}}
            if "catalogue" in path
            else raw
        )

    collect(store, now=NOW, fetch=fetch)
    assert store.recording("123")["state"] == "needs_transcript"
    assert not export_documents(store)["files"]
    raw = RAW
    collect(store, now=NOW + timedelta(hours=7), fetch=fetch)
    manager, calls = model()
    first = generate_transcript(store, "123", manager)
    assert generate_transcript(store, "123", manager)["id"] == first["id"]
    assert len(calls) == 1
    snapshot = export_documents(store)
    assert snapshot["files"][0]["markdown"] == store.document(first["id"])["markdown"]
    assert "[00:00:10]" in first["markdown"] and "[00:01:40]" in first["markdown"]
    assert "NEVER_EXPORT" not in json.dumps(snapshot) and "SECRET_MEDIA" not in json.dumps(snapshot)
    collect(store, now=NOW + timedelta(days=1), fetch=fetch, force=True)
    generate_transcript(store, "123", manager)
    assert len(calls) == 1
    raw = json.loads(json.dumps(RAW))
    raw["list"][0]["all_content"][1]["Text"] = "尺寸是四十毫米。"
    collect(store, now=NOW + timedelta(days=2), fetch=fetch, force=True)
    assert not store.recording("123")["documents"]
    second = generate_transcript(store, "123", manager)
    assert "四十" in second["markdown"]
    assert "三十" in store.document(first["id"])["markdown"]
    # The initial no-data response and the first populated transcript are both retained.
    assert len(list((store.root / "sources/123/versions").glob("*.json"))) == 2
    assert len(export_documents(store)["files"]) == 2


def test_empty_or_auth_error_never_erases_existing_source(monkeypatch, tmp_path):
    store, item = setup(monkeypatch, tmp_path)
    state = [RAW]

    def fetch(path):
        if "catalogue" in path:
            return {"success": True, "result": {"code": 200, "data": [item]}}
        if isinstance(state[0], Exception):
            raise state[0]
        return state[0]

    collect(store, now=NOW, fetch=fetch)
    manager, _ = model()
    first = generate_transcript(store, "123", manager)
    state[0] = {"code": 10002, "list": []}
    collect(store, now=NOW, fetch=fetch, force=True)
    assert store.recording("123")["documents"][0]["id"] == first["id"]
    state[0] = AuthenticationRequired()
    result = collect(store, now=NOW, fetch=fetch, force=True)
    assert result["errors"] and store.sync_status("lesson:123")["state"] == "auth_required"
    assert store.document(first["id"])["markdown"]


def test_future_lessons_and_wrong_course_never_become_recordings(monkeypatch, tmp_path):
    store, item = setup(monkeypatch, tmp_path)
    item["title"] = "2026-10-16第6-7节"
    collect(
        store, now=NOW, fetch=lambda _: {"success": True, "result": {"code": 200, "data": [item]}}
    )
    assert not store.listing()["recordings"]
    item["course_id"] = "999"
    assert collect(store, now=NOW, fetch=lambda _: {"success": True, "result": {"data": [item]}})[
        "errors"
    ]
    assert not store.listing()["recordings"]


def test_old_notes_kept_but_cannot_satisfy_transcript_generation(monkeypatch, tmp_path):
    store, item = setup(monkeypatch, tmp_path)
    collect(
        store,
        now=NOW,
        fetch=lambda p: (
            {"success": True, "result": {"code": 200, "data": [item]}} if "catalogue" in p else RAW
        ),
    )
    old = store.save_document(
        "123", "旧详细笔记" * 40, source_hash=store.source_hash("123"), model="host"
    )
    assert not store.recording("123")["documents"]
    assert store.recording("123")["history"][0]["id"] == old["id"]
    assert store.recording("123")["state"] == "pending"
    store.set_state("123", "needs_transcript", "旧任务正在用音频重新转写")
    collect(
        store,
        now=NOW,
        fetch=lambda p: (
            {"success": True, "result": {"code": 200, "data": [item]}} if "catalogue" in p else RAW
        ),
    )
    assert store.recording("123")["state"] == "pending"
    assert not store.recording("123")["detail"]
    bad = SimpleNamespace(
        model="host",
        provider_complete=lambda *a, **k: SimpleNamespace(
            text='[{"id":0,"text":"伪造的概要"}]', finish_reason="stop", tool_calls=[]
        ),
    )
    with pytest.raises(ValueError, match="遗漏"):
        generate_transcript(store, "123", bad)
    assert not store.recording("123")["documents"]
    assert store.document(old["id"])["markdown"]

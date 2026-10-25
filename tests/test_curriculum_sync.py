import json
import urllib.error
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from coworker.curriculum import CourseArchive
from coworker.curriculum_sync import (
    AccessDenied,
    AuthenticationRequired,
    collect,
    export_documents,
    fetch_json,
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


def test_platform_request_includes_language_without_login_credentials(monkeypatch):
    def platform(request, **kwargs):
        if not request.get_header("Accept-language"):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
        assert not request.has_header("Authorization")
        assert not request.has_header("Cookie")
        return BytesIO(json.dumps(RAW).encode())

    monkeypatch.setattr("urllib.request.urlopen", platform)
    assert fetch_json("/courseapi/v3/web-socket/search-trans-result?sub_id=123&format=json") == RAW


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationRequired),
        (419, AuthenticationRequired),
        (403, PermissionError),
        (500, urllib.error.HTTPError),
    ],
)
def test_http_rejections_distinguish_authentication_from_access_denial(
    monkeypatch, status, expected
):
    def platform(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, status, "Rejected", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", platform)
    with pytest.raises(expected):
        fetch_json("/courseapi/v2/course/catalogue?course_id=53450")


@pytest.mark.parametrize("target", ["catalogue", "transcript"])
def test_business_access_denial_is_not_reported_as_expired_login(monkeypatch, tmp_path, target):
    store, item = setup(monkeypatch, tmp_path)

    def platform(request, **kwargs):
        if "catalogue" in request.full_url:
            payload = (
                {"success": False, "result": {"code": 403}}
                if target == "catalogue"
                else {"success": True, "result": {"code": 200, "data": [item]}}
            )
        else:
            payload = {"code": 403}
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", platform)
    result = collect(store, now=NOW, fetch=fetch_json)
    assert list(result["errors"].values()) == ["平台拒绝访问，请检查请求条件或权限"]
    if target == "transcript":
        assert store.sync_status("lesson:123")["state"] == "access_denied"
        assert store.recording("123")["state"] == "failed"


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
    state[0] = AccessDenied()
    result = collect(store, now=NOW, fetch=fetch, force=True)
    assert store.sync_status("lesson:123")["state"] == "access_denied"
    assert "重新认证" not in json.dumps(result["errors"], ensure_ascii=False)
    assert store.recording("123")["documents"][0]["id"] == first["id"]
    state[0] = RAW
    result = collect(store, now=NOW, fetch=fetch, force=True)
    assert not result["errors"]
    assert store.sync_status("lesson:123")["state"] == "available"
    assert store.recording("123")["documents"][0]["id"] == first["id"]


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


@pytest.mark.parametrize(("title", "period"), [(" 2026-10-10第1节", 1), (" 2026-10-10第2节", 2)])
def test_single_period_platform_titles_are_valid_recordings(monkeypatch, tmp_path, title, period):
    store, item = setup(monkeypatch, tmp_path)
    item["title"] = title
    result = collect(
        store,
        now=NOW,
        fetch=lambda path: (
            {"success": True, "result": {"code": 200, "data": [item]}}
            if "catalogue" in path
            else RAW
        ),
    )
    assert not result["errors"]
    assert store.listing()["coverage"]["design"]["complete"]
    record = store.recording("123")
    assert record["date"] == "2026-10-10"
    assert record["first"] == record["last"] == period
    assert record["title"] == title


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

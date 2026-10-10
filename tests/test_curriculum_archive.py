import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from coworker.curriculum import CourseArchive, clean_notes_text, generate_notes, platform_segments
from coworker.server.curriculum import attach_curriculum

RAW = {"code": 0, "list": [{"all_content": [{"BeginSec": 10, "EndSec": 20, "Text": "课程内容"}]}]}
META = {
    "id": "123",
    "course": "design",
    "platform_course_id": "53450",
    "name": "三维造型与工业设计",
    "title": "2026-10-09第6-7节",
    "date": "2026-10-09",
    "first": 6,
    "last": 7,
    "teacher": "张哲硕",
    "room": "文3-115",
    "source_url": "https://classroom.hzcu.edu.cn/livingroom?course_id=53450&sub_id=123",
    "warnings": [],
}


@pytest.fixture
def store(tmp_path):
    archive = CourseArchive(tmp_path / "archive")
    archive.import_recording(META, RAW)
    archive.save_coverage(
        "design", {"complete": True, "from": "2026-09-14", "through": "2026-10-10"}
    )
    return archive


def test_recordings_match_actual_periods_not_course_level_information(store):
    assert len(store.occurrence("design", "2026-10-09", 6, 7)["recordings"]) == 1
    assert store.occurrence("design", "2026-10-09", 8, 9) == {
        "state": "not_listed",
        "recordings": [],
    }
    assert store.occurrence("design", "2026-10-16", 6, 7)["state"] == "not_checked"


def test_user_excluded_courses_are_hidden_without_destroying_existing_sources(store):
    coverage = store.listing()["coverage"]["design"]
    store.save_coverage("design", {**coverage, "excluded": True})
    assert not store.listing()["recordings"]
    assert "design" not in {c["key"] for c in store.listing()["courses"]}
    assert store.occurrence("design", "2026-10-09", 6, 7) == {"state": "excluded", "recordings": []}
    assert store.segments("123")
    assert json.loads((store.root / "sources/123/platform.json").read_text()) == RAW


def test_source_is_preserved_separately_and_never_in_archive_payload(store):
    private = store.root / "sources/123/platform.json"
    assert json.loads(private.read_text()) == RAW
    data = json.dumps(store.listing(), ensure_ascii=False)
    assert "课程内容" not in data
    assert "all_content" not in data
    assert "sources/" not in data


def test_full_retranscription_does_not_overwrite_native_subtitles(store):
    store.save_supplement(
        "123",
        [{"start": 12, "end": 21, "text": "English source corrected by ASR"}],
        engine="local",
        reviewed_ranges=[[0, 30]],
        replaces_native=True,
    )
    assert [s["text"] for s in store.segments("123")] == ["English source corrected by ASR"]
    assert json.loads((store.root / "sources/123/platform.json").read_text()) == RAW


def test_changed_source_invalidates_current_notes_but_preserves_old_document(store):
    first = store.save_document(
        "123",
        "# 第一版\n\n" + "已整理内容。" * 30,
        source_hash=store.source_hash("123"),
        model="host",
    )
    assert store.recording("123")["documents"]
    store.save_supplement(
        "123",
        [{"start": 30, "end": 35, "text": "新增内容"}],
        engine="local",
        reviewed_ranges=[[25, 40]],
    )
    assert store.recording("123")["documents"] == []
    assert store.document(first["id"])["markdown"].startswith("# 第一版")
    with pytest.raises(ValueError, match="来源发生变化"):
        store.save_document("123", "内容" * 100, source_hash=first["source_hash"], model="host")


def test_new_revision_replaces_current_perspective_without_losing_old_link(store):
    first = store.save_document(
        "123", "旧稿" * 100, source_hash=store.source_hash("123"), model="host"
    )
    second = store.save_document(
        "123", "修订稿" * 100, source_hash=store.source_hash("123"), model="host"
    )
    assert [d["id"] for d in store.recording("123")["documents"]] == [second["id"]]
    assert store.document(first["id"])["markdown"] == "旧稿" * 100


def test_explicit_regeneration_uses_a_new_reply_instead_of_the_old_checkpoint(store):
    replies = iter(["原笔记。" * 40, "重新核对后的笔记。" * 40])
    manager = SimpleNamespace(
        model="host",
        provider_complete=lambda *args, **kwargs: SimpleNamespace(
            text=next(replies), finish_reason="stop", tool_calls=[]
        ),
    )
    first = generate_notes(store, "123", manager)
    second = generate_notes(store, "123", manager, regenerate=True)
    assert "重新核对后的笔记" in second["markdown"]
    assert store.document(first["id"])["markdown"] == first["markdown"]


def test_classes_after_collection_are_not_called_missing_recordings(store):
    store.save_coverage(
        "design",
        {
            "complete": True,
            "from": "2026-09-14",
            "through": "2026-10-10",
            "checked_at": "2026-10-09T16:30:00+00:00",
        },
    )
    assert store.occurrence("design", "2026-10-10", 6, 7)["state"] == "not_ended"
    assert store.occurrence("design", "2026-10-08", 6, 7)["state"] == "not_listed"


def test_archive_routes_inherit_existing_server_authentication(tmp_path, monkeypatch):
    from coworker.server import SessionManager, create_app

    monkeypatch.setenv("COWORKER_API_TOKEN", "test-archive-token")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    with TestClient(create_app(SessionManager(data_dir=tmp_path / "state"))) as client:
        assert client.get("/v1/curriculum/archive").status_code == 401
        response = client.get(
            "/v1/curriculum/archive", headers={"x-openworker-token": "test-archive-token"}
        )
        assert response.status_code == 200


def test_explicit_missing_subtitles_is_empty_not_misreported_as_recording_absent():
    assert platform_segments({"code": 10002, "total": 0, "list": []}) == []
    with pytest.raises(ValueError):
        platform_segments({"code": 401, "list": []})


def test_invalid_timestamp_and_truncated_generation_never_become_ready(store):
    with pytest.raises(ValueError):
        platform_segments(
            {
                "code": 0,
                "list": [{"all_content": [{"BeginSec": 20, "EndSec": 10, "Text": "invalid"}]}],
            }
        )
    manager = SimpleNamespace(
        model="host",
        provider_complete=lambda *a, **k: SimpleNamespace(
            text="partial" * 30, tool_calls=[], finish_reason="length"
        ),
    )
    with pytest.raises(ValueError, match="未完整"):
        generate_notes(store, "123", manager)
    assert store.recording("123")["state"] == "failed"
    assert not store.recording("123")["documents"]


@pytest.mark.parametrize("text,reason", [("OK", "stop"), ("课堂内容。" * 50, None)])
def test_unconfirmed_or_empty_generation_never_becomes_a_finished_document(store, text, reason):
    manager = SimpleNamespace(
        model="host",
        provider_complete=lambda *a, **k: SimpleNamespace(
            text=text, tool_calls=[], finish_reason=reason
        ),
    )
    with pytest.raises(ValueError, match="未完整"):
        generate_notes(store, "123", manager)
    assert not store.recording("123")["documents"]


def test_authoring_boilerplate_is_removed_without_losing_classroom_steps():
    text = "## 整理步骤\n\n1. 整理字幕\n\n# 最终答案：Go 语言程序设计课堂笔记\n\n- 材料分段：1/1\n\n## 编译步骤 [00:05:00]\n\n1. go build\n2. 运行程序"
    cleaned = clean_notes_text(text, "GO语言程序设计")
    assert "整理字幕" not in cleaned and "最终答案" not in cleaned and "材料分段" not in cleaned
    assert "1. go build\n2. 运行程序" in cleaned


def test_long_source_is_split_when_a_detailed_reply_hits_the_output_limit(store):
    store.import_recording(
        META,
        {
            "code": 0,
            "list": [
                {
                    "all_content": [
                        {
                            "BeginSec": i * 10,
                            "EndSec": i * 10 + 5,
                            "Text": "课堂知识点和例题。" * 40,
                        }
                        for i in range(30)
                    ]
                }
            ],
        },
    )
    replies = iter(
        [
            ("被截断的草稿" * 30, "length"),
            ("前半课堂笔记。" * 30, "stop"),
            ("后半课堂笔记。" * 30, "stop"),
        ]
    )

    def complete(*args, **kwargs):
        text, reason = next(replies)
        return SimpleNamespace(text=text, finish_reason=reason, tool_calls=[])

    doc = generate_notes(store, "123", SimpleNamespace(model="host", provider_complete=complete))
    assert "前半课堂笔记" in doc["markdown"] and "后半课堂笔记" in doc["markdown"]
    assert "被截断的草稿" not in doc["markdown"]


def test_routes_download_only_documents_and_sources_have_no_route(tmp_path, monkeypatch):
    monkeypatch.setenv("EDISON_CURRICULUM_ROOT", str(tmp_path / "archive"))
    app = FastAPI()
    attach_curriculum(app, SimpleNamespace(_data_base=tmp_path))
    archive = app.state.curriculum
    archive.import_recording(META, RAW)
    doc = archive.save_document(
        "123",
        "# 课堂笔记\n\n" + "详细课堂内容。" * 30,
        source_hash=archive.source_hash("123"),
        model="host",
    )
    with TestClient(app) as client:
        assert client.get("/v1/curriculum/archive").status_code == 200
        assert client.get("/v1/curriculum/sources/123/platform.json").status_code == 404
        assert client.get("/v1/curriculum/documents/missing").status_code == 404
        response = client.get("/v1/curriculum/documents/" + doc["id"] + "/download")
        assert response.text == doc["markdown"]
        assert "attachment" in response.headers["content-disposition"]


def test_generation_is_idempotent_and_uses_host_model(store):
    calls = []

    def complete(model, *args, **kwargs):
        calls.append(model)
        return SimpleNamespace(
            text="# 课堂笔记\n\n" + "详细课堂笔记内容。" * 35, tool_calls=[], finish_reason="stop"
        )

    manager = SimpleNamespace(model="configured-host-model", provider_complete=complete)
    first = generate_notes(store, "123", manager)
    assert generate_notes(store, "123", manager)["id"] == first["id"]
    assert calls == ["configured-host-model"]

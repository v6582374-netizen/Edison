"""Durable reading state is independent of library placement and view geometry."""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.reader import ReaderStore, StaleBook
from coworker.server import SessionManager, create_app

TOKEN = {"X-OpenWorker-Token": "reader-reading-test"}


@pytest.fixture
def reader(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_API_TOKEN", TOKEN["X-OpenWorker-Token"])
    app = create_app(SessionManager(workspace=tmp_path, data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        original = (Path(__file__).parent / "fixtures/reader/epub3.epub").read_bytes()
        book = client.post("/v1/reader/books", headers={**TOKEN, "Content-Type": "application/epub+zip"}, content=original).json()["book"]
        yield client, app.state.reader, book


def update(book, revision=0, index=0, offset=30):
    position = {"cfi": "epubcfi(/6/2!/4/2,/1:0,/1:3)", "section": "OPS/text/ch1.xhtml",
                "index": index, "offset": offset, "length": 100, "quote": "测试文"}
    return {"content_version": book["content_version"], "revision": revision,
            "operation_id": str(uuid.uuid4()), "position": position, "furthest": position,
            "preferences": {"font_size": 28, "contents_open": False}}


def test_position_furthest_and_preferences_survive_recreation(reader):
    client, store, book = reader
    url = f"/v1/reader/books/{book['id']}/reading"
    empty = client.get(url, headers=TOKEN).json()
    assert empty["revision"] == 0 and empty["position"] is None
    assert empty["preferences"]["font_size"] == 20
    first = client.put(url, headers=TOKEN, json=update(book, index=1, offset=80)).json()
    second = client.put(url, headers=TOKEN, json=update(book, revision=1, offset=10)).json()
    assert second["position"]["index"] == 0
    assert second["furthest"] == first["position"]
    assert second["progress"] == first["progress"] == .9
    assert ReaderStore(store.root).reading(book["id"]) == second
    # Library classification has its own revision and cannot reset reading state.
    assert client.patch(url.removesuffix("/reading"), headers=TOKEN, json={"revision": 0, "location": "archive"}).status_code == 200
    assert client.get(url, headers=TOKEN).json() == second
    assert "no-store" in client.get(url, headers=TOKEN).headers["cache-control"]


def test_fast_forward_then_back_preserves_high_water_mark(reader):
    client, _, book = reader
    body = update(book, offset=1)
    body["furthest"] = update(book, index=1, offset=90)["position"]
    saved = client.put(f"/v1/reader/books/{book['id']}/reading", headers=TOKEN, json=body).json()
    assert saved["position"]["index"] == 0 and saved["progress"] == .95


def test_lost_response_retry_is_idempotent_but_stale_writer_cannot_overwrite(reader):
    client, _, book = reader
    url = f"/v1/reader/books/{book['id']}/reading"
    body = update(book)
    first = client.put(url, headers=TOKEN, json=body).json()
    assert client.put(url, headers=TOKEN, json=body).json() == first
    reused_id = copy.deepcopy(body); reused_id["position"]["offset"] = 45
    assert client.put(url, headers=TOKEN, json=reused_id).status_code == 409
    assert client.put(url, headers=TOKEN, json=update(book)).status_code == 409
    assert client.get(url, headers=TOKEN).json() == first


@pytest.mark.parametrize("change,expected", [
    ({"revision": -1}, 422), ({"content_version": "another-book"}, 409),
    ({"preferences": {"font_size": 999, "contents_open": True}}, 422),
    ({"position.index": 90}, 422), ({"position.offset": 200}, 422),
    ({"position.cfi": "not-a-cfi"}, 422), ({"position.quote": "x" * 65}, 422),
])
def test_invalid_or_foreign_record_does_not_mutate(reader, change, expected):
    client, store, book = reader
    body = update(book)
    for field, value in change.items():
        if "." in field:
            parent, child = field.split("."); body[parent][child] = value
        else:
            body[field] = value
    response = client.put(f"/v1/reader/books/{book['id']}/reading", headers=TOKEN, json=body)
    assert response.status_code == expected
    assert store.reading(book["id"])["revision"] == 0


def test_reading_routes_require_host_auth(reader):
    client, _, book = reader
    url = f"/v1/reader/books/{book['id']}/reading"
    assert client.get(url).status_code == 401
    assert client.put(url, json=update(book)).status_code == 401


def test_two_first_writers_cannot_both_commit(reader):
    _, store, book = reader
    def save(body):
        try:
            return store.save_reading(book["id"], body)["revision"]
        except StaleBook:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(save, [update(book, offset=5), update(book, offset=90)]))
    assert set(result) == {1, "conflict"}

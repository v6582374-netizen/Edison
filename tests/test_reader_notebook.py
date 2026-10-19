"""Notebook identity, atomic snapshots, lost responses and content boundaries."""
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from coworker.reader import ReaderStore, StaleBook
from test_reader_reading import reader, TOKEN


def update(book, revision=0):
    return {"content_version": book["content_version"], "revision": revision, "operation_id": str(uuid.uuid4()),
            "book_note": "整本理解", "highlights": [{"id": str(uuid.uuid4()), "color": "yellow", "note": "自己的想法",
            "anchor": {"kind": "text", "cfi": "epubcfi(/6/2!/4/2,/1:0,/1:3)", "section": "OPS/text/ch1.xhtml",
                       "index": 0, "quote": "中😀", "offset": 0, "length": 3}}]}


def test_notebook_lifecycle_and_independent_revisions(reader):
    client, store, book = reader
    url = f"/v1/reader/books/{book['id']}/notebook"
    empty = client.get(url, headers=TOKEN)
    assert empty.json()["highlights"] == [] and empty.json()["revision"] == 0
    assert "no-store" in empty.headers["cache-control"]
    body = update(book)
    first = client.put(url, headers=TOKEN, json=body).json()
    assert first["revision"] == 1
    assert ReaderStore(store.root).notebook(book["id"]) == first
    assert client.put(url, headers=TOKEN, json=body).json() == first  # lost response
    second = {**first, "operation_id": str(uuid.uuid4())}
    second["highlights"][0]["note"] = "修改后的理解"
    second["highlights"][0]["color"] = "pink"
    saved = client.put(url, headers=TOKEN, json=second).json()
    assert saved["highlights"][0]["id"] == body["highlights"][0]["id"]
    assert saved["highlights"][0]["note"] == "修改后的理解"
    assert store.reading(book["id"])["revision"] == 0 and store.book(book["id"])["revision"] == 0
    saved.update(operation_id=str(uuid.uuid4()), highlights=[])
    deleted = client.put(url, headers=TOKEN, json=saved).json()
    assert deleted["highlights"] == [] and deleted["book_note"] == "整本理解"
    assert client.put(url, headers=TOKEN, json=second).status_code == 409  # cannot resurrect deletion


def test_concurrent_first_writes_and_reused_operation(reader):
    client, store, book = reader
    bodies = [update(book), update(book)]
    def save(body):
        try: return store.save_notebook(book["id"], body)
        except StaleBook: return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, bodies))
    assert sum(result is not None for result in results) == 1
    winner = bodies[next(i for i, result in enumerate(results) if result)]
    winner["book_note"] = "same ID, changed payload"
    assert client.put(f"/v1/reader/books/{book['id']}/notebook", headers=TOKEN, json=winner).status_code == 409


@pytest.mark.parametrize("case,status", [("version",409),("section",422),("offset",422),("kind",422),("duplicate_id",422),("duplicate_range",422),("color",422),("huge_note",422)])
def test_invalid_input_preserves_notebook(reader, case, status):
    client, store, book = reader
    body = update(book)
    if case == "version": body["content_version"] = "other-version"
    elif case == "section": body["highlights"][0]["anchor"]["index"] = 10
    elif case == "offset": body["highlights"][0]["anchor"]["offset"] = 1  # UTF-16 emoji needs two units
    elif case == "kind": body["highlights"][0]["anchor"]["kind"] = "img"
    elif case.startswith("duplicate"):
        duplicate = copy.deepcopy(body["highlights"][0])
        if case == "duplicate_id": duplicate["anchor"]["cfi"] = "epubcfi(/6/4!/4/2,/1:0,/1:3)"
        else: duplicate["id"] = str(uuid.uuid4())
        body["highlights"].append(duplicate)
    elif case == "color": body["highlights"][0]["color"] = "red"
    else: body["book_note"] = "x" * 50001
    response = client.put(f"/v1/reader/books/{book['id']}/notebook", headers=TOKEN, json=body)
    assert response.status_code == status
    assert store.notebook(book["id"])["revision"] == 0


def test_notebook_requires_auth_and_existing_book(reader):
    client, _, book = reader
    url = f"/v1/reader/books/{book['id']}/notebook"
    assert client.get(url).status_code == 401
    assert client.put(url, json=update(book)).status_code == 401
    assert client.get("/v1/reader/books/missing/notebook", headers=TOKEN).status_code == 404

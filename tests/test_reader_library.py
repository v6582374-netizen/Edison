"""Reader's durable contract, through the real authenticated FastAPI application."""
import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coworker.reader import ReaderStore
from coworker.server import SessionManager, create_app

FIXTURES = Path(__file__).parent / "fixtures" / "reader"
TOKEN = "isolated-reader-test-token"
HEADERS = {"X-OpenWorker-Token": TOKEN, "Content-Type": "application/epub+zip",
           "X-Reader-Filename": "same-name.epub"}


def revised_epub(replacements=None, additions=None):
    output = io.BytesIO()
    with zipfile.ZipFile(FIXTURES / "epub3.epub") as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            for old, new in (replacements or {}).items():
                data = data.replace(old, new)
            target.writestr(name, data)
        for name, data in (additions or {}).items():
            target.writestr(name, data)
    return output.getvalue()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_API_TOKEN", TOKEN)
    return create_app(SessionManager(workspace=tmp_path, data_dir=tmp_path / "data"))


def upload(client, data=None):
    return client.post("/v1/reader/books", content=data or (FIXTURES / "epub3.epub").read_bytes(), headers=HEADERS)


def test_original_identity_placement_and_restart(app, tmp_path):
    with TestClient(app) as client:
        result = upload(client)
        assert result.status_code == 201
        book = result.json()["book"]
        content = (FIXTURES / "epub3.epub").read_bytes()
        assert book["sha256"] == hashlib.sha256(content).hexdigest()
        assert book["content_version"] == book["sha256"] + ":reader-safe-epub-v1"
        path = f"/v1/reader/books/{book['id']}"
        json_headers = {"X-OpenWorker-Token": TOKEN}
        placed = client.patch(path, headers=json_headers, json={"location": "later", "revision": 0})
        assert placed.status_code == 200
        duplicate = upload(client).json()
        assert not duplicate["created"]
        assert duplicate["book"]["id"] == book["id"]
        assert duplicate["book"]["location"] == "later"
        assert duplicate["book"]["revision"] == 1
        original = client.get(path + "/original", headers=HEADERS)
        assert original.content == content
        assert original.headers["content-disposition"].startswith("attachment;")
        assert original.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in original.headers["cache-control"]
        changed = upload(client, revised_epub({b"urn:spike:3": b"urn:spike:new"})).json()["book"]
        assert changed["id"] != book["id"]  # same filename never merges distinct bytes
        assert client.patch(path, headers=json_headers, json={"location": "archive", "revision": 0}).status_code == 409
        assert client.get(path, headers=HEADERS).json()["location"] == "later"
    restarted = ReaderStore(tmp_path / "data" / "reader")
    assert len(restarted.listing()) == 2
    assert restarted.book(book["id"])["location"] == "later"
    assert restarted.original(book["id"]).read_bytes() == content
    assert not list(restarted.incoming.iterdir())


def test_all_reader_routes_require_existing_host_auth(app):
    with TestClient(app) as client:
        book = upload(client).json()["book"]
        path = f"/v1/reader/books/{book['id']}"
        for method, url in [("GET", "/v1/reader/books"), ("POST", "/v1/reader/books"),
                            ("GET", path), ("PATCH", path), ("GET", path + "/original")]:
            assert client.request(method, url).status_code == 401
        assert client.get(path, headers={"X-OpenWorker-Token": "wrong"}).status_code == 401


@pytest.mark.parametrize("kind", ["broken", "fixed", "image-spine", "missing", "traversal", "duplicate", "dtd", "drm"])
def test_rejected_import_does_not_create_records_or_files(app, kind):
    variants = {
        "broken": b"this is not a zip",
        "fixed": revised_epub({b"</metadata>": b'<meta property="rendition:layout">pre-paginated</meta></metadata>'}),
        "image-spine": revised_epub({b'href="text/ch1.xhtml" media-type="application/xhtml+xml"': b'href="text/ch1.xhtml" media-type="image/svg+xml"'}),
        "missing": revised_epub({b'idref="ch1"': b'idref="missing"'}),
        "traversal": revised_epub(additions={"../outside": b"no"}),
        "duplicate": revised_epub(additions={"mimetype": b"application/epub+zip"}),
        "dtd": revised_epub({b"<container ": b'<!DOCTYPE container [<!ENTITY attack SYSTEM "file:///etc/passwd">]><container '}),
        "drm": revised_epub(additions={"META-INF/encryption.xml": b'<encryption><EncryptionMethod Algorithm="unknown-drm"/></encryption>'}),
    }
    with TestClient(app) as client:
        assert upload(client, variants[kind]).status_code == 422
        assert client.get("/v1/reader/books", headers=HEADERS).json() == {"books": []}
    assert not list(app.state.reader.incoming.iterdir())
    assert not list(app.state.reader.files.iterdir())


def test_upload_limit_and_invalid_placement(app, monkeypatch):
    with TestClient(app) as client:
        book = upload(client).json()["book"]
        assert client.patch(f"/v1/reader/books/{book['id']}", headers={"X-OpenWorker-Token": TOKEN},
                            json={"location": "deleted", "revision": 0}).status_code == 422
        monkeypatch.setattr("coworker.server.reader.MAX_UPLOAD", 10)
        assert upload(client).status_code == 413
    assert not list(app.state.reader.incoming.iterdir())


def test_concurrent_duplicate_import_has_one_identity(tmp_path):
    store = ReaderStore(tmp_path / "reader")
    sources = []
    for index in range(2):
        path = store.incoming / f"{index}.epub"
        path.write_bytes((FIXTURES / "epub2.epub").read_bytes())
        sources.append(path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: store.import_file(p, p.name), sources))
    assert len({book["id"] for book, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    assert len(list(store.files.iterdir())) == 1


def test_missing_original_can_be_restored_without_losing_identity(app):
    with TestClient(app) as client:
        book = upload(client).json()["book"]
        app.state.reader.original(book["id"]).unlink()
        path = f"/v1/reader/books/{book['id']}/original"
        assert client.get(path, headers=HEADERS).status_code == 409
        assert upload(client).json()["book"]["id"] == book["id"]
        assert client.get(path, headers=HEADERS).status_code == 200

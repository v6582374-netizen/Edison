"""Literal note search and a portable, consistent snapshot of saved records."""
import json
import uuid
import pytest
from test_reader_reading import reader, TOKEN
from test_reader_notebook import update


def test_search_all_scopes_literal_pagination_and_deletion(reader):
    client, store, book = reader
    body = update(book)
    body["book_note"] = "中文 查找 [.*] 😀"
    body["highlights"][0]["anchor"].update(quote="中文😀", length=5)
    body["highlights"][0]["note"] = "中文 笔记 Needle"
    store.save_notebook(book["id"], body)
    store.place(book["id"], "archive", 0)
    result = client.get("/v1/reader/search", headers=TOKEN, params={"q":"中文", "limit":2}).json()
    assert result["total"] == 3 and len(result["results"]) == 2
    assert {item["location"] for item in result["results"]} == {"archive"}
    assert result["results"][0]["kind"] == "book_note"
    page = client.get("/v1/reader/search", headers=TOKEN, params={"q":"中文", "offset":2,"limit":2}).json()
    assert page["results"][0]["kind"] == "highlight_note"
    assert client.get("/v1/reader/search", headers=TOKEN, params={"q":"[.*]"}).json()["total"] == 1
    assert client.get("/v1/reader/search", headers=TOKEN, params={"q":"needle"}).json()["total"] == 0
    body.update(revision=1, operation_id=str(uuid.uuid4()), highlights=[])
    store.save_notebook(book["id"], body)
    assert client.get("/v1/reader/search", headers=TOKEN, params={"q":"中文"}).json()["total"] == 1


def test_json_roundtrip_preserves_full_data_and_markdown_is_literal(reader):
    client, store, book = reader
    body = update(book)
    body["book_note"] = "第一行\n\n<script>alert(1)</script> [链接](https://example.test)"
    body["highlights"][0]["note"] = "`code` **内容** 😀 e\u0301\n第二行"
    saved = store.save_notebook(book["id"], body)
    response = client.get(f"/v1/reader/books/{book['id']}/export", headers=TOKEN)
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]
    data = json.loads(response.content)
    assert data["format"] == "edison.reader" and data["version"] == 1
    assert data["book"]["sha256"] == book["sha256"]
    assert data["notebook"] == saved and data["reading"] == store.reading(book["id"])
    assert data["anchor_validation"] == "not_performed"
    markdown = client.get(f"/v1/reader/books/{book['id']}/export?format=markdown", headers=TOKEN).text
    assert markdown.count("### ") == len(saved["highlights"])
    assert saved["highlights"][0]["id"] in markdown and "中😀" in markdown
    assert "<script>" not in markdown and "&lt;script&gt;" in markdown
    assert "\\[链接\\]\\(" in markdown and "第二行" in markdown
    assert store.notebook(book["id"]) == saved  # export is a read, never a mutation


@pytest.mark.parametrize("query", [{"q":""},{"q":"   "},{"q":"x"*201},{"q":"x","offset":-1},{"q":"x","limit":101}])
def test_invalid_search_rejected(reader,query):
    client,_,_=reader
    assert client.get("/v1/reader/search",headers=TOKEN,params=query).status_code==422


def test_export_empty_unknown_and_auth_boundaries(reader):
    client,store,book=reader
    url=f"/v1/reader/books/{book['id']}/export"
    assert client.get(url).status_code==401
    assert client.get("/v1/reader/search?q=x").status_code==401
    assert client.get("/v1/reader/books/missing/export",headers=TOKEN).status_code==404
    assert client.get(url+"?format=html",headers=TOKEN).status_code==422
    assert client.get(url,headers=TOKEN).json()["notebook"]["highlights"]==[]
    assert store.notebook(book["id"])["revision"]==0

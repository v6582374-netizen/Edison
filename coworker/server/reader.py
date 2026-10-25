"""Reader routes inherit Edison's authentication; originals are attachments only."""

from __future__ import annotations

import tempfile
import json
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from ..reader import InvalidBook, MAX_UPLOAD, ReaderStore, StaleBook, notebook_markdown


class Placement(BaseModel):
    location: Literal["inbox", "later", "archive"]
    revision: int = Field(ge=0)


class ReadingPosition(BaseModel):
    kind: Literal["text", "img", "svg", "section"] = "text"
    cfi: str = Field(min_length=10, max_length=4096, pattern=r"^epubcfi\(.+\)$")
    section: str = Field(min_length=1, max_length=2048)
    index: int = Field(ge=0, strict=True)
    quote: str = Field(max_length=64)
    offset: int = Field(ge=0, strict=True)
    length: int = Field(ge=0, le=100_000_000, strict=True)

    @model_validator(mode="after")
    def valid_offsets(self):
        if (self.offset > self.length
                or (self.kind == "text" and (not self.quote or self.offset >= self.length))
                or (self.kind != "text" and self.quote)
                or (self.kind == "section" and (self.offset or self.length))):
            raise ValueError("Invalid text position")
        return self


class ReadingPreferences(BaseModel):
    font_size: Literal[16, 20, 24, 28, 32] = 20
    contents_open: bool = Field(default=True, strict=True)


class ReadingUpdate(BaseModel):
    content_version: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=0, strict=True)
    operation_id: str = Field(min_length=1, max_length=80)
    position: ReadingPosition | None
    furthest: ReadingPosition | None
    preferences: ReadingPreferences


class HighlightAnchor(ReadingPosition):
    kind: Literal["text"] = "text"
    quote: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def within_body(self):
        if self.offset + len(self.quote.encode("utf-16-le")) // 2 > self.length:
            raise ValueError("Highlight extends past the body")
        return self


class Highlight(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9-]{1,80}$")
    anchor: HighlightAnchor
    color: Literal["yellow", "green", "pink"] = "yellow"
    note: str = Field(default="", max_length=20000)


class NotebookUpdate(BaseModel):
    content_version: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=0, strict=True)
    operation_id: str = Field(min_length=1, max_length=80)
    book_note: str = Field(max_length=50000)
    highlights: list[Highlight] = Field(max_length=2000)

    @model_validator(mode="after")
    def unique_ranges(self):
        if len({h.id for h in self.highlights}) != len(self.highlights) or len({h.anchor.cfi for h in self.highlights}) != len(self.highlights):
            raise ValueError("Duplicate highlight identity or range")
        if len(self.model_dump_json().encode()) > 8_000_000:
            raise ValueError("Notebook exceeds 8 MB")
        return self


def attach_reader(app: FastAPI, manager):
    store = ReaderStore(manager._data_base / "reader")
    app.state.reader = store

    @app.get("/v1/reader/books")
    def listing():
        return JSONResponse({"books": store.listing()}, headers={"Cache-Control": "private, no-store"})

    @app.post("/v1/reader/books")
    async def upload(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] not in {"application/epub+zip", "application/octet-stream"}:
            raise HTTPException(415, "请选择 EPUB 文件")
        filename = unquote(request.headers.get("x-reader-filename", "book.epub"))
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=store.incoming, suffix=".epub", delete=False) as incoming:
                path = Path(incoming.name)
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "EPUB 文件不能超过 64 MB")
                    incoming.write(chunk)
            book, created = await run_in_threadpool(store.import_file, path, filename)
            return JSONResponse({"book": book, "created": created}, status_code=201 if created else 200,
                                headers={"Cache-Control": "private, no-store"})
        except InvalidBook as error:
            raise HTTPException(422, str(error)) from error
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    @app.get("/v1/reader/books/{book_id}")
    def book(book_id: str):
        try:
            return JSONResponse(store.book(book_id), headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None

    @app.patch("/v1/reader/books/{book_id}")
    def place(book_id: str, body: Placement):
        try:
            return store.place(book_id, body.location, body.revision)
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None
        except StaleBook as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/v1/reader/books/{book_id}/original")
    def original(book_id: str):
        try:
            record = store.book(book_id)
            path = store.original(book_id)
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None
        if not path.is_file():
            raise HTTPException(409, "原书文件缺失，请检查服务器存储")
        return FileResponse(path, media_type="application/epub+zip", filename=f"{record['id']}.epub",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; sandbox"})

    @app.get("/v1/reader/books/{book_id}/reading")
    def reading(book_id: str):
        try:
            return JSONResponse(store.reading(book_id), headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None

    @app.put("/v1/reader/books/{book_id}/reading")
    def save_reading(book_id: str, body: ReadingUpdate):
        try:
            return JSONResponse(store.save_reading(book_id, body.model_dump()),
                                headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None
        except StaleBook as error:
            raise HTTPException(409, str(error)) from error
        except InvalidBook as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/v1/reader/books/{book_id}/notebook")
    def notebook(book_id: str):
        try:
            return JSONResponse(store.notebook(book_id), headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None

    @app.put("/v1/reader/books/{book_id}/notebook")
    def save_notebook(book_id: str, body: NotebookUpdate):
        try:
            return JSONResponse(store.save_notebook(book_id, body.model_dump()), headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None
        except StaleBook as error:
            raise HTTPException(409, str(error)) from error
        except InvalidBook as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/v1/reader/search")
    def search(q: str = Query(min_length=1, max_length=200), offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
        if not q.strip():
            raise HTTPException(422, "请输入查找文字")
        return JSONResponse(store.search_notes(q, offset, limit), headers={"Cache-Control": "private, no-store"})

    @app.get("/v1/reader/books/{book_id}/export")
    def export(book_id: str, format: Literal["json", "markdown"] = "json"):
        try:
            snapshot = store.export_book(book_id)
        except KeyError:
            raise HTTPException(404, "书籍不存在") from None
        content = json.dumps(snapshot, ensure_ascii=False, indent=2) if format == "json" else notebook_markdown(snapshot)
        extension = "json" if format == "json" else "md"
        return Response(content, media_type="application/json" if format == "json" else "text/markdown",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Disposition": f'attachment; filename="reader-{book_id}.{extension}"'})

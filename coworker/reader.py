"""Reader owns immutable EPUB originals and their library placement."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree as XML
from defusedxml.common import DefusedXmlException

MAX_UPLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
CONTENT_RULE = "reader-safe-epub-v1"
OPF = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"


class InvalidBook(ValueError):
    pass


class StaleBook(ValueError):
    pass


def _resource(base: str, href: str) -> str:
    parsed = urlsplit(href)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or path.startswith("/") or "\\" in path or "\0" in path:
        raise InvalidBook("EPUB 包含无效的资源路径")
    parts = list(PurePosixPath(base).parent.parts)
    for part in path.split("/"):
        if part == "..":
            if not parts:
                raise InvalidBook("EPUB 资源路径超出文件范围")
            parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def inspect_epub(path: Path) -> dict:
    """Validate without extracting files or fetching any referenced URL."""
    if not 0 < path.stat().st_size <= MAX_UPLOAD:
        raise InvalidBook("EPUB 文件不能超过 64 MB")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 10000:
                raise InvalidBook("EPUB 资源数量过多")
            names: set[str] = set()
            total = 0
            for entry in entries:
                name = entry.filename
                if name in names or name.startswith("/") or "\\" in name or "\0" in name or ".." in name.split("/"):
                    raise InvalidBook("EPUB 包含不安全或重复的资源路径")
                names.add(name)
                total += entry.file_size
                if entry.flag_bits & 1 or entry.file_size > MAX_UPLOAD or total > MAX_EXPANDED:
                    raise InvalidBook("EPUB 加密或解压体积超过限制")
                # Check actual decompression and CRC; nothing is written outside the archive.
                with archive.open(entry) as source:
                    size = 0
                    while chunk := source.read(65536):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise InvalidBook("EPUB 资源解压体积超过限制")
            if archive.read("mimetype").strip() != b"application/epub+zip":
                raise InvalidBook("请选择有效的 EPUB 文件")

            def xml(name: str):
                if name not in names or archive.getinfo(name).file_size > 4 * 1024 * 1024:
                    raise InvalidBook("EPUB 缺少必要目录或目录过大")
                return XML.fromstring(archive.read(name), forbid_dtd=True)

            container = xml("META-INF/container.xml")
            roots = container.findall(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
            if not roots:
                raise InvalidBook("EPUB 缺少书目")
            package_path = _resource("root", roots[0].get("full-path", ""))
            package = xml(package_path)
            if package.get("version") not in {"2.0", "3.0"}:
                raise InvalidBook("暂不支持该 EPUB 版本")
            metadata = package.find(f"{OPF}metadata")
            manifest = package.find(f"{OPF}manifest")
            spine = package.find(f"{OPF}spine")
            if metadata is None or manifest is None or spine is None or not len(spine):
                raise InvalidBook("EPUB 缺少书目或章节")
            if any((m.get("property") == "rendition:layout" and (m.text or "").strip() == "pre-paginated")
                   or (m.get("name") == "fixed-layout" and m.get("content") == "true") for m in metadata):
                raise InvalidBook("暂不支持固定版式 EPUB；请使用可重排版本")
            for options_path in ("META-INF/com.apple.ibooks.display-options.xml", "META-INF/com.kobobooks.display-options.xml"):
                if options_path in names and any(o.get("name") == "fixed-layout" and (o.text or "").strip() == "true" for o in xml(options_path).iter()):
                    raise InvalidBook("暂不支持固定版式 EPUB；请使用可重排版本")
            if "META-INF/encryption.xml" in names:
                allowed = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
                if any(e.get("Algorithm") not in allowed for e in xml("META-INF/encryption.xml").iter() if e.tag.endswith("EncryptionMethod")):
                    raise InvalidBook("暂不支持 DRM 加密 EPUB")
            items = {item.get("id"): item for item in manifest}
            if len(items) != len(manifest) or None in items:
                raise InvalidBook("EPUB 资源标识重复或缺失")
            for ref in spine:
                item = items.get(ref.get("idref"))
                if item is None or _resource(package_path, item.get("href", "")) not in names:
                    raise InvalidBook("EPUB 缺少章节资源")
                if "rendition:layout-pre-paginated" in ref.get("properties", "").split():
                    raise InvalidBook("暂不支持固定版式 EPUB；请使用可重排版本")
                if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                    raise InvalidBook("暂不支持以图片或 SVG 作为独立章节的 EPUB")

            def text(tag: str) -> str:
                return " · ".join("".join(node.itertext()).strip() for node in metadata.findall(DC + tag))[:1000]

            return {"title": text("title") or "未命名书籍", "author": text("creator"),
                    "language": text("language"), "sections": len(spine)}
    except (zipfile.BadZipFile, KeyError, XML.ParseError, DefusedXmlException, RuntimeError, NotImplementedError) as error:
        raise InvalidBook("EPUB 文件损坏或包含不支持的内容") from error


class ReaderStore:
    def __init__(self, root: Path):
        self.root = root
        self.files = root / "originals"
        self.incoming = root / "incoming"
        self.files.mkdir(parents=True, exist_ok=True)
        self.incoming.mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
                title TEXT NOT NULL, author TEXT NOT NULL, language TEXT NOT NULL,
                sections INTEGER NOT NULL, size INTEGER NOT NULL,
                location TEXT NOT NULL DEFAULT 'inbox' CHECK(location IN ('inbox','later','archive')),
                revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                content_rule TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS reading_records (
                book_id TEXT PRIMARY KEY REFERENCES books(id), revision INTEGER NOT NULL,
                position TEXT, furthest TEXT, preferences TEXT NOT NULL, updated_at REAL NOT NULL,
                operation_id TEXT NOT NULL, operation_hash TEXT NOT NULL, content_version TEXT NOT NULL)""")

            db.execute("""CREATE TABLE IF NOT EXISTS notebooks (
                book_id TEXT PRIMARY KEY REFERENCES books(id), revision INTEGER NOT NULL,
                content_version TEXT NOT NULL, data TEXT NOT NULL, updated_at REAL NOT NULL,
                operation_id TEXT NOT NULL, operation_hash TEXT NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "reader.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _book(row) -> dict:
        result = dict(row)
        result["content_version"] = result["sha256"] + ":" + result["content_rule"]
        return result

    def listing(self) -> list[dict]:
        with self.connect() as db:
            return [self._book(row) for row in db.execute("SELECT * FROM books ORDER BY created_at DESC, id")]

    def book(self, book_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if row is None:
            raise KeyError(book_id)
        return self._book(row)

    def import_file(self, path: Path, filename: str) -> tuple[dict, bool]:
        metadata = inspect_epub(path)
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM books WHERE sha256=?", (sha,)).fetchone()
            if existing:
                if not (self.files / f"{sha}.epub").is_file():
                    os.replace(path, self.files / f"{sha}.epub")
                return self._book(existing), False
            book_id = uuid.uuid4().hex
            size = path.stat().st_size
            os.replace(path, self.files / f"{sha}.epub")
            db.execute("""INSERT INTO books
                (id,sha256,filename,title,author,language,sections,size,created_at,content_rule)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (book_id, sha, filename[:255], metadata["title"],
                metadata["author"], metadata["language"], metadata["sections"], size, time.time(), CONTENT_RULE))
        return self.book(book_id), True

    def place(self, book_id: str, location: str, revision: int) -> dict:
        with self.connect() as db:
            changed = db.execute("UPDATE books SET location=?,revision=revision+1 WHERE id=? AND revision=?",
                                 (location, book_id, revision)).rowcount
            if not changed:
                self.book(book_id)
                raise StaleBook("书籍已在另一页面更新，请刷新后重试")
        return self.book(book_id)

    def original(self, book_id: str) -> Path:
        return self.files / f"{self.book(book_id)['sha256']}.epub"

    @staticmethod
    def _reading(book: dict, row) -> dict:
        position = json.loads(row["position"]) if row and row["position"] else None
        furthest = json.loads(row["furthest"]) if row and row["furthest"] else None
        progress = ((furthest["index"] + furthest["offset"] / max(1, furthest["length"]))
                    / book["sections"]) if furthest else 0
        return {"content_version": row["content_version"] if row else book["content_version"], "revision": row["revision"] if row else 0,
                "position": position, "furthest": furthest, "progress": progress,
                "preferences": json.loads(row["preferences"]) if row else {"font_size": 20, "contents_open": True},
                "updated_at": row["updated_at"] if row else None}

    def reading(self, book_id: str) -> dict:
        book = self.book(book_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM reading_records WHERE book_id=?", (book_id,)).fetchone()
            return self._reading(book, row)

    def save_reading(self, book_id: str, update: dict) -> dict:
        book = self.book(book_id)
        if update["content_version"] != book["content_version"]:
            raise StaleBook("书籍内容版本已改变，未保存旧位置")
        position = update["position"]
        for candidate in (position, update["furthest"]):
            if candidate and candidate["index"] >= book["sections"]:
                raise InvalidBook("阅读位置超出书籍章节范围")
        encoded = json.dumps(update, sort_keys=True, ensure_ascii=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reading_records WHERE book_id=?", (book_id,)).fetchone()
            if row and row["content_version"] != book["content_version"]:
                raise StaleBook("旧阅读记录属于其他内容版本，需要明确迁移")
            # A response can be lost after commit. Retrying that exact operation is safe.
            if row and row["operation_id"] == update["operation_id"] and row["operation_hash"] == digest:
                return self._reading(book, row)
            revision = row["revision"] if row else 0
            if update["revision"] != revision or (row and row["operation_id"] == update["operation_id"]):
                raise StaleBook("阅读记录已在另一页面更新，当前记录未覆盖服务器")
            furthest = json.loads(row["furthest"]) if row and row["furthest"] else None
            for candidate in (position, update["furthest"]):
                if candidate and (not furthest or (candidate["index"], candidate["offset"]) > (furthest["index"], furthest["offset"])):
                    furthest = candidate
            db.execute("""INSERT INTO reading_records VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(book_id) DO UPDATE SET revision=excluded.revision,
                position=excluded.position,furthest=excluded.furthest,preferences=excluded.preferences,
                updated_at=excluded.updated_at,operation_id=excluded.operation_id,operation_hash=excluded.operation_hash""",
                (book_id, revision + 1, json.dumps(position), json.dumps(furthest),
                 json.dumps(update["preferences"]), time.time(), update["operation_id"], digest, book["content_version"]))
            return self._reading(book, db.execute("SELECT * FROM reading_records WHERE book_id=?", (book_id,)).fetchone())

    @staticmethod
    def _notebook(book, row):
        return {"content_version": row["content_version"] if row else book["content_version"],
                "revision": row["revision"] if row else 0,
                "updated_at": row["updated_at"] if row else None,
                **(json.loads(row["data"]) if row else {"book_note": "", "highlights": []})}

    def notebook(self, book_id):
        book = self.book(book_id)
        with self.connect() as db:
            return self._notebook(book, db.execute("SELECT * FROM notebooks WHERE book_id=?", (book_id,)).fetchone())

    def save_notebook(self, book_id, update):
        book = self.book(book_id)
        if update["content_version"] != book["content_version"]:
            raise StaleBook("书籍内容版本已改变，未覆盖笔记")
        if any(item["anchor"]["index"] >= book["sections"] for item in update["highlights"]):
            raise InvalidBook("高亮超出章节范围")
        digest = hashlib.sha256(json.dumps(update, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        data = json.dumps({key: update[key] for key in ("book_note", "highlights")}, ensure_ascii=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM notebooks WHERE book_id=?", (book_id,)).fetchone()
            if row and row["content_version"] != book["content_version"]:
                raise StaleBook("旧笔记属于其他内容版本，需要明确迁移")
            if row and row["operation_id"] == update["operation_id"] and row["operation_hash"] == digest:
                return self._notebook(book, row)
            if update["revision"] != (row["revision"] if row else 0) or (row and row["operation_id"] == update["operation_id"]):
                raise StaleBook("笔记已在另一页面更新，当前编辑未覆盖服务器")
            db.execute("""INSERT INTO notebooks VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(book_id) DO UPDATE SET revision=excluded.revision,
                data=excluded.data, updated_at=excluded.updated_at,
                operation_id=excluded.operation_id, operation_hash=excluded.operation_hash""",
                (book_id, update["revision"] + 1, book["content_version"], data,
                 time.time(), update["operation_id"], digest))
            return self._notebook(book, db.execute("SELECT * FROM notebooks WHERE book_id=?", (book_id,)).fetchone())

    def search_notes(self, query: str, offset: int = 0, limit: int = 50):
        results = []
        def excerpt(text):
            index = text.find(query)
            start, end = max(0, index - 60), min(len(text), index + len(query) + 90)
            return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
        with self.connect() as db:
            rows = db.execute("""SELECT b.id, b.title, b.author, b.location, n.content_version, n.data
                FROM books b JOIN notebooks n ON n.book_id=b.id ORDER BY b.created_at DESC, b.id""")
            for row in rows:
                data = json.loads(row["data"])
                base = {key: row[key] for key in ("title", "author", "location", "content_version")}
                base["book_id"] = row["id"]
                candidates = [("book_note", None, data["book_note"])]
                for item in sorted(data["highlights"], key=lambda h: (h["anchor"]["index"], h["anchor"]["offset"], h["id"])):
                    candidates.extend([("highlight", item["id"], item["anchor"]["quote"]), ("highlight_note", item["id"], item["note"])])
                for kind, identity, text in candidates:
                    if query in text:
                        results.append({**base, "kind": kind, "highlight_id": identity, "excerpt": excerpt(text)})
        return {"query": query, "total": len(results), "offset": offset, "results": results[offset:offset + limit]}

    def export_book(self, book_id):
        # One read transaction: metadata and both independent revisions describe
        # the same committed database snapshot, even while another tab saves.
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            if row is None:
                raise KeyError(book_id)
            book = self._book(row)
            reading = self._reading(book, db.execute("SELECT * FROM reading_records WHERE book_id=?", (book_id,)).fetchone())
            notebook = self._notebook(book, db.execute("SELECT * FROM notebooks WHERE book_id=?", (book_id,)).fetchone())
            return {"format": "edison.reader", "version": 1, "exported_at": time.time(),
                    "anchor_validation": "not_performed", "book": book, "reading": reading, "notebook": notebook}


def notebook_markdown(snapshot):
    # Export literal user text: never turn an EPUB title or a note into active HTML.
    def literal(text):
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for char in "\\`*_{}[]()#+-.!|":
            text = text.replace(char, "\\" + char)
        return text
    def paragraph(text):
        return "\n".join(literal(line) + "  " for line in text.split("\n"))
    book, notebook = snapshot["book"], snapshot["notebook"]
    lines = ["# " + literal(book["title"]), "", "作者：" + literal(book["author"]), "",
             "内容版本：`" + book["content_version"] + "`", "",
             f"Notebook 修订：{notebook['revision']}；高亮：{len(notebook['highlights'])} 条。", "",
             "此文件包含服务器已保存的摘录与笔记。原文锚点未在导出时重新核验；JSON 保留完整定位信息，原 EPUB 需单独保存。", "",
             "## 整本笔记", "", paragraph(notebook["book_note"]) if notebook["book_note"] else "（无）", "", "## 高亮与笔记"]
    for i, item in enumerate(sorted(notebook["highlights"], key=lambda h: (h["anchor"]["index"], h["anchor"]["offset"], h["id"])), 1):
        lines.extend(["", f"### {i}. 第 {item['anchor']['index'] + 1} 章 · {item['color']}", "",
                      "标识：`" + item["id"] + "`", "",
                      "\n".join("> " + literal(line) + "  " for line in item["anchor"]["quote"].split("\n")), "",
                      "笔记：", "", paragraph(item["note"]) if item["note"] else "（无）"])
    return "\n".join(lines) + "\n"

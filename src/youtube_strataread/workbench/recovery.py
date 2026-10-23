"""Consistent YouTube library backup, verified restore and program rollback gate.

A backup holds the workspace database and the knowledge files under
``assets/`` only. Google authorization, the content-session profile and its
snapshots are managed separately and are never copied, so no unmanaged
credential copy is created. A backup is taken only inside a durable cutover
hold after every writer has been proven stopped, and is void if any admitted
work starts while it is being taken.

Restoring never writes into an existing non-empty directory: disaster recovery
does not silently overwrite the latest library. Ordinary program rollback does
not restore at all; it runs an earlier program on the latest library, admitted
only when that program declares a data contract covering the library's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

from youtube_strataread.workbench.cutover import CutoverBlocked
from youtube_strataread.workbench.workspace import LocalWorkspace

_RECORD = "youtube_backup"
_DATABASE = "workspace.sqlite3"
_MANIFEST = "manifest.json"
_LIBRARY = "<library>/"
_PATH_COLUMNS = (("transcripts", "path"), ("manuscripts", "path"), ("candidates", "manuscript_path"))
_CONTRACT = re.compile(r"^YOUTUBE_DATA_CONTRACT = (\d+)$", re.M)
STATIC_WEB_NOTE = "静态 Web 资源回退（promote_web_release）只切换前端文件，不包含后端程序、资料或凭据回退。"


class _Changed(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files(root: Path) -> tuple[list[dict], list[str]]:
    """Knowledge files under assets/, and anything there that is not a plain file."""
    files, skipped = [], []
    assets = root / "assets"
    if assets.is_symlink():
        raise ValueError("资料文件根目录不能是符号链接；请先将知识资料放入资料库，再取得备份。")
    if assets.is_dir():
        for path in sorted(assets.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped.append(relative)  # Never follow a link out of the library.
            elif path.is_file():
                files.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
    return files, skipped


def _prefixes(root: Path) -> tuple[str, ...]:
    """Stored paths use the root as configured, which may differ from its resolved form."""
    return tuple(dict.fromkeys(str(path).rstrip(os.sep) + os.sep for path in (root.absolute(), root.resolve())))


def _relative(value: str, prefixes: tuple[str, ...]) -> str | None:
    return next((value[len(prefix):] for prefix in prefixes if value.startswith(prefix)), None)


def library_facts(database: Path, root: Path) -> dict:
    """Every stored row with library-relative paths, plus a readable summary."""
    prefixes = _prefixes(root)

    def normal(value):
        relative = _relative(value, prefixes) if isinstance(value, str) else None
        return value if relative is None else _LIBRARY + relative

    with closing(sqlite3.connect(database)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {}
        for table in tables:
            query = f'SELECT * FROM "{table}"' + (" WHERE key != ?" if table == "workspace_meta" else "")
            values = connection.execute(query, (_RECORD,) if table == "workspace_meta" else ()).fetchall()
            rows[table] = sorted(([normal(value) for value in row] for row in values),
                                 key=lambda item: json.dumps(item, default=repr))
        meta = dict(connection.execute("SELECT key, value FROM workspace_meta").fetchall())

        def count(sql):
            return int(connection.execute(sql).fetchone()[0])

        def grouped(column):
            return dict(connection.execute(f"SELECT {column}, COUNT(*) FROM candidates GROUP BY {column} ORDER BY {column}"))

        summary = {
            "assets": count("SELECT COUNT(*) FROM candidates"),
            "documents": count("SELECT COUNT(*) FROM candidates WHERE manuscript_version IS NOT NULL"),
            "manuscript_versions": count("SELECT COUNT(*) FROM manuscripts"),
            "transcripts": count("SELECT COUNT(*) FROM transcripts"),
            "reading": grouped("reading_state"),
            "preparation": grouped("preparation_state"),
            "commenced": count("SELECT COUNT(*) FROM candidates WHERE commenced_at IS NOT NULL"),
            "manual_requests": count("SELECT COUNT(*) FROM manual_video_requests"),
            "auto_update": meta.get("drain_paused", "1") != "1",
            "disconnection": meta.get("youtube_disconnect_local") or "",
            "data_contract": int(meta.get("youtube_data_contract") or 0),
        }
    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=repr).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "summary": summary}


def _references(database: Path, root: Path) -> tuple[list[str], list[str]]:
    """Stored file paths inside and outside the library root."""
    prefixes = _prefixes(root)
    inside, outside = set(), set()
    with closing(sqlite3.connect(database)) as connection:
        for table, column in _PATH_COLUMNS:
            for (value,) in connection.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"):
                relative = _relative(value, prefixes)
                (outside.add(value) if relative is None else inside.add(relative))
    return sorted(inside), sorted(outside)


def _snapshot_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(target)) as copy:
        live.backup(copy)  # One consistent read of the whole database.
        copy.execute("PRAGMA journal_mode=DELETE")  # A self-contained file without -wal/-shm.
    target.chmod(0o600)


def _copy_assets(source: Path, target: Path, files: list[dict]) -> None:
    for item in files:
        destination = target / item["path"]
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source / item["path"], destination)
        destination.chmod(0o600)


def _empty_target(target: Path) -> None:
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError("恢复目标已存在且非空；灾难恢复不会覆盖现有资料库，请指定空目录。")


def restore_files(backup: Path, target: Path) -> dict:
    """Copy a backup into an empty directory, mapping stored paths to the new root."""
    manifest = json.loads((backup / _MANIFEST).read_text(encoding="utf-8"))
    _empty_target(target)
    if _sha256(backup / _DATABASE) != manifest["database_sha256"]:
        raise ValueError("备份数据库校验失败；请保留现有资料库并选用已验证的备份。")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(backup / _DATABASE, target / _DATABASE)
    (target / _DATABASE).chmod(0o600)
    _copy_assets(backup, target, manifest["files"])
    destination = str(target.resolve()) + os.sep
    with closing(sqlite3.connect(target / _DATABASE)) as connection, connection:
        for source in manifest["library_roots"]:
            if source == destination:
                continue
            for table, column in _PATH_COLUMNS:
                connection.execute(
                    f"UPDATE {table} SET {column} = ? || substr({column}, ?) WHERE substr({column}, 1, ?) = ?",
                    (destination, len(source) + 1, len(source), source))
    return manifest


def check_restored(manifest: dict, root: Path) -> dict:
    """Compare a restored library with the facts recorded when it was backed up."""
    database = root / _DATABASE
    with closing(sqlite3.connect(database)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        documents = connection.execute(
            "SELECT video_id, manuscript_version FROM candidates WHERE manuscript_version IS NOT NULL").fetchall()
        transcripts = [row[0] for row in connection.execute("SELECT video_id FROM transcripts")]
    facts = library_facts(database, root)
    files, _ = _files(root)
    # Read through the same library interfaces the reading service uses.
    library = LocalWorkspace(root)
    readable = sum(1 for video_id, version in documents
                   if library.document(video_id)["version"] == version and library.document(video_id)["markdown"])
    usable = sum(1 for video_id in transcripts if library.retained_transcript(video_id) is not None)
    translations = sum(1 for item in files if item["path"].endswith("/translation.md"))
    checks = {
        "integrity": integrity == "ok",
        "facts_match": facts["sha256"] == manifest["facts"]["sha256"],
        "files_match": files == manifest["files"],
        "documents_readable": readable == len(documents),
        "transcripts_usable": usable == len(transcripts),
        "references_inside_library": not manifest["outside_library"] and not manifest["missing_files"],
        # Only the database and knowledge files: no profile, snapshot or credential file.
        "only_knowledge_files": {entry.name for entry in root.iterdir()} <= {
            _DATABASE, f"{_DATABASE}-wal", f"{_DATABASE}-shm", f"{_DATABASE}-journal", "assets"},
    }
    return {"ok": all(checks.values()), "checks": checks, "summary": facts["summary"],
            "documents_readable": readable, "transcripts_usable": usable, "translations": translations}


def verify(backup: Path) -> dict:
    """Restore into a throwaway directory beside the backup and compare facts."""
    with tempfile.TemporaryDirectory(prefix=".verify-", dir=backup.parent) as scratch:
        target = Path(scratch) / "library"
        manifest = restore_files(backup, target)
        return check_restored(manifest, target)


class LibraryBackup:
    """Maintainer action inside the cutover hold; records results without credentials."""

    def __init__(self, workspace, hold, destination: Path, *, separately_managed: list[Path]) -> None:
        self.workspace = workspace
        self.hold = hold
        self.destination = destination
        self.separately_managed = separately_managed

    def status(self) -> dict | None:
        raw = self.workspace.meta(_RECORD)
        return json.loads(raw) if raw else None

    def run(self) -> dict:
        generation = self.hold.quiescent()
        if generation is None:
            raise CutoverBlocked("一致性备份只能在切换暂停内、所有写者确认结束后取得。")
        root = self.workspace.root
        destination = self.destination.resolve()
        for path in (destination, *[item.resolve() for item in self.separately_managed]):
            if path == root.resolve() or path.is_relative_to(root.resolve()):
                raise ValueError("备份目录、凭据与内容会话必须位于资料库之外，避免产生未受管理的凭据副本。")
        if any(destination.is_relative_to(item.resolve()) for item in self.separately_managed):
            raise ValueError("备份目录不能放在凭据或内容会话目录中。")
        created = self.workspace.now()
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%SZ-", time.gmtime(created)), dir=destination))
        try:
            _snapshot_database(self.workspace.database_path, target / _DATABASE)
            files, skipped = _files(root)
            _copy_assets(root, target, files)
            facts = library_facts(target / _DATABASE, root)
            copied, _ = _files(target)
            # Writer coordination is proven by the hold; this proves the copy
            # equals the live library and nothing was admitted meanwhile.
            if (facts != library_facts(self.workspace.database_path, root) or copied != files
                    or _files(root)[0] != files or self.hold.quiescent() != generation):
                raise _Changed
            inside, outside = _references(target / _DATABASE, root)
            present = {item["path"] for item in files}
            not_copied = sorted({*skipped, *(entry.name for entry in root.iterdir()
                                              if entry.name not in {_DATABASE, f"{_DATABASE}-wal", f"{_DATABASE}-shm", "assets"})})
            manifest = {
                "format": 1, "created_at": created, "library_roots": list(_prefixes(root)),
                "data_contract": facts["summary"]["data_contract"],
                "cutover": {key: value for key, value in self.hold.status().items()
                            if key in {"state", "started_at", "drained", "active_requests"}},
                "facts": facts, "files": files, "database_sha256": _sha256(target / _DATABASE),
                "outside_library": outside, "missing_files": sorted(set(inside) - present),
                "not_copied": not_copied,
                "separately_managed": ["Google 官方授权凭据", "YouTube 内容会话 profile 与快照"],
            }
            (target / _MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            verification = {**verify(target), "verified_at": self.workspace.now()}
            if self.hold.quiescent() != generation:
                raise _Changed
            manifest["restore_verification"] = verification
            (target / _MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (target / _MANIFEST).chmod(0o600)
        except _Changed:
            shutil.rmtree(target, ignore_errors=True)
            raise CutoverBlocked("备份期间出现新的写入，本次备份已作废；收尾完成后请重新取得。") from None
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        record = {"path": str(target), "created_at": created, "verified": verification["ok"],
                  "checks": verification["checks"], "summary": facts["summary"],
                  "files": len(files), "translations": verification["translations"],
                  "outside_library": len(outside), "missing_files": len(manifest["missing_files"]),
                  "separately_managed": manifest["separately_managed"]}
        self.workspace.set_meta(_RECORD, json.dumps(record, ensure_ascii=False))
        return record


def restore(backup: Path, target: Path) -> dict:
    """Disaster restore into an empty directory; acquisition stays held afterwards."""
    manifest = restore_files(backup, target)
    result = check_restored(manifest, target)
    if not result["ok"]:
        # The target was empty before; never leave an unverified library to be served.
        for entry in target.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        return {**result, "restored": False}
    with closing(sqlite3.connect(target / _DATABASE)) as connection, connection:
        row = connection.execute("SELECT value FROM workspace_meta WHERE key='youtube_cutover'").fetchone()
        state = json.loads(row[0]) if row else {}
        state.update(held=True, state="aborted", drained=False, restored_from=manifest["created_at"],
                     reason="已从一致性备份恢复；备份之后的资料与连接变化不在其中，采集保持暂停。")
        record = {"path": str(backup), "created_at": manifest["created_at"], "verified": True,
                  "checks": result["checks"], "summary": result["summary"], "files": len(manifest["files"]),
                  "translations": result["translations"], "outside_library": 0, "missing_files": 0,
                  "separately_managed": manifest["separately_managed"], "restored": True}
        for key, value in (("youtube_cutover", state), (_RECORD, record)):
            connection.execute("INSERT INTO workspace_meta(key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))
    return {**result, "restored": True}


def check_program(program: Path, library: Path) -> dict:
    """Whether an earlier backend program may run on the latest library."""
    source = program / "src" / "youtube_strataread" / "workbench" / "workspace.py"
    match = _CONTRACT.search(source.read_text(encoding="utf-8")) if source.is_file() else None
    with closing(sqlite3.connect(f"file:{library / _DATABASE}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT value FROM workspace_meta WHERE key='youtube_data_contract'").fetchone()
    current = int(row[0]) if row and str(row[0]).isdigit() else 0
    declared = int(match.group(1)) if match else None
    if declared is None:
        reason = "目标程序没有声明数据契约，无法证明兼容当前资料；拒绝回退。"
    elif declared < current:
        reason = f"目标程序只支持数据契约 {declared}，资料库为 {current}；拒绝回退。"
    else:
        reason = f"目标程序支持数据契约 {declared}，覆盖资料库的 {current}；可在最新资料上运行。"
    compatible = declared is not None and declared >= current
    return {"compatible": compatible, "program_contract": declared, "library_contract": current, "reason": reason,
            "if_refused": None if compatible else "保留当前能读取最新资料的服务，采集保持切换暂停；不要整库回卷或恢复旧凭据。",
            "note": STATIC_WEB_NOTE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m youtube_strataread.workbench.recovery", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, arguments in {"verify": ("backup",), "restore": ("backup", "target"),
                            "check-program": ("program", "library")}.items():
        command = commands.add_parser(name)
        for argument in arguments:
            command.add_argument(argument, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify(args.backup.resolve())
            passed = result["ok"]
        elif args.command == "restore":
            result = restore(args.backup.resolve(), args.target.resolve())
            passed = result["restored"]
        else:
            result = check_program(args.program.resolve(), args.library.resolve())
            passed = result["compatible"]
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

"""Durable attempt and publication boundaries, used only by isolated experiments."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from youtube_strataread.workbench.translation import TranslationError, TranslationPipeline
from youtube_strataread.workbench.workspace import LocalWorkspace


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def durable_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def experiment_lock(root):
    # Outside the experiment tree: a rejected identity must not mutate its files.
    key = hashlib.sha256(str(root).encode()).hexdigest()
    lock = Path(tempfile.gettempdir()) / ("edison-experiment-" + key + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def attempt_path(root, attempt_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", attempt_id):
        raise ValueError("Invalid experiment attempt ID")
    return root / "attempts" / attempt_id


def verify_attempt(root, attempt_id, mode, identity):
    if mode not in {"new", "resume"}:
        raise ValueError("Experiment mode must be new or resume")
    directory = attempt_path(root, attempt_id)
    path = directory / "identity.json"
    if path.exists():
        if mode != "resume":
            raise ValueError("Attempt already exists; explicitly resume or select a new attempt ID")
        if json.loads(path.read_text()) != json.loads(encoded(identity)):
            raise ValueError(
                "Attempt identity mismatch; source, prompts, model or configuration changed"
            )
    elif mode == "resume":
        raise ValueError("Cannot resume an unknown attempt")
    return directory


class StrictBaselinePipeline(TranslationPipeline):
    """Finish metadata is mandatory in experiments; production defaults stay intact."""

    def _request(self, key, messages, *, validator=None, **options):
        original = self.complete
        finish = None

        def observe(*args, **kwargs):
            nonlocal finish
            turn = original(*args, **kwargs)
            finish = getattr(turn, "finish_reason", None)
            return turn

        def validate(text):
            if finish is None:
                raise TranslationError("模型未提供结束原因，无法确认输出完整，未发布稿件。")
            if validator:
                validator(text)

        self.complete = observe
        try:
            return super()._request(key, messages, validator=validate, **options)
        finally:
            self.complete = original


class ExperimentWorkspace(LocalWorkspace):
    """Private experiment publisher with a unique durable key per generation attempt."""

    def bind_attempt(self, attempt_id, directory, fault=None):
        self.attempt_id, self.attempt_directory, self.fault = attempt_id, directory, fault
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS experiment_publications (
                attempt_id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
                version INTEGER NOT NULL, path TEXT NOT NULL, created_at REAL NOT NULL,
                bundle_sha256 TEXT NOT NULL)""")

    def translation_checkpoint(self, video_id):
        return self.attempt_directory / "checkpoint.json"

    def mirror_checkpoint(self, video_id):
        # Keep the established offline inspection/continuation seam readable.
        # Explicit experiment resumes always use the attempt-owned canonical file.
        source = self.translation_checkpoint(video_id)
        mirror = super().translation_checkpoint(video_id)
        if source.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            durable_json(mirror, json.loads(source.read_text()))
        else:
            mirror.unlink(missing_ok=True)

    def publication(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM experiment_publications WHERE attempt_id=?", (self.attempt_id,)
            ).fetchone()
        return dict(row) if row else None

    def recover_publication(self, video_id):
        publication = self.publication()
        if not publication:
            return False
        # Cancellation is an explicit person decision, never undone by recovery.
        if self.asset(video_id)["preparation_state"] == "cancelled":
            return False
        self.translation_checkpoint(video_id).unlink(missing_ok=True)
        self.set_preparation_state(video_id, "ready")
        return True

    def save_manuscript(
        self,
        video_id,
        markdown,
        *,
        generator,
        transcript_characters,
        translation=None,
        provenance=None,
    ):
        content = {"manuscript.md": markdown.rstrip() + "\n"}
        if translation is not None:
            content["translation.md"] = translation.rstrip() + "\n"
        if provenance is not None:
            content["translation-run.json"] = encoded(provenance)
        digest = hashlib.sha256(encoded(content).encode()).hexdigest()
        directory = self.attempt_directory / "publication"
        # This transaction also serializes concurrent publishers allocating versions.
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT version,path,created_at,bundle_sha256 FROM experiment_publications WHERE attempt_id=?",
                (self.attempt_id,),
            ).fetchone()
            if existing:
                if existing[3] != digest:
                    raise ValueError("Attempt already published different content")
                return {"version": existing[0], "path": existing[1], "created_at": existing[2]}
            # An uncommitted bundle belongs only to this attempt. Never touch history.
            if directory.exists() and any(
                (directory / name).read_text() != text for name, text in content.items()
            ):
                shutil.rmtree(directory)
            if not directory.exists():
                staging = self.attempt_directory / ("publication-" + uuid.uuid4().hex + ".tmp")
                staging.mkdir()
                for name, text in content.items():
                    with (staging / name).open("w", encoding="utf-8") as stream:
                        stream.write(text)
                        stream.flush()
                        os.fsync(stream.fileno())
                sync_directory(staging)
                staging.replace(directory)
                sync_directory(directory.parent)
            if self.fault:
                self.fault("after_bundle")
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM manuscripts WHERE video_id=?", (video_id,)
            ).fetchone()[0]
            created = time.time()
            path = str(directory / "manuscript.md")
            connection.execute(
                "INSERT INTO manuscripts(video_id,version,path,markdown,created_at) VALUES(?,?,?,?,?)",
                (video_id, version, path, content["manuscript.md"], created),
            )
            connection.execute(
                "UPDATE candidates SET manuscript_version=?,manuscript_path=? WHERE video_id=?",
                (version, path, video_id),
            )
            connection.execute(
                "INSERT INTO generation_records(video_id,manuscript_version,generator,transcript_characters,manuscript_characters,created_at) VALUES(?,?,?,?,?,?)",
                (
                    video_id,
                    version,
                    generator,
                    transcript_characters,
                    len(content["manuscript.md"]),
                    created,
                ),
            )
            connection.execute(
                "INSERT INTO experiment_publications VALUES(?,?,?,?,?,?)",
                (self.attempt_id, video_id, version, path, created, digest),
            )
        if self.fault:
            self.fault("after_commit")
        return {"version": version, "path": path, "created_at": created}

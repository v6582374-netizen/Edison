#!/usr/bin/env python3
"""One bounded continuous-archive pass; run every 15 minutes with a system timer."""

import argparse
import fcntl
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from coworker.curriculum import CourseArchive
from coworker.curriculum_sync import collect, generate_transcript
from coworker.secrets import state_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-lessons", type=int, default=4)
    parser.add_argument("--only", nargs="*", help="Limit preparation to these platform lesson IDs")
    args = parser.parse_args()
    store = CourseArchive(args.root or state_dir() / "curriculum")
    with (store.root / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another archive pass is running", flush=True)
            return
        status = collect(store, force=args.force)
        print(
            json.dumps(
                {"acquired": status["acquired"], "errors": status["errors"]}, ensure_ascii=False
            ),
            flush=True,
        )
        if args.collect_only:
            return
        pending = [
            r
            for r in store.listing()["recordings"]
            if not any(d["perspective"] == "transcript" for d in r["documents"])
            and store._read_source(r["id"], "platform-segments", [])
            and (
                args.force
                or store.sync_status("generation:" + r["id"]).get("next_try", 0) <= time.time()
            )
        ]
        if args.only:
            pending = [r for r in pending if r["id"] in args.only]
        if not pending:
            return
        # Load the existing provider only when there is actual work, not for idle polls.
        from coworker.config import load_config
        from coworker.server.manager import SessionManager

        manager = SessionManager(data_dir=state_dir(), model=load_config().model)
        if not manager.get_settings().get("model_ready"):
            status["generation_error"] = "请先配置 Edison 文字模型"
            store.save_sync_status("automation", status)
            return
        status.pop("generation_error", None)
        for record in pending[: max(0, args.max_lessons)]:
            rid = record["id"]
            status.update(
                preparing=rid,
                pending=len(pending),
                generation_started_at=datetime.now(timezone.utc).isoformat(),
            )
            store.save_sync_status("automation", status)
            try:
                doc = generate_transcript(store, rid, manager)
                print(record["course"], rid, "ready", len(doc["markdown"]), flush=True)
                status["errors"].pop("generation:" + rid, None)
                store.save_sync_status("generation:" + rid, {})
            except Exception as exc:
                print(rid, "preparation failed", type(exc).__name__, flush=True)
                status["errors"]["generation:" + rid] = "字幕整理失败，来源保留，将重试"
                failures = store.sync_status("generation:" + rid).get("failures", 0) + 1
                store.save_sync_status(
                    "generation:" + rid,
                    {
                        "failures": failures,
                        "next_try": time.time() + min(6 * 3600, 900 * 2 ** min(failures, 4)),
                    },
                )
            pending = [r for r in pending if r["id"] != rid]
        status.update(
            preparing=None, pending=len(pending), finished_at=datetime.now(timezone.utc).isoformat()
        )
        store.save_sync_status("automation", status)


if __name__ == "__main__":
    main()

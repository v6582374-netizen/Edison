#!/usr/bin/env python3
"""Private SSH transport for Web documents and a local-backup acknowledgement."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from coworker.curriculum import CourseArchive
from coworker.curriculum_sync import export_documents
from coworker.secrets import state_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--ack", action="store_true")
    args = parser.parse_args()
    store = CourseArchive(args.root or state_dir() / "curriculum")
    if args.ack:
        ack = json.loads(sys.stdin.read(100000))
        revision = ack.get("revision", "")
        if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Invalid backup revision")
        conflicts = ack.get("conflicts", [])
        if not isinstance(conflicts, list) or any(not isinstance(c, str) for c in conflicts):
            raise ValueError("Invalid conflicts")
        current = export_documents(store)
        store.save_sync_status(
            "backup",
            {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "revision": revision,
                "current_revision": current["revision"],
                "state": "conflict"
                if conflicts
                else "synced"
                if revision == current["revision"]
                else "pending",
                "files": int(ack.get("files", 0)),
                "conflicts": conflicts[:100],
            },
        )
        print('{"ok":true}')
    else:
        print(json.dumps(export_documents(store), ensure_ascii=False))


if __name__ == "__main__":
    main()

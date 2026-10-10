#!/usr/bin/env python3
"""One-off preparation using Edison's existing model, provider and credential store."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coworker.config import load_config
from coworker.curriculum import CourseArchive, generate_notes
from coworker.secrets import state_dir
from coworker.server.manager import SessionManager

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--root", type=Path)
p.add_argument("--only", nargs="*")
p.add_argument("--retry-failed", action="store_true")
p.add_argument(
    "--regenerate",
    action="store_true",
    help="Create a reviewed replacement without deleting old document links",
)
p.add_argument("--workers", type=int, default=2, choices=[1, 2, 3])
a = p.parse_args()
manager = SessionManager(data_dir=state_dir(), model=load_config().model)
store = CourseArchive(a.root or state_dir() / "curriculum")
if not manager.get_settings().get("model_ready"):
    raise SystemExit("Edison text model is not configured")


def prepare(record):
    if a.only and record["id"] not in a.only:
        return
    if not a.regenerate and (
        record["documents"]
        or record["state"] not in ({"pending", "failed"} if a.retry_failed else {"pending"})
    ):
        return
    try:
        doc = generate_notes(store, record["id"], manager, regenerate=a.regenerate)
        print(record["course"], record["title"], "ready", len(doc["markdown"]), flush=True)
    except Exception as e:
        print(record["id"], "failed", type(e).__name__, flush=True)


with ThreadPoolExecutor(max_workers=a.workers) as pool:
    list(pool.map(prepare, store.listing()["recordings"]))

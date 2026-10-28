#!/usr/bin/env python3
"""Prepare a finite spec 78 acceptance run without making model calls by default.

The optional real-provider run is deliberately gated twice so a normal invocation only
validates immutable sample identities and prints the operational plan. It never reads or
prints credentials and does not compare translation/manuscript content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

SAMPLES = (
    ("short-1", "RLYq760Jyuo", 68, "b644bfedf760d0caf194cd7efe03eedee4ce4a32a84bf64be252009154780444"),
    ("long-2", "nk15CT41MFc", 7339, "15795e22dbcde5b9b70d9a030513dd5ad1c53da002f573ef37f976d0eda1208d"),
    ("four-hour-public", "bqeuFiAUU4o", 15334, "a1de9a9ba3f824fa24d2dc7e510648e874a2a117111116bb4b8eaa6e48cb5bf8"),
)
ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("EDISON_SPEC78_SOURCE_ROOT", "/Users/shiwen/Downloads/CodexProjects/Edison/.local/translation-experiment-inputs"))

def inspect_samples() -> list[dict[str, object]]:
    rows = []
    for name, video_id, duration, expected in SAMPLES:
        path = SOURCE_ROOT / f"{name}.srt"
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        rows.append({"name": name, "video_id": video_id, "duration_seconds": duration,
                     "path": str(path), "sha256": digest, "identity_matches": digest == expected})
    return rows

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", action="store_true", help="print that a real run is explicitly requested")
    args = parser.parse_args()
    rows = inspect_samples()
    payload = {"workflow": "original-upstream-v1", "composition_rule": "whole-markdown-v1",
               "automatic_composition_request_limit": 3, "semantic_comparison": False,
               "samples": rows}
    if args.real and os.environ.get("EDISON_SPEC78_ALLOW_REAL") != "1":
        payload["real_run"] = "blocked: set EDISON_SPEC78_ALLOW_REAL=1 explicitly after reviewing the isolated plan"
    elif args.real:
        payload["real_run"] = "authorized by environment; invoke the isolated runner separately"
    else:
        payload["real_run"] = "not requested; no provider or network calls made"
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(row["identity_matches"] for row in rows if row["sha256"] is not None) else 2

if __name__ == "__main__":
    raise SystemExit(main())

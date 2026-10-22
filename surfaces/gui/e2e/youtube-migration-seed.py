#!/usr/bin/env python3
"""Build an isolated old-library sample for the historical migration drill.

The sample lives only under the system temp directory. This script never reads
the developer profile, the service credential file, or a production library.
Token checks print kept or absent and do not print credential material.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from youtube_strataread.workbench.connection import SubscriptionSource
from youtube_strataread.workbench.discovery import Candidate
from youtube_strataread.workbench.keychain import NativeKeychainVault, ServiceAccountFile
from youtube_strataread.workbench.workspace import LocalWorkspace

EVIDENCE = 1_700_000_000
CLIENT = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_ID"
SECRET = "YOUTUBE_WORKBENCH_OAUTH_CLIENT_SECRET"
REFRESH = "YOUTUBE_WORKBENCH_OAUTH_REFRESH_TOKEN"
SOURCE_REFRESH = "synthetic-source-refresh"
FOREIGN_REFRESH = "synthetic-foreign-refresh"
SUBTITLE_429 = "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"


def candidate(video_id: str, title: str, channel: str = "channel") -> Candidate:
    return Candidate(
        video_id=video_id, channel_id=channel, channel_title=title, title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=datetime.fromtimestamp(EVIDENCE, timezone.utc).isoformat(),
        published_ts=float(EVIDENCE), source_observed_at=float(EVIDENCE),
    )


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    if not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise SystemExit("migration drill refuses paths outside the temp directory")
    phase = sys.argv[2]
    library = (root / "library").resolve()
    credentials = root / "credentials.json"
    backend = ServiceAccountFile(credentials)
    vault = NativeKeychainVault(namespace=str(library), backend=backend)
    if phase == "token":
        refresh = vault.load(REFRESH)
        if refresh == SOURCE_REFRESH:
            print("kept")
        elif refresh is None:
            print("absent")
        else:
            print("changed")
        return
    if phase != "seed":
        raise SystemExit("unknown drill phase")
    workspace = LocalWorkspace.open(library)
    old = root / "old-assets"
    old.mkdir()
    transcript = old / "transcript.srt"
    transcript.write_text("1\n00:00:00,000 --> 00:00:01,000\nkept line\n", encoding="utf-8")
    outside = root / "elsewhere" / "note.srt"
    outside.parent.mkdir()
    outside.write_text("outside\n", encoding="utf-8")
    workspace.add_candidate(candidate("mappedvideo", "Mapped asset"))
    with sqlite3.connect(workspace.database_path) as db:
        db.execute(
            "INSERT INTO transcripts (video_id, language, path, content, created_at) VALUES ('mappedvideo', 'en', ?, 'kept', ?)",
            (str(transcript), EVIDENCE))
        db.execute("UPDATE candidates SET manuscript_path=? WHERE video_id='mappedvideo'", (str(outside),))

    def plant(video_id: str, title: str, state: str, reason: str, *, kind=None, attempts=0, commenced=None, retry_at=None):
        workspace.add_candidate(candidate(video_id, title))
        with sqlite3.connect(workspace.database_path) as db:
            db.execute(
                """UPDATE candidates SET preparation_state=?, failure_reason=?, acquisition_failure_kind=?,
                   rate_limit_attempts=?, commenced_at=?, retry_at=?, preparation_completed_at=?, stage_updated_at=?
                   WHERE video_id=?""",
                (state, reason, kind, attempts, commenced, retry_at, EVIDENCE, EVIDENCE, video_id),
            )

    plant("legacy429aa", "明确限流", "unavailable", SUBTITLE_429, attempts=3, retry_at=EVIDENCE + 500)
    plant("legacynet01", "明确网络", "waiting_retry", "字幕获取暂时失败，等待共享能力探测。",
          kind="transient", attempts=2, commenced=EVIDENCE - 50)
    plant("legacyunk01", "未知失败样本", "unavailable", "player response was empty", attempts=4)
    # commenced_at keeps the host age recheck from rewriting this model budget
    # before the maintainer opens the cutover hold.
    plant("legacymod01", "模型限流", "waiting_retry", "模型服务限流；同账号请求暂停。",
          attempts=2, retry_at=EVIDENCE + 50, commenced=EVIDENCE - 20)
    plant("legacypriv1", "私有视频", "failed", "private video", kind="private")
    workspace.add_candidate(candidate("keptread001", "保留文稿", channel="kept-channel"))
    workspace.set_preparation_state("keptread001", "ready")
    workspace.save_manuscript("keptread001", "完整译文保留。\n", generator="fixture", transcript_characters=4)
    workspace.set_reading_state("keptread001", "read")
    with sqlite3.connect(workspace.database_path) as db:
        db.execute("UPDATE candidates SET request_kind='manual', commenced_at=? WHERE video_id='keptread001'", (EVIDENCE - 10,))
    workspace.set_excluded_channels(["excluded-ch"])
    workspace.add_candidate(candidate("unsubvideo1", "已退订", channel="old-channel"))
    workspace.replace_subscription_sources([SubscriptionSource("old-channel", "Old")])
    workspace.replace_subscription_sources([])
    source = NativeKeychainVault(namespace=str((root / "old-namespace").resolve()), backend=backend)
    source.save_many({CLIENT: "fixture-client", SECRET: "fixture-secret", REFRESH: SOURCE_REFRESH})
    foreign = NativeKeychainVault(namespace=str((root / "foreign-namespace").resolve()), backend=backend)
    foreign.save_many({CLIENT: "foreign-client", SECRET: "foreign-secret", REFRESH: FOREIGN_REFRESH})
    destination = library / "assets"
    plan = {
        "credential_namespace": str((root / "old-namespace").resolve()),
        "expected_client_id": "fixture-client",
        "path_map": [{"from": str(old.resolve()), "to": str(destination.resolve())}],
        "foreign_namespace": str((root / "foreign-namespace").resolve()),
        "outside_path": str(outside.resolve()),
        "mapped_path": str((destination / "transcript.srt").resolve()),
        "evidence_at": EVIDENCE,
        "cooldown_until": EVIDENCE + 15 * 60,
    }
    (root / "migration-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    print("seeded")


if __name__ == "__main__":
    main()

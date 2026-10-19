"""Typed external errors survive the private caption subprocess protocol."""

import sys

import pytest

from youtube_strataread.workbench.caption_process import CaptionProcess
from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.retry import classify_caption


@pytest.mark.parametrize(
    ("kind", "category"),
    [
        ("private", "private"),
        ("deleted", "deleted"),
        ("access_denied", "access_denied"),
        ("unknown", "unknown"),
    ],
)
def test_fresh_worker_preserves_video_failure_reason(tmp_path, kind, category):
    worker = tmp_path / "external.py"
    worker.write_text(
        'import json,sys\njson.loads(sys.stdin.readline())\nprint(json.dumps({"event":"error","kind":'
        + repr(kind)
        + "}),flush=True)\n"
    )
    with pytest.raises(ContentSessionError) as failure:
        CaptionProcess([sys.executable, str(worker)]).acquire(
            "https://www.youtube.com/watch?v=abcdefghijk", tmp_path / "cookies"
        )
    assert classify_caption(failure.value, 1000).category == category


@pytest.mark.parametrize("failure_type", ["PermissionError", "FileNotFoundError", "ImportError"])
def test_worker_filesystem_failure_is_sanitized_maintenance(tmp_path, failure_type):
    worker = tmp_path / "external.py"
    worker.write_text(
        """from youtube_strataread.downloader import youtube
from youtube_strataread.workbench.caption_process import _run
def denied(*args, **kwargs):
    raise FAILURE_TYPE("credential-secret-path")
youtube.download_subtitles = denied
_run()
""".replace("FAILURE_TYPE", failure_type)
    )
    with pytest.raises(ContentSessionError) as failure:
        CaptionProcess([sys.executable, str(worker)]).acquire(
            "https://www.youtube.com/watch?v=abcdefghijk", tmp_path / "cookies"
        )
    assert failure.value.kind == "maintenance"
    assert "credential-secret-path" not in str(failure.value)

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

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


@pytest.mark.parametrize("noise", ["progress", "python", "raw_fd", "child"])
def test_real_ytdlp_subtitle_progress_cannot_corrupt_worker_protocol(tmp_path, capfd, noise):
    import http.server
    import threading

    srt = "1\n00:00:00,000 --> 00:00:01,000\nSynthetic caption.\n"
    fetched = threading.Event()

    class Captions(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/captions.srt"
            body = srt.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-subrip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            fetched.set()

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Captions)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    incidental = {
        "progress": "pass",
        "python": "print('sensitive-external-diagnostic', flush=True); print('sensitive-external-diagnostic', file=sys.stderr, flush=True)",
        "raw_fd": "os.write(1, b'sensitive-external-diagnostic\\n'); os.write(2, b'sensitive-external-diagnostic\\n')",
        "child": "subprocess.run([sys.executable, '-c', \"import os; os.write(1,b'sensitive-external-diagnostic'); os.write(2,b'sensitive-external-diagnostic')\"], check=True)",
    }[noise]
    worker = tmp_path / "local_captions.py"
    worker.write_text(
        """import os,subprocess,sys,yt_dlp
from yt_dlp.extractor.common import InfoExtractor
from youtube_strataread.workbench.caption_process import _run
class LocalCaptionsIE(InfoExtractor):
    _VALID_URL = r'https://www.youtube.com/watch\\?v=(?P<id>abcdefghijk)'
    def _real_extract(self, url):
        INCIDENTAL
        return {'id':'abcdefghijk', 'title':'Synthetic title', 'duration':1,
                'availability':'public',
                'subtitles':{'en':[{'ext':'srt','url':CAPTION_URL}]},
                'formats':[{'url':'http://127.0.0.1/never-download.mp4','ext':'mp4'}]}
class LocalYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(self, params):
        super().__init__(params, auto_init=False)
        self.add_info_extractor(LocalCaptionsIE())
        self.add_default_info_extractors()
yt_dlp.YoutubeDL = LocalYoutubeDL
_run()
""".replace("CAPTION_URL", repr(f"http://127.0.0.1:{server.server_port}/captions.srt")).replace(
            "INCIDENTAL", incidental
        )
    )
    metadata, commenced = [], []
    try:
        result = CaptionProcess([sys.executable, str(worker)]).acquire(
            "https://www.youtube.com/watch?v=abcdefghijk",
            tmp_path / "cookies",
            on_metadata=lambda video, duration: metadata.append((video, duration)),
            before_subtitles=lambda info: commenced.append(info["id"]),
        )
        assert fetched.is_set()
        assert result.video_id == "abcdefghijk"
        assert result.srt_text == srt
        assert metadata == [("abcdefghijk", 1.0)]
        assert commenced == ["abcdefghijk"]
        captured = capfd.readouterr()
        assert "sensitive-external-diagnostic" not in captured.out + captured.err
    finally:
        server.shutdown()


def test_withdrawn_snapshot_stops_inflight_caption_process(tmp_path):
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    worker = tmp_path / 'waiting.py'
    marker = tmp_path / 'worker-pid'
    worker.write_text('import os,sys,time\nfrom pathlib import Path\nsys.stdin.readline()\nPath(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    withdrawn = Event()
    with ThreadPoolExecutor() as pool:
        running = pool.submit(CaptionProcess([sys.executable, str(worker), str(marker)]).acquire,
            'https://www.youtube.com/watch?v=abcdefghijk', tmp_path / 'cookies',
            can_use=lambda: not withdrawn.is_set())
        deadline = time.monotonic()+3
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert marker.exists()
        withdrawn.set()
        with pytest.raises(ContentSessionError, match='断开'):
            running.result(timeout=3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)

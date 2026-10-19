"""Request limits at HTTP, shared workspace, and real yt-dlp subtitle boundaries."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from test_workbench_library import candidate, ready_service

from youtube_strataread.downloader.request_policy import (
    INITIAL_COOLDOWN,
    MAX_COOLDOWN,
    REQUEST_INTERVAL,
    VIDEO_INTERVAL,
    YouTubeRateLimited,
    YouTubeRequestPolicy,
    YouTubeRequestsStopped,
    rate_limit_error,
)
from youtube_strataread.downloader.youtube import YouTubeError, download_subtitles
from youtube_strataread.workbench.shorts import YouTubeShortsClassifier
from youtube_strataread.workbench.workspace import LocalWorkspace
from youtube_strataread.workbench.youtube_api import YouTubeAPIError, YouTubeDataAPI


def http429(retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://www.youtube.com/api/timedtext", 429, "Too Many Requests", headers, None
    )


@pytest.mark.parametrize(
    "header,expected",
    [
        ("3600", 4600),
        (formatdate(8200, usegmt=True), 8200),
        ("bad", 1000),
        ("-1", 1000),
    ],
)
def test_retry_after_survives_nested_download_errors(header, expected):
    error = RuntimeError("yt-dlp wrapper")
    error.exc_info = (HTTPError, http429(header), None)
    assert rate_limit_error(error, now=1000).retry_at == expected
    assert rate_limit_error(YouTubeError("no subtitles")) is None
    assert rate_limit_error(HTTPError("url", 404, "Not Found", {}, None)) is None


def test_cycle_in_exception_chain_is_safe():
    error = RuntimeError("HTTP Error 429: Too Many Requests")
    error.__cause__ = error
    assert isinstance(rate_limit_error(error), YouTubeRateLimited)


def test_backoff_persists_and_honours_server_deadline(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    policy = ws.youtube_requests
    now = [1000.0]
    policy.now = lambda: now[0]
    first = policy.limit(YouTubeRateLimited())
    assert first.retry_at == 1000 + INITIAL_COOLDOWN
    assert policy.limit(first).retry_at == first.retry_at
    assert policy.snapshot()["strikes"] == 1
    policy.caption_succeeded()  # success arriving after a concurrent 429 must not clear it
    assert policy.snapshot()["strikes"] == 1
    resumed = YouTubeRequestPolicy(ws.database_path)
    resumed.now = lambda: now[0]
    with pytest.raises(YouTubeRateLimited):
        resumed.before_request()
    now[0] = first.retry_at
    second = resumed.limit(YouTubeRateLimited(now[0] + 7200))
    assert second.retry_at == now[0] + 7200
    now[0] = second.retry_at
    third = resumed.limit(YouTubeRateLimited())
    assert third.retry_at == now[0] + INITIAL_COOLDOWN * 4
    for _ in range(8):
        now[0] = resumed.snapshot()["cooldown_until"]
        resumed.limit(YouTubeRateLimited())
    assert resumed.snapshot()["cooldown_until"] == now[0] + MAX_COOLDOWN
    now[0] = resumed.snapshot()["cooldown_until"]
    resumed.caption_succeeded()
    assert resumed.snapshot()["strikes"] == 0


def test_request_and_video_spacing_share_durable_reservations(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    policy = ws.youtube_requests
    clock = [1000.0]
    policy.now = lambda: clock[0]
    policy.stopped = SimpleNamespace(
        is_set=lambda: False, wait=lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    policy.before_request()
    policy.before_request()
    assert clock[0] == 1000 + REQUEST_INTERVAL
    policy.before_video()
    policy.before_video()
    assert clock[0] == 1000 + REQUEST_INTERVAL + VIDEO_INTERVAL
    other = YouTubeRequestPolicy(ws.database_path)
    other.now, other.stopped = policy.now, policy.stopped
    other.before_video()
    assert clock[0] == 1000 + REQUEST_INTERVAL + VIDEO_INTERVAL * 2


def test_waiting_is_interruptible_and_does_not_hold_database(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    policy = ws.youtube_requests
    policy.before_video()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(policy.before_video)
        ws.set_meta("unrelated_write", "still responsive")
        policy.stopped.set()
        with pytest.raises(YouTubeRequestsStopped):
            future.result(timeout=2)


def test_concurrent_policy_instances_do_not_burst_requests(tmp_path, monkeypatch):
    import youtube_strataread.downloader.request_policy as module

    monkeypatch.setattr(module, "REQUEST_INTERVAL", 0.05)
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")

    def request():
        YouTubeRequestPolicy(ws.database_path).before_request()
        return time.monotonic()

    with ThreadPoolExecutor(max_workers=4) as pool:
        stamps = sorted(pool.map(lambda _: request(), range(4)))
    assert all(b - a >= 0.04 for a, b in zip(stamps, stamps[1:], strict=False))


def test_subtitle_429_blocks_acquisition_but_not_official_api(tmp_path, monkeypatch):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    for key in ["one", "two"]:
        ws.add_candidate(candidate(key))
    service = ready_service(ws)
    service.captions = SimpleNamespace(
        acquire=Mock(side_effect=YouTubeError("HTTP Error 429: Too Many Requests"))
    )
    assert service.run_next()
    assert ws.activity()["rate_limited"] == 1
    assert not service.run_next()
    assert ws.asset("two")["preparation_state"] == "queued"
    service.resume()
    ws.queue_regeneration("one")
    ws.retry_failed()
    assert not service.run_next()
    network = Mock(side_effect=AssertionError("cooldown must prevent outbound traffic"))
    monkeypatch.setattr("youtube_strataread.workbench.shorts.urlopen", network)
    with pytest.raises(YouTubeRateLimited):
        YouTubeShortsClassifier(ws.youtube_requests).classify("normal00001")
    network.assert_not_called()
    from io import BytesIO
    monkeypatch.setattr("youtube_strataread.workbench.youtube_api.urlopen", lambda *a, **kw: BytesIO(b'{"items":[]}'))
    assert YouTubeDataAPI(ws).get('channels', {'part':'contentDetails','id':'channel'}, 'fixture') == {'items':[]}
    service.captions.acquire.assert_called_once()
    deadline = ws.youtube_requests.snapshot()["cooldown_until"]
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    ws.youtube_requests.now = lambda: deadline + 1
    assert ready_service(ws).run_next()
    assert ws.asset("one")["preparation_state"] == "ready"
    assert ws.youtube_requests.snapshot()["strikes"] == 0



def test_rate_limited_item_can_be_cancelled_without_resetting_cooldown(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    ws.add_candidate(candidate("one"))
    service = ready_service(ws)
    service.captions = SimpleNamespace(acquire=Mock(side_effect=YouTubeRateLimited()))
    service.run_next()
    deadline = ws.youtube_requests.snapshot()["cooldown_until"]
    assert ws.change_queue(["one"])["changed"] == ["one"]
    assert ws.change_queue(["one"], restore=True)["changed"] == ["one"]
    assert not service.run_next()
    assert ws.youtube_requests.snapshot()["cooldown_until"] == deadline


def test_api_429_uses_a_separate_global_gate_from_captions(tmp_path, monkeypatch):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    monkeypatch.setattr(
        "youtube_strataread.workbench.shorts.urlopen", Mock(side_effect=http429("7200"))
    )
    ws.add_candidate(candidate("normal00001"))
    service = ready_service(ws)
    service.shorts = YouTubeShortsClassifier(ws.youtube_requests)
    assert service.run_next()
    assert ws.activity()["rate_limited"] == 1
    assert ws.youtube_requests.snapshot()["cooldown_until"] > time.time() + 7100
    other = LocalWorkspace.open(tmp_path / "api")
    fetch = Mock(side_effect=http429("3600"))
    monkeypatch.setattr("youtube_strataread.workbench.youtube_api.urlopen", fetch)
    api = YouTubeDataAPI(other)
    with pytest.raises(YouTubeAPIError, match="官方 API"):
        api.get('channels', {'id':'channel','part':'contentDetails'}, 'fixture')
    assert api.gate()['retry_at'] > time.time() + 3500
    assert not other.youtube_requests.cooling_down()


def test_only_legacy_subtitle_429_records_are_recovered_once(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    for key in ["limited", "no-captions", "cancelled"]:
        ws.add_candidate(candidate(key))
    reason = "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
    ws.set_preparation_state("limited", "unavailable", reason)
    ws.set_preparation_state("no-captions", "unavailable", "no subtitles available")
    ws.change_queue(["cancelled"])
    with sqlite3.connect(ws.database_path) as db:
        db.execute("DELETE FROM workspace_meta WHERE key = 'youtube_rate_limit_migration_v1'")
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    assert ws.asset("limited")["preparation_state"] == "rate_limited"
    assert ws.asset("no-captions")["preparation_state"] == "unavailable"
    assert ws.asset("cancelled")["preparation_state"] == "cancelled"
    ws.change_queue(["limited"])
    assert LocalWorkspace.open(tmp_path).asset("limited")["preparation_state"] == "cancelled"


@pytest.mark.parametrize("status", [200, 429])
def test_real_ytdlp_subtitle_download_preserves_retry_after_and_does_not_reprobe(
    tmp_path, monkeypatch, status
):
    from yt_dlp import YoutubeDL

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(status)
            if status == 429:
                self.send_header("Retry-After", "3600")
            self.end_headers()
            self.wfile.write(b"1\n00:00:00,000 --> 00:00:01,000\nSource\n")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/caption.srt"
    info = {
        "id": "normal00001",
        "title": "Fixture",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": "https://www.youtube.com/watch?v=normal00001",
        "subtitles": {"en": [{"ext": "srt", "url": url}]},
        "automatic_captions": {},
        "formats": [{"url": url, "ext": "mp4", "format_id": "fixture"}],
    }
    extractions = []

    def extract(self, url, download=False):
        extractions.append(url)
        return self.process_ie_result(info.copy(), download=download)

    monkeypatch.setattr(YoutubeDL, "extract_info", extract)
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    try:
        if status == 200:
            assert (
                "Source"
                in download_subtitles(
                    info["webpage_url"], request_policy=ws.youtube_requests
                ).srt_text
            )
        else:
            with pytest.raises(YouTubeRateLimited) as captured:
                download_subtitles(info["webpage_url"], request_policy=ws.youtube_requests)
            assert captured.value.retry_at > time.time() + 3500
            assert ws.youtube_requests.snapshot()["strikes"] == 1
        assert extractions == [info["webpage_url"]]
        assert requests == ["/caption.srt"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_shutdown_after_cooldown_is_saved_recovers_the_interrupted_item(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    ws.add_candidate(candidate("one"))
    ws.claim_next_queued_asset()
    ws.youtube_requests.limit(YouTubeRateLimited())
    restarted = LocalWorkspace.open(tmp_path)
    restarted.recover_interrupted_preparations()
    assert restarted.asset("one")["preparation_state"] == "rate_limited"
    assert not restarted.claim_next_queued_asset()


def test_waiting_for_another_requests_cooldown_does_not_consume_retry_budget(tmp_path):
    ws = LocalWorkspace.open(tmp_path)
    ws.set_meta("drain_paused", "0")
    ws.add_candidate(candidate("one"))
    service = ready_service(ws)

    def acquire(url):
        ws.youtube_requests.limit(YouTubeRateLimited())
        ws.youtube_requests.before_request()

    service.captions = SimpleNamespace(acquire=acquire)
    assert service.run_next()
    assert ws.asset("one")["preparation_state"] == "rate_limited"
    with sqlite3.connect(ws.database_path) as db:
        assert (
            db.execute(
                "SELECT rate_limit_attempts FROM candidates WHERE video_id='one'"
            ).fetchone()[0]
            == 0
        )


def test_old_caption_success_cannot_clear_a_newer_failure_even_after_its_deadline(tmp_path):
    workspace = LocalWorkspace.open(tmp_path)
    policy = workspace.youtube_requests
    clock = [1000.0]
    policy.now = lambda: clock[0]
    started_sequence = policy.snapshot().get('failure_sequence', 0)
    failure = policy.limit(YouTubeRateLimited())
    clock[0] = failure.retry_at + 1
    policy.caption_succeeded(observed_sequence=started_sequence)
    assert policy.snapshot()['strikes'] == 1


def test_recovery_probe_has_one_owner_and_releases_after_process_crash(tmp_path):
    import os
    import subprocess
    import sys

    workspace = LocalWorkspace.open(tmp_path)
    policy = workspace.youtube_requests
    policy.now = lambda: 1000.0
    deadline = policy.limit(YouTubeRateLimited()).retry_at
    policy.now = lambda: deadline
    with policy.acquisition():
        another = YouTubeRequestPolicy(workspace.database_path)
        another.now = lambda: deadline
        with pytest.raises(YouTubeRateLimited), another.acquisition():
            pytest.fail('a second recovery probe must not run')
    script = '''import sys, time
from pathlib import Path
from youtube_strataread.downloader.request_policy import YouTubeRequestPolicy
policy = YouTubeRequestPolicy(Path(sys.argv[1]))
policy.now = lambda: float(sys.argv[2])
with policy.acquisition():
    print("owned", flush=True)
    time.sleep(30)
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(workspace.database_path), str(deadline)],
                             stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == 'owned'
        with pytest.raises(YouTubeRateLimited), policy.acquisition():
            pytest.fail('the subprocess owns the probe')
    finally:
        child.kill()
        child.wait(timeout=5)
    with policy.acquisition():
        assert policy.snapshot()['strikes'] == 1


def test_standalone_downloader_import_does_not_require_posix_locking():
    import os
    import subprocess
    import sys

    script = '''import importlib.abc, sys
class NoPosixLock(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fcntl":
            raise ModuleNotFoundError("fcntl is absent on Windows")
sys.modules.pop("fcntl", None)
sys.meta_path.insert(0, NoPosixLock())
from youtube_strataread.downloader.youtube import download_subtitles
assert callable(download_subtitles)
'''
    result = subprocess.run([sys.executable, '-c', script], text=True,
                            capture_output=True, env=os.environ.copy(), timeout=10)
    assert result.returncode == 0, result.stderr

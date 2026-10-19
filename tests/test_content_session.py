"""Public session lease contracts; external browser/YouTube only are controlled."""

import pytest

from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.content_session import ContentSession, ContentSessionError

SRT = "1\n00:00:00,000 --> 00:00:02,000\nA real caption response.\n"


class Browser:
    def __init__(self):
        self.running = False
        self.handoff = False

    def start(self, profile):
        self.running = True

    def snapshot(self):
        if not self.handoff:
            raise ContentSessionError("user", "请在 YouTube 页面完成登录。")
        return "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfixture-secret\n"

    def close(self):
        self.running = False


def test_google_login_is_not_caption_success_and_snapshot_consumers_cannot_write_back(tmp_path):
    browser = Browser()
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    with pytest.raises(ContentSessionError, match="YouTube"):
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: None)
    assert session.status()["state"] == "needs_user"
    browser.handoff = True
    session.open()
    session.confirm(
        "abcdefghijk",
        same_account=True,
        fetch=lambda *args: SubtitleResult("abcdefghijk", "Title", "en", False, SRT),
    )
    assert session.status()["state"] == "available"
    assert session.status()["account_consistency"] == "user_confirmed_not_verified"
    with session.snapshot() as first:
        first.path.write_text("downloader response mutation")
    with session.snapshot() as second:
        assert "fixture-secret" in second.path.read_text()
    session.close_browser()
    session.shutdown()
    restarted = ContentSession(tmp_path / "session", browser=Browser())
    assert restarted.status()["state"] == "unverified"
    with restarted.snapshot() as current:
        assert "fixture-secret" in current.path.read_text()
    restarted.shutdown()


@pytest.mark.parametrize(
    "result",
    [
        None,
        SubtitleResult("other-video", "Title", "en", False, SRT),
        SubtitleResult("abcdefghijk", "Title", "en", False, ""),
        SubtitleResult("abcdefghijk", "Title", "en", False, "HTTP 200"),
        SubtitleResult("abcdefghijk", "Title", "live_chat", False, SRT),
    ],
)
def test_connection_requires_matching_parseable_nonempty_captions(tmp_path, result):
    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    with pytest.raises(ContentSessionError, match="字幕"):
        session.open()
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    assert session.status()["state"] == "interrupted"
    session.shutdown()


def test_profile_single_writer_idle_close_and_old_leases_invalidated(tmp_path):
    clock = [1000]
    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser, now=lambda: clock[0])
    session.open()
    competing = ContentSession(tmp_path / "session", browser=Browser())
    with pytest.raises(ContentSessionError, match="另一服务"):
        competing.open()
    result = SubtitleResult("abcdefghijk", "Title", "en", False, SRT)
    session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    with session.snapshot() as stale:
        session.open()
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        with pytest.raises(ContentSessionError, match="旧请求"):
            session.accept(stale, result, "abcdefghijk")
    session.open()
    clock[0] = 1900
    session.tick()
    assert not browser.running
    assert session.status()["snapshot_present"]
    session.open()
    assert browser.running
    with session.snapshot() as stale:
        session.clear()
        with pytest.raises(ContentSessionError, match="旧请求"):
            session.accept(stale, result, "abcdefghijk")
    assert not session.status()["snapshot_present"]
    assert list((session.root / "consumers").iterdir()) == []
    session.shutdown()


def test_disconnected_consumers_cannot_recreate_secret_files(tmp_path):
    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    session.confirm(
        "abcdefghijk",
        same_account=True,
        fetch=lambda *args: SubtitleResult("abcdefghijk", "T", "en", False, SRT),
    )
    with session.snapshot() as lease:
        session.clear()
        with pytest.raises(FileNotFoundError):
            lease.path.write_text("late response cookie")
    session.shutdown()


def test_storage_failure_is_reportable_without_writing_state(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("x")
    session = ContentSession(root)
    with pytest.raises(ContentSessionError) as failure:
        session.open()
    session.record_failure(failure.value)


def test_old_failure_cannot_overwrite_new_verified_session(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    result = SubtitleResult("abcdefghijk", "T", "en", False, SRT)
    session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    started, release = threading.Event(), threading.Event()

    def slow_failure(*args):
        started.set()
        assert release.wait(5)
        raise ContentSessionError("user", "Old challenge")

    with ThreadPoolExecutor() as pool:
        stale = pool.submit(session.verify, "abcdefghijk", fetch=slow_failure)
        assert started.wait(5)
        session.open()
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        release.set()
        with pytest.raises(ContentSessionError):
            stale.result()
    assert session.status()["state"] == "available"
    session.shutdown()


def test_failure_publication_checks_generation_atomically(tmp_path):
    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    result = SubtitleResult("abcdefghijk", "T", "en", False, SRT)
    session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    with session.snapshot() as old:
        session.open()
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        with pytest.raises(ContentSessionError, match="旧请求"):
            session.record_failure(ContentSessionError("user", "Old challenge"), lease=old)
    assert session.status()["state"] == "available"
    session.shutdown()


def test_new_verification_wins_while_old_failure_waits_at_publication_boundary(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    ready, release = threading.Event(), threading.Event()

    class DelayedPublication(ContentSession):
        # Instrument the public concurrency boundary, not storage or private state.
        def record_failure(self, error, **kwargs):
            if str(error) == "delayed external failure":
                ready.set()
                assert release.wait(5)
            return super().record_failure(error, **kwargs)

    browser = Browser()
    browser.handoff = True
    session = DelayedPublication(tmp_path / "session", browser=browser)
    session.open()
    result = SubtitleResult("abcdefghijk", "T", "en", False, SRT)
    session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)

    def failed_fetch(*args):
        raise ContentSessionError("network", "delayed external failure")

    with ThreadPoolExecutor() as pool:
        old = pool.submit(session.verify, "abcdefghijk", fetch=failed_fetch)
        assert ready.wait(5)
        session.open()
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        release.set()
        with pytest.raises(ContentSessionError, match="旧请求"):
            old.result()
    assert session.status()["state"] == "available"
    session.shutdown()


def test_confirm_closes_human_browser_even_when_caption_check_fails(tmp_path):
    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()

    def unavailable(url, path):
        assert not browser.running
        raise ContentSessionError("network", "network unavailable")

    with pytest.raises(ContentSessionError):
        session.confirm("abcdefghijk", same_account=True, fetch=unavailable)
    assert not session.status()["browser_running"]
    session.shutdown()


def test_profile_read_failure_preserves_last_snapshot_and_generation(tmp_path):
    class NativeReadFailure(Browser):
        unreadable = False

        def snapshot(self):
            if self.unreadable:
                raise ContentSessionError("maintenance", "native key storage unavailable")
            return super().snapshot()

    browser = NativeReadFailure()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    result = SubtitleResult("abcdefghijk", "T", "en", False, SRT)
    session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    generation = session.status()["generation"]
    session.open()
    browser.unreadable = True
    with pytest.raises(ContentSessionError):
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
    assert session.status()["generation"] == generation
    assert not session.status()["browser_running"]
    assert session.status()["state"] == "needs_maintenance"
    with session.snapshot() as snapshot:
        assert "fixture-secret" in snapshot.path.read_text()
    session.shutdown()


def test_profile_owner_remains_exclusive_after_browser_closes_during_extraction(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    stopped, release = threading.Event(), threading.Event()

    class StoppedBrowserRead(Browser):
        def snapshot(self):
            self.close()
            stopped.set()
            assert release.wait(5)
            return super().snapshot()

    browser = StoppedBrowserRead()
    browser.handoff = True
    session = ContentSession(tmp_path / "session", browser=browser)
    session.open()
    competing = ContentSession(tmp_path / "session", browser=Browser())
    result = SubtitleResult("abcdefghijk", "T", "en", False, SRT)
    with ThreadPoolExecutor() as pool:
        checking = pool.submit(
            session.confirm, "abcdefghijk", same_account=True, fetch=lambda *args: result
        )
        assert stopped.wait(5)
        try:
            with pytest.raises(ContentSessionError, match="另一服务"):
                competing.open()
        finally:
            release.set()
        assert checking.result()["state"] == "available"
    session.shutdown()


def test_failed_browser_close_still_withdraws_content_permission(tmp_path):
    class UncooperativeBrowser(Browser):
        fail = False
        def close(self):
            if self.fail:
                raise OSError('browser still stopping')
            super().close()
    browser = UncooperativeBrowser()
    browser.handoff = True
    session = ContentSession(tmp_path / 'session', browser=browser)
    session.open()
    session.confirm('abcdefghijk', same_account=True,
        fetch=lambda *args: SubtitleResult('abcdefghijk', 'T', 'en', False, SRT))
    session.open()
    with session.snapshot() as lease:
        browser.fail = True
        with pytest.raises(ContentSessionError):
            session.clear()
        with pytest.raises(ContentSessionError, match='清理'):
            session.open()
        with pytest.raises(ContentSessionError):
            session.confirm('abcdefghijk', same_account=True, fetch=lambda *args: None)
        with pytest.raises(ContentSessionError, match='旧请求'):
            session.accept(lease, SubtitleResult('abcdefghijk', 'T', 'en', False, SRT), 'abcdefghijk')
        with pytest.raises(ContentSessionError), session.snapshot():
            pytest.fail('withdrawn credentials remain usable')
    browser.fail = False
    session.shutdown()
    restarted = ContentSession(tmp_path / 'session', browser=Browser())
    assert restarted.status()['state'] == 'disconnected'
    assert not restarted.status()['snapshot_present']
    restarted.shutdown()


@pytest.mark.parametrize('failed_path', ['consumers', 'current.cookies', 'state.json'])
def test_cleanup_io_failure_stops_browser_and_caption_use(tmp_path, monkeypatch, failed_path):
    import os
    import shutil
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from youtube_strataread.workbench.caption_process import CaptionProcess
    from youtube_strataread.workbench.library import YtDlpCaptions

    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / 'session', browser=browser)
    session.open()
    session.confirm('abcdefghijk', same_account=True,
        fetch=lambda *args: SubtitleResult('abcdefghijk', 'T', 'en', False, SRT))
    session.open()
    worker = tmp_path / 'waiting.py'
    marker, release = tmp_path / 'pid', tmp_path / 'release'
    worker.write_text('import json,os,sys,time\nfrom pathlib import Path\npayload=json.loads(sys.stdin.readline())\nPath(sys.argv[1]).write_text(str(os.getpid()))\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)\nprint(json.dumps({"event":"error","kind":"network"}),flush=True)\n')
    def command(self, command=None):
        self.command = [sys.executable, str(worker), str(marker), str(release)]
    monkeypatch.setattr(CaptionProcess, '__init__', command)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(YtDlpCaptions(content_session=session).acquire, 'https://youtu.be/abcdefghijk?v=abcdefghijk')
        deadline = time.monotonic()+3
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert marker.exists()
        original_remove, original_unlink, original_replace = shutil.rmtree, Path.unlink, os.replace
        def remove(path, *args, **kwargs):
            if failed_path == 'consumers' and Path(path) == session.root / failed_path:
                raise PermissionError('controlled cleanup failure')
            return original_remove(path, *args, **kwargs)
        def unlink(path, *args, **kwargs):
            if failed_path == 'current.cookies' and path == session.root / failed_path:
                raise PermissionError('controlled cleanup failure')
            return original_unlink(path, *args, **kwargs)
        def replace(source, destination):
            if failed_path == 'state.json' and Path(destination) == session.root / failed_path:
                raise PermissionError('controlled cleanup failure')
            return original_replace(source, destination)
        monkeypatch.setattr(shutil, 'rmtree', remove)
        monkeypatch.setattr(Path, 'unlink', unlink)
        monkeypatch.setattr(os, 'replace', replace)
        try:
            with pytest.raises((OSError, ContentSessionError)):
                session.clear()
            assert not browser.running
            with pytest.raises(ContentSessionError):
                pending.result(timeout=2)
            with pytest.raises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
            with pytest.raises(ContentSessionError):
                session.open()
        finally:
            release.touch()
            monkeypatch.setattr(shutil, 'rmtree', original_remove)
            monkeypatch.setattr(Path, 'unlink', original_unlink)
            monkeypatch.setattr(os, 'replace', original_replace)
            session.clear()
    session.shutdown()


def test_disconnect_stops_late_shorts_fallback_request(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    from threading import Event

    from youtube_strataread.workbench.shorts import YouTubeShortsClassifier

    browser = Browser()
    browser.handoff = True
    session = ContentSession(tmp_path / 'session', browser=browser)
    session.open()
    session.confirm('abcdefghijk', same_account=True,
        fetch=lambda *args: SubtitleResult('abcdefghijk', 'T', 'en', False, SRT))
    entered, release = Event(), Event()
    requests = []
    def page(request, cookies):
        requests.append(request.full_url)
        if len(requests) == 1:
            entered.set()
            assert release.wait(3)
        response=BytesIO(b'<html>Unclassified</html>')
        response.status=200
        response.geturl=lambda: request.full_url
        return response
    monkeypatch.setattr('youtube_strataread.workbench.shorts.open_page', page)
    classifier=YouTubeShortsClassifier()
    classifier.content_session=session
    with ThreadPoolExecutor() as pool:
        pending=pool.submit(classifier.classify,'abcdefghijk')
        assert entered.wait(2)
        try:
            session.clear()
        finally:
            release.set()
        with pytest.raises(ContentSessionError):
            pending.result()
    assert len(requests)==1
    session.shutdown()

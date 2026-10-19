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
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        with pytest.raises(ContentSessionError, match="旧请求"):
            session.accept(stale, result, "abcdefghijk")
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
        session.confirm("abcdefghijk", same_account=True, fetch=lambda *args: result)
        release.set()
        with pytest.raises(ContentSessionError, match="旧请求"):
            old.result()
    assert session.status()["state"] == "available"
    session.shutdown()

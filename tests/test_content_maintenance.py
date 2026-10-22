"""Unattended content-session maintenance through the session's public boundary.

Only the native browser, the external caption response and time are controlled.
"""

import pytest

from youtube_strataread.downloader.youtube import SubtitleResult
from youtube_strataread.workbench.content_session import ContentSession, ContentSessionError

SRT = "1\n00:00:00,000 --> 00:00:02,000\nA real caption response.\n"
VIDEO = "abcdefghijk"


class Browser:
    def __init__(self):
        self.running = False
        self.signed_in = True
        self.starts = 0
        self.snapshots = 0

    def start(self, profile):
        self.running = True
        self.starts += 1

    def command(self, command, arguments):
        return {}

    def snapshot(self):
        self.running = False
        if not self.signed_in:
            raise ContentSessionError("user", "请在 YouTube 页面完成登录。")
        self.snapshots += 1
        return f"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsession-{self.snapshots}\n"

    def release_resources(self, observation=None):
        self.close()

    def close(self):
        self.running = False


def caption(path_seen=None, *, signed_out_for=()):
    def fetch(url, path):
        text = path.read_text()
        if path_seen is not None:
            path_seen.append(text)
        if any(value in text for value in signed_out_for):
            raise ContentSessionError("user", "YouTube 要求完成真人验证，请打开服务器浏览器。")
        return SubtitleResult(VIDEO, "Title", "en", False, SRT)
    return fetch


@pytest.fixture
def connected(tmp_path):
    clock = [1_000_000.0]
    browser = Browser()
    session = ContentSession(tmp_path / "session", browser=browser, now=lambda: clock[0], unattended=True)
    session.open()
    session.confirm(VIDEO, same_account=True, fetch=caption())
    yield session, browser, clock
    session.shutdown()


def test_signed_out_snapshot_is_maintained_unattended_and_new_consumers_use_the_new_snapshot(connected):
    session, browser, clock = connected
    with pytest.raises(ContentSessionError) as failure:
        session.acquire(VIDEO, fetch=caption(signed_out_for=("session-1",)))
    assert failure.value.kind == "session"
    assert not session.acquisition_ready()
    session.tick()
    assert browser.running and browser.starts == 2
    status = session.status()
    assert status["state"] == "maintaining"
    assert status["maintenance"]["state"] == "running"
    assert status["maintenance"]["deadline_at"] == clock[0] + 180
    clock[0] += 60
    session.tick()
    assert not browser.running
    seen = []
    session.acquire(VIDEO, fetch=caption(seen, signed_out_for=("session-1",)))
    assert "session-2" in seen[0]
    assert session.status()["state"] == "available"
    assert session.status()["maintenance"]["used"] == 1


def lose_session(session, value):
    with pytest.raises(ContentSessionError) as failure:
        session.acquire(VIDEO, fetch=caption(signed_out_for=(value,)))
    return failure.value


def test_runs_are_capped_at_three_minutes_and_two_per_rolling_day_across_restart(tmp_path, connected):
    session, browser, clock = connected
    lose_session(session, "session-1")
    session.tick()
    first = clock[0]
    clock[0] += 181
    session.tick()
    assert not browser.running and browser.snapshots == 1
    status = session.status()["maintenance"]
    assert status["state"] == "due" and status["used"] == 1 and "3 分钟" in status["reason"]
    clock[0] += 3600
    session.tick()
    assert browser.starts == 3
    clock[0] += 60
    session.tick()
    session.acquire(VIDEO, fetch=caption(signed_out_for=("session-1",)))
    lose_session(session, "session-2")
    session.shutdown()
    restarted = ContentSession(tmp_path / "session", browser=browser, now=lambda: clock[0], unattended=True)
    assert not restarted.acquisition_ready(), "the queue may ask before the first heartbeat"
    restarted.tick()
    assert browser.starts == 3 and not browser.running
    status = restarted.status()
    assert status["state"] == "maintaining"
    assert status["maintenance"] | {"reason": None} == {
        "state": "waiting_budget", "basis": "signed_out", "reason": None, "next_at": first + 86400,
        "deadline_at": None, "used": 2, "limit": 2}
    assert not restarted.acquisition_ready()
    clock[0] = first + 86400 + 1
    restarted.tick()
    assert browser.starts == 4
    restarted.shutdown()


def test_run_interrupted_by_host_exit_stays_spent(tmp_path, connected):
    session, browser, clock = connected
    lose_session(session, "session-1")
    session.tick()
    session.shutdown()
    restarted = ContentSession(tmp_path / "session", browser=browser, now=lambda: clock[0], unattended=True)
    assert restarted.status()["maintenance"] | {"reason": None} == {
        "state": "due", "basis": "signed_out", "reason": None, "next_at": None,
        "deadline_at": None, "used": 1, "limit": 2}
    restarted.shutdown()


def test_refused_maintained_snapshot_hands_off_without_more_automatic_runs(connected):
    session, browser, clock = connected
    lose_session(session, "session-1")
    session.tick()
    clock[0] += 60
    session.tick()
    lost = lose_session(session, "session-2")
    assert lost.kind == "session"
    status = session.status()
    assert status["state"] == "needs_user" and status["maintenance"]["state"] == "handoff"
    assert "密码或验证码" in status["reason"]
    for _ in range(3):
        clock[0] += 86400
        session.tick()
    assert browser.starts == 2 and not session.acquisition_ready()
    session.open()
    clock[0] += 14 * 60
    session.browser_command("click", {"x": 1, "y": 1})
    clock[0] += 14 * 60
    session.tick()
    assert browser.running, "a person's handoff is not limited to three minutes"
    clock[0] += 61
    session.tick()
    assert not browser.running
    session.open()
    session.confirm(VIDEO, same_account=True, fetch=caption())
    assert session.status()["state"] == "available" and session.acquisition_ready()


def test_signed_out_profile_during_maintenance_hands_off_immediately(connected):
    session, browser, clock = connected
    lose_session(session, "session-1")
    browser.signed_in = False
    session.tick()
    clock[0] += 60
    session.tick()
    assert session.status()["maintenance"]["state"] == "handoff"
    assert not browser.running and browser.starts == 2


def test_disconnect_during_maintenance_cannot_revive_identity(connected):
    session, browser, clock = connected
    lose_session(session, "session-1")
    session.tick()
    session.clear()
    clock[0] += 60
    session.tick()
    status = session.status()
    assert status["state"] == "disconnected" and not status["snapshot_present"]
    assert not browser.running and browser.snapshots == 1
    clock[0] += 86400
    session.tick()
    assert browser.starts == 2


@pytest.mark.parametrize("kind", ["network", "rate_limit", "no_captions", "maintenance", "unknown"])
def test_ordinary_failures_never_start_the_browser(connected, kind):
    session, browser, clock = connected

    def fetch(url, path):
        raise ContentSessionError(kind, "ordinary failure")

    def storage(url, path):
        raise OSError("disk")

    with pytest.raises(ContentSessionError):
        session.acquire(VIDEO, fetch=fetch)
    with pytest.raises(OSError):
        session.acquire(VIDEO, fetch=storage)
    clock[0] += 86400
    session.tick()
    assert browser.starts == 1 and session.status()["maintenance"]["state"] == "idle"
    assert session.acquisition_ready()


class Resources:
    def __init__(self):
        self.waiting = True

    def status(self):
        return {"browser_waiting": self.waiting, "captions_waiting": False, "release_browser": False,
                "reason": "服务器可用内存不足 1.25 GiB，等待打开登录画面。" if self.waiting else None}

    def require_browser_start(self):
        if self.waiting:
            raise ContentSessionError("resource", self.status()["reason"])

    def require_captions(self):
        pass

    def browser_released(self):
        pass


class Requests:
    cooldown_until = 0

    def snapshot(self):
        return {"cooldown_until": self.cooldown_until}


def test_waits_for_cooldown_and_resources_with_reason_and_next_time(tmp_path):
    clock = [1_000_000.0]
    browser, resources, requests = Browser(), Resources(), Requests()
    resources.waiting = False
    session = ContentSession(tmp_path / "session", browser=browser, now=lambda: clock[0],
                             resources=resources, requests=requests, unattended=True)
    session.open()
    session.confirm(VIDEO, same_account=True, fetch=caption())
    lose_session(session, "session-1")
    requests.cooldown_until = clock[0] + 900
    resources.waiting = True
    session.tick()
    status = session.status()["maintenance"]
    assert (status["state"], status["next_at"]) == ("waiting_cooldown", clock[0] + 900)
    assert "冷却" in status["reason"]
    clock[0] += 901
    session.tick()
    status = session.status()["maintenance"]
    assert (status["state"], status["next_at"]) == ("waiting_resources", clock[0] + 60)
    assert "1.25 GiB" in status["reason"] and browser.starts == 1
    resources.waiting = False
    clock[0] += 30
    session.tick()
    assert browser.starts == 1
    clock[0] += 31
    session.tick()
    assert browser.starts == 2
    session.shutdown()


def test_response_cookie_rotation_is_maintained_and_older_results_cannot_overwrite(connected):
    session, browser, clock = connected

    def rotating(url, path):
        path.write_text(path.read_text().replace("session-1", "rotated-by-response"))
        return SubtitleResult(VIDEO, "Title", "en", False, SRT)

    def unparsable_save(url, path):
        path.write_text("CookieJar save")
        return SubtitleResult(VIDEO, "Title", "en", False, SRT)

    session.acquire(VIDEO, fetch=unparsable_save)
    assert session.status()["maintenance"]["basis"] is None
    session.acquire(VIDEO, fetch=rotating)
    assert session.status()["maintenance"]["basis"] == "rotation"
    assert session.acquisition_ready()
    session.tick()
    with session.snapshot() as stale:
        clock[0] += 60
        session.tick()
        with pytest.raises(ContentSessionError, match="旧请求"):
            session.accept(stale, SubtitleResult(VIDEO, "Title", "en", False, SRT), VIDEO)
    seen = []
    session.acquire(VIDEO, fetch=caption(seen))
    assert "session-2" in seen[0] and "rotated-by-response" not in seen[0]
    assert session.status()["maintenance"]["state"] == "idle"

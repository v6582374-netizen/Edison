from __future__ import annotations

import io

import pytest

from youtube_strataread.workbench import session_cookies


def test_header_and_cookies_txt_become_youtube_only_cookie_files():
    header = session_cookies.to_cookie_file("Cookie: SID=abc; __Secure-3PSID=def; bad name=x")
    assert "\tSID\tabc" in header and "\t__Secure-3PSID\tdef" in header and "bad" not in header
    exported = "\n".join([
        ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tv",
        "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t0\tSAPISID\tw",
        ".bank.example\tTRUE\t/\tTRUE\t0\tsession\tsecret",
    ])
    kept = session_cookies.to_cookie_file(exported)
    assert "LOGIN_INFO" in kept and "SAPISID" in kept and "bank" not in kept
    with pytest.raises(ValueError):
        session_cookies.to_cookie_file(".bank.example\tTRUE\t/\tTRUE\t0\tsession\tsecret")


def page(signed_in: bool, status: str) -> io.BytesIO:
    body = f'"LOGGED_IN":{"true" if signed_in else "false"} "playabilityStatus":{{"status":"{status}"}}'
    return io.BytesIO(body.encode())


@pytest.mark.parametrize(("signed_in", "status", "usable"), [
    (True, "OK", True), (True, "LOGIN_REQUIRED", False), (False, "OK", False),
])
def test_usable_means_signed_in_and_not_bot_checked(monkeypatch, tmp_path, signed_in, status, usable):
    monkeypatch.setattr(session_cookies, "open_page", lambda *a, **k: page(signed_in, status))
    assert session_cookies.verify(tmp_path / "c.txt", "abcdefghijk")["usable"] is usable


def test_login_required_is_exposed_separately_from_cookie_usability(monkeypatch, tmp_path):
    monkeypatch.setattr(session_cookies, "open_page", lambda *a, **k: page(False, "LOGIN_REQUIRED"))
    assert session_cookies.verify(tmp_path / "c.txt", "abcdefghijk")["blocked"] is True


def test_failed_replacement_keeps_the_working_session(monkeypatch, tmp_path):
    path = tmp_path / "cookies.txt"
    path.write_text("working")
    monkeypatch.setattr(session_cookies, "open_page", lambda *a, **k: page(False, "OK"))
    assert session_cookies.replace(path, "SID=new", "abcdefghijk")["saved"] is False
    assert path.read_text() == "working"
    assert not path.with_name("cookies.txt.candidate").exists()
    monkeypatch.setattr(session_cookies, "open_page", lambda *a, **k: page(True, "OK"))
    assert session_cookies.replace(path, "SID=new", "abcdefghijk")["saved"] is True
    assert "\tSID\tnew" in path.read_text() and path.stat().st_mode & 0o777 == 0o600


def test_verified_cookie_replacement_retries_only_session_blocked_videos(monkeypatch, tmp_path):
    from test_workbench_library import candidate

    from coworker.server import youtube
    from youtube_strataread.workbench.cutover import CutoverHold
    from youtube_strataread.workbench.workspace import LocalWorkspace

    workspace = LocalWorkspace.open(tmp_path / 'workspace')
    workspace.add_candidate(candidate('blocked0001'))
    workspace.add_candidate(candidate('other00001'))
    workspace.set_preparation_state('blocked0001', 'failed', session_cookies.SESSION_BLOCKED_REASON)
    workspace.set_preparation_state('other00001', 'failed', 'unrelated failure')
    workbench = youtube.YouTubeWorkbench.__new__(youtube.YouTubeWorkbench)
    workbench.workspace = workspace
    workbench.cutover = CutoverHold(workspace)
    workbench.managed_content = False  # The legacy cookie entry is retired only in managed mode.
    path = tmp_path / 'cookies.txt'
    monkeypatch.setattr(youtube, 'youtube_cookie_file', lambda: path)
    monkeypatch.setattr(session_cookies, 'replace', lambda *args: {'saved': True})
    result = workbench.dispatch('session_cookies.replace', {'text': 'valid'})
    assert result['ok']
    assert result['result']['resumed'] == 1
    assert workspace.asset('blocked0001')['preparation_state'] == 'queued'
    assert workspace.asset('other00001')['preparation_state'] == 'failed'

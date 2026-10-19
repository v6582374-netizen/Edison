"""Real browser protocol; no Google credentials or platform requests."""

import contextlib
import os

import pytest

from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.session_browser import ServerBrowser


def test_real_browser_remote_screen_input_and_profile_survive_close(tmp_path, monkeypatch):
    playwright = pytest.importorskip(
        "playwright.sync_api", reason="Install the browser extra for real Chromium coverage"
    )
    from pathlib import Path

    with playwright.sync_playwright() as installed:
        executable = os.environ.get("EDISON_CONTENT_CHROMIUM") or installed.chromium.executable_path
        if not Path(executable).is_file():
            pytest.skip(
                "Real Chromium missing: python -m playwright install chromium; this is not browser acceptance"
            )
    monkeypatch.setenv("EDISON_CONTENT_BROWSER_ENABLED", "1")
    # Explicit local page is a protocol test, never a content-login proof.
    page = tmp_path / "page.html"
    page.write_text(
        '<html><body><input autofocus aria-label="Test"><script>document.cookie="test=value"</script></body></html>'
    )
    browser = ServerBrowser(start_url=page.as_uri(), headless=True)
    browser.start(tmp_path / "profile")
    screen = browser.command("screen", {})
    assert screen["image"].startswith("iVBOR")
    assert screen["width"] == 1100
    browser.command("text", {"text": "hello"})
    browser.command("key", {"key": "Tab"})
    with pytest.raises(ContentSessionError, match="YouTube"):
        browser.snapshot()
    browser.close()
    browser.start(tmp_path / "profile")
    assert browser.command("screen", {})["image"].startswith("iVBOR")
    browser.close()


def test_close_kills_orphaned_descendants_even_when_driver_already_exited(tmp_path):
    import signal
    import subprocess
    import sys
    import time

    marker = tmp_path / "ticks"
    child = (
        "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path("
        + repr(str(marker))
        + ");\nwhile True:\n p.write_text(str(time.monotonic())); time.sleep(.03)"
    )
    launcher = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);"
    process = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    browser = ServerBrowser()
    browser.process = process
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        process.wait(timeout=5)
        browser.close()
        time.sleep(0.1)
        stopped_at = marker.read_text()
        time.sleep(0.15)
        assert marker.read_text() == stopped_at
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def test_timed_out_transport_cannot_return_a_previous_commands_response(tmp_path):
    import subprocess
    import sys

    browser = ServerBrowser(command_timeout=0.05)
    worker = tmp_path / "delayed.py"
    worker.write_text("""import json,sys,time
for line in sys.stdin:
    command=json.loads(line)['command']
    time.sleep(.2)
    print(json.dumps({'ok':True,'result':{'command':command}}),flush=True)
""")
    browser.process = subprocess.Popen(
        [sys.executable, str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        with pytest.raises(ContentSessionError, match="超时"):
            browser.command("screen", {})
        with pytest.raises(ContentSessionError, match="退出"):
            browser.command("snapshot", {})
    finally:
        browser.close()

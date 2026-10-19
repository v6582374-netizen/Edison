"""Real browser protocol; no Google credentials or platform requests."""

import contextlib
import os

import pytest

from youtube_strataread.workbench.content_session import ContentSessionError
from youtube_strataread.workbench.session_browser import ServerBrowser


def test_real_native_browser_input_profile_flush_and_reopen(tmp_path, monkeypatch):
    import http.server
    import shutil
    import sys
    import threading
    import time

    from yt_dlp.cookies import extract_cookies_from_browser

    executable = os.environ.get("EDISON_CONTENT_CHROMIUM")
    display = os.environ.get("EDISON_CONTENT_X11VNC") or shutil.which("x11vnc")
    if sys.platform != "linux" or not executable or not display or not shutil.which("Xvfb"):
        pytest.skip("Native Linux Chrome/Xvfb/x11vnc required; not Google login evidence")
    phases = {}

    @contextlib.contextmanager
    def phase(name):
        started = time.monotonic()
        try:
            yield
        finally:
            phases[name] = round(time.monotonic() - started, 3)

    seen = threading.Event()
    typed = threading.Event()
    cookie_received = threading.Event()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/typed?value=hello"):
                typed.set()
                if "test=value" in self.headers.get("Cookie", ""):
                    cookie_received.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "test=value; Max-Age=3600; Path=/")
            self.end_headers()
            self.wfile.write(b"<input autofocus oninput=\"fetch('/typed?value='+this.value)\">")
            seen.set()

        def log_message(self, *_):
            pass

    http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setenv("EDISON_CONTENT_BROWSER_ENABLED", "1")
    browser = ServerBrowser(start_url=f"http://127.0.0.1:{http.server_port}/")
    try:
        with phase("first_start"):
            browser.start(tmp_path / "profile")
        assert seen.wait(10)
        time.sleep(1)
        with phase("first_screen"):
            screen = browser.command("screen", {})
        assert screen["image"].startswith("iVBOR")
        assert screen["width"] == 1100
        assert "origin" not in screen  # visible address bar, never guessed DOM origin
        browser.command("text", {"text": "hello"})
        assert typed.wait(5)
        assert cookie_received.wait(5)
        # Local fixture cookies must not become a YouTube snapshot.
        with phase("snapshot_and_close"), pytest.raises(ContentSessionError) as excluded:
            browser.snapshot()
        assert excluded.value.kind == "user", (
            "fixture domain exclusion must not hide a maintenance/timeout failure"
        )
        assert not browser.alive
        with phase("independent_profile_read"):
            cookies = extract_cookies_from_browser("chrome", profile=str(tmp_path / "profile"))
        assert any(c.name == "test" and c.value == "value" for c in cookies)
        with phase("reopen"):
            browser.start(tmp_path / "profile")
            assert browser.command("screen", {})["image"].startswith("iVBOR")
    finally:
        with phase("final_close"):
            browser.close()
        http.shutdown()
        print("native_phase_seconds", phases)


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


def test_incomplete_native_cookie_decryption_is_maintenance_not_a_snapshot(tmp_path, monkeypatch):
    import yt_dlp.cookies

    from youtube_strataread.workbench.session_browser import profile_snapshot

    def extraction(browser, *, profile, logger):
        logger.warning("synthetic sensitive detail", only_once=True)
        return []

    monkeypatch.setattr(yt_dlp.cookies, "extract_cookies_from_browser", extraction)
    with pytest.raises(ContentSessionError) as failure:
        profile_snapshot(tmp_path)
    assert failure.value.kind == "maintenance"
    assert "synthetic sensitive detail" not in str(failure.value)

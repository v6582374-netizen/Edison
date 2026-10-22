"""Real browser protocol; no Google credentials or platform requests."""

import contextlib
import errno
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
    from urllib.parse import parse_qs, urlsplit

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

    typed_value = "hello:/中 !@#%+"
    seen = threading.Event()
    typed = threading.Event()
    cookie_received = threading.Event()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if parse_qs(urlsplit(self.path).query).get("value") == [typed_value]:
                typed.set()
                if "test=value" in self.headers.get("Cookie", ""):
                    cookie_received.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "test=value; Max-Age=3600; Path=/")
            self.end_headers()
            self.wfile.write(
                b"<input autofocus oninput=\"fetch('/typed?value='+encodeURIComponent(this.value))\">"
            )
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
        assert_private_native_listeners(browser)
        assert seen.wait(10)
        time.sleep(1)
        with phase("first_screen"):
            screen = browser.command("screen", {})
        assert screen["image"].startswith("iVBOR")
        assert screen["width"] == 1100
        assert "origin" not in screen  # visible address bar, never guessed DOM origin
        browser.command("text", {"text": typed_value})
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
            assert_private_native_listeners(browser)
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


@pytest.mark.parametrize("partial", [False, True])
def test_timed_out_transport_cannot_return_a_previous_commands_response(tmp_path, monkeypatch, partial):
    import subprocess
    import sys

    from youtube_strataread.workbench.resource_governor import LinuxResources, ResourceObservation
    monkeypatch.setattr(LinuxResources, "observe", lambda self, **_: ResourceObservation(2*1024**3, 0, 0, True))
    browser = ServerBrowser(command_timeout=0.05)
    worker = tmp_path / "delayed.py"
    worker.write_text("""import json,sys,time
for line in sys.stdin:
    command=json.loads(line)['command']
    response=json.dumps({'ok':True,'result':{'command':command}})
    split=5 if sys.argv[1]=='partial' else 0
    sys.stdout.write(response[:split]);sys.stdout.flush()
    time.sleep(.2)
    print(response[split:],flush=True)
""")
    browser.process = subprocess.Popen(
        [sys.executable, str(worker), "partial" if partial else "silent"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        with pytest.raises(ContentSessionError, match="超时") as error:
            browser.command("screen", {})
        assert error.value.kind == "maintenance"
        assert browser.last_resource_release is None
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


def assert_private_native_listeners(browser):
    """Observe the real process group through ss, independently of launch flags."""
    import subprocess
    from pathlib import Path

    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            with contextlib.suppress(ProcessLookupError):
                if os.getpgid(int(entry.name)) == browser.process.pid:
                    members.append(int(entry.name))
    tcp = subprocess.check_output(["ss", "-H", "-ltnp"], text=True)
    unix = subprocess.check_output(["ss", "-H", "-lxnp"], text=True)
    assert members
    assert not any(f"pid={pid}," in tcp for pid in members), tcp
    assert any(
        "vnc.sock" in line and any(f"pid={pid}," in line for pid in members)
        for line in unix.splitlines()
    ), unix


@pytest.mark.parametrize("family", ["ipv4", "ipv6"])
def test_native_handoff_rejects_tcp_listener_and_reaps_processes(tmp_path, monkeypatch, family):
    import shutil
    import socket
    import sys
    from pathlib import Path

    executable = os.environ.get("EDISON_CONTENT_CHROMIUM")
    display = os.environ.get("EDISON_CONTENT_X11VNC") or shutil.which("x11vnc")
    if sys.platform != "linux" or not executable or not display or not shutil.which("Xvfb"):
        pytest.skip("Native Linux Chrome/Xvfb/x11vnc required")
    # Deliberately misconfigure the real desktop executable at its system boundary.
    # A loopback listener is also forbidden; no public interface is requested.
    address = ("127.0.0.1", 0) if family == "ipv4" else ("::1", 0)
    with socket.socket(socket.AF_INET if family == "ipv4" else socket.AF_INET6) as probe:
        probe.bind(address)
        port = probe.getsockname()[1]
    pid_file = tmp_path / "display.pid"
    wrapper = tmp_path / "misconfigured-vnc"
    wrapper.write_text(
        f"#!{shutil.which('python3')}\n"
        "import os,sys,pathlib\n"
        "args=sys.argv[1:]\n"
        f"family={family!r}\n"
        "if family == 'ipv4':\n"
        "    args.remove('-noipv4')\n"
        "    args.extend(['-listen','127.0.0.1'])\n"
        "else:\n"
        "    args.remove('-no6')\n"
        "    args.remove('-noipv6')\n"
        "    args.extend(['-6','-listen6','::1'])\n"
        f"args[args.index('-rfbport')+1]={str(port)!r}\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        f"os.execv({display!r}, [{display!r}, *args])\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("EDISON_CONTENT_BROWSER_ENABLED", "1")
    monkeypatch.setenv("EDISON_CONTENT_X11VNC", str(wrapper))
    browser = ServerBrowser(start_url="about:blank")
    try:
        with pytest.raises(ContentSessionError) as failure:
            browser.start(tmp_path / "profile")
        assert failure.value.kind == "maintenance"
        assert not browser.alive
        assert not Path("/proc", pid_file.read_text()).exists()
        with socket.socket(socket.AF_INET if family == "ipv4" else socket.AF_INET6) as check:
            check.settimeout(1)
            assert check.connect_ex((address[0], port)) == errno.ECONNREFUSED
    finally:
        browser.close()


@pytest.mark.parametrize("detection", ["idle", "command", "stalled_close"])
def test_native_handoff_retires_listener_opened_after_start(tmp_path, monkeypatch, detection):
    import json
    import shutil
    import signal
    import socket
    import sys
    import time
    from pathlib import Path

    executable = os.environ.get("EDISON_CONTENT_CHROMIUM")
    display = os.environ.get("EDISON_CONTENT_X11VNC") or shutil.which("x11vnc")
    if sys.platform != "linux" or not executable or not display or not shutil.which("Xvfb"):
        pytest.skip("Native Linux Chrome/Xvfb/x11vnc required")
    info_file = tmp_path / "desktop.json"
    trigger = tmp_path / "enable-listener"
    listening = tmp_path / "listening"
    wrapper = tmp_path / "controlled-vnc"
    wrapper.write_text(
        f"#!{shutil.which('python3')}\n"
        "import os,sys,pathlib,json,socket,time\n"
        "sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.set_inheritable(True)\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        f"    while not pathlib.Path({str(trigger)!r}).exists(): time.sleep(.01)\n"
        "    sock.listen(); sock.close()\n"
        f"    pathlib.Path({str(listening)!r}).touch()\n"
        "    os._exit(0)\n"
        "info={'pid':os.getpid(), 'port':sock.getsockname()[1]}\n"
        f"pathlib.Path({str(info_file)!r}).write_text(json.dumps(info))\n"
        f"os.execv({display!r}, [{display!r}, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("EDISON_CONTENT_BROWSER_ENABLED", "1")
    monkeypatch.setenv("EDISON_CONTENT_X11VNC", str(wrapper))
    chrome_pid = None
    chrome_pid_file = tmp_path / "chrome.pid"
    if detection == "stalled_close":
        chrome_wrapper = tmp_path / "controlled-chrome"
        chrome_wrapper.write_text(
            f"#!{shutil.which('python3')}\n"
            "import os,sys,pathlib\n"
            f"pathlib.Path({str(chrome_pid_file)!r}).write_text(str(os.getpid()))\n"
            f"os.execv({executable!r}, [{executable!r}, *sys.argv[1:]])\n"
        )
        chrome_wrapper.chmod(0o700)
        monkeypatch.setenv("EDISON_CONTENT_CHROMIUM", str(chrome_wrapper))
    browser = ServerBrowser(start_url="about:blank")
    try:
        browser.start(tmp_path / "profile")
        assert_private_native_listeners(browser)
        info = json.loads(info_file.read_text())
        # The real x11vnc process inherits a bound, non-listening TCP socket.
        # A fork sharing that same kernel socket starts listening only now.
        # This exercises live proc ownership without mocking process/socket data.
        if detection == "stalled_close":
            chrome_pid = int(chrome_pid_file.read_text())
            os.kill(chrome_pid, signal.SIGSTOP)
        trigger.touch()
        deadline = time.monotonic() + 3
        while not listening.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert listening.exists()
        if detection == "stalled_close":
            # Privacy teardown cannot wait for Chrome's normal profile shutdown.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with socket.socket() as check:
                    check.settimeout(0.1)
                    if check.connect_ex(("127.0.0.1", info["port"])) == errno.ECONNREFUSED:
                        break
                time.sleep(0.1)
            else:
                pytest.fail("desktop TCP listener outlived the privacy failure")
            assert browser.alive  # Chrome shutdown is still waiting, by construction.
            assert (
                Path("/proc", str(chrome_pid), "stat").read_text().rsplit(")", 1)[1].split()[0]
                == "T"
            )
            os.kill(chrome_pid, signal.SIGCONT)
            chrome_pid = None
        elif detection == "idle":
            # No browser request may be needed to retire an exposed idle desktop.
            browser.process.wait(timeout=5)
            assert not browser.alive
            with socket.socket() as check:
                check.settimeout(1)
                assert check.connect_ex(("127.0.0.1", info["port"])) == errno.ECONNREFUSED
        else:
            with pytest.raises(ContentSessionError) as failure:
                browser.command("screen", {})
            assert failure.value.kind == "maintenance"
        browser.close()
        assert not Path("/proc", str(info["pid"])).exists()
        with socket.socket() as check:
            check.settimeout(1)
            assert check.connect_ex(("127.0.0.1", info["port"])) == errno.ECONNREFUSED
    finally:
        if chrome_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(chrome_pid, signal.SIGCONT)
        browser.close()


def test_idle_native_driver_releases_pressure_without_client_requests(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import sys
    import time
    from youtube_strataread.workbench import session_browser
    executable = os.environ.get('EDISON_CONTENT_CHROMIUM')
    display = os.environ.get('EDISON_CONTENT_X11VNC') or shutil.which('x11vnc')
    if sys.platform != 'linux' or not executable or not display or not shutil.which('Xvfb'):
        pytest.skip('Native Linux Chrome/Xvfb/x11vnc required')
    trigger = tmp_path/'idle-pressure'
    # Control only the child kernel observation; actual native browser/display
    # and process lifecycle remain real. No screen command or host tick occurs.
    driver = f'''import runpy,sys
from pathlib import Path
from youtube_strataread.workbench.resource_governor import LinuxResources,ResourceObservation
original=LinuxResources.observe
def observe(self,**kwargs):
    state=original(self,**kwargs)
    if Path({str(trigger)!r}).exists():
        return ResourceObservation(state.available,1153433600,state.browser_swap,state.constrained)
    return state
LinuxResources.observe=observe
module=sys.argv.pop(1)
runpy.run_module(module,run_name="__main__")
'''
    original_popen = subprocess.Popen
    def native_driver(argv, **kwargs):
        if argv[1:3] == ['-m', session_browser.__name__]:
            argv = [sys.executable, '-c', driver, *argv[2:]]
        return original_popen(argv, **kwargs)
    monkeypatch.setattr(session_browser.subprocess, 'Popen', native_driver)
    browser = ServerBrowser(start_url='about:blank')
    try:
        browser.start(tmp_path/'profile')
        trigger.touch()
        deadline = time.monotonic()+10
        while browser.alive and time.monotonic()<deadline:
            time.sleep(.05)
        assert browser.resource_released
        assert not (tmp_path/'current.cookies').exists()
    finally:
        browser.close()

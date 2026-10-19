"""Ordinary native browser with a private authenticated remote desktop view.

The subprocess is the only Chromium profile writer. No debugging/VNC port is
opened, and neither screenshots nor input are persisted or logged. Its process
group is the lifecycle boundary for the resource governor to constrain.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from youtube_strataread.workbench.content_session import ContentSessionError


class ServerBrowser:
    def __init__(
        self,
        *,
        owner_fd=None,
        start_url="https://www.youtube.com/",
        headless=False,
        command_timeout=30,
    ):
        self.command_timeout = command_timeout
        self.headless = headless
        self.owner_fd = owner_fd
        self.start_url = start_url
        self.process = None

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def start(self, profile: Path):
        if os.environ.get("EDISON_CONTENT_BROWSER_ENABLED") != "1":
            raise ContentSessionError(
                "maintenance", "服务器内容浏览器尚未通过启用配置；请先完成资源准入。"
            )
        if os.name != "posix":
            raise ContentSessionError(
                "maintenance", "服务器内容浏览器需要支持进程组的 POSIX 服务环境。"
            )
        if self.process and self.process.poll() is None:
            return
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                __name__,
                str(profile),
                self.start_url,
                "headless" if self.headless else "headed",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
            bufsize=1,
            pass_fds=(self.owner_fd,) if self.owner_fd is not None else (),
        )
        try:
            self._read(timeout=45)
        except BaseException:
            self.close()
            raise

    def _read(self, timeout=30):
        if not select.select([self.process.stdout], [], [], timeout)[0]:
            self.close()
            raise ContentSessionError("maintenance", "服务器浏览器响应超时，请关闭后重新打开。")
        try:
            result = json.loads(self.process.stdout.readline())
        except (ValueError, OSError):
            self.close()
            raise ContentSessionError(
                "maintenance", "服务器浏览器已退出，请检查运行环境。"
            ) from None
        if not result.get("ok"):
            kind = result.get("kind", "maintenance")
            raise ContentSessionError(
                kind,
                "请在服务器 YouTube 页面完成登录及站点交接。"
                if kind == "user"
                else "浏览器操作参数无效，请刷新画面后重试。"
                if kind == "unknown"
                else "服务器浏览器操作失败，请检查浏览器安装、沙箱与存储权限。",
            )
        return result.get("result", {})

    def command(self, command, arguments):
        if not self.process or self.process.poll() is not None:
            raise ContentSessionError("maintenance", "服务器浏览器已退出，请重新打开。")
        try:
            self.process.stdin.write(
                json.dumps({"command": command, "arguments": arguments}) + "\n"
            )
            self.process.stdin.flush()
            return self._read(timeout=self.command_timeout)
        except ContentSessionError as error:
            if error.kind == "maintenance":
                self.close()
            raise
        except (BrokenPipeError, OSError):
            self.close()
            raise ContentSessionError("maintenance", "服务器浏览器连接已结束。") from None

    def snapshot(self):
        try:
            return self.command("snapshot", {})["cookies"]
        finally:
            self.close()

    def close(self):
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                process.stdin.close()  # graceful browser/profile flush on EOF
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=20)
            # Includes orphaned Chromium descendants even if the driver exited.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            until = time.monotonic() + 3
            while time.monotonic() < until:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            self.process = None


def profile_snapshot(profile):
    """Read a stopped browser's own profile; never claim cookies prove login."""
    from yt_dlp.cookies import extract_cookies_from_browser

    class QuietLogger:
        failed = False

        def debug(self, *_, **kwargs):
            pass

        def info(self, message, **kwargs):
            if "could not be decrypted" in message:
                self.failed = True

        def warning(self, *_, **kwargs):
            self.failed = True

        def error(self, *_, **kwargs):
            self.failed = True

    logger = QuietLogger()
    jar = extract_cookies_from_browser("chrome", profile=str(profile), logger=logger)
    if logger.failed:
        raise ContentSessionError(
            "maintenance", "浏览器会话读取不完整，请检查服务身份和系统密钥存储。"
        )
    rows = ["# Netscape HTTP Cookie File"]
    for cookie in jar:
        domain = cookie.domain
        if not any(
            domain.lstrip(".") == allowed or domain.lstrip(".").endswith("." + allowed)
            for allowed in ("youtube.com", "google.com")
        ):
            continue
        values = [
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            cookie.path,
            "TRUE" if cookie.secure else "FALSE",
            str(max(0, int(cookie.expires or 0))),
            cookie.name,
            cookie.value,
        ]
        if any("\t" in value or "\n" in value or "\r" in value for value in values):
            raise ContentSessionError("maintenance", "浏览器会话格式无法安全读取。")
        rows.append("\t".join(values))
    if len(rows) == 1:
        raise ContentSessionError("user", "请打开服务器浏览器并完成 YouTube 登录及站点交接。")
    return "\n".join(rows) + "\n"


def _verify_private_display(process, socket_path):
    """Fail closed unless this live Linux desktop owns only its UNIX listener."""
    if process.poll() is not None:
        raise RuntimeError("native desktop exited")
    root = Path("/proc") / str(process.pid)
    sockets = set()
    for descriptor in (root / "fd").iterdir():
        try:
            target = descriptor.readlink().as_posix()
        except FileNotFoundError:
            continue  # an accepted connection may close during inspection
        if target.startswith("socket:[") and target.endswith("]"):
            sockets.add(target[8:-1])
    for family in ("tcp", "tcp6"):
        # Unreadable/missing proc tables are not proof that the transport is private.
        for row in (root / "net" / family).read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] == "0A" and fields[9] in sockets:
                raise RuntimeError("native desktop opened a TCP listener")
    for row in (root / "net" / "unix").read_text().splitlines()[1:]:
        fields = row.split(maxsplit=7)
        if (
            len(fields) == 8
            and fields[6] in sockets
            and int(fields[3], 16) & 0x10000
            and fields[4] == "0001"
            and fields[7] == str(socket_path)
        ):
            return
    raise RuntimeError("native desktop private listener could not be verified")


def _serve(profile, start_url, runtime):
    import shutil

    from youtube_strataread.workbench.session_desktop import (
        Desktop,
        close_native_windows,
        wait_for_native_window,
    )

    def reply(result=None, kind=None):
        print(json.dumps({"ok": kind is None, "result": result, "kind": kind}), flush=True)

    executable = os.environ.get("EDISON_CONTENT_CHROMIUM") or shutil.which("google-chrome-stable")
    display_executable = os.environ.get("EDISON_CONTENT_X11VNC") or shutil.which("x11vnc")
    if not executable or not display_executable:
        raise RuntimeError("native browser dependencies unavailable")
    browser = subprocess.Popen(
        [
            executable,
            "--user-data-dir=" + str(profile),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1100,760",
            start_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    display = None
    desktop = None
    quit_requested = False

    def stop_desktop():
        # This process owns no durable state. Revoke its ingress immediately,
        # before any Chrome window/dialog or profile-flush wait can block cleanup.
        try:
            if display is not None:
                if display.poll() is None:
                    display.kill()
                display.wait(timeout=3)
        finally:
            if desktop is not None:
                desktop.__exit__(None, None, None)

    def stop_browser():
        nonlocal quit_requested
        if browser.poll() is None:
            if desktop is None or quit_requested:
                raise RuntimeError("desktop did not complete orderly close")
            quit_requested = True
            # Use the normal native window-manager close protocol. Keyboard
            # shortcuts/omnibox navigation can race focus and input dispatch.
            # Termination is emergency cleanup, never proof of profile flushing.
            close_native_windows(os.environ["DISPLAY"], alive=lambda: browser.poll() is None)
            browser.wait(timeout=15)
        if browser.returncode != 0:
            raise RuntimeError("browser exited unexpectedly")

    try:
        socket_path = runtime / "vnc.sock"
        display = subprocess.Popen(
            [
                display_executable,
                "-norc",
                "-no6",
                "-noipv6",
                "-noipv4",
                "-rfbportv6",
                "-1",
                "-display",
                os.environ["DISPLAY"],
                "-auth",
                os.environ["XAUTHORITY"],
                "-unixsock",
                str(socket_path),
                "-rfbport",
                "0",
                "-forever",
                "-shared",
                "-noxdamage",
                "-nopw",
                "-quiet",
                "-noclipboard",
                "-nosetclipboard",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        while not socket_path.exists():
            if (
                browser.poll() is not None
                or display.poll() is not None
                or time.monotonic() >= deadline
            ):
                raise RuntimeError("native desktop failed")
            time.sleep(0.05)
        desktop = Desktop(socket_path)
        _verify_private_display(display, socket_path)
        wait_for_native_window(os.environ["DISPLAY"], alive=lambda: browser.poll() is None)
        _verify_private_display(display, socket_path)
        reply()
        while True:
            # Readiness is not a lifetime guarantee: inspect idle desktops too.
            _verify_private_display(display, socket_path)
            if not select.select([sys.stdin], [], [], 1)[0]:
                continue
            line = sys.stdin.readline()
            if not line:
                break
            try:
                # A listener may appear while select waited for human input.
                _verify_private_display(display, socket_path)
                payload = json.loads(line)
                command, args = payload["command"], payload.get("arguments", {})
                if browser.poll() is not None:
                    raise RuntimeError("browser exited")
                if command == "screen":
                    reply(desktop.screen())
                elif command == "click":
                    desktop.click(args["x"], args["y"])
                    reply()
                elif command == "text":
                    desktop.text(args["text"])
                    reply()
                elif command == "key":
                    desktop.key(args["key"])
                    reply()
                elif command == "scroll":
                    desktop.scroll(args["delta"])
                    reply()
                elif command == "youtube":
                    desktop.navigate("https://www.youtube.com/")
                    reply()
                elif command == "snapshot":
                    stop_desktop()
                    stop_browser()
                    reply({"cookies": profile_snapshot(profile)})
                    break
                else:
                    raise ValueError("unsupported command")
            except ContentSessionError as error:
                reply(kind=error.kind)
                if command == "snapshot":
                    break
            except (ValueError, KeyError, TypeError, OverflowError):
                reply(kind="unknown")
                if command == "snapshot":
                    break
            except Exception:
                # A truncated/invalid/timed-out RFB stream is permanently
                # poisoned. Retire the entire driver before another request.
                reply(kind="maintenance")
                break
    finally:
        try:
            stop_desktop()
        finally:
            try:
                stop_browser()
            finally:
                if browser.poll() is None:
                    browser.terminate()
                    try:
                        browser.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        browser.kill()
                        browser.wait(timeout=3)


def _display(root):
    """Dedicated authenticated X server in the same browser process group."""
    import secrets
    import shutil
    import tempfile
    from contextlib import contextmanager

    @contextmanager
    def running():
        # UNIX socket paths are short even when the stable profile path is long.
        runtime = Path(tempfile.mkdtemp(prefix="edison-display-", dir="/tmp"))
        display = ":" + str(1000 + secrets.randbelow(50000))
        authority = runtime / "Xauthority"
        try:
            subprocess.run(
                [
                    "xauth",
                    "-f",
                    str(authority),
                    "add",
                    display,
                    "MIT-MAGIC-COOKIE-1",
                    secrets.token_hex(16),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            os.chmod(authority, 0o600)
            server = subprocess.Popen(
                [
                    "Xvfb",
                    display,
                    "-screen",
                    "0",
                    "1100x760x24",
                    "-nolisten",
                    "tcp",
                    "-auth",
                    str(authority),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                socket = Path("/tmp/.X11-unix") / ("X" + display[1:])
                deadline = time.monotonic() + 5
                while not socket.exists():
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("display startup failed")
                    time.sleep(0.05)
                os.environ.update(DISPLAY=display, XAUTHORITY=str(authority))
                yield runtime
            finally:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)
        finally:
            shutil.rmtree(runtime)

    return running()


if __name__ == "__main__":
    try:
        os.umask(0o077)
        if sys.platform != "linux" or sys.argv[3] == "headless":
            raise RuntimeError("native human handoff requires a Linux display")
        with _display(Path(sys.argv[1]).parent) as runtime:
            _serve(Path(sys.argv[1]), sys.argv[2], runtime)
    except Exception:
        print(json.dumps({"ok": False, "kind": "maintenance"}), flush=True)

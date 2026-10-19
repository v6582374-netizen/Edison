"""Private-pipe browser driver with an authenticated Web remote view.

The subprocess is the only Chromium profile writer. No debugging/VNC port is
opened, and neither screenshots nor input are persisted or logged. Its process
group is the lifecycle boundary for the resource governor to constrain.
"""

from __future__ import annotations

import base64
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
    def __init__(self, *, owner_fd=None, start_url="https://www.youtube.com/", headless=False):
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
            raise ContentSessionError("maintenance", "服务器浏览器响应超时，请关闭后重新打开。")
        try:
            result = json.loads(self.process.stdout.readline())
        except (ValueError, OSError):
            raise ContentSessionError(
                "maintenance", "服务器浏览器已退出，请检查运行环境。"
            ) from None
        if not result.get("ok"):
            kind = result.get("kind", "maintenance")
            raise ContentSessionError(
                kind,
                "请在服务器 YouTube 页面完成登录及站点交接。"
                if kind == "user"
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
            return self._read()
        except (BrokenPipeError, OSError):
            raise ContentSessionError("maintenance", "服务器浏览器连接已结束。") from None

    def snapshot(self):
        return self.command("snapshot", {})["cookies"]

    def close(self):
        process = self.process
        if not process:
            return
        try:
            if process.poll() is None:
                process.stdin.close()  # graceful browser/profile flush on EOF
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=8)
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


def _cookies(context):
    rows = ["# Netscape HTTP Cookie File"]
    for cookie in context.cookies():
        domain = cookie["domain"]
        if not any(
            domain.lstrip(".") == allowed or domain.lstrip(".").endswith("." + allowed)
            for allowed in ("youtube.com", "google.com")
        ):
            continue
        values = [
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            cookie["path"],
            "TRUE" if cookie["secure"] else "FALSE",
            str(max(0, int(cookie["expires"]))),
            cookie["name"],
            cookie["value"],
        ]
        if any("\t" in value or "\n" in value or "\r" in value for value in values):
            continue
        rows.append("\t".join(values))
    return "\n".join(rows) + "\n"


def _serve(profile, start_url, *, headless=False):
    from urllib.parse import urlsplit

    from playwright.sync_api import sync_playwright

    def reply(result=None, kind=None):
        print(json.dumps({"ok": kind is None, "result": result, "kind": kind}), flush=True)

    os.umask(0o077)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile,
            headless=headless,
            chromium_sandbox=True,
            viewport={"width": 1100, "height": 760},
            executable_path=os.environ.get("EDISON_CONTENT_CHROMIUM") or None,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
        reply()
        try:
            for line in sys.stdin:
                try:
                    payload = json.loads(line)
                    command, args = payload["command"], payload.get("arguments", {})
                    live = [item for item in context.pages if not item.is_closed()]
                    page = live[-1] if live else context.new_page()
                    if command == "screen":
                        reply(
                            {
                                "image": base64.b64encode(page.screenshot(type="png")).decode(),
                                "width": 1100,
                                "height": 760,
                                "origin": urlsplit(page.url).netloc,
                            }
                        )
                    elif command == "click":
                        x, y = float(args["x"]), float(args["y"])
                        if not (0 <= x <= 1100 and 0 <= y <= 760):
                            raise ValueError("bounds")
                        page.mouse.click(x, y)
                        reply()
                    elif command == "text":
                        value = args["text"]
                        if not isinstance(value, str) or len(value) > 4096:
                            raise ValueError("text")
                        page.keyboard.insert_text(value)
                        reply()
                    elif command == "key":
                        key = args["key"]
                        if key not in {
                            "Enter",
                            "Tab",
                            "Shift+Tab",
                            "Backspace",
                            "Delete",
                            "Escape",
                            "ArrowLeft",
                            "ArrowRight",
                            "ArrowUp",
                            "ArrowDown",
                            "Home",
                            "End",
                            "ControlOrMeta+A",
                        }:
                            raise ValueError("key")
                        page.keyboard.press(key)
                        reply()
                    elif command == "scroll":
                        delta = max(-760, min(760, float(args["delta"])))
                        page.mouse.wheel(0, delta)
                        reply()
                    elif command == "youtube":
                        page.goto("https://www.youtube.com/", wait_until="domcontentloaded")
                        reply()
                    elif command == "snapshot":
                        host = urlsplit(page.url).hostname
                        signed_in = host in {"www.youtube.com", "youtube.com"} and page.evaluate(
                            "() => !!(window.ytcfg && window.ytcfg.get('LOGGED_IN'))"
                        )
                        if not signed_in:
                            reply(kind="user")
                        else:
                            reply({"cookies": _cookies(context)})
                    else:
                        raise ValueError("command")
                except Exception:
                    reply(kind="maintenance")
        finally:
            context.close()


def _display(root):
    """Dedicated authenticated X server in the same browser process group."""
    import secrets
    import shutil
    import tempfile
    from contextlib import contextmanager

    @contextmanager
    def running():
        runtime = Path(tempfile.mkdtemp(prefix="display-", dir=root))
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
                yield
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
        from contextlib import ExitStack

        with ExitStack() as stack:
            if sys.argv[3] != "headless" and sys.platform.startswith("linux"):
                stack.enter_context(_display(Path(sys.argv[1]).parent))
            _serve(sys.argv[1], sys.argv[2], headless=sys.argv[3] == "headless")
    except Exception:
        print(json.dumps({"ok": False, "kind": "maintenance"}), flush=True)

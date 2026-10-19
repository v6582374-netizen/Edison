"""RFB protocol boundary using a real private UNIX socket, no browser/account."""

import base64
import socket
import struct
import tempfile
import threading
import zlib
from pathlib import Path

import pytest

from youtube_strataread.workbench.session_desktop import Desktop


def test_desktop_displays_native_pixels_and_forwards_only_human_input(tmp_path):
    temporary = tempfile.TemporaryDirectory(prefix="ed-rfb-", dir="/tmp")
    path = Path(temporary.name) / "display.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen()
    received = []

    def read(sock, size):
        data = b""
        while len(data) < size:
            data += sock.recv(size - len(data))
        return data

    def server():
        with listener.accept()[0] as sock:
            sock.sendall(b"RFB 003.008\n")
            assert read(sock, 12) == b"RFB 003.008\n"
            sock.sendall(b"\x01\x01")
            assert read(sock, 1) == b"\x01"
            sock.sendall(b"\0\0\0\0")
            read(sock, 1)
            sock.sendall(struct.pack("!HH", 2, 1) + bytes(16) + struct.pack("!I", 0))
            received.append(read(sock, 20))  # negotiated truecolor pixels
            received.append(read(sock, 8))  # raw encoding only
            received.append(read(sock, 10))  # whole framebuffer request
            sock.sendall(
                b"\0\0\0\x01" + struct.pack("!HHHHi", 0, 0, 2, 1, 0) + b"\0\0\xff\0\0\xff\0\0"
            )
            received.append(read(sock, 12))  # press/release pointer
            received.append(read(sock, 16))  # unicode key press/release

    thread = threading.Thread(target=server)
    thread.start()
    with Desktop(path) as desktop:
        screen = desktop.screen()
        assert (screen["width"], screen["height"]) == (2, 1)
        png = base64.b64decode(screen["image"])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert (
            zlib.decompress(png[41 : 41 + struct.unpack("!I", png[33:37])[0]])
            == b"\0\xff\0\0\0\xff\0"
        )
        assert screen.get("origin") is None
        with pytest.raises(ValueError):
            desktop.click(2, 0)
        desktop.click(1, 0)
        desktop.text("中")
    thread.join(3)
    listener.close()
    temporary.cleanup()
    assert not thread.is_alive()
    assert received[3] == b"\x05\x01\0\x01\0\0\x05\0\0\x01\0\0"
    assert received[4] == struct.pack("!BBHI", 4, 1, 0, 0x01004E2D) + struct.pack(
        "!BBHI", 4, 0, 0, 0x01004E2D
    )


def test_truncated_desktop_retires_handoff_and_reopen_uses_fresh_connection(tmp_path, monkeypatch):
    import subprocess
    import sys

    from youtube_strataread.workbench import session_browser
    from youtube_strataread.workbench.content_session import ContentSession, ContentSessionError

    temporary = tempfile.TemporaryDirectory(prefix="ed-rfb-", dir="/tmp")
    path = Path(temporary.name) / "display.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen()

    def read(sock, size):
        value = b""
        while len(value) < size:
            piece = sock.recv(size - len(value))
            if not piece:
                raise OSError("closed fixture peer")
            value += piece
        return value

    def serve():
        for truncated in (True, False):
            with listener.accept()[0] as sock:
                sock.sendall(b"RFB 003.008\n")
                read(sock, 12)
                sock.sendall(b"\x01\x01")
                read(sock, 1)
                sock.sendall(bytes(4))
                read(sock, 1)
                sock.sendall(struct.pack("!HH", 1, 1) + bytes(16) + bytes(4))
                read(sock, 20)
                read(sock, 8)
                read(sock, 10)
                frame = b"\0\0\0\x01" + struct.pack("!HHHHi", 0, 0, 1, 1, 0)
                sock.sendall(frame + (b"\0" if truncated else b"\0\0\xff\0"))

    worker = tmp_path / "external-desktop-driver.py"
    worker.write_text("""import json,sys
from pathlib import Path
from youtube_strataread.workbench.session_desktop import Desktop
with Desktop(Path(sys.argv[1])) as desktop:
    print(json.dumps({'ok':True,'result':{}}),flush=True)
    for line in sys.stdin:
        try:
            result=desktop.screen()
            print(json.dumps({'ok':True,'result':result}),flush=True)
        except OSError:
            # A reported transport failure must retire even a still-live driver.
            print(json.dumps({'ok':False,'kind':'maintenance'}),flush=True)
""")
    original_popen = subprocess.Popen

    def external_driver(argv, **kwargs):
        if argv[1:3] == ["-m", session_browser.__name__]:
            argv = [sys.executable, str(worker), str(path)]
        return original_popen(argv, **kwargs)

    monkeypatch.setattr(session_browser.subprocess, "Popen", external_driver)
    monkeypatch.setenv("EDISON_CONTENT_BROWSER_ENABLED", "1")
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    browser = session_browser.ServerBrowser()
    session = ContentSession(tmp_path / "content", browser=browser)
    try:
        session.open()
        with pytest.raises(ContentSessionError) as failed:
            session.browser_command("screen")
        assert failed.value.kind == "maintenance"
        assert not session.status()["browser_running"]
        session.open()
        assert session.browser_command("screen")["image"].startswith("iVBOR")
    finally:
        session.shutdown()
        listener.close()
        temporary.cleanup()
    thread.join(3)
    assert not thread.is_alive()

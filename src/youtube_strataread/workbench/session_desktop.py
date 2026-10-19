"""Private UNIX RFB view of an ordinary browser desktop.

Only explicit human events are forwarded. No browser debugging, page scripting,
clipboard, framebuffer persistence or input logging is used.
"""

from __future__ import annotations

import base64
import socket
import struct
import zlib
from pathlib import Path


class DesktopError(OSError):
    """A broken desktop stream cannot be reused for another human command."""


class Desktop:
    def __init__(self, path: Path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(10)
        try:
            self.socket.connect(str(path))
            if self._read(12) != b"RFB 003.008\n":
                raise DesktopError("unsupported desktop protocol")
            self.socket.sendall(b"RFB 003.008\n")
            count = self._read(1)[0]
            if not count or 1 not in self._read(count):
                raise DesktopError("private desktop unavailable")
            self.socket.sendall(b"\x01")
            if self._read(4) != bytes(4):
                raise DesktopError("private desktop unavailable")
            self.socket.sendall(b"\x01")
            self.width, self.height = struct.unpack("!HH", self._read(4))
            if not (0 < self.width <= 1920 and 0 < self.height <= 1200):
                raise DesktopError("unsupported desktop size")
            self._read(16)
            length = struct.unpack("!I", self._read(4))[0]
            if length > 4096:
                raise DesktopError("invalid desktop name")
            self._read(length)
            # 32-bit little-endian BGRX pixels, truecolor, no server palette.
            self.socket.sendall(
                b"\0\0\0\0" + struct.pack("!BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
            )
            self.socket.sendall(struct.pack("!BBHi", 2, 0, 1, 0))
            self.pixels = bytearray(self.width * self.height * 4)
        except BaseException:
            self.socket.close()
            raise

    def _read(self, size):
        parts = bytearray()
        while len(parts) < size:
            piece = self.socket.recv(size - len(parts))
            if not piece:
                raise OSError("desktop closed")
            parts.extend(piece)
        return bytes(parts)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.socket.close()

    def screen(self):
        self.socket.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, self.width, self.height))
        # Bound unexpected messages; a broken display must not occupy the host.
        for _ in range(32):
            kind = self._read(1)[0]
            if kind == 2:  # bell
                continue
            if kind == 3:  # discard clipboard without exposing or keeping it
                self._read(3)
                length = struct.unpack("!I", self._read(4))[0]
                if length > 65536:
                    raise DesktopError("desktop clipboard too large")
                self._read(length)
                continue
            if kind != 0:
                raise DesktopError("unsupported desktop response")
            self._read(1)
            count = struct.unpack("!H", self._read(2))[0]
            if count > 4096:
                raise DesktopError("invalid desktop update")
            for _ in range(count):
                x, y, width, height, encoding = struct.unpack("!HHHHi", self._read(12))
                if encoding != 0 or x + width > self.width or y + height > self.height:
                    raise DesktopError("invalid desktop rectangle")
                for row in range(height):
                    offset = ((y + row) * self.width + x) * 4
                    self.pixels[offset : offset + width * 4] = self._read(width * 4)
            raw = bytearray()
            for y in range(self.height):
                raw.append(0)
                row = self.pixels[y * self.width * 4 : (y + 1) * self.width * 4]
                for x in range(0, len(row), 4):
                    raw.extend((row[x + 2], row[x + 1], row[x]))

            def chunk(kind, payload):
                return (
                    struct.pack("!I", len(payload))
                    + kind
                    + payload
                    + struct.pack("!I", zlib.crc32(kind + payload))
                )

            png = b"\x89PNG\r\n\x1a\n" + chunk(
                b"IHDR", struct.pack("!IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
            )
            png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
            return {
                "image": base64.b64encode(png).decode(),
                "width": self.width,
                "height": self.height,
            }
        raise DesktopError("desktop update unavailable")

    def _pointer(self, mask, x, y):
        self.socket.sendall(struct.pack("!BBHH", 5, mask, x, y))

    def click(self, x, y):
        x, y = int(x), int(y)
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError("desktop bounds")
        self._pointer(1, x, y)
        self._pointer(0, x, y)

    def _key(self, key, down):
        self.socket.sendall(struct.pack("!BBHI", 4, int(down), 0, key))

    def text(self, value):
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("invalid input")
        for char in value:
            number = ord(char)
            key = 0xFF0D if char == "\n" else number if 32 <= number <= 255 else 0x01000000 | number
            self._key(key, True)
            self._key(key, False)

    def key(self, value):
        keys = {
            "Enter": 0xFF0D,
            "Tab": 0xFF09,
            "Backspace": 0xFF08,
            "Delete": 0xFFFF,
            "Escape": 0xFF1B,
            "ArrowLeft": 0xFF51,
            "ArrowUp": 0xFF52,
            "ArrowRight": 0xFF53,
            "ArrowDown": 0xFF54,
            "Home": 0xFF50,
            "End": 0xFF57,
        }
        if value in ("Shift+Tab", "ControlOrMeta+A"):
            modifier, key = (0xFFE1, 0xFF09) if value == "Shift+Tab" else (0xFFE3, ord("a"))
            self._key(modifier, True)
            try:
                self._key(key, True)
                self._key(key, False)
            finally:
                self._key(modifier, False)
        elif value in keys:
            self._key(keys[value], True)
            self._key(keys[value], False)
        else:
            raise ValueError("unsupported key")

    def scroll(self, delta):
        delta = max(-760, min(760, float(delta)))
        mask = 8 if delta < 0 else 16
        for _ in range(min(12, int(abs(delta) / 60) + bool(delta))):
            self._pointer(mask, self.width // 2, self.height // 2)
            self._pointer(0, self.width // 2, self.height // 2)

    def navigate(self, url):
        # Only the product's explicit YouTube navigation button calls this.
        self._key(0xFFE3, True)
        self._key(ord("l"), True)
        self._key(ord("l"), False)
        self._key(0xFFE3, False)
        self.text(url)
        self.key("Enter")


def wait_for_native_window(display_name, *, alive, timeout=10):
    """Wait for a mapped native window, not a webpage or authentication state.

    Xvfb/RFB can be ready before Chrome has any window to receive keyboard input.
    Only window geometry/map state is read; no page title, address or pixels.
    """
    import ctypes
    import time

    class Attributes(ctypes.Structure):
        _fields_ = [
            ("x", ctypes.c_int),
            ("y", ctypes.c_int),
            ("width", ctypes.c_int),
            ("height", ctypes.c_int),
            ("border_width", ctypes.c_int),
            ("depth", ctypes.c_int),
            ("visual", ctypes.c_void_p),
            ("root", ctypes.c_ulong),
            ("window_class", ctypes.c_int),
            ("bit_gravity", ctypes.c_int),
            ("win_gravity", ctypes.c_int),
            ("backing_store", ctypes.c_int),
            ("backing_planes", ctypes.c_ulong),
            ("backing_pixel", ctypes.c_ulong),
            ("save_under", ctypes.c_int),
            ("colormap", ctypes.c_ulong),
            ("map_installed", ctypes.c_int),
            ("map_state", ctypes.c_int),
            ("all_event_masks", ctypes.c_long),
            ("your_event_mask", ctypes.c_long),
            ("do_not_propagate_mask", ctypes.c_long),
            ("override_redirect", ctypes.c_int),
            ("screen", ctypes.c_void_p),
        ]

    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XQueryTree.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    x11.XGetWindowAttributes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(Attributes),
    ]
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XGetInputFocus.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int),
    ]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    # A window may disappear between the tree query and attributes query.
    handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    handler = handler_type(lambda *_: 0)
    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XSetErrorHandler.restype = ctypes.c_void_p
    previous_handler = x11.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
    display = x11.XOpenDisplay(display_name.encode())
    if not display:
        x11.XSetErrorHandler(previous_handler)
        raise DesktopError("native display unavailable")
    try:
        root = x11.XDefaultRootWindow(display)
        deadline = time.monotonic() + timeout
        while alive() and time.monotonic() < deadline:
            root_out, parent = ctypes.c_ulong(), ctypes.c_ulong()
            children = ctypes.POINTER(ctypes.c_ulong)()
            count = ctypes.c_uint()
            if x11.XQueryTree(
                display,
                root,
                ctypes.byref(root_out),
                ctypes.byref(parent),
                ctypes.byref(children),
                ctypes.byref(count),
            ):
                try:
                    for index in range(count.value):
                        attributes = Attributes()
                        if (
                            x11.XGetWindowAttributes(
                                display, children[index], ctypes.byref(attributes)
                            )
                            and attributes.map_state == 2
                            and attributes.width >= 200
                            and attributes.height >= 200
                        ):
                            # This private display has only the owned browser.
                            # Focus its mapped window before accepting human keys.
                            x11.XSetInputFocus(display, children[index], 2, 0)
                            x11.XSync(display, 0)
                            focus, revert = ctypes.c_ulong(), ctypes.c_int()
                            x11.XGetInputFocus(display, ctypes.byref(focus), ctypes.byref(revert))
                            if focus.value == children[index]:
                                return
                finally:
                    if children:
                        x11.XFree(children)
            time.sleep(0.05)
        raise DesktopError("native browser window did not become ready")
    finally:
        x11.XCloseDisplay(display)
        x11.XSetErrorHandler(previous_handler)

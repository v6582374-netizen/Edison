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


class Desktop:
    def __init__(self, path: Path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(10)
        try:
            self.socket.connect(str(path))
            if self._read(12) != b"RFB 003.008\n":
                raise ValueError("unsupported desktop protocol")
            self.socket.sendall(b"RFB 003.008\n")
            count = self._read(1)[0]
            if not count or 1 not in self._read(count):
                raise ValueError("private desktop unavailable")
            self.socket.sendall(b"\x01")
            if self._read(4) != bytes(4):
                raise ValueError("private desktop unavailable")
            self.socket.sendall(b"\x01")
            self.width, self.height = struct.unpack("!HH", self._read(4))
            if not (0 < self.width <= 1920 and 0 < self.height <= 1200):
                raise ValueError("unsupported desktop size")
            self._read(16)
            length = struct.unpack("!I", self._read(4))[0]
            if length > 4096:
                raise ValueError("invalid desktop name")
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
                    raise ValueError("desktop clipboard too large")
                self._read(length)
                continue
            if kind != 0:
                raise ValueError("unsupported desktop response")
            self._read(1)
            count = struct.unpack("!H", self._read(2))[0]
            if count > 4096:
                raise ValueError("invalid desktop update")
            for _ in range(count):
                x, y, width, height, encoding = struct.unpack("!HHHHi", self._read(12))
                if encoding != 0 or x + width > self.width or y + height > self.height:
                    raise ValueError("invalid desktop rectangle")
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
        raise ValueError("desktop update unavailable")

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

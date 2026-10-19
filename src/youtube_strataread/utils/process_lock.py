"""Nonblocking process ownership released by the OS when its handle closes."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


def acquire_process_lock(path: Path) -> BinaryIO:
    """Return an owned handle or raise; POSIX and Windows use kernel locks.

    The lock file is stable and is never unlinked while another process might
    use it. The Windows CRT locks its first byte; POSIX uses an advisory flock.
    Closing the returned handle (including process death) releases ownership.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise BlockingIOError(error.errno, "Process ownership is already held") from None
                raise
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise

"""Small differences between Linux and Windows, in one place."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
FROZEN = bool(getattr(sys, "frozen", False))          # True in the installed Windows program (built with PyInstaller)

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def app_dir() -> Path:
    """Where the installed program lives (next to it: yt-dlp, ffmpeg and the browser host)."""
    return Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def quiet() -> dict:
    """subprocess keyword arguments: no console window flashing up for helper programs on Windows."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def detached() -> dict:
    """subprocess keyword arguments: start a program that keeps running after Warpdown (or the browser host) ends."""
    if IS_WINDOWS:
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def start_independent(cmd: list[str]) -> str:
    """Start a program that must keep running after the caller ends, even if the caller is a browser's native-messaging
    host: Firefox ends the host together with everything the host started (a Windows "job"). Returns how it was started."""
    import subprocess
    quiet_io = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if not IS_WINDOWS:
        subprocess.Popen(cmd, start_new_session=True, **quiet_io)
        return "new session"
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:                                                    # leave the browser's job, if its rules allow that
        subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **quiet_io)
        return "breakaway"
    except OSError:
        pass
    if len(cmd) == 1:                                       # otherwise let Explorer start it: not part of the browser's job
        try:
            subprocess.Popen(["explorer.exe", cmd[0]], **quiet_io)
            return "explorer"
        except OSError:
            pass
    subprocess.Popen(cmd, creationflags=flags, **quiet_io)
    return "detached"


def helper(name: str) -> str | None:
    """Path of a helper program shipped next to the installed program (Windows build), else None."""
    exe = app_dir() / (name + (".exe" if IS_WINDOWS else ""))
    return str(exe) if exe.is_file() else None


if FROZEN:                                            # let every `which("ffmpeg")` find the bundled helpers
    os.environ["PATH"] = str(app_dir()) + os.pathsep + os.environ.get("PATH", "")


_WRITE_LOCK = threading.Lock()


def pwrite(fd: int, data: bytes, offset: int) -> int:
    """Write `data` at `offset` without moving anything for other threads. Windows has no os.pwrite, so there the
    seek and the write happen together under a lock (many download segments share one file)."""
    if hasattr(os, "pwrite"):
        return os.pwrite(fd, data, offset)
    with _WRITE_LOCK:
        os.lseek(fd, offset, os.SEEK_SET)
        view, done = memoryview(data), 0
        while done < len(view):
            done += os.write(fd, view[done:])
    return len(data)


OPEN_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)      # Windows must not translate line endings

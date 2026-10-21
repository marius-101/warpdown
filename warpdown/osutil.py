"""Small differences between Linux and Windows, in one place."""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
FROZEN = bool(getattr(sys, "frozen", False))          # True in the installed Windows program (built with PyInstaller)

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def app_dir() -> Path:
    """Where the installed program lives (next to it: yt-dlp, ffmpeg, aria2c and the browser host)."""
    return Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def quiet() -> dict:
    """subprocess keyword arguments: no console window flashing up for helper programs on Windows."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def detached() -> dict:
    """subprocess keyword arguments: start a program that keeps running after Warpdown (or the browser host) ends."""
    if IS_WINDOWS:
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def helper(name: str) -> str | None:
    """Path of a helper program shipped next to the installed program (Windows build), else None."""
    exe = app_dir() / (name + (".exe" if IS_WINDOWS else ""))
    return str(exe) if exe.is_file() else None


if FROZEN:                                            # let every `which("ffmpeg")` find the bundled helpers
    os.environ["PATH"] = str(app_dir()) + os.pathsep + os.environ.get("PATH", "")

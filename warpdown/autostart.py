"""Start Warpdown when the user logs in (minimised in the tray), so browser hand-offs never wait for a cold start."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import osutil

NAME = "Warpdown"


def _command() -> list[str]:
    if osutil.FROZEN:
        return [sys.executable, "--background"]
    return [sys.executable, "-m", "warpdown", "--background"]


def _linux_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "warpdown.desktop"


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


def is_enabled() -> bool:
    if osutil.IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, NAME)
                return True
        except OSError:
            return False
    return _linux_file().exists()


def set_enabled(on: bool) -> None:
    cmd = _command()
    if osutil.IS_WINDOWS:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, " ".join(_quote(a) for a in cmd))
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
        return
    f = _linux_file()
    if not on:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    root = osutil.app_dir()
    f.write_text("[Desktop Entry]\nType=Application\nName=Warpdown\nComment=Download manager (tray)\n"
                 f"Exec=env PYTHONPATH={_quote(str(root))} {' '.join(_quote(a) for a in cmd)}\n"
                 "Icon=warpdown\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")

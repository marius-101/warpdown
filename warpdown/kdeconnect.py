"""Send finished downloads to a paired phone through KDE Connect (kdeconnect-cli)."""

from __future__ import annotations

import shutil
import subprocess

from . import osutil


def available() -> bool:
    return shutil.which("kdeconnect-cli") is not None


def devices() -> list[tuple[str, str]]:
    """Reachable, paired devices as (id, name)."""
    if not available():
        return []
    try:
        out = subprocess.run(["kdeconnect-cli", "-a", "--id-name-only"], capture_output=True, text=True,
                             timeout=10, **osutil.quiet()).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        dev_id, _, name = line.strip().partition(" ")
        if dev_id:
            found.append((dev_id, name or dev_id))
    return found


def send(device_id: str, path) -> None:
    """Start the transfer; KDE Connect carries on in the background."""
    subprocess.Popen(["kdeconnect-cli", "-d", device_id, "--share", str(path)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **osutil.quiet())

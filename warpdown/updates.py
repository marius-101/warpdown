"""Update check: asks GitHub for the newest release (once a day, or on request) and compares it with this version.

Nothing is installed automatically: Warpdown only tells the user that a new version exists, links the file that fits
how this copy was installed (.deb, Flatpak, Windows installer or portable zip) and says how to install it.
The request contains no personal data (GitHub sees the IP address, like for any web page).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

from . import osutil

REPO = "marius-101/warpdown"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_URL = f"https://github.com/{REPO}/releases/latest"


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.8.5' / '0.8.5-beta' -> (0, 8, 5). Anything without digits -> ()."""
    m = re.search(r"\d+(?:\.\d+)*", text or "")
    return tuple(int(p) for p in m.group(0).split(".")) if m else ()


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


# How this copy was installed -> (the release file that replaces it, how to install that file).
# The patterns follow the file names the release workflow publishes.
_KINDS = {
    "deb": (r"^warpdown_.*_amd64\.deb$", "Download the .deb and install it over this version: sudo apt install ./{file}"),
    "flatpak": (r"^warpdown-.*\.flatpak$", "Download the .flatpak and install it over this version: "
                                           "flatpak install --user --reinstall {file}"),
    "installer": (r"^warpdown-setup-.*\.exe$", "Download the installer and run it; it replaces this version and keeps "
                                               "your settings and downloads."),
    "portable": (r"^warpdown-windows-portable-.*\.zip$", "Download the zip, close Warpdown and unpack it in place of "
                                                          "this folder."),
    "source": ("", "Update the source checkout: git pull, then pip install -r requirements.txt."),
    "": ("", "Download the new version from its release page."),
}


def install_kind() -> str:
    """'deb', 'flatpak', 'installer', 'portable', 'source' or '' (a build nobody published)."""
    if os.environ.get("FLATPAK_ID"):
        return "flatpak"
    if not osutil.FROZEN:
        return "source"
    here = osutil.app_dir()
    if osutil.IS_WINDOWS:
        return "installer" if any(here.glob("unins*.exe")) else "portable"     # Inno Setup puts its uninstaller there
    if sys.platform.startswith("linux") and here == Path("/opt/warpdown"):
        return "deb"
    return ""


def pick_download(assets: list, kind: str) -> tuple[str, str]:
    """(file name, download URL) of the release file for this kind of install, or ("", "") when there is none."""
    pattern = _KINDS.get(kind, ("", ""))[0]
    if pattern:
        for a in assets or []:
            name = str(a.get("name") or "")
            if re.match(pattern, name) and a.get("browser_download_url"):
                return name, str(a["browser_download_url"])
    return "", ""


def how_to_update(kind: str, file: str = "") -> str:
    """One sentence for the user: what to do with the new version."""
    hint = _KINDS.get(kind, _KINDS[""])[1]
    if "{file}" in hint and not file:
        return _KINDS[""][1]
    return hint.format(file=file)


def fetch_latest(timeout: float = 10.0) -> tuple[str, str]:
    """(version, page to open) of the newest release. Raises RuntimeError with a readable message on any problem."""
    version, page, _assets = fetch_release(timeout)
    return version, page


def fetch_release(timeout: float = 10.0) -> tuple[str, str, list]:
    """(version, release page, its files) of the newest published release (drafts and pre-releases are skipped by
    GitHub). Raises RuntimeError with a readable message on any problem."""
    try:
        r = httpx.get(API_URL, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": "Warpdown-update-check"}, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as e:
        raise RuntimeError(str(e) or e.__class__.__name__) from e
    except ValueError as e:
        raise RuntimeError("unreadable answer from GitHub") from e
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        raise RuntimeError("no release found")
    assets = data.get("assets")
    return tag.lstrip("vV"), str(data.get("html_url") or PAGE_URL), assets if isinstance(assets, list) else []


def check(timeout: float = 10.0) -> tuple[str, str, str]:
    """(newest version, the link to offer, how to update) for this copy of Warpdown. The link is the matching release
    file when there is one, otherwise the release page."""
    version, page, assets = fetch_release(timeout)
    kind = install_kind()
    file, url = pick_download(assets, kind)
    return version, url or page, how_to_update(kind, file)

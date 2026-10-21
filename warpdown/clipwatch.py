"""Clipboard watcher logic (pure Python): which web addresses in the clipboard look like something to download."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import ytdlp
from .util import CATEGORIES

_URL = re.compile(r"https?://[^\s<>\"'`]+", re.I)
EXTRA_EXTS = {".torrent", ".apk", ".dmg", ".pkg", ".ipa", ".bin", ".img", ".jar", ".whl", ".xpi", ".crx", ".vsix"}
DOWNLOAD_EXTS = set().union(*CATEGORIES.values()) | EXTRA_EXTS


def extract_urls(text: str) -> list[str]:
    """All web addresses in `text`, without trailing punctuation, each once, in order."""
    out: list[str] = []
    for u in _URL.findall(text or ""):
        while u and u[-1] in ".,;:!?'\"":
            u = u[:-1]
        for close, open_ in ((")", "("), ("]", "[")):          # a closing bracket that has no partner is punctuation
            while u.endswith(close) and u.count(close) > u.count(open_):
                u = u[:-1]
        if u not in out and urlsplit(u).netloc:
            out.append(u)
    return out


def is_downloadable(url: str) -> bool:
    """A file with a known type in its path, or a page on a video site that yt-dlp understands."""
    parts = urlsplit(url)
    path = parts.path.lower()
    dot = path.rfind(".")
    if dot != -1 and path[dot:] in DOWNLOAD_EXTS and "/" not in path[dot:]:
        return True
    return bool(ytdlp.looks_like_video_site(url) and (parts.path.strip("/") or parts.query))


_MAGNET = re.compile(r"magnet:\?[^\s<>\"'`]+", re.I)


def pick(text: str) -> list[str]:
    magnets = list(dict.fromkeys(m for m in _MAGNET.findall(text or "") if "xt=urn:btih:" in m.lower()))
    return magnets + [u for u in extract_urls(text) if is_downloadable(u)]

"""Download subtitle files (WebVTT / SRT / ASS, or HLS subtitle playlists) next to a video."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .engine import SSL_CONTEXT, USER_AGENT
from .util import sanitize_filename

SUB_EXTS = (".vtt", ".srt", ".ass", ".ssa")
_SRT = re.compile(r"^\s*\d+\s*\n\s*\d\d:\d\d:\d\d[,.]\d\d\d\s*-->", re.M)


def sniff_subtitle(text: str) -> str:
    """Return '.vtt' / '.srt' / '.ass' for recognisable subtitle text, '' otherwise."""
    t = text.lstrip("\ufeff \t\r\n")
    if t.startswith("WEBVTT"):
        return ".vtt"
    if t.startswith("[Script Info]"):
        return ".ass"
    if _SRT.search(text[:4000]):
        return ".srt"
    return ""


def merge_vtt(parts: list[str]) -> str:
    """Join WebVTT segments, dropping the repeated WEBVTT header block of later segments."""
    out: list[str] = []
    for i, p in enumerate(parts):
        p = p.replace("\r\n", "\n").lstrip("\ufeff")
        if i and p.startswith("WEBVTT"):
            p = p.partition("\n\n")[2]
        if p.strip():
            out.append(p.strip())
    return "\n\n".join(out) + "\n"


def _segments(playlist: str, base: str) -> list[str]:
    return [urljoin(base, l.strip()) for l in playlist.splitlines() if l.strip() and not l.startswith("#")]


def tag_for(lang: str, label: str, url: str) -> str:
    raw = lang or label or Path(unquote(urlsplit(url).path)).stem
    return sanitize_filename(raw).replace(" ", "_")[:24].strip("._") or "sub"


def fetch_subtitle(url: str, headers: dict, video_path: Path, lang: str = "", label: str = "") -> Path | None:
    """Fetch one subtitle and save it as '<video stem>.<lang>.<ext>'. Returns the path, or None if skipped."""
    h = {"User-Agent": USER_AGENT, **headers}
    with httpx.Client(follow_redirects=True, verify=SSL_CONTEXT, timeout=httpx.Timeout(30, connect=15),
                      headers=h) as c:
        r = c.get(url)
        r.raise_for_status()
        text = r.text
        if text.lstrip("\ufeff \r\n").startswith("#EXTM3U"):        # HLS subtitle playlist
            parts = []
            for seg in _segments(text, str(r.url)):
                sr = c.get(seg)
                sr.raise_for_status()
                parts.append(sr.text)
            if not parts:
                return None
            text = merge_vtt(parts)
    if "#xywh=" in text[:5000]:       # thumbnail-sprite track, not subtitles
        return None
    ext = sniff_subtitle(text)
    if not ext:
        return None
    stem = video_path.with_suffix("").name
    tag = tag_for(lang, label, url)
    dest = video_path.with_name(f"{stem}.{tag}{ext}")
    n = 2
    while dest.exists():
        dest = video_path.with_name(f"{stem}.{tag}{n}{ext}")
        n += 1
    dest.write_text(text, encoding="utf-8")
    return dest

"""Download subtitle files (WebVTT / SRT / ASS, or HLS subtitle playlists) next to a video."""

from __future__ import annotations

import re
from glob import escape as glob_escape
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


def _fetch_text(url: str, headers: dict) -> str:
    """Download a subtitle (merging HLS subtitle playlists) and return its text."""
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
            text = merge_vtt(parts) if parts else ""
    return text


def peek_subtitle(url: str, headers: dict) -> dict | None:
    """Quick look for the UI: {'ext', 'cues', 'bytes'} or None if it isn't a real subtitle file."""
    text = _fetch_text(url, headers)
    if "#xywh=" in text[:5000]:
        return None
    ext = sniff_subtitle(text)
    if not ext:
        return None
    return {"ext": ext, "cues": text.count("-->"), "bytes": len(text.encode("utf-8"))}


def matches_langs(tag: str, codes: list[str]) -> bool:
    """True if a subtitle tag like 'eng' / 'English' / 'en-US' matches any wanted code (empty list = all)."""
    if not codes:
        return True
    t = tag.lower()
    return any(t.startswith(c) or c.startswith(t) and len(t) >= 2 for c in codes)


def parse_langs(text: str) -> list[str]:
    return [c.strip().lower() for c in text.split(",") if c.strip()]


def fetch_subtitle(url: str, headers: dict, video_path: Path, lang: str = "", label: str = "") -> Path | None:
    """Fetch one subtitle and save it as '<video stem>.<lang>.<ext>'. Returns the path, or None if skipped.

    If an identical file for this video already exists, that path is returned instead of saving a copy.
    """
    text = _fetch_text(url, headers)
    if not text or "#xywh=" in text[:5000]:       # empty, or a thumbnail-sprite track
        return None
    ext = sniff_subtitle(text)
    if not ext:
        return None
    stem = video_path.with_suffix("").name
    tag = tag_for(lang, label, url)
    for old in video_path.parent.glob(f"{glob_escape(stem)}.*{ext}"):
        try:
            if old.read_text(encoding="utf-8") == text:
                return old
        except OSError:
            pass
    dest = video_path.with_name(f"{stem}.{tag}{ext}")
    n = 2
    while dest.exists():
        dest = video_path.with_name(f"{stem}.{tag}{n}{ext}")
        n += 1
    dest.write_text(text, encoding="utf-8")
    return dest

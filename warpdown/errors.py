"""Plain-language explanations for download errors (what happened, and what to try)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Explanation:
    short: str      # one line for the Status column
    hint: str       # "what to try", shown in the tooltip
    raw: str        # the original technical message


# (pattern, short text, hint). The first match wins, so specific rules come before general ones.
_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"Torrents need aria2", re.I),
     "Torrents need the free aria2 program",
     "Install it once: sudo apt install aria2 (Windows: put aria2c.exe next to Warpdown), then resume."),
    (re.compile(r"DRM|unsupported encryption|Widevine|PlayReady|FairPlay", re.I),
     "This video is copy-protected (DRM)",
     "Warpdown cannot download DRM-protected video."),
    (re.compile(r"Unsupported URL|no video formats found|Unable to extract|There is no video|No video found", re.I),
     "No downloadable video found on this page",
     "The site may not be supported, or it hides its stream. Play the video first, then open the Warpdown toolbar "
     "icon in the browser and check \"Detected media\". Protected players can't be downloaded."),
    (re.compile(r"Sign in|Private video|members[- ]only|login required|age[- ]restricted|Confirm your age", re.I),
     "The site wants you to sign in",
     "Private, members-only or age-restricted videos need your login. Try the download button on the page while "
     "you are signed in."),
    (re.compile(r"page needs to be reloaded", re.I),
     "YouTube asked to try again",
     "Update yt-dlp in Settings, then retry."),
    (re.compile(r"\b403\b", re.I),
     "The site refused the download (403)",
     "Some sites block download tools, or only serve the video to the page that started it. Try the download "
     "button on the page while the video is playing. If the site doesn't allow downloads, Warpdown can't get around that."),
    (re.compile(r"\b404\b", re.I),
     "The file was not found (404)",
     "The link may be wrong or the file was removed. Get a fresh link from the page."),
    (re.compile(r"\b410\b", re.I),
     "The link has expired or was removed (410)",
     "Get a fresh link from the page."),
    (re.compile(r"\b401\b", re.I),
     "A login is required (401)",
     "Open the link in your browser while signed in, then use the Warpdown button there."),
    (re.compile(r"\b429\b|Too Many Requests", re.I),
     "The server says too many requests (429)",
     "Warpdown retries automatically. Fewer simultaneous downloads (Settings) can help."),
    (re.compile(r"Server error|\b(500|502|503|504)\b", re.I),
     "The server had a problem",
     "Usually temporary: Warpdown retries automatically."),
    (re.compile(r"No space left", re.I),
     "The disk is full",
     "Free some space or choose another folder, then resume."),
    (re.compile(r"Permission denied|Read-only file system|Is a directory", re.I),
     "Warpdown can't write to that folder",
     "Choose another folder, or fix the folder's permissions, then resume."),
    (re.compile(r"ffmpeg failed|ffmpeg", re.I),
     "Joining video and audio failed (ffmpeg)",
     "Check that ffmpeg is installed (sudo apt install ffmpeg), then retry."),
    (re.compile(r"Live streams aren't supported|is live", re.I),
     "Live streams aren't supported",
     "Wait until the live stream has ended and a recording is available."),
    (re.compile(r"Server ignored Range", re.I),
     "This server doesn't support resuming",
     "Try again; if it keeps failing, use fewer connections in Settings."),
    (re.compile(r"timed out|timeout|Temporary failure|Name or service not known|nodename nor servname|"
                r"Network is unreachable|No route to host|Connection (refused|reset|aborted)|Broken pipe|"
                r"Server disconnected|RemoteProtocolError|Read error|incomplete|ended unexpectedly|Failed to resolve", re.I),
     "Connection problem",
     "Check your internet connection. Warpdown retries automatically when this looks temporary."),
]


def explain(error: str) -> Explanation:
    """Turn a raw error message into a short sentence plus a hint. Unknown errors keep their own (shortened) text."""
    raw = (error or "").strip()
    for pattern, short, hint in _RULES:
        if pattern.search(raw):
            return Explanation(short, hint, raw)
    first = raw.splitlines()[0] if raw else "The download failed"
    return Explanation(first[:90] + ("…" if len(first) > 90 else ""),
                       "Hover for the technical details, or use \"Copy error details\" in the right-click menu.", raw)


def tooltip(error: str, url: str = "") -> str:
    """Longer text for the Status column's tooltip: explanation, advice, technical details, address."""
    e = explain(error)
    parts = [e.short, "", f"What to try: {e.hint}", "", f"Technical details: {e.raw[:400]}"]
    if url:
        parts += ["", f"Address: {url[:200]}"]
    return "\n".join(parts)

"""Small helpers: filenames, formatting, paths."""

from __future__ import annotations

import os
import re
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlsplit


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    p = base / "kudm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    p = base / "kudm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_download_dir() -> Path:
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        return Path(xdg)
    return Path.home() / "Downloads"


_BAD = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def sanitize_filename(name: str) -> str:
    name = _BAD.sub("_", name).strip().strip(".")
    return name[:200] or "download"


def filename_from_headers(content_disposition: str | None, url: str) -> str:
    if content_disposition:
        msg = Message()
        msg["content-disposition"] = content_disposition
        fn = msg.get_filename()
        if fn:
            return sanitize_filename(fn)
        m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", content_disposition, re.I)
        if m:
            return sanitize_filename(unquote(m.group(1)))
    path = unquote(urlsplit(url).path)
    return sanitize_filename(os.path.basename(path) or urlsplit(url).netloc or "download")


def unique_path(directory: Path, name: str) -> Path:
    """Return a path in `directory` that does not collide with files or partials."""
    p = directory / name
    stem, suffix = p.stem, p.suffix
    i = 1
    while p.exists() or Path(str(p) + ".part").exists():
        p = directory / f"{stem} ({i}){suffix}"
        i += 1
    return p


def fmt_size(n: float) -> str:
    if n < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


def fmt_speed(bps: float) -> str:
    return f"{fmt_size(bps)}/s" if bps > 0 else ""


def fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


CATEGORIES = {
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts"},
    "Music": {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus"},
    "Compressed": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".txt", ".epub"},
    "Programs": {".deb", ".rpm", ".appimage", ".run", ".sh", ".exe", ".msi", ".iso", ".flatpak", ".snap"},
}


def category_for(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "Other"


def ensure_category_dirs(base: Path) -> None:
    """Create the IDM-style category folders (Video, Music, ...) inside `base`."""
    for cat in (*CATEGORIES, "Other"):
        (Path(base) / cat).mkdir(parents=True, exist_ok=True)

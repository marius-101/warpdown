"""Small helpers: filenames, formatting, paths."""

from __future__ import annotations

import os
import re
import shutil
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urlsplit


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    p = base / "warpdown"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    p = base / "warpdown"
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
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff", ".avif", ".heic", ".ico"},
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


_EXT_BY_TYPE = {
    "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "video/x-msvideo": ".avi", "video/x-flv": ".flv", "video/mpeg": ".mpg",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/flac": ".flac",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/aac": ".aac", "audio/webm": ".weba",
    "application/pdf": ".pdf", "application/zip": ".zip", "application/x-7z-compressed": ".7z",
    "application/vnd.rar": ".rar", "application/x-rar-compressed": ".rar", "application/gzip": ".gz",
    "application/x-iso9660-image": ".iso", "application/vnd.debian.binary-package": ".deb",
}


def has_extension(name: str) -> bool:
    return re.fullmatch(r"\.[A-Za-z][A-Za-z0-9]{0,4}", os.path.splitext(name)[1]) is not None


def sniff_ext(head: bytes) -> str:
    """Guess a file extension from its first bytes ("" if unknown)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return ".m4a" if head[8:12] in (b"M4A ", b"M4B ") else ".mp4"
    if head[:4] == b"\x1aE\xdf\xa3":
        return ".mkv"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return ".avi"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    for magic, ext in ((b"OggS", ".ogg"), (b"fLaC", ".flac"), (b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"),
                       (b"Rar!", ".rar"), (b"7z\xbc\xaf", ".7z"), (b"\x1f\x8b", ".gz"), (b"\xfd7zXZ", ".xz")):
        if head.startswith(magic):
            return ext
    return ""


def guess_extension(name: str, content_type: str = "", head: bytes = b"") -> str:
    """Extension to append when `name` has none: from Content-Type, else from magic bytes."""
    if has_extension(name):
        return ""
    return _EXT_BY_TYPE.get(content_type.split(";")[0].strip().lower(), "") or sniff_ext(head)


def migrate_legacy_data() -> list[str]:
    """Warpdown was called KUDM before version 0.5: copy the download list and settings over, once.

    Copies (never moves), so the old folders stay as a backup. Does nothing if the new folders already exist.
    """
    done: list[str] = []
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    conf_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    old, new = data_home / "kudm", data_home / "warpdown"
    if old.is_dir() and not new.exists():
        shutil.copytree(old, new)
        done.append(f"download list copied from {old}")
    old, new = conf_home / "kudm", conf_home / "warpdown"
    if old.is_dir() and not new.exists():
        shutil.copytree(old, new)
        legacy = new / "kudm.conf"                       # QSettings file is named after the application
        if legacy.exists():
            legacy.rename(new / "warpdown.conf")
        done.append(f"settings copied from {old}")
    return done


def category_folder(base: Path, name: str) -> Path | None:
    """Folder shown by a left-hand list entry: the download folder itself, a category subfolder, or None
    for entries that aren't folders (Unfinished / Finished)."""
    if name == "All Downloads":
        return Path(base)
    if name in CATEGORIES or name == "Other":
        return Path(base) / name
    return None

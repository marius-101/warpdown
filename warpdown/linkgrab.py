"""Logic behind "Download all links on this page": classify, de-duplicate and filter what the browser found.

Pure Python (no Qt) so it is easy to test. The browser extension sends raw {url, text, source} entries; the links dialog
shows them and lets the user pick which to download.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .util import category_for, sanitize_filename

# Links that normally lead to another web page rather than to a file.
PAGE_EXTS = {"", ".html", ".htm", ".xhtml", ".shtml", ".php", ".asp", ".aspx", ".jsp", ".cgi"}
KIND_ALL, KIND_PAGES = "All files", "Web pages"
SOURCE_LABELS = {"link": "Link", "image": "Image", "media": "Media"}
MAX_LINKS = 5000


@dataclass
class Link:
    url: str
    text: str = ""
    source: str = "link"          # "link" | "image" | "media"
    ext: str = ""
    category: str = "Other"       # Video / Music / ... / Images / Other / Web pages
    name: str = ""                # what to show in the list


def url_extension(url: str) -> str:
    return os.path.splitext(unquote(urlsplit(url).path))[1].lower()


def name_from_url(url: str, text: str = "") -> str:
    base = os.path.basename(unquote(urlsplit(url).path).rstrip("/"))
    if base:
        return sanitize_filename(base)
    return sanitize_filename(text)[:60] if text.strip() else (urlsplit(url).hostname or "download")


def classify(url: str, text: str = "", source: str = "link") -> Link | None:
    """Turn a raw entry into a Link, or None if it can't be downloaded (not http/https)."""
    url = url.strip().split("#", 1)[0]           # a #fragment is meaningless for a download
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    ext = url_extension(url)
    name = name_from_url(url, text)
    if source == "image":
        category = "Images"
    elif source == "link" and ext in PAGE_EXTS:
        category = KIND_PAGES
    else:
        category = category_for(name) if ext else "Other"
    return Link(url=url, text=" ".join(text.split())[:120], source=source if source in SOURCE_LABELS else "link",
                ext=ext, category=category, name=name)


def build_links(raw: list[dict]) -> list[Link]:
    """Classify and de-duplicate (by address without #fragment); the first entry wins, but link text is kept."""
    seen: dict[str, Link] = {}
    for r in (raw or [])[:MAX_LINKS]:
        if not isinstance(r, dict):
            continue
        link = classify(str(r.get("url", "")), str(r.get("text", "")), str(r.get("source", "link")))
        if link is None:
            continue
        old = seen.get(link.url)
        if old is None:
            seen[link.url] = link
        elif not old.text and link.text:           # same address found twice: keep whichever has a text
            old.text = link.text
    return list(seen.values())


def category_counts(links: list[Link]) -> dict[str, int]:
    out: dict[str, int] = {}
    for l in links:
        out[l.category] = out.get(l.category, 0) + 1
    return out


def matches(link: Link, kind: str = KIND_ALL, text: str = "", hide_pages: bool = True) -> bool:
    """Does `link` pass the dialog's filters? Every word of `text` must appear in the address or the link text."""
    if kind == KIND_PAGES:
        if link.category != KIND_PAGES:
            return False
    elif kind == KIND_ALL:
        if hide_pages and link.category == KIND_PAGES:
            return False
    elif link.category != kind:
        return False
    words = text.lower().split()
    if words:
        hay = f"{link.url} {link.text}".lower()
        return all(w in hay for w in words)
    return True


def same_site(url: str, netloc: str) -> bool:
    return urlsplit(url).netloc.lower() == netloc.lower()

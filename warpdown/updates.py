"""Update check: asks GitHub for the newest release (once a day, or on request) and compares it with this version.

Nothing is installed automatically: Warpdown only tells the user that a new version exists and opens its download page.
The request contains no personal data (GitHub sees the IP address, like for any web page).
"""

from __future__ import annotations

import re

import httpx

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


def fetch_latest(timeout: float = 10.0) -> tuple[str, str]:
    """(version, page to open) of the newest release. Raises RuntimeError with a readable message on any problem."""
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
    return tag.lstrip("vV"), str(data.get("html_url") or PAGE_URL)

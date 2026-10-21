"""Batch downloads: turn pasted text into a list of URLs, expanding ranges like photo[001-100].jpg.

Range syntax inside [...]:  [1-20]  [01-20] (zero-padded)  [1-100:5] (step)  [a-f]  [A-F].
Several ranges in one address multiply: img[1-3]_[a-b].png -> 6 addresses. Anything else in brackets (such as an IPv6
host, [::1]) is left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LIMIT = 10_000
_RANGE = re.compile(r"\[([^\[\]/?#]+)\]")
_URL = re.compile(r"https?://\S+", re.I)
_NUM = re.compile(r"^(\d+)-(\d+)(?::(\d+))?$")
_ALPHA = re.compile(r"^([A-Za-z])-([A-Za-z])$")


class TooManyUrls(ValueError):
    pass


def _values(spec: str) -> list[str] | None:
    """The values a bracket stands for, or None if it is not a range."""
    m = _NUM.match(spec)
    if m:
        a, b, step = m.group(1), m.group(2), int(m.group(3) or 1)
        if step < 1:
            return None
        lo, hi = int(a), int(b)
        width = len(a) if a.startswith("0") and len(a) > 1 else 0
        if lo > hi:
            lo, hi = hi, lo
        if (hi - lo) // step + 1 > LIMIT:
            raise TooManyUrls(spec)
        return [str(n).zfill(width) for n in range(lo, hi + 1, step)]
    m = _ALPHA.match(spec)
    if m:
        a, b = m.group(1), m.group(2)
        if a.isupper() != b.isupper():
            return None
        lo, hi = sorted((ord(a), ord(b)))
        return [chr(c) for c in range(lo, hi + 1)]
    return None


def expand_line(line: str, limit: int = LIMIT) -> list[str]:
    """Expand every range in `line`. Raises TooManyUrls if the result would exceed `limit`."""
    parts: list = []                      # literal strings and lists of alternatives
    pos = 0
    total = 1
    for m in _RANGE.finditer(line):
        vals = _values(m.group(1))
        if vals is None:
            continue
        parts.append(line[pos:m.start()])
        parts.append(vals)
        total *= len(vals)
        if total > limit:
            raise TooManyUrls(line)
        pos = m.end()
    parts.append(line[pos:])
    out = [""]
    for p in parts:
        out = [o + p for o in out] if isinstance(p, str) else [o + v for o in out for v in p]
    return out


@dataclass
class BatchResult:
    urls: list[str] = field(default_factory=list)
    lines_used: int = 0
    skipped: list[str] = field(default_factory=list)      # lines that contained no web address
    truncated: bool = False                                # more than `limit` addresses: the rest was dropped


def parse_text(text: str, limit: int = LIMIT) -> BatchResult:
    """One or more web addresses per line (extra words are ignored); ranges are expanded; duplicates removed."""
    res = BatchResult()
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = _URL.findall(line)
        if not found:
            res.skipped.append(line[:80])
            continue
        res.lines_used += 1
        for u in found:
            u = u.rstrip(".,;)'\"")
            try:
                expanded = expand_line(u, limit)
            except TooManyUrls:
                res.truncated = True
                continue
            for e in expanded:
                if e in seen:
                    continue
                if len(res.urls) >= limit:
                    res.truncated = True
                    return res
                seen.add(e)
                res.urls.append(e)
    return res

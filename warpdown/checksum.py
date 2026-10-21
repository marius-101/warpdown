"""Checksum verification: compare a finished file against an expected hash (SHA-256, SHA-1, MD5, SHA-512)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_BY_LENGTH = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def parse_expected(text: str) -> tuple[str, str] | None:
    """'sha256:ABC…', 'ABC…  file.iso' or a bare hex digest -> (algorithm, lowercase digest); None if not a checksum."""
    text = (text or "").strip()
    if not text:
        return None
    algo = ""
    m = re.match(r"^(md5|sha1|sha256|sha512)\s*[:=]\s*(\S+)", text, re.I)
    if m:
        algo, text = m.group(1).lower(), m.group(2)
    token = text.split()[0] if text.split() else ""      # "digest  filename" lines as written by sha256sum
    if not _HEX.match(token) or len(token) not in _BY_LENGTH:
        return None
    guess = _BY_LENGTH[len(token)]
    if algo and algo != guess:
        return None
    return guess, token.lower()


def file_digest(path: Path, algo: str, chunk: int = 1 << 20) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, expected: str) -> tuple[bool, str]:
    """Returns (matches, algorithm). Raises ValueError for an unusable checksum text, OSError if the file is unreadable."""
    parsed = parse_expected(expected)
    if not parsed:
        raise ValueError("That is not a valid MD5, SHA-1, SHA-256 or SHA-512 checksum.")
    algo, digest = parsed
    return file_digest(path, algo) == digest, algo


def sidecar_candidates(url: str) -> list[str]:
    """Where a download site often publishes the checksum of <url>."""
    base = url.split("?", 1)[0].split("#", 1)[0]
    return [base + ext for ext in (".sha256", ".sha256sum", ".sha512", ".md5")]


def find_in_text(text: str, filename: str) -> str | None:
    """Pick the digest for `filename` from the content of a checksum file (one digest alone, or 'digest  name' lines)."""
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    for l in lines:
        parts = l.replace("*", " ").split()
        if len(parts) >= 2 and parts[-1].split("/")[-1] == filename and parse_expected(parts[0]):
            return parts[0]
    if len(lines) == 1 and parse_expected(lines[0]) and len(lines[0].split()) == 1:
        return lines[0]
    return None

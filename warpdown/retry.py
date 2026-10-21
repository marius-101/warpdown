"""Automatic retry: which download errors are worth trying again, and how long to wait."""

from __future__ import annotations

import re

DELAYS = (15, 30, 60, 120, 300)      # seconds before attempt 1, 2, 3, ...; the last delay repeats

# Errors that will not fix themselves: wrong address, no permission, no space, protected content.
_PERMANENT = re.compile(
    r"Client error '(400|401|403|404|405|410|451)|HTTP Error (400|401|403|404|405|410|451)"
    r"|No space left|Permission denied|Read-only file system|Is a directory|File name too long"
    r"|Sign in|Private video|Unsupported URL|not a valid URL|DRM|unsupported encryption|Unsupported", re.I)
# Errors of the connection or the server's mood: worth another try.
_TRANSIENT = re.compile(
    r"Server error|\b(429|500|502|503|504)\b|timed out|timeout|Temporary failure|Name or service not known"
    r"|nodename nor servname|Network is unreachable|No route to host|Connection (refused|reset|aborted)|Broken pipe"
    r"|RemoteProtocolError|Server disconnected|incomplete|ended unexpectedly|Read error|\bEOF\b|\bSSL\b"
    r"|Max retries|Unable to download|Failed to resolve|network", re.I)


def is_transient(error: str) -> bool:
    """True if `error` looks like a temporary network/server problem (unknown errors are NOT retried)."""
    if not error or _PERMANENT.search(error):
        return False
    return bool(_TRANSIENT.search(error))


def delay_for(attempt: int) -> int:
    """Seconds to wait before retry number `attempt` (1-based)."""
    return DELAYS[min(max(attempt, 1) - 1, len(DELAYS) - 1)]

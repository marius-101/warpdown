"""Speed limits: a global one (optionally different during chosen hours) plus an optional one per download."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime

from .engine import RateLimiter


@dataclass
class LimitSchedule:
    """Between `start` and `end` (may cross midnight) the global limit is `kb` KB/s (0 = unlimited) instead of the normal one."""
    enabled: bool = False
    kb: int = 0
    start: str = "08:00"
    end: str = "23:00"

    @staticmethod
    def _t(text: str) -> dtime:
        h, m = text.split(":")
        return dtime(int(h), int(m))

    def active(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        s, e, t = self._t(self.start), self._t(self.end), now.time()
        return s <= t < e if s <= e else (t >= s or t < e)


class ChainedLimiter:
    """Applies the global limiter and the download's own limiter. `rate` is the stricter of the two (for yt-dlp)."""

    def __init__(self, shared: RateLimiter, own: RateLimiter):
        self.shared, self.own = shared, own

    @property
    def rate(self) -> int:
        rates = [r for r in (self.shared.rate, self.own.rate) if r > 0]
        return min(rates) if rates else 0

    def acquire(self, n: int) -> None:
        self.shared.acquire(n)
        self.own.acquire(n)

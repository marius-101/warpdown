"""Download statistics: bytes and files per day and per category, kept in stats.json."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from .util import data_dir


class Stats:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "stats.json"
        self.days: dict[str, dict] = {}        # "2026-10-21" -> {"bytes": n, "files": n}
        self.cats: dict[str, dict] = {}        # category -> {"bytes": n, "files": n}
        try:
            d = json.loads(self.path.read_text())
            self.days, self.cats = d.get("days", {}), d.get("cats", {})
        except (OSError, ValueError, AttributeError):
            pass

    def record(self, size: int, category: str, day: date | None = None) -> None:
        key = (day or date.today()).isoformat()
        for table, k in ((self.days, key), (self.cats, category or "Other")):
            row = table.setdefault(k, {"bytes": 0, "files": 0})
            row["bytes"] += max(0, int(size))
            row["files"] += 1
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"days": self.days, "cats": self.cats}))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except OSError:
            pass

    def total(self) -> tuple[int, int]:
        return (sum(r["bytes"] for r in self.days.values()), sum(r["files"] for r in self.days.values()))

    def last_days(self, n: int, today: date | None = None) -> list[tuple[date, int]]:
        today = today or date.today()
        out = []
        for i in range(n - 1, -1, -1):
            d = today - timedelta(days=i)
            out.append((d, self.days.get(d.isoformat(), {}).get("bytes", 0)))
        return out

    def period(self, n: int, today: date | None = None) -> tuple[int, int]:
        today = today or date.today()
        keys = {(today - timedelta(days=i)).isoformat() for i in range(n)}
        rows = [r for k, r in self.days.items() if k in keys]
        return sum(r["bytes"] for r in rows), sum(r["files"] for r in rows)

    def reset(self) -> None:
        self.days, self.cats = {}, {}
        self.save()

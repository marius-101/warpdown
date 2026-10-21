"""Scheduler rules (pure Python): when may downloads run, and what happens when they are all finished."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .osutil import IS_WINDOWS

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")        # index = datetime.weekday()
AFTER_NOTHING, AFTER_QUIT, AFTER_SLEEP, AFTER_POWEROFF = "nothing", "quit", "sleep", "poweroff"
AFTER_LABELS = {AFTER_NOTHING: "Do nothing", AFTER_QUIT: "Quit Warpdown", AFTER_SLEEP: "Put the computer to sleep",
                AFTER_POWEROFF: "Shut the computer down"}


def power_command(action: str) -> list[str] | None:
    """The command that carries out a 'when finished' action, or None if Warpdown handles it itself."""
    if IS_WINDOWS:
        return {AFTER_SLEEP: ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                AFTER_POWEROFF: ["shutdown", "/s", "/t", "0"]}.get(action)
    return {AFTER_SLEEP: ["systemctl", "suspend"], AFTER_POWEROFF: ["systemctl", "poweroff"]}.get(action)


def _parse_time(text: str) -> tuple[int, int] | None:
    try:
        h, m = text.split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


@dataclass
class Schedule:
    """Downloads may run on the selected `days`.

    With a `stop` time this is a window (start..stop, may cross midnight: it belongs to the day it started).
    Without one, `start` is a trigger: at that time the queue is released and runs until it is finished.
    """

    enabled: bool = False
    start: tuple[int, int] = (2, 0)
    stop: tuple[int, int] | None = None
    days: frozenset = frozenset(range(7))

    @property
    def window_mode(self) -> bool:
        return self.stop is not None

    # ---- the window ----
    def gate(self, now: datetime) -> bool | None:
        """Window mode: may downloads run right now? None in trigger mode (the manager handles the trigger)."""
        if not self.enabled:
            return True
        if self.stop is None:
            return None
        s, t, cur = time(*self.start), time(*self.stop), now.time().replace(second=0, microsecond=0)
        today, yesterday = now.weekday(), (now.weekday() - 1) % 7
        if s == t:
            return today in self.days
        if s < t:
            return today in self.days and s <= cur < t
        return (cur >= s and today in self.days) or (cur < t and yesterday in self.days)

    def start_due(self, prev: datetime, now: datetime) -> bool:
        """Trigger mode: did an allowed start time pass between `prev` (exclusive) and `now` (inclusive)?"""
        if not self.enabled or self.stop is not None or now <= prev:
            return False
        day = prev.date()
        while day <= now.date():
            at = datetime.combine(day, time(*self.start))
            if day.weekday() in self.days and prev < at <= now:
                return True
            day += timedelta(days=1)
        return False

    def next_start(self, now: datetime) -> datetime | None:
        if not self.enabled or not self.days:
            return None
        for offset in range(8):
            day = (now + timedelta(days=offset)).date()
            at = datetime.combine(day, time(*self.start))
            if day.weekday() in self.days and at > now:
                return at
        return None

    def describe(self, now: datetime | None = None) -> str:
        if not self.enabled:
            return "The scheduler is off: downloads start as soon as they are added."
        days = "every day" if len(self.days) == 7 else ("on " + ", ".join(DAY_NAMES[d] for d in sorted(self.days)))
        if not self.days:
            return "No days selected: downloads will never start."
        s = f"{self.start[0]:02d}:{self.start[1]:02d}"
        if self.stop is None:
            text = f"Downloads wait until {s} {days}, then run until they are finished."
        else:
            t = f"{self.stop[0]:02d}:{self.stop[1]:02d}"
            text = f"Downloads run from {s} to {t} {days}; outside that time they wait."
        nxt = self.next_start(now or datetime.now())
        return text + (f"\nNext start: {nxt:%a %d %b, %H:%M}" if nxt else "")

    # ---- saving ----
    def to_settings(self) -> dict:
        return {"sched_enabled": self.enabled, "sched_start": f"{self.start[0]:02d}:{self.start[1]:02d}",
                "sched_stop": "" if self.stop is None else f"{self.stop[0]:02d}:{self.stop[1]:02d}",
                "sched_days": ",".join(str(d) for d in sorted(self.days))}

    @classmethod
    def from_settings(cls, d: dict) -> "Schedule":
        days = frozenset(int(x) for x in str(d.get("sched_days", "0,1,2,3,4,5,6")).split(",") if x.strip().isdigit()
                         and 0 <= int(x) <= 6)
        return cls(enabled=str(d.get("sched_enabled", "false")).lower() == "true",
                   start=_parse_time(str(d.get("sched_start", "02:00"))) or (2, 0),
                   stop=_parse_time(str(d.get("sched_stop", ""))), days=days)

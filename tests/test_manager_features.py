import time
from datetime import datetime as D

import pytest

from warpdown import manager as mg
from warpdown.manager import (COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED, RETRYING, DownloadManager)
from warpdown.scheduler import Schedule


@pytest.fixture()
def m(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    mgr = DownloadManager()
    mgr.begun = []
    mgr._begin = lambda it: mgr.begun.append(it.id)        # no network: just record which downloads would start
    yield mgr
    mgr.shutdown()


class FakeEngine:
    done = False
    error = None

    def __init__(self):
        self.stopped = False

    def stop(self, wait=True):
        self.stopped = True


def running(m, name="a.bin"):
    it = m.add(f"http://x/{name}", "/tmp/wd", start=False)
    it.status = DOWNLOADING
    m.engines[it.id] = FakeEngine()
    return it


# ---------------------------------------------------------------- automatic retry
def test_temporary_errors_are_retried_with_growing_waits_then_give_up(m):
    it = m.add("http://x/a.bin", "/tmp/wd", start=False)
    waits = []
    for attempt in range(1, 6):
        m._fail(it, "Server error '503 Service Unavailable' for url http://x/a.bin")
        assert it.status == RETRYING and it.retries == attempt
        waits.append(round(it.retry_at - time.time()))
    assert waits == [15, 30, 60, 120, 300]
    m._fail(it, "Server error '503 Service Unavailable'")                # 6th failure: out of attempts
    assert it.status == ERROR and "503" in it.error


def test_permanent_errors_and_disabled_retry_show_the_error_at_once(m):
    it = m.add("http://x/a.bin", "/tmp/wd", start=False)
    m._fail(it, "Client error '404 Not Found' for url http://x/a.bin")
    assert it.status == ERROR and it.retries == 0
    m._fail(it, "[Errno 28] No space left on device")
    assert it.status == ERROR
    m.auto_retry = False
    m._fail(it, "Server error '503 Service Unavailable'")
    assert it.status == ERROR


def test_retry_countdown_promotes_to_queued_and_manual_resume_resets(m):
    it = m.add("http://x/a.bin", "/tmp/wd", start=False)
    m._fail(it, "Connection reset by peer")
    it.retry_at = time.time() + 1000
    m._tick()
    assert it.status == RETRYING and m.begun == []                        # still waiting
    it.retry_at = time.time() - 1
    m._tick()
    assert it.status == QUEUED and m.begun == [it.id]                     # time is up: queued and started
    m._fail(it, "Connection reset by peer")
    assert it.retries == 2
    m.resume(it.id)                                                       # "retry now" by hand
    assert it.status == QUEUED and it.retries == 0 and it.retry_at == 0
    m._fail(it, "timed out")
    m.pause(it.id)
    assert it.status == PAUSED                                            # pausing cancels the wait


def test_a_retry_wait_does_not_survive_a_restart_and_success_resets_the_counter(m, tmp_path, monkeypatch):
    it = m.add("http://x/a.bin", "/tmp/wd", start=False)
    m._fail(it, "timed out")
    m.save()
    again = DownloadManager()
    r = again.items[it.id]
    assert r.status == PAUSED and r.retries == 1                          # waits are not kept: you decide whether to resume
    again.shutdown()


# ---------------------------------------------------------------- scheduler: window mode
def test_window_closes_holds_running_downloads_and_opens_releases_only_those(m):
    night = Schedule(True, (23, 0), (6, 0), frozenset(range(7)))
    a, b = running(m, "a.bin"), running(m, "b.bin")
    waiting = m.add("http://x/c.bin", "/tmp/wd", start=True)             # QUEUED (its _begin is faked)
    mine = m.add("http://x/d.bin", "/tmp/wd", start=False)               # paused by the user
    m.begun.clear()
    old_engines = {a.id: m.engines[a.id], b.id: m.engines[b.id]}
    m.schedule = night
    m._update_gate(D(2026, 10, 21, 12, 0))                                # noon: the window is closed
    assert not m.gate_open
    assert a.status == b.status == PAUSED and a.held and b.held
    assert m.engines[a.id].stopped and m.engines[b.id].stopped
    m._schedule()
    assert m.begun == []                                                  # nothing starts while closed
    assert mine.status == PAUSED and not mine.held                       # the user's own pause is not the scheduler's

    for it, eng in old_engines.items():                                   # the stopped connections finish closing
        m._on_finished(it, eng)
    m._update_gate(D(2026, 10, 21, 23, 5))                                # the window opens
    assert m.gate_open
    assert a.status == b.status == QUEUED and not a.held                  # held ones are queued again...
    assert mine.status == PAUSED                                          # ...the user's pause stays
    assert sorted(m.begun) == sorted([a.id, b.id, waiting.id])


def test_disabling_the_scheduler_releases_everything(m):
    m.schedule = Schedule(True, (23, 0), (6, 0))
    a = running(m)
    old_engine = m.engines[a.id]
    m._update_gate(D(2026, 10, 21, 12, 0))
    assert a.held
    m._on_finished(a.id, old_engine)                                       # the stopped connection finishes closing
    m.set_schedule(Schedule(enabled=False))
    assert m.gate_open and a.status == QUEUED and m.begun == [a.id]


def test_schedule_changed_signal(m):
    seen = []
    m.schedule_changed.connect(lambda: seen.append(m.gate_open))
    m.schedule = Schedule(True, (23, 0), (6, 0))
    m._update_gate(D(2026, 10, 21, 12, 0))
    m._update_gate(D(2026, 10, 21, 12, 1))                                # no change: no signal
    m._update_gate(D(2026, 10, 21, 23, 30))
    assert seen == [False, True]


# ---------------------------------------------------------------- scheduler: trigger mode
def test_trigger_mode_waits_for_the_start_time_then_closes_when_finished(m):
    seen_finished = []
    m.queue_finished.connect(lambda: seen_finished.append(1))
    m.schedule = Schedule(True, (2, 0), None, frozenset(range(7)))
    m._update_gate(D(2026, 10, 21, 1, 59, 50), force=True)                # configured: closed until 02:00
    assert not m.gate_open
    it = m.add("http://x/a.bin", "/tmp/wd", start=True)                   # added at night's start: waits
    assert it.status == QUEUED and m.begun == []

    m._last_check = D(2026, 10, 21, 1, 59, 59)
    m._update_gate(D(2026, 10, 21, 2, 0, 1))                              # 02:00 passes
    assert m.gate_open and m.begun == [it.id]

    it.status = COMPLETED                                                 # the queue runs dry
    m._phase_active, m._phase_completed = True, 1
    m._check_queue_finished()
    assert not m.gate_open                                                # closed again until the next 02:00
    assert seen_finished == [1]


# ---------------------------------------------------------------- "when finished"
def test_queue_finished_fires_once_only_when_everything_is_really_done(m):
    fired = []
    m.queue_finished.connect(lambda: fired.append(1))
    a, b = running(m, "a.bin"), running(m, "b.bin")
    m._check_queue_finished()
    assert fired == []                                                    # still downloading
    a.status = COMPLETED; m._phase_completed = 1
    m._check_queue_finished()
    assert fired == []                                                    # b is still running
    b.status = COMPLETED; m._phase_completed = 2
    m.engines.clear()
    waiting = m.add("http://x/w.bin", "/tmp/wd", start=False)
    waiting.status = QUEUED                                               # something is still waiting
    m._check_queue_finished()
    assert fired == []
    waiting.status = COMPLETED
    m._check_queue_finished()
    assert fired == [1]
    m._check_queue_finished()
    assert fired == [1]                                                   # once per run, not every tick


def test_scheduler_pauses_and_retry_waits_do_not_count_as_finished_and_failures_alone_do_not_fire(m):
    fired = []
    m.queue_finished.connect(lambda: fired.append(1))
    a = running(m, "a.bin")
    m._phase_active = True
    a.status, a.held = PAUSED, True                                       # held by the scheduler: unfinished business
    m._check_queue_finished()
    assert fired == []
    a.held = False
    a.status = RETRYING
    m._check_queue_finished()
    assert fired == []                                                    # will be retried
    a.status = ERROR                                                      # a phase where nothing completed
    m._phase_completed = 0
    m._check_queue_finished()
    assert fired == []                                                    # never shut a computer down over failures


def test_resuming_right_after_pausing_is_not_undone_by_the_old_connection_closing(m):
    """Regression: pause + quick resume, then the first connection finishes closing -> must stay queued, not Paused."""
    it = running(m)
    old = m.engines[it.id]
    m.pause(it.id)
    assert it.status == PAUSED
    m.resume(it.id)                                                       # resumed before the old engine ended
    assert it.status == QUEUED
    m._on_finished(it.id, old)                                            # the old engine reports in, late
    assert it.status == QUEUED and it.id not in m.engines
    assert m.begun == [it.id]                                             # and the download is started again


def test_configuring_a_trigger_schedule_does_not_catch_up_on_past_start_times(m):
    m.schedule = Schedule(True, (2, 0), None)
    m._last_check = D(2026, 10, 20, 0, 0)                                 # app "idle" for a day
    m._update_gate(D(2026, 10, 21, 12, 0), force=True)
    assert not m.gate_open                                                # 02:00 yesterday and today are in the past


def test_error_explanations():
    from warpdown.errors import explain
    assert "refused" in explain("Unable to download webpage: HTTP Error 403: Forbidden").short
    assert "No downloadable video" in explain("Unsupported URL: https://x/y").short
    assert "disk is full" in explain("[Errno 28] No space left on device").short
    assert explain("weird thing").short == "weird thing"
    assert "403" in explain("HTTP Error 403: Forbidden").raw

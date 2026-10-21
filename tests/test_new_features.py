"""Speed limits, checksums, queue order/priority, search, statistics, VPN pause and torrents (against a fake aria2)."""
import json
import threading
import time
from datetime import date, datetime as D
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from warpdown import checksum, clipwatch, manager as mg, torrent, vpn
from warpdown.engine import Job, RateLimiter
from warpdown.limits import ChainedLimiter, LimitSchedule
from warpdown.manager import COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED, DownloadManager
from warpdown.stats import Stats


@pytest.fixture()
def m(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    mgr = DownloadManager()
    mgr.begun = []
    mgr._begin = lambda it: (mgr.begun.append(it.id), setattr(it, "status", DOWNLOADING))
    yield mgr
    mgr.shutdown()


def pump(qapp, seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------- speed limits
def test_limit_schedule_windows_including_past_midnight():
    day = LimitSchedule(True, 100, "08:00", "17:00")
    assert day.active(D(2026, 1, 1, 9, 0)) and not day.active(D(2026, 1, 1, 17, 0)) and not day.active(D(2026, 1, 1, 7, 59))
    night = LimitSchedule(True, 100, "22:00", "06:00")
    assert night.active(D(2026, 1, 1, 23, 0)) and night.active(D(2026, 1, 1, 5, 0)) and not night.active(D(2026, 1, 1, 12, 0))
    assert not LimitSchedule(False, 100, "00:00", "23:59").active(D(2026, 1, 1, 12, 0))


def test_global_limit_follows_the_schedule(m):
    m.set_speed_limit(2000 * 1024)
    assert m.limiter.rate == 2000 * 1024
    m.set_limit_schedule(LimitSchedule(True, 300, "00:00", "23:59"))
    m._apply_limit(D(2026, 1, 1, 12, 0))
    assert m.limiter.rate == 300 * 1024
    m._apply_limit(D(2026, 1, 1, 23, 59, 30))                 # outside the window again
    assert m.limiter.rate == 2000 * 1024


def test_chained_limiter_uses_the_stricter_rate():
    g, o = RateLimiter(500), RateLimiter(0)
    c = ChainedLimiter(g, o)
    assert c.rate == 500
    o.rate = 200
    assert c.rate == 200
    g.rate = 0
    assert c.rate == 200
    o.rate = 0
    assert c.rate == 0


def test_item_limit_is_stored_and_updates_a_running_download(m):
    it = m.add("http://x/a.bin", "/tmp/wd", start=False)
    m._own_limiters[it.id] = RateLimiter(0)
    m.set_item_limit(it.id, 250)
    assert it.limit_kb == 250 and m._own_limiters[it.id].rate == 250 * 1024
    m.set_item_limit(it.id, -5)
    assert it.limit_kb == 0


# ---------------------------------------------------------------- checksums
def test_checksum_parsing():
    assert checksum.parse_expected("sha256:" + "A" * 64) == ("sha256", "a" * 64)
    assert checksum.parse_expected("b" * 32 + "  disk.iso") == ("md5", "b" * 32)
    assert checksum.parse_expected("sha1=" + "c" * 40) == ("sha1", "c" * 40)
    assert checksum.parse_expected("md5:" + "d" * 64) is None          # wrong length for the named algorithm
    assert checksum.parse_expected("not a checksum") is None and checksum.parse_expected("") is None


def test_checksum_files_and_verification(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    import hashlib
    good = hashlib.sha256(b"hello").hexdigest()
    assert checksum.verify(f, good) == (True, "sha256")
    assert checksum.verify(f, "0" * 64) == (False, "sha256")
    with pytest.raises(ValueError):
        checksum.verify(f, "nonsense")
    listing = f"{good}  a.bin\n{'e' * 64} *b.bin\n"
    assert checksum.find_in_text(listing, "a.bin") == good
    assert checksum.find_in_text(listing, "b.bin") == "e" * 64
    assert checksum.find_in_text(listing, "c.bin") is None
    assert checksum.find_in_text(good + "\n", "anything.iso") == good
    assert checksum.sidecar_candidates("http://x/a.iso?dl=1")[0] == "http://x/a.iso.sha256"


def test_manager_verifies_a_finished_download(m, qapp, tmp_path):
    import hashlib
    f = tmp_path / "file.bin"
    f.write_bytes(b"warpdown")
    it = m.add("http://x/file.bin", str(tmp_path), filename="file.bin", start=False)
    it.status = COMPLETED
    m.set_checksum(it.id, hashlib.sha256(b"warpdown").hexdigest())
    pump(qapp)
    assert it.verified == "ok" and it.verify_algo == "sha256"
    m.set_checksum(it.id, "f" * 64)
    pump(qapp)
    assert it.verified == "bad"
    with pytest.raises(ValueError):
        m.set_checksum(it.id, "garbage")


# ---------------------------------------------------------------- queue order and priority
def names(m):
    return [i.url.rsplit("/", 1)[1] for i in m.queue_order()]


def test_move_within_the_queue(m):
    a, b, c, d = (m.add(f"http://x/{n}", "/tmp/wd", start=False) for n in "abcd")
    assert names(m) == ["a", "b", "c", "d"]
    m.move([c.id], "top")
    assert names(m) == ["c", "a", "b", "d"]
    m.move([c.id], "down")
    assert names(m) == ["a", "c", "b", "d"]
    m.move([d.id], "up")
    assert names(m) == ["a", "c", "d", "b"]
    m.move([a.id, c.id], "bottom")
    assert names(m) == ["d", "b", "a", "c"]
    m.move([d.id], "up")                                    # already first: nothing changes
    assert names(m) == ["d", "b", "a", "c"]
    m.move([a.id, c.id], "down")                            # already last: nothing changes
    assert names(m) == ["d", "b", "a", "c"]


def test_priority_decides_what_starts_first(m):
    m.max_active = 1
    a = m.add("http://x/a", "/tmp/wd", start=False)
    b = m.add("http://x/b", "/tmp/wd", start=False)
    c = m.add("http://x/c", "/tmp/wd", start=False)
    for it in (a, b, c):
        it.status = QUEUED
    m.set_priority(c.id, 1)
    m.set_priority(a.id, -1)
    assert names(m) == ["c", "b", "a"]
    m._schedule()
    assert m.begun == [c.id]
    m.set_priority(c.id, 9)
    assert c.priority == 1


def test_model_lists_in_queue_order_and_filters_by_search(m, qapp):
    from warpdown import gui
    a = m.add("http://x/holiday.mp4", "/tmp/wd", filename="holiday.mp4", start=False)
    b = m.add("http://x/report.pdf", "/tmp/wd", filename="report.pdf", start=False)
    model = gui.Model(m)
    assert model.rows == [a.id, b.id]
    m.move([b.id], "top")
    model.rebuild()
    assert model.rows == [b.id, a.id]
    model.search = "HOLI mp4"
    model.rebuild()
    assert model.rows == [a.id]
    model.search = "nothing like this"
    model.rebuild()
    assert model.rows == []


# ---------------------------------------------------------------- VPN
def test_vpn_names():
    assert all(vpn.is_vpn_name(n) for n in ("wg0", "tun0", "proton0", "tailscale0", "ProtonVPN", "NordLynx", "OpenVPN Data Channel Offload"))
    assert not any(vpn.is_vpn_name(n) for n in ("eth0", "enp3s0", "wlan0", "lo", "docker0", "Ethernet", "Wi-Fi"))


def test_downloads_pause_when_the_vpn_drops_and_resume_when_it_returns(m, monkeypatch):
    state = {"up": True}
    monkeypatch.setattr(mg, "vpn_connected", lambda: state["up"])
    events = []
    m.vpn_changed.connect(events.append)
    eng = type("E", (), {"done": False, "error": None, "stopped": False,
                         "stop": lambda self, wait=True: setattr(self, "stopped", True)})()
    run = m.add("http://x/run", "/tmp/wd", start=False)
    run.status = DOWNLOADING
    m.engines[run.id] = eng
    waiting = m.add("http://x/wait", "/tmp/wd", start=False)
    waiting.status = QUEUED
    m.set_require_vpn(True)
    assert not m.vpn_blocked
    state["up"] = False
    m.set_require_vpn(True)                                   # re-check right away
    assert m.vpn_blocked and events == [True]
    assert run.status == PAUSED and run.held and eng.stopped
    m.begun.clear()
    m._schedule()
    assert m.begun == []                                      # nothing may start while the VPN is down
    m.engines.pop(run.id)                                     # the stopped engine reports back
    state["up"] = True
    m._vpn_checked = 0.0
    m._tick()
    assert not m.vpn_blocked and events == [True, False]
    assert not run.held and run.id in m.begun
    m.set_require_vpn(False)


def test_turning_the_vpn_rule_off_releases_everything(m, monkeypatch):
    monkeypatch.setattr(mg, "vpn_connected", lambda: False)
    it = m.add("http://x/a", "/tmp/wd", start=False)
    it.status = QUEUED
    m.set_require_vpn(True)
    assert m.vpn_blocked
    m.set_require_vpn(False)
    assert not m.vpn_blocked and m.begun == [it.id]


# ---------------------------------------------------------------- statistics
def test_statistics(tmp_path):
    s = Stats(tmp_path / "stats.json")
    today = date(2026, 10, 21)
    s.record(1000, "Video", today)
    s.record(500, "Music", today)
    s.record(2000, "Video", date(2026, 10, 18))
    s.record(9999, "Other", date(2026, 8, 1))
    assert s.total() == (13499, 4)
    assert s.period(1, today) == (1500, 2)
    assert s.period(7, today) == (3500, 3)
    days = s.last_days(3, today)
    assert [b for _d, b in days] == [0, 0, 1500]
    assert s.cats["Video"] == {"bytes": 3000, "files": 2}
    again = Stats(tmp_path / "stats.json")                    # survives a restart
    assert again.total() == (13499, 4)
    again.reset()
    assert Stats(tmp_path / "stats.json").total() == (0, 0)


def test_finished_downloads_are_counted(m):
    it = m.add("http://x/a.zip", "/tmp/wd", filename="a.zip", start=False)
    it.status, it.size, it.category = DOWNLOADING, 4096, "Compressed"
    eng = type("E", (), {"done": True, "error": None, "job": Job("http://x/a.zip", Path("/tmp/wd/a.zip"), 4096, True, 1, {}, {}),
                         "downloaded": lambda self: 4096})()
    m.engines[it.id] = eng
    m._on_finished(it.id, eng)
    assert it.status == COMPLETED
    assert m.stats.total()[1] == 1 and m.stats.cats["Compressed"]["bytes"] == 4096


# ---------------------------------------------------------------- torrents
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Test%20Distro"


def test_torrent_sources_are_recognised(m):
    assert torrent.is_torrent_source(MAGNET) and torrent.is_torrent_source("https://x/y/a.torrent?z=1")
    assert torrent.is_torrent_source("/home/u/a.torrent") and not torrent.is_torrent_source("https://x/a.zip")
    assert torrent.magnet_name(MAGNET) == "Test Distro"
    assert m.add(MAGNET, "/tmp/wd", start=False).kind == "torrent"
    assert clipwatch.pick(f"look: {MAGNET} and https://x/f.zip") == [MAGNET, "https://x/f.zip"]


def test_torrent_without_aria2_explains_what_to_install(m, monkeypatch, tmp_path):
    monkeypatch.setattr(torrent, "available", lambda: False)
    it = m.add(MAGNET, str(tmp_path), start=False)
    it.status = QUEUED
    DownloadManager._begin(m, it)
    assert it.status == ERROR and "aria2" in it.error
    from warpdown.errors import explain
    assert "aria2" in explain(it.error).short


class FakeAria2(BaseHTTPRequestHandler):
    calls: list = []
    polls = {"g1": 0, "g2": 0}

    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert req["params"][0] == "token:secret"
        method, params = req["method"], req["params"][1:]
        FakeAria2.calls.append(method)
        result = "OK"
        if method == "aria2.getVersion":
            result = {"version": "1.37"}
        elif method == "aria2.addUri":
            result = "g1"
        elif method == "aria2.tellStatus":
            gid = params[0]
            FakeAria2.polls[gid] += 1
            n = FakeAria2.polls[gid]
            if gid == "g1":
                result = {"status": "complete", "followedBy": ["g2"]}          # the magnet's metadata finished
            else:
                done = n >= 2
                result = {"status": "complete" if done else "active", "totalLength": "1000",
                          "completedLength": "1000" if done else "400", "downloadSpeed": "0" if done else "100",
                          "connections": "5", "bittorrent": {"info": {"name": "Test Distro"}}}
        body = json.dumps({"jsonrpc": "2.0", "id": "w", "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeDownloader(torrent.Aria2Downloader):
    def _spawn(self):
        self.srv = HTTPServer(("127.0.0.1", 0), FakeAria2)
        self._port, self._token = self.srv.server_address[1], "secret"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _shutdown_aria2(self):
        super()._shutdown_aria2()
        self.srv.shutdown()


def test_torrent_download_follows_a_magnet_to_completion(tmp_path):
    FakeAria2.calls, FakeAria2.polls = [], {"g1": 0, "g2": 0}
    finished = threading.Event()
    lim = RateLimiter(100 * 1024)
    job = Job(MAGNET, tmp_path / "torrent", -1, False, 1, {}, {})
    eng = FakeDownloader(job, lim, on_finish=lambda d: finished.set())
    eng.start()
    assert finished.wait(15)
    assert eng.done and not eng.error
    assert eng.name == "Test Distro" and eng.job.dest == tmp_path / "Test Distro"
    assert eng.downloaded() == 1000 and eng.estimated_size() == 1000
    assert "aria2.addUri" in FakeAria2.calls and "aria2.changeGlobalOption" in FakeAria2.calls
    assert FakeAria2.calls[-1] == "aria2.shutdown"

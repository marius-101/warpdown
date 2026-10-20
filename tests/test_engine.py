import os
import time

from warpdown.engine import Downloader, Job, probe
from tests.rangeserver import make_server

PAYLOAD = os.urandom(12 * 1024 * 1024 + 123)


def run(job, timeout=60):
    d = Downloader(job)
    d.start()
    d._main_thread.join(timeout)
    return d


def test_probe_and_segmented(tmp_path):
    srv, url = make_server(PAYLOAD)
    p = probe(url)
    assert p.size == len(PAYLOAD) and p.resumable and p.filename == "test file.bin"
    d = run(Job(url, tmp_path / "out.bin", p.size, p.resumable, connections=8))
    assert d.done, d.error
    assert (tmp_path / "out.bin").read_bytes() == PAYLOAD
    assert not (tmp_path / "out.bin.part").exists()
    srv.shutdown()


def test_no_range_fallback(tmp_path):
    srv, url = make_server(PAYLOAD, ranges=False)
    p = probe(url)
    assert not p.resumable
    d = run(Job(url, tmp_path / "out.bin", p.size, p.resumable))
    assert d.done, d.error
    assert (tmp_path / "out.bin").read_bytes() == PAYLOAD
    srv.shutdown()


def test_pause_resume(tmp_path):
    srv, url = make_server(PAYLOAD, delay=0.05)
    p = probe(url)
    job = Job(url, tmp_path / "out.bin", p.size, p.resumable, connections=4)
    d = Downloader(job)
    d.start()
    time.sleep(0.8)
    d.stop()
    got = d.downloaded()
    assert 0 < got < len(PAYLOAD) and not d.done
    d2 = Downloader(job)
    d2.start()
    d2._main_thread.join(60)
    assert d2.done, d2.error
    assert (tmp_path / "out.bin").read_bytes() == PAYLOAD
    srv.shutdown()

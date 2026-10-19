import os
import time


from kudm.manager import COMPLETED, DownloadManager
from tests.rangeserver import make_server

PAYLOAD = os.urandom(5 * 1024 * 1024 + 7)


def test_manager_end_to_end(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    app = qapp
    srv, url = make_server(PAYLOAD)
    m = DownloadManager()
    it = m.add(url, str(tmp_path))
    t0 = time.time()
    while it.status != COMPLETED and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.05)
    assert it.status == COMPLETED, (it.status, it.error)
    assert (tmp_path / "Other" / "test file.bin").read_bytes() == PAYLOAD
    m.shutdown()
    assert DownloadManager().items[it.id].status == COMPLETED
    srv.shutdown()


def test_category_folders(qapp, tmp_path, monkeypatch):
    from kudm.util import CATEGORIES, ensure_category_dirs
    ensure_category_dirs(tmp_path)
    assert {d.name for d in tmp_path.iterdir()} == set(CATEGORIES) | {"Other"}

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    srv, url = make_server(PAYLOAD)
    m = DownloadManager()
    it = m.add(url + "/clip.mp4", str(tmp_path), filename="clip.mp4")       # -> Video/
    odd = m.add(url + "/x", str(tmp_path), filename="notes.xyz")            # unknown -> Other/
    nosort = m.add(url + "/y", str(tmp_path), filename="b.zip", sort=False)  # opted out -> root
    t0 = time.time()
    while any(i.status != COMPLETED for i in (it, odd, nosort)) and time.time() - t0 < 40:
        qapp.processEvents()
        time.sleep(0.05)
    assert (tmp_path / "Video" / "clip.mp4").exists()
    assert (tmp_path / "Other" / "notes.xyz").exists()
    assert (tmp_path / "b.zip").exists()
    m.shutdown()
    srv.shutdown()


def test_extension_guessing_and_sorting(qapp, tmp_path, monkeypatch):
    """A page-title name with no extension must still become .mp4 and land in Video/."""
    from kudm.util import guess_extension, sniff_ext
    mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 64
    assert sniff_ext(mp4) == ".mp4" and guess_extension("clip", "video/mp4") == ".mp4"
    assert guess_extension("clip.mkv", "video/mp4") == "" and guess_extension("Episode 1", "", mp4) == ".mp4"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    srv, url = make_server(mp4 + os.urandom(2 * 1024 * 1024))
    m = DownloadManager()
    it = m.add(url, str(tmp_path), filename="Echovideo - Some Title : Episode 1")
    t0 = time.time()
    while it.status != COMPLETED and time.time() - t0 < 30:
        qapp.processEvents()
        time.sleep(0.05)
    assert it.status == COMPLETED, (it.status, it.error)
    assert (tmp_path / "Video" / "Echovideo - Some Title : Episode 1.mp4").exists()
    m.shutdown()
    srv.shutdown()

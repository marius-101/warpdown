import sys
import os
import time


from warpdown.manager import COMPLETED, DownloadManager
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
    from warpdown.util import CATEGORIES, ensure_category_dirs
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
    from warpdown.util import guess_extension, sniff_ext
    mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 64
    assert sniff_ext(mp4) == ".mp4" and guess_extension("clip", "video/mp4") == ".mp4"
    assert guess_extension("clip.mkv", "video/mp4") == "" and guess_extension("Episode 1", "", mp4) == ".mp4"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    srv, url = make_server(mp4 + os.urandom(2 * 1024 * 1024))
    m = DownloadManager()
    it = m.add(url, str(tmp_path), filename="Example Site - Some Title : Episode 1")
    t0 = time.time()
    while it.status != COMPLETED and time.time() - t0 < 30:
        qapp.processEvents()
        time.sleep(0.05)
    assert it.status == COMPLETED, (it.status, it.error)
    expected = "Example Site - Some Title _ Episode 1.mp4" if sys.platform.startswith("win") else "Example Site - Some Title : Episode 1.mp4"
    assert (tmp_path / "Video" / expected).exists()               # Windows cannot have ":" in a file name
    m.shutdown()
    srv.shutdown()


def test_migration_from_kudm(tmp_path, monkeypatch):
    """0.5 renamed KUDM -> Warpdown: the download list and settings must carry over, once, without overwriting."""
    from warpdown.util import migrate_legacy_data
    data, conf = tmp_path / "data", tmp_path / "conf"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(conf))
    (data / "kudm").mkdir(parents=True)
    (data / "kudm" / "downloads.json").write_text('[{"url": "http://x/a.bin", "directory": "/tmp"}]')
    (conf / "kudm").mkdir(parents=True)
    (conf / "kudm" / "kudm.conf").write_text("[General]\nconnections=12\n")

    assert len(migrate_legacy_data()) == 2
    assert (data / "warpdown" / "downloads.json").read_text().startswith('[{"url"')
    assert (conf / "warpdown" / "warpdown.conf").read_text() == "[General]\nconnections=12\n"
    assert (data / "kudm" / "downloads.json").exists()            # old data stays as a backup

    (data / "warpdown" / "downloads.json").write_text("[]")      # user data created later must never be overwritten
    assert migrate_legacy_data() == []
    assert (data / "warpdown" / "downloads.json").read_text() == "[]"


def test_legacy_resume_state_file_is_adopted(tmp_path):
    from warpdown.engine import Downloader, Job
    dest = tmp_path / "big.bin"
    (tmp_path / "big.bin.part.kudm").write_text("{}")
    d = Downloader(Job("http://x/big.bin", dest))
    assert d.state_file.exists() and not (tmp_path / "big.bin.part.kudm").exists()

import functools
import http.server
import shutil
import subprocess
import threading
import time

import pytest

from warpdown import ytdlp
from warpdown.engine import Job
from warpdown.manager import COMPLETED, DownloadManager

pytestmark = pytest.mark.skipif(not (ytdlp.available() and shutil.which("ffmpeg")),
                                reason="needs yt-dlp and ffmpeg")


@pytest.fixture()
def site(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-c:a", "aac", str(d / "clip.mp4")],
                   check=True)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    h.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/clip.mp4"
    srv.shutdown()


def test_parse_progress_and_hosts():
    assert ytdlp.parse_progress("WDPROG|1024|223779|NA|334629.3|Clip.f395.mp4\n") == ("Clip.f395.mp4", 1024, 223779, 334629.3)
    assert ytdlp.parse_progress("WDPROG|500|NA|9000|NA|a.m4a")[2] == 9000      # falls back to the estimate
    assert ytdlp.parse_progress("[download] 5%") is None
    assert ytdlp.looks_like_video_site("https://www.youtube.com/watch?v=x") and ytdlp.looks_like_video_site("https://youtu.be/x")
    assert not ytdlp.looks_like_video_site("https://example.com/a.mp4")
    assert ytdlp.is_youtube("https://m.youtube.com/watch?v=x") and not ytdlp.is_youtube("https://vimeo.com/1")


def test_fetch_info_and_download(site, tmp_path):
    info = ytdlp.fetch_info(site)
    assert info["title"] == "clip" and not info["live"] and info["subs"] == []

    out = tmp_path / "out"
    d = ytdlp.YtdlpDownloader(Job(site, out / "My Clip.mp4", opts={"format": ytdlp.QUALITIES["Best available"]}))
    d.start()
    d._main_thread.join(90)
    assert d.done, d.error
    assert d.job.dest == out / "My Clip.mp4" and d.job.dest.stat().st_size > 1000
    assert d.downloaded() > 0 and d.estimated_size() >= d.downloaded()


def test_bad_url_reports_error(tmp_path):
    d = ytdlp.YtdlpDownloader(Job("http://127.0.0.1:9/nothing", tmp_path / "x.mp4"))
    d.start()
    d._main_thread.join(60)
    assert not d.done and d.error


def test_manager_ytdlp_item_sorted_into_video_folder(qapp, site, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    m = DownloadManager()
    it = m.add(site, str(tmp_path / "dl"), filename="Great Video.mp4", kind="ytdlp",
               opts={"format": ytdlp.QUALITIES["Best available"], "subs": []})
    t0 = time.time()
    while it.status != COMPLETED and it.error == "" and time.time() - t0 < 90:
        qapp.processEvents()
        time.sleep(0.05)
    assert it.status == COMPLETED, (it.status, it.error)
    assert (tmp_path / "dl" / "Video" / "Great Video.mp4").exists() and it.category == "Video"
    m.shutdown()


def test_cookies_are_not_sent_to_youtube():
    h = {"Cookie": "SID=secret", "Referer": "https://x/", "User-Agent": "UA"}
    yt = ytdlp._common_args(h, "https://www.youtube.com/watch?v=abc")
    assert "Cookie:SID=secret" not in " ".join(yt) and "--referer" in yt and "--user-agent" in yt
    other = ytdlp._common_args(h, "https://vimeo.com/123")
    assert "Cookie:SID=secret" in other                       # other sites may need the login

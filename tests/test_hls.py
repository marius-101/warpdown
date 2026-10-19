import functools
import http.server
import os
import shutil
import subprocess
import threading
import time

import pytest

from kudm.engine import Job, is_hls, probe
from kudm.hls import HlsDownloader, HlsError, parse_master, parse_media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed to build test streams")


def make_stream(d, encrypted: bool):
    d.mkdir()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=25",
           "-f", "lavfi", "-i", "sine=duration=6", "-c:v", "libx264", "-c:a", "aac", "-f", "hls",
           "-hls_time", "2", "-hls_playlist_type", "vod", "-hls_segment_filename", str(d / "seg%d.ts")]
    if encrypted:
        (d / "enc.key").write_bytes(os.urandom(16))
        (d / "keyinfo").write_text(f"enc.key\n{d / 'enc.key'}\n")
        cmd += ["-hls_key_info_file", str(d / "keyinfo")]
    subprocess.run(cmd + [str(d / "index.m3u8")], check=True)


def serve(d):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    h.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/index.m3u8"


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


@pytest.mark.parametrize("encrypted", [False, True])
def test_hls_download(tmp_path, encrypted):
    make_stream(tmp_path / "site", encrypted)
    srv, url = serve(tmp_path / "site")
    p = probe(url)
    assert p.hls and p.filename == "video.mp4"
    d = HlsDownloader(Job(url, tmp_path / "out" / "video.mp4", connections=4))
    d.start()
    d._main_thread.join(60)
    assert d.done, d.error
    assert abs(duration(tmp_path / "out" / "video.mp4") - 6.0) < 0.5
    assert not (tmp_path / "out" / "video.mp4.parts").exists()
    srv.shutdown()


def test_parsers():
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720\nhigh/p.m3u8\n"
    assert parse_master(master, "http://h/a/m.m3u8") == ("http://h/a/high/p.m3u8", None)
    assert parse_master("#EXTM3U\n#EXTINF:2,\ns.ts\n", "http://h/") is None
    with pytest.raises(HlsError, match="DRM"):
        parse_media('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k"\n#EXTINF:2,\ns.ts\n#EXT-X-ENDLIST\n', "http://h/")
    assert parse_media("#EXTM3U\n#EXTINF:2,\ns.ts\n", "http://h/").live
    assert is_hls("http://h/x/index.m3u8?token=1") and not is_hls("http://h/x.mp4")


def test_playlist_without_m3u8_extension_is_detected(tmp_path):
    """Real-world case: URL like /cdn/abc?t.m3u8 served as text/plain; detect by content."""
    from tests.rangeserver import make_server
    body = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow.m3u8\n"
    srv, url = make_server(body)          # serves everything as a plain 200/206 file
    p = probe(url)
    assert p.hls and p.filename.endswith(".mp4")
    srv.shutdown()
    assert is_hls("https://h/cdn/abc?t.m3u8&v.m3u8")

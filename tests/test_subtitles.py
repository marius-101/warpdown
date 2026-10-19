import functools
import http.server
import os
import threading
import time

from kudm.hls import parse_subtitle_tracks
from kudm.manager import COMPLETED, DownloadManager
from kudm.subtitles import fetch_subtitle, merge_vtt, sniff_subtitle, tag_for
from tests.rangeserver import make_server

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"


def serve_dir(d):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    h.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_sniff_and_merge():
    assert sniff_subtitle(VTT) == ".vtt" and sniff_subtitle(SRT) == ".srt"
    assert sniff_subtitle("[Script Info]\nTitle: x") == ".ass" and sniff_subtitle("<html>nope</html>") == ""
    merged = merge_vtt([VTT, "WEBVTT\nX-TIMESTAMP-MAP=MPEGTS:0,LOCAL:0\n\n00:00:03.000 --> 00:00:04.000\nBye\n"])
    assert merged.count("WEBVTT") == 1 and "Hello" in merged and "Bye" in merged
    assert tag_for("eng", "", "http://x/a.vtt") == "eng" and tag_for("", "", "http://x/en-US.vtt") == "en-US"


def test_fetch_plain_hls_and_thumbnails(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.vtt").write_text(VTT)
    (site / "thumbs.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nsprite.jpg#xywh=0,0,160,90\n")
    (site / "s0.vtt").write_text(VTT)
    (site / "s1.vtt").write_text("WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nBye\n")
    (site / "sub.m3u8").write_text("#EXTM3U\n#EXTINF:2,\ns0.vtt\n#EXTINF:2,\ns1.vtt\n#EXT-X-ENDLIST\n")
    srv, base = serve_dir(site)
    video = tmp_path / "out" / "Movie.mp4"
    video.parent.mkdir()
    p = fetch_subtitle(f"{base}/a.vtt", {}, video, "eng")
    assert p.name == "Movie.eng.vtt" and "Hello" in p.read_text()
    p2 = fetch_subtitle(f"{base}/a.vtt", {}, video, "eng")          # same language twice -> no overwrite
    assert p2.name == "Movie.eng2.vtt"
    assert fetch_subtitle(f"{base}/thumbs.vtt", {}, video, "x") is None
    p3 = fetch_subtitle(f"{base}/sub.m3u8", {}, video, "jpn")
    assert p3.suffix == ".vtt" and "Hello" in p3.read_text() and "Bye" in p3.read_text()
    srv.shutdown()


def test_parse_subtitle_tracks():
    m = ('#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English",LANGUAGE="eng",URI="subs/en.m3u8"\n'
         '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="Jp",URI="a.m3u8"\n')
    assert parse_subtitle_tracks(m, "http://h/x/m.m3u8") == [
        {"url": "http://h/x/subs/en.m3u8", "lang": "eng", "label": "English"}]


def test_manager_saves_subtitles_next_to_video(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "en.vtt").write_text(VTT)
    (site / "de.srt").write_text(SRT)
    ssrv, sbase = serve_dir(site)
    vsrv, vurl = make_server(os.urandom(1024 * 1024))
    m = DownloadManager()
    it = m.add(vurl, str(tmp_path), filename="Show - Ep 1.mp4",
               subs=[{"url": f"{sbase}/en.vtt", "lang": "eng", "label": ""},
                     {"url": f"{sbase}/de.srt", "lang": "ger", "label": ""},
                     {"url": f"{sbase}/missing.vtt", "lang": "x", "label": ""}])   # 404 must not break anything
    t0 = time.time()
    while (it.status != COMPLETED or not it.sub_files) and time.time() - t0 < 30:
        qapp.processEvents()
        time.sleep(0.05)
    names = sorted(p.rsplit("/", 1)[-1] for p in it.sub_files)
    assert it.status == COMPLETED and names == ["Show - Ep 1.eng.vtt", "Show - Ep 1.ger.srt"]
    assert (tmp_path / "Video" / "Show - Ep 1.eng.vtt").exists()
    m.shutdown()
    ssrv.shutdown()
    vsrv.shutdown()

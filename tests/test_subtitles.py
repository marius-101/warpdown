import pytest
import sys
from pathlib import Path
import functools
import http.server
import os
import threading
import time

from warpdown.hls import parse_subtitle_tracks
from warpdown.manager import COMPLETED, DownloadManager
from warpdown.subtitles import fetch_subtitle, merge_vtt, sniff_subtitle, tag_for
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
    (site / "b.vtt").write_text("WEBVTT\n\n00:00:09.000 --> 00:00:10.000\nDifferent track, same language\n")
    p2 = fetch_subtitle(f"{base}/b.vtt", {}, video, "eng")          # different content, same language -> eng2
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
    names = sorted(Path(p).name for p in it.sub_files)
    assert it.status == COMPLETED and names == ["Show - Ep 1.eng.vtt", "Show - Ep 1.ger.srt"]
    assert (tmp_path / "Video" / "Show - Ep 1.eng.vtt").exists()
    m.shutdown()
    ssrv.shutdown()
    vsrv.shutdown()


def test_language_filter_and_dedupe(tmp_path):
    from warpdown.subtitles import matches_langs, parse_langs, peek_subtitle
    assert parse_langs("eng, en ,") == ["eng", "en"]
    assert matches_langs("eng", ["eng", "en"]) and matches_langs("en-US", ["en"]) and matches_langs("English", ["eng"])
    assert not matches_langs("ara", ["eng", "en"]) and not matches_langs("chi", ["eng", "en"])
    assert matches_langs("ara", [])                      # empty filter = everything

    site = tmp_path / "site"
    site.mkdir()
    (site / "eng.vtt").write_text(VTT)
    (site / "thumbs.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nsprite.jpg#xywh=0,0,160,90\n")
    srv, base = serve_dir(site)
    info = peek_subtitle(f"{base}/eng.vtt", {})
    assert info["cues"] == 1 and info["ext"] == ".vtt"
    assert peek_subtitle(f"{base}/thumbs.vtt", {}) is None
    video = tmp_path / "V.mp4"
    a = fetch_subtitle(f"{base}/eng.vtt", {}, video, "eng")
    b = fetch_subtitle(f"{base}/eng.vtt", {}, video, "eng")      # identical content -> reuse, no eng2
    assert a == b and len(list(tmp_path.glob("V.*.vtt"))) == 1
    srv.shutdown()


def test_labels_tags_and_default_selection():
    from warpdown.subtitles import default_checked, tag_for, track_kind
    assert track_kind("English (SDH)") == "sdh" and track_kind("Signs & Songs") == "signs" and track_kind("English") == ""
    assert tag_for("eng", "English (SDH)", "http://x/a.vtt") == "eng.sdh"
    assert tag_for("eng", "English", "http://x/a.vtt") == "eng"
    tracks = [
        {"url": "http://x/sdh.vtt", "lang": "eng", "label": "English (SDH)"},
        {"url": "http://x/ara.vtt", "lang": "ara", "label": "Arabic"},
        {"url": "http://x/signs.vtt", "lang": "eng", "label": "English Signs"},
        {"url": "http://x/full.vtt", "lang": "eng", "label": "English"},
        {"url": "http://x/full2.vtt", "lang": "eng", "label": "English"},
    ]
    # wanted English: exactly one tick, the first *plain* English track (not SDH, not signs)
    assert default_checked(tracks, ["eng", "en"]) == [False, False, False, True, False]
    assert default_checked(tracks[:3], ["eng"]) == [True, False, False]       # only flagged ones exist -> first flagged
    assert default_checked(tracks, ["ger"]) == [False, True, False, True, False]  # no match -> one plain track per language
    assert default_checked(tracks, []) == [False, True, False, True, False]   # empty filter = every language, one each


def test_probe_reports_hls_subtitle_tracks(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "m.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="s",NAME="English (SDH)",LANGUAGE="eng",URI="s1.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1000,SUBTITLES="s"\nv.m3u8\n')
    srv, base = serve_dir(site)
    from warpdown.engine import probe
    p = probe(f"{base}/m.m3u8")
    assert p.hls and p.sub_tracks == [{"url": f"{base}/s1.m3u8", "lang": "eng", "label": "English (SDH)"}]
    srv.shutdown()

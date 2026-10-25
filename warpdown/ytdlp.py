"""yt-dlp integration: YouTube & other video sites that don't expose a plain file or HLS playlist.

yt-dlp is run as a subprocess (`python -m yt_dlp`), so a yt-dlp crash or update never takes Warpdown down.
DRM-protected streams are not (and cannot be) handled; yt-dlp itself refuses those.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from glob import escape as glob_escape
from pathlib import Path
from urllib.parse import urlsplit

from . import osutil
from .engine import RateLimiter

def _make_cmd() -> list[str]:
    exe = osutil.helper("yt-dlp")                       # the installed Windows program ships yt-dlp.exe
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


CMD = _make_cmd()

# Hosts where we switch to yt-dlp automatically (the user can still untick it).
VIDEO_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "dailymotion.com", "twitch.tv",
               "facebook.com", "fb.watch", "instagram.com", "tiktok.com", "twitter.com", "x.com", "reddit.com",
               "soundcloud.com", "bilibili.com", "streamable.com")

QUALITIES = {
    "Best available": "bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480]+ba/b[height<=480]",
    "360p": "bv*[height<=360]+ba/b[height<=360]",
    "Audio only (MP3)": "ba/b",
}
AUDIO_ONLY = "Audio only (MP3)"


def available() -> bool:
    if osutil.helper("yt-dlp"):
        return True
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def looks_like_video_site(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


# File hosts: their addresses are files (or a sign-in page when something is missing), never a page with a video.
FILE_HOSTS = ("drive.google.com", "drive.usercontent.google.com", "docs.google.com", "googleusercontent.com",
              "dropbox.com", "dl.dropboxusercontent.com", "onedrive.live.com", "1drv.ms", "github.com",
              "githubusercontent.com", "sourceforge.net", "mediafire.com", "mega.nz", "wetransfer.com")


def is_file_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in FILE_HOSTS)


def is_youtube(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host.endswith(("youtube.com", "youtu.be", "youtube-nocookie.com"))


def js_runtime() -> str | None:
    """A JavaScript runtime yt-dlp can use for YouTube (full quality needs one)."""
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


def version() -> str:
    exe = osutil.helper("yt-dlp")
    if exe:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, **osutil.quiet())
            return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    try:
        from importlib.metadata import version as _v
        return _v("yt-dlp")
    except Exception:  # noqa: BLE001
        return ""


def update() -> tuple[bool, str]:
    """pip-upgrade yt-dlp inside Warpdown's own environment (sites change often; old versions break)."""
    exe = osutil.helper("yt-dlp")
    cmd = [exe, "-U"] if exe else [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **osutil.quiet())
    out = (r.stdout if r.returncode == 0 else r.stderr).strip().splitlines()
    return r.returncode == 0, out[-1] if out else ""


def _common_args(headers: dict, url: str = "") -> list[str]:
    args = ["--no-playlist", "--no-warnings", "--ignore-config"]
    rt = js_runtime()
    if rt:
        args += ["--js-runtimes", rt]
    if headers.get("Referer"):
        args += ["--referer", headers["Referer"]]
    if headers.get("User-Agent"):
        args += ["--user-agent", headers["User-Agent"]]
    # Never forward browser cookies to YouTube: they don't match yt-dlp's own session and make YouTube answer
    # "The page needs to be reloaded" (and they're your login, which yt-dlp doesn't need for public videos).
    if headers.get("Cookie") and not is_youtube(url):
        args += ["--add-header", f"Cookie:{headers['Cookie']}"]
    return args


def fetch_info(url: str, headers: dict | None = None, timeout: float = 90) -> dict:
    """Title, duration, max height and subtitle tracks for the dialog. Raises RuntimeError with yt-dlp's message."""
    r = subprocess.run([*CMD, *_common_args(headers or {}, url), "--dump-single-json", "--skip-download", url],
                       **osutil.quiet(),
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        msg = [l for l in r.stderr.splitlines() if l.strip()]
        raise RuntimeError((msg[-1] if msg else "yt-dlp failed").replace("ERROR: ", "")[:300])
    d = json.loads(r.stdout)
    heights = sorted({f["height"] for f in d.get("formats", []) if f.get("height")})
    subs = []
    for lang, items in (d.get("subtitles") or {}).items():
        subs.append({"lang": lang, "label": (items[0].get("name") if items else "") or lang, "auto": False})
    for lang, items in (d.get("automatic_captions") or {}).items():
        subs.append({"lang": lang, "label": ((items[0].get("name") if items else "") or lang) + " (auto-generated)",
                     "auto": True})
    return {"title": d.get("title") or "video", "duration": d.get("duration") or 0, "max_height": heights[-1] if heights else 0,
            "uploader": d.get("uploader") or "", "live": bool(d.get("is_live")), "subs": subs,
            "has_audio_only": any(f.get("vcodec") == "none" for f in d.get("formats", []))}


def parse_progress(line: str) -> tuple[str, int, int, float] | None:
    """'WDPROG|downloaded|total|estimate|speed|filename' -> (filename, downloaded, total, speed)."""
    if not line.startswith("WDPROG|"):
        return None
    parts = line.rstrip("\n").split("|", 5)
    if len(parts) < 6:
        return None

    def num(x: str) -> float:
        try:
            return float(x)
        except ValueError:
            return 0.0
    return parts[5], int(num(parts[1])), int(num(parts[2]) or num(parts[3])), num(parts[4])


class YtdlpDownloader:
    """Same interface as engine.Downloader / hls.HlsDownloader (job.opts carries yt-dlp options)."""

    def __init__(self, job, limiter: RateLimiter | None = None, on_finish=None):
        self.job = job
        self.limiter = limiter or RateLimiter(0)
        self.on_finish = on_finish
        self.error: str | None = None
        self.done = False
        self.sub_files: list[str] = []
        self.sub_tracks: list = []
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._streams: dict[str, tuple[int, int]] = {}     # filename -> (downloaded, total): video + audio streams
        self._speed = 0.0
        self._final: str | None = None
        self._tail: deque[str] = deque(maxlen=6)
        self._samples: list[tuple[float, int]] = []

    # ---- public ----
    def start(self) -> None:
        self._stop.clear()
        self.error, self.done = None, False
        self._main_thread = threading.Thread(target=self._main, daemon=True, name="warpdown-ytdlp")
        self._main_thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            p.terminate()
        if wait and getattr(self, "_main_thread", None):
            self._main_thread.join(timeout=20)

    def downloaded(self) -> int:
        return sum(d for d, _ in self._streams.values())

    def estimated_size(self) -> int:
        return sum(max(t, d) for d, t in self._streams.values()) or -1

    def snapshot(self) -> tuple[int, float]:
        now = time.monotonic()
        done = self.downloaded()
        self._samples.append((now, done))
        self._samples = [s for s in self._samples if now - s[0] <= 3.0]
        t0, b0 = self._samples[0]
        return done, max((done - b0) / (now - t0), 0.0) if now > t0 else self._speed

    # ---- internals ----
    def _args(self) -> list[str]:
        job, o = self.job, self.job.opts
        stem = job.dest.with_suffix("")
        args = [*CMD, *_common_args(job.headers, job.url), "--newline", "--progress", "--no-simulate", "--continue",
                "--progress-template",
                "download:WDPROG|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
                "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.filename)s",
                "--print", "after_move:WDDONE|%(filepath)s", "-o", f"{stem}.%(ext)s"]
        if o.get("audio_only"):
            args += ["-f", QUALITIES[AUDIO_ONLY], "-x", "--audio-format", "mp3"]
        else:
            # highest resolution first; at equal resolution prefer widely-playable H.264/AAC over AV1
            args += ["-f", o.get("format") or QUALITIES["Best available"], "-S", "res,vcodec:h264,acodec:aac",
                     "--merge-output-format", "mp4"]
        rate = self.limiter.rate
        if rate > 0:
            args += ["--limit-rate", str(rate)]
        subs = o.get("subs") or []
        if subs:
            args += ["--write-subs", "--sub-langs", ",".join(sorted({s["lang"] for s in subs})), "--sub-format", "vtt/best"]
            if any(s.get("auto") for s in subs):
                args += ["--write-auto-subs"]
        return [*args, job.url]

    def _main(self) -> None:
        try:
            self.job.dest.parent.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(self._args(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                          bufsize=1, errors="replace", **osutil.quiet())
            for line in self._proc.stdout:
                prog = parse_progress(line)
                if prog:
                    name, dl, total, speed = prog
                    self._streams[name] = (dl, total)
                    self._speed = speed
                elif line.startswith("WDDONE|"):
                    self._final = line.split("|", 1)[1].strip()
                elif line.strip():
                    self._tail.append(line.strip())
            rc = self._proc.wait()
            if self._stop.is_set():
                return                                   # paused/cancelled: partial files are kept for resume
            if self._final and Path(self._final).exists():
                self.job.dest = Path(self._final)
                stem = self.job.dest.with_suffix("").name
                self.sub_files = sorted(str(p) for ext in ("vtt", "srt", "ass")
                                        for p in self.job.dest.parent.glob(f"{glob_escape(stem)}.*.{ext}"))
                self.done = True
            else:
                errs = [l for l in self._tail if l.startswith("ERROR")]
                self.error = ((errs or list(self._tail) or [f"yt-dlp exited with code {rc}"])[-1]
                              .replace("ERROR: ", ""))[:300]
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or e.__class__.__name__
        finally:
            if self.on_finish:
                self.on_finish(self)



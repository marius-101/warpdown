"""HLS (.m3u8) downloader: parallel segment fetch, AES-128 decryption, ffmpeg remux to MP4.

DRM (SAMPLE-AES, Widevine, FairPlay...) is deliberately NOT supported.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .engine import SSL_CONTEXT, USER_AGENT, Job, RateLimiter


class HlsError(Exception):
    pass


@dataclass
class Segment:
    uri: str
    seq: int
    key_uri: str | None = None
    iv: bytes | None = None


@dataclass
class MediaPlaylist:
    segments: list[Segment]
    init_uri: str | None = None
    live: bool = False


def _attrs(line: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line)}


def parse_master(text: str, base: str) -> tuple[str, str | None] | None:
    """Return (best video playlist url, separate audio playlist url or None) or None if not a master."""
    if "#EXT-X-STREAM-INF" not in text:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    audio: dict[str, str] = {}
    for l in lines:
        if l.startswith("#EXT-X-MEDIA:"):
            a = _attrs(l[13:])
            if a.get("TYPE") == "AUDIO" and a.get("URI"):
                audio.setdefault(a.get("GROUP-ID", ""), urljoin(base, a["URI"]))
    best = (-1, "", None)
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF:") and i + 1 < len(lines) and not lines[i + 1].startswith("#"):
            a = _attrs(l[18:])
            bw = int(a.get("BANDWIDTH", a.get("AVERAGE-BANDWIDTH", 0)) or 0)
            if bw > best[0]:
                best = (bw, urljoin(base, lines[i + 1]), audio.get(a.get("AUDIO", "")))
    if not best[1]:
        raise HlsError("Master playlist has no variants")
    return best[1], best[2]


def parse_subtitle_tracks(text: str, base: str) -> list[dict]:
    """EXT-X-MEDIA TYPE=SUBTITLES entries of a master playlist: [{url, lang, label}]."""
    out = []
    for l in text.splitlines():
        if l.startswith("#EXT-X-MEDIA:"):
            a = _attrs(l[13:])
            if a.get("TYPE") == "SUBTITLES" and a.get("URI"):
                out.append({"url": urljoin(base, a["URI"]), "lang": a.get("LANGUAGE", ""), "label": a.get("NAME", "")})
    return out


def parse_media(text: str, base: str) -> MediaPlaylist:
    seq = 0
    segs: list[Segment] = []
    key_uri: str | None = None
    iv: bytes | None = None
    init = None
    ended = False
    pending = False
    for raw in text.splitlines():
        l = raw.strip()
        if not l:
            continue
        if l.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(l.split(":", 1)[1])
        elif l.startswith("#EXT-X-KEY:"):
            a = _attrs(l[11:])
            m = a.get("METHOD", "NONE")
            if m == "NONE":
                key_uri, iv = None, None
            elif m == "AES-128":
                key_uri = urljoin(base, a["URI"])
                iv = bytes.fromhex(a["IV"][2:].zfill(32)) if a.get("IV") else None
            else:
                raise HlsError(f"Unsupported encryption ({m}); DRM-protected streams can't be downloaded")
        elif l.startswith("#EXT-X-MAP:"):
            init = urljoin(base, _attrs(l[11:])["URI"])
        elif l.startswith("#EXT-X-BYTERANGE"):
            raise HlsError("Byte-range HLS streams aren't supported yet")
        elif l.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif l.startswith("#EXTINF"):
            pending = True
        elif not l.startswith("#") and pending:
            segs.append(Segment(urljoin(base, l), seq, key_uri, iv))
            seq += 1
            pending = False
    if not segs:
        raise HlsError("Playlist contains no segments")
    return MediaPlaylist(segs, init, live=not ended)


def decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    out = dec.update(data) + dec.finalize()
    pad = out[-1] if out else 0
    return out[:-pad] if 0 < pad <= 16 else out


class HlsDownloader:
    """Same interface as engine.Downloader."""

    def __init__(self, job: Job, limiter: RateLimiter | None = None, on_finish=None):
        self.job = job
        self.limiter = limiter or RateLimiter(0)
        self.on_finish = on_finish
        self.error: str | None = None
        self.done = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._bytes = 0
        self._segs_done = 0
        self._segs_total = 0
        self._samples: list[tuple[float, int]] = []
        self.parts = Path(str(job.dest) + ".parts")
        self.sub_tracks: list[dict] = []   # subtitle renditions found in the master playlist

    # ---- public ----
    def start(self) -> None:
        self._stop.clear()
        self.error, self.done = None, False
        self._main_thread = threading.Thread(target=self._main, daemon=True, name="warpdown-hls")
        self._main_thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and getattr(self, "_main_thread", None):
            self._main_thread.join(timeout=20)

    def downloaded(self) -> int:
        return self._bytes

    def estimated_size(self) -> int:
        if self._segs_done == 0:
            return -1
        return int(self._bytes / self._segs_done * self._segs_total)

    def snapshot(self) -> tuple[int, float]:
        now = time.monotonic()
        self._samples.append((now, self._bytes))
        self._samples = [s for s in self._samples if now - s[0] <= 3.0]
        t0, b0 = self._samples[0]
        return self._bytes, max((self._bytes - b0) / (now - t0), 0.0) if now > t0 else 0.0

    # ---- internals ----
    def _client(self) -> httpx.Client:
        return httpx.Client(follow_redirects=True, verify=SSL_CONTEXT, timeout=httpx.Timeout(30, connect=15),
                            headers={"User-Agent": USER_AGENT, **self.job.headers})

    def _get(self, c: httpx.Client, url: str) -> bytes:
        for attempt in range(6):
            if self._stop.is_set():
                raise InterruptedError
            try:
                r = c.get(url)
                r.raise_for_status()
                return r.content
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    raise
                if attempt == 5 or self._stop.wait(min(2 ** attempt, 15)):
                    raise
        raise RuntimeError("unreachable")

    def _resolve(self, c: httpx.Client, url: str) -> tuple[MediaPlaylist, str | None]:
        text = self._get(c, url).decode("utf-8", "replace")
        m = parse_master(text, url)
        if m:
            self.sub_tracks = parse_subtitle_tracks(text, url)
        audio_url = None
        if m:
            url, audio_url = m
            text = self._get(c, url).decode("utf-8", "replace")
        pl = parse_media(text, url)
        if pl.live:
            raise HlsError("Live streams aren't supported")
        return pl, audio_url

    def _fetch_all(self, c: httpx.Client, pl: MediaPlaylist, tag: str) -> list[Path]:
        self.parts.mkdir(parents=True, exist_ok=True)
        keys: dict[str, bytes] = {}
        files: list[Path] = []
        todo: list[tuple[Segment | None, Path]] = []
        if pl.init_uri:
            p = self.parts / f"{tag}_init"
            files.append(p)
            todo.append((None, p))
        for i, s in enumerate(pl.segments):
            p = self.parts / f"{tag}_{i:06d}"
            files.append(p)
            todo.append((s, p))
        self._segs_total += len(pl.segments)
        queue = [t for t in todo if not t[1].exists()]
        self._segs_done += len(todo) - len(queue)
        for _s, p in todo:
            if p.exists():
                self._bytes += p.stat().st_size
        lock = threading.Lock()
        err: list[Exception] = []

        def worker():
            with self._client() as wc:
                while not self._stop.is_set() and not err:
                    with lock:
                        if not queue:
                            return
                        seg, path = queue.pop(0)
                    try:
                        data = self._get(wc, pl.init_uri if seg is None else seg.uri)
                        if seg is not None and seg.key_uri:
                            with lock:
                                key = keys.get(seg.key_uri)
                            if key is None:
                                key = self._get(wc, seg.key_uri)
                                with lock:
                                    keys[seg.key_uri] = key
                            data = decrypt(data, key, seg.iv or seg.seq.to_bytes(16, "big"))
                        self.limiter.acquire(len(data))
                        tmp = path.with_suffix(".tmp")
                        tmp.write_bytes(data)
                        tmp.replace(path)
                        with self._lock:
                            self._bytes += len(data)
                            self._segs_done += 1
                    except InterruptedError:
                        return
                    except Exception as e:  # noqa: BLE001
                        err.append(e)
                        return

        ts = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(self.job.connections, 16)))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        if err:
            raise err[0]
        return files

    @staticmethod
    def _concat(files: list[Path], out: Path) -> None:
        with open(out, "wb") as o:
            for f in files:
                with open(f, "rb") as i:
                    shutil.copyfileobj(i, o, 1 << 20)

    def _main(self) -> None:
        job = self.job
        try:
            job.dest.parent.mkdir(parents=True, exist_ok=True)
            with self._client() as c:
                pl, audio_url = self._resolve(c, job.url)
                v_files = self._fetch_all(c, pl, "v")
                a_files = []
                if audio_url and not self._stop.is_set():
                    apl = parse_media(self._get(c, audio_url).decode("utf-8", "replace"), audio_url)
                    a_files = self._fetch_all(c, apl, "a")
            if self._stop.is_set():
                return
            fmp4 = pl.init_uri is not None
            v_raw = self.parts / ("video.mp4" if fmp4 else "video.ts")
            self._concat(v_files, v_raw)
            inputs = [v_raw]
            if a_files:
                a_raw = self.parts / "audio.raw"
                self._concat(a_files, a_raw)
                inputs.append(a_raw)
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                tmp_out = self.parts / "final.mp4"
                cmd = [ffmpeg, "-y", "-loglevel", "error"]
                for f in inputs:
                    cmd += ["-i", str(f)]
                cmd += ["-c", "copy", "-movflags", "+faststart", str(tmp_out)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode != 0:
                    raise HlsError("ffmpeg failed: " + r.stderr.strip()[-200:])
                tmp_out.replace(job.dest.with_suffix(".mp4"))
                job.dest = job.dest.with_suffix(".mp4")
            else:  # no ffmpeg: keep the raw transport stream
                job.dest = job.dest.with_suffix(".mp4" if fmp4 else ".ts")
                v_raw.replace(job.dest)
            shutil.rmtree(self.parts, ignore_errors=True)
            self.done = True
        except InterruptedError:
            pass
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or e.__class__.__name__
        finally:
            if self.on_finish:
                self.on_finish(self)

"""Multi-segment HTTP download engine with dynamic segment splitting and resume.

Like IDM: the file is split into segments fetched in parallel; when a connection
finishes early it steals half of the largest remaining segment, so all connections
stay busy until the end.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from urllib.parse import urlsplit

from .util import filename_from_headers, guess_extension

CHUNK = 64 * 1024
MIN_SPLIT = 1024 * 1024  # don't split segments with less than 1 MiB left
UNKNOWN = 1 << 62
# One shared TLS context, built once at import time on the main thread. Creating contexts
# concurrently from many threads (each loading the CA bundle) segfaults in OpenSSL here.
SSL_CONTEXT = ssl.create_default_context()
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) KUDM/0.1"


@dataclass
class ProbeResult:
    url: str  # final URL after redirects
    size: int  # -1 if unknown
    resumable: bool
    filename: str
    content_type: str = ""
    hls: bool = False
    head: bytes = b""  # first bytes of the file, for type sniffing


def is_hls(url: str, content_type: str = "") -> bool:
    return urlsplit(url).path.lower().endswith(".m3u8") or "mpegurl" in content_type.lower()


def probe(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0) -> ProbeResult:
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=h, verify=SSL_CONTEXT) as c:
        # A ranged GET is more reliable than HEAD (many servers mishandle HEAD).
        with c.stream("GET", url, headers={"Range": "bytes=0-31"}) as r:
            r.raise_for_status()
            cd = r.headers.get("content-disposition")
            ctype = r.headers.get("content-type", "")
            if is_hls(str(r.url), ctype):
                base = os.path.splitext(filename_from_headers(cd, str(r.url)))[0]
                name = "video" if base.lower() in ("index", "master", "playlist", "chunklist", "") else base
                return ProbeResult(str(r.url), -1, False, name + ".mp4", ctype, hls=True)
            if r.status_code == 206:
                cr = r.headers.get("content-range", "")
                size = int(cr.rsplit("/", 1)[-1]) if cr.rsplit("/", 1)[-1].isdigit() else -1
                resumable = size > 0
            else:
                size = int(r.headers.get("content-length", -1))
                resumable = False
            fname = filename_from_headers(cd, str(r.url))
            try:
                head = next(r.iter_bytes(32), b"")[:32]
            except httpx.HTTPError:
                head = b""
            return ProbeResult(str(r.url), size, resumable, fname + guess_extension(fname, ctype, head), ctype,
                               head=head)


class RateLimiter:
    """Shared token-bucket-ish limiter. rate in bytes/s; 0 = unlimited."""

    def __init__(self, rate: int = 0):
        self.rate = rate
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def acquire(self, n: int) -> None:
        rate = self.rate
        if rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + n / rate
            delay = start - now
        if delay > 0:
            time.sleep(min(delay, 5))


@dataclass
class Segment:
    start: int
    end: int  # exclusive
    pos: int
    active: bool = False

    @property
    def remaining(self) -> int:
        return self.end - self.pos


class StopDownload(Exception):
    pass


@dataclass
class Job:
    url: str
    dest: Path  # final path; data goes to dest + ".part"
    size: int = -1
    resumable: bool = False
    connections: int = 8
    headers: dict[str, str] = field(default_factory=dict)


class Downloader:
    """Runs one download in background threads. Thread-safe snapshot() for UI."""

    def __init__(self, job: Job, limiter: RateLimiter | None = None,
                 on_finish: Callable[["Downloader"], None] | None = None):
        self.job = job
        self.limiter = limiter or RateLimiter(0)
        self.on_finish = on_finish
        self.part = Path(str(job.dest) + ".part")
        self.state_file = Path(str(job.dest) + ".part.kudm")
        self.segments: list[Segment] = []
        self.error: str | None = None
        self.done = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._fd = -1
        self._speed_samples: list[tuple[float, int]] = []
        self._fatal: Exception | None = None

    # ---- public API -------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self.error = None
        self.done = False
        self._fatal = None
        t = threading.Thread(target=self._main, daemon=True, name="kudm-dl")
        self._main_thread = t
        t.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and getattr(self, "_main_thread", None):
            self._main_thread.join(timeout=15)

    def downloaded(self) -> int:
        with self._lock:
            return sum(s.pos - s.start for s in self.segments)

    def snapshot(self) -> tuple[int, float]:
        """(bytes downloaded, speed in B/s over a ~3s window)."""
        now = time.monotonic()
        done = self.downloaded()
        self._speed_samples.append((now, done))
        self._speed_samples = [s for s in self._speed_samples if now - s[0] <= 3.0]
        t0, b0 = self._speed_samples[0]
        speed = (done - b0) / (now - t0) if now > t0 else 0.0
        return done, max(speed, 0.0)

    # ---- internals --------------------------------------------------
    def _init_segments(self) -> None:
        job = self.job
        if job.size > 0 and job.resumable and self.state_file.exists() and self.part.exists():
            try:
                st = json.loads(self.state_file.read_text())
                if st.get("size") == job.size and st.get("url_size") == job.size:
                    self.segments = [Segment(s, e, p) for s, e, p in st["segments"]]
                    return
            except (ValueError, KeyError, OSError):
                pass
        if job.size > 0 and job.resumable:
            n = max(1, min(job.connections, job.size // MIN_SPLIT or 1))
            step = job.size // n
            self.segments = [
                Segment(i * step, job.size if i == n - 1 else (i + 1) * step, i * step)
                for i in range(n)
            ]
        else:
            self.segments = [Segment(0, job.size if job.size > 0 else UNKNOWN, 0)]

    def _save_state(self) -> None:
        if not (self.job.resumable and self.job.size > 0):
            return
        with self._lock:
            data = {"size": self.job.size, "url_size": self.job.size,
                    "segments": [[s.start, s.end, s.pos] for s in self.segments]}
        tmp = Path(str(self.state_file) + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.state_file)

    def _main(self) -> None:
        job = self.job
        try:
            job.dest.parent.mkdir(parents=True, exist_ok=True)
            self._init_segments()
            fresh = not self.part.exists()
            self._fd = os.open(self.part, os.O_RDWR | os.O_CREAT, 0o644)
            if fresh and job.size > 0:
                os.ftruncate(self._fd, job.size)
            nthreads = len(self.segments) if job.resumable else 1
            nthreads = max(1, min(nthreads, job.connections))
            self._threads = [
                threading.Thread(target=self._worker, daemon=True, name=f"kudm-w{i}")
                for i in range(nthreads)
            ]
            for t in self._threads:
                t.start()
            last_save = time.monotonic()
            while any(t.is_alive() for t in self._threads):
                time.sleep(0.2)
                if time.monotonic() - last_save > 2:
                    self._save_state()
                    last_save = time.monotonic()
            for t in self._threads:
                t.join()
            if self._fatal is not None:
                self.error = str(self._fatal)
            elif self._stop.is_set():
                self._save_state()
            elif all(s.pos >= s.end for s in self.segments if s.end != UNKNOWN) or job.size <= 0:
                if job.size <= 0:
                    os.ftruncate(self._fd, self.segments[0].pos)
                os.close(self._fd)
                self._fd = -1
                os.replace(self.part, job.dest)
                self.state_file.unlink(missing_ok=True)
                self.done = True
            else:
                self.error = "Download ended unexpectedly"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        finally:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
            if self.on_finish:
                self.on_finish(self)

    def _claim(self) -> Segment | None:
        """Pick an idle unfinished segment, or steal half of the biggest active one."""
        with self._lock:
            for s in self.segments:
                if not s.active and s.remaining > 0:
                    s.active = True
                    return s
            if not self.job.resumable:
                return None
            victim = max((s for s in self.segments if s.active), key=lambda s: s.remaining, default=None)
            if victim is None or victim.remaining < 2 * MIN_SPLIT:
                return None
            mid = victim.pos + victim.remaining // 2
            new = Segment(mid, victim.end, mid, active=True)
            victim.end = mid
            self.segments.append(new)
            return new

    def _worker(self) -> None:
        job = self.job
        headers = {"User-Agent": USER_AGENT, **job.headers}
        try:
            with httpx.Client(follow_redirects=True, headers=headers, verify=SSL_CONTEXT,
                              timeout=httpx.Timeout(30.0, connect=15.0)) as client:
                while not self._stop.is_set():
                    seg = self._claim()
                    if seg is None:
                        return
                    try:
                        self._run_segment(client, seg)
                    finally:
                        seg.active = False
        except StopDownload:
            return
        except Exception as e:  # noqa: BLE001
            self._fatal = e
            self._stop.set()

    def _run_segment(self, client: httpx.Client, seg: Segment) -> None:
        job = self.job
        retries = 0
        while seg.pos < seg.end and not self._stop.is_set():
            hdrs = {}
            if job.resumable:
                hdrs["Range"] = f"bytes={seg.pos}-{seg.end - 1 if seg.end != UNKNOWN else ''}"
            try:
                with client.stream("GET", job.url, headers=hdrs) as r:
                    if job.resumable and r.status_code != 206:
                        raise httpx.HTTPError(f"Server ignored Range (HTTP {r.status_code})")
                    r.raise_for_status()
                    for chunk in r.iter_bytes(CHUNK):
                        if self._stop.is_set():
                            return
                        n = min(len(chunk), seg.end - seg.pos)
                        if n <= 0:
                            return
                        os.pwrite(self._fd, chunk[:n], seg.pos)
                        seg.pos += n
                        self.limiter.acquire(n)
                        if seg.pos >= seg.end:
                            return
                    if seg.end == UNKNOWN:
                        seg.end = seg.pos  # unknown-length stream finished
                        return
                retries = 0
            except (httpx.TransportError, httpx.HTTPStatusError, httpx.HTTPError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 \
                        and e.response.status_code != 429:
                    raise
                retries += 1
                if retries > 8:
                    raise
                if self._stop.wait(min(2 ** retries, 20)):
                    return

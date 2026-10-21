"""Torrent and magnet downloads, done by aria2 (https://aria2.github.io) which Warpdown starts and controls over its
local JSON-RPC interface. Same interface as engine.Downloader / hls.HlsDownloader / ytdlp.YtdlpDownloader."""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import osutil
from .engine import RateLimiter

INSTALL_HINT = ("Torrents need aria2. Install it with: sudo apt install aria2" if not sys.platform.startswith("win")
                else "Torrents need aria2. Download aria2 for Windows and put aria2c.exe next to Warpdown or in your PATH.")


def binary() -> str | None:
    return osutil.helper("aria2c") or shutil.which("aria2c")


def available() -> bool:
    return binary() is not None


def is_torrent_source(url: str) -> bool:
    u = (url or "").strip()
    if u.lower().startswith("magnet:"):
        return True
    path = urlsplit(u).path if "://" in u else u
    return path.lower().endswith(".torrent")


def magnet_name(url: str) -> str:
    """The display name a magnet link carries (dn=…), if any."""
    try:
        return unquote(parse_qs(urlsplit(url).query).get("dn", [""])[0]).strip()
    except ValueError:
        return ""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Aria2Downloader:
    def __init__(self, job, limiter: RateLimiter | None = None, on_finish=None):
        self.job = job
        self.limiter = limiter or RateLimiter(0)
        self.on_finish = on_finish
        self.error: str | None = None
        self.done = False
        self.sub_files: list[str] = []
        self.sub_tracks: list = []
        self.name = ""                  # real name of the torrent, known once the metadata has arrived
        self.peers = 0
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._port = 0
        self._token = ""
        self._dl = 0
        self._total = 0
        self._speed = 0.0
        self._last_rate = -1

    # ---- public ----
    def start(self) -> None:
        self._stop.clear()
        self.error, self.done = None, False
        self._thread = threading.Thread(target=self._main, daemon=True, name="warpdown-torrent")
        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and getattr(self, "_thread", None):
            self._thread.join(timeout=20)

    def downloaded(self) -> int:
        return self._dl

    def estimated_size(self) -> int:
        return self._total if self._total > 0 else -1

    def snapshot(self) -> tuple[int, float]:
        return self._dl, self._speed

    # ---- aria2 ----
    def _spawn(self) -> None:
        exe = binary()
        if not exe:
            raise RuntimeError(INSTALL_HINT)
        self._port, self._token = free_port(), secrets.token_hex(16)
        args = [exe, "--enable-rpc", f"--rpc-listen-port={self._port}", f"--rpc-secret={self._token}",
                "--rpc-listen-all=false", f"--dir={self.job.dest.parent}", "--seed-time=0", "--continue=true",
                "--file-allocation=none", "--auto-file-renaming=false", "--console-log-level=error",
                "--summary-interval=0", "--bt-save-metadata=false", "--follow-torrent=true"]
        self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                      **osutil.quiet())

    def _rpc(self, method: str, *params):
        body = json.dumps({"jsonrpc": "2.0", "id": "w", "method": method,
                           "params": [f"token:{self._token}", *params]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self._port}/jsonrpc", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(out["error"].get("message", "aria2 error"))
        return out["result"]

    def _wait_ready(self) -> None:
        end = time.monotonic() + 15
        while time.monotonic() < end and not self._stop.is_set():
            if self._proc and self._proc.poll() is not None:
                tail = (self._proc.stdout.read() if self._proc.stdout else "").strip().splitlines()
                raise RuntimeError(tail[-1] if tail else f"aria2 exited with code {self._proc.returncode}")
            try:
                self._rpc("aria2.getVersion")
                return
            except (OSError, RuntimeError):
                time.sleep(0.2)
        if not self._stop.is_set():
            raise RuntimeError("aria2 did not start")

    def _add(self) -> str:
        src = self.job.url
        if os.path.isfile(src):
            return self._rpc("aria2.addTorrent", base64.b64encode(Path(src).read_bytes()).decode(), [])
        return self._rpc("aria2.addUri", [src])

    def _apply_rate(self) -> None:
        rate = self.limiter.rate
        if rate != self._last_rate:
            self._last_rate = rate
            self._rpc("aria2.changeGlobalOption", {"max-overall-download-limit": str(max(0, rate))})

    def _poll(self, gid: str) -> tuple[str, str]:
        """Update progress from aria2. Returns (gid to follow, state) with state in running/complete/error."""
        st = self._rpc("aria2.tellStatus", gid, ["status", "totalLength", "completedLength", "downloadSpeed",
                                                  "followedBy", "errorMessage", "bittorrent", "numSeeders", "connections"])
        if st.get("followedBy"):                      # a magnet link first fetches the torrent file, then the data
            return st["followedBy"][0], "running"
        info = (st.get("bittorrent") or {}).get("info") or {}
        if info.get("name"):
            self.name = info["name"]
        self._dl, self._total = int(st.get("completedLength", 0)), int(st.get("totalLength", 0))
        self._speed = float(st.get("downloadSpeed", 0))
        self.peers = int(st.get("connections", 0))
        status = st.get("status", "")
        if status == "error":
            self.error = (st.get("errorMessage") or "aria2 reported an error")[:300]
            return gid, "error"
        return gid, "complete" if status == "complete" else "running"

    def _main(self) -> None:
        try:
            self.job.dest.parent.mkdir(parents=True, exist_ok=True)
            self._spawn()
            self._wait_ready()
            if self._stop.is_set():
                return
            gid = self._add()
            while not self._stop.is_set():
                self._apply_rate()
                gid, state = self._poll(gid)
                if state == "complete" and not self._stop.is_set():
                    if self.name:
                        self.job.dest = self.job.dest.parent / self.name
                    self.done = True
                    break
                if state == "error":
                    break
                self._stop.wait(1.0)
        except Exception as e:  # noqa: BLE001
            self.error = str(e) or e.__class__.__name__
        finally:
            self._shutdown_aria2()
            if self.on_finish:
                self.on_finish(self)

    def _shutdown_aria2(self) -> None:
        try:
            if self._port:
                self._rpc("aria2.shutdown")           # lets aria2 write its resume file
        except Exception:  # noqa: BLE001
            pass
        p = self._proc
        if p:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    p.kill()

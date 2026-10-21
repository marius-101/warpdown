"""Download queue/manager: persistence, scheduling, progress for the UI."""

from __future__ import annotations

import json
import os
import threading
from urllib.parse import urlsplit
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import Downloader, Job, RateLimiter, is_hls, probe
from .inbox import Inbox
from .hls import HlsDownloader
from .subtitles import fetch_subtitle, matches_langs, tag_for
from .ytdlp import YtdlpDownloader
from .util import category_for, data_dir, ensure_category_dirs, guess_extension, unique_path

QUEUED, PROBING, DOWNLOADING, PAUSED, COMPLETED, ERROR = (
    "Queued", "Connecting", "Downloading", "Paused", "Completed", "Error")


@dataclass
class Item:
    url: str
    directory: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filename: str = ""
    size: int = -1
    resumable: bool = False
    status: str = QUEUED
    downloaded: int = 0
    speed: float = 0.0
    error: str = ""
    category: str = "Other"
    added: float = field(default_factory=time.time)
    connections: int = 8
    subs: list = field(default_factory=list)       # [{url, lang, label}] sent by the browser
    auto_subs: bool = True    # also save subtitle tracks found in the HLS playlist (False = user chose in the dialog)
    sub_files: list = field(default_factory=list)  # subtitle files saved next to the video
    kind: str = "http"   # "http", "hls" or "ytdlp"
    opts: dict = field(default_factory=dict)   # yt-dlp options chosen in the dialog (quality, subtitles)
    prepared: bool = False   # destination folder/name already decided (yt-dlp items)
    sort: bool = False  # file into <directory>/<Category>/ once the file type is known
    headers: dict = field(default_factory=dict)  # referer, cookie, user-agent from the browser

    @property
    def path(self) -> Path:
        return Path(self.directory) / self.filename

    @property
    def progress(self) -> float:
        return self.downloaded / self.size if self.size > 0 else 0.0


# ---- background jobs: module-level, plain data + a queue only (see inbox.py for why) ----
def _probe_job(q, iid: str, url: str, headers: dict) -> None:
    try:
        q.put(("probed", (iid, probe(url, headers), "")))
    except Exception as e:  # noqa: BLE001
        q.put(("probed", (iid, None, str(e) or e.__class__.__name__)))


def _subtitle_job(q, iid: str, tracks: list, headers: dict, video) -> None:
    saved = []
    for t in tracks[:12]:
        try:
            p = fetch_subtitle(t["url"], headers, video, t.get("lang", ""), t.get("label", ""))
            if p:
                saved.append(str(p))
        except Exception:  # noqa: BLE001  (a missing subtitle must never fail the video)
            pass
    q.put(("subs", (iid, saved)))


class DownloadManager(QObject):
    changed = Signal(str)       # item id
    added = Signal(str)
    removed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: dict[str, Item] = {}
        self.engines: dict[str, Downloader] = {}
        self.limiter = RateLimiter(0)
        self.max_active = 3
        self.default_connections = 8
        self.sort_by_category = True
        self.sub_langs: list[str] = []   # wanted subtitle languages for auto-found tracks; [] = all
        self._file = data_dir() / "downloads.json"
        # Worker threads talk to this object only through the inbox (plain data in a queue), never via Qt signals.
        self._inbox = Inbox({"finished": self._on_finished, "probed": self._on_probed, "subs": self._on_subs_done}, self)
        self._load()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

    # ---- persistence ------------------------------------------------
    def _load(self) -> None:
        try:
            for d in json.loads(self._file.read_text()):
                it = Item(**d)
                if it.category == "General":   # renamed in 0.2
                    it.category = "Other"
                if it.status in (DOWNLOADING, PROBING, QUEUED):
                    it.status = PAUSED
                it.speed = 0.0
                self.items[it.id] = it
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        tmp = self._file.with_suffix(".tmp")
        rows = []
        for i in self.items.values():
            d = asdict(i)
            if i.status == COMPLETED:        # a finished download no longer needs the site's login cookie
                d["headers"] = {k: v for k, v in d["headers"].items() if k not in ("cookie", "cookie_origin")}
            rows.append(d)
        tmp.write_text(json.dumps(rows, indent=1))
        os.chmod(tmp, 0o600)                 # the list can contain cookies of unfinished downloads: owner only
        tmp.replace(self._file)

    # ---- API --------------------------------------------------------
    def add(self, url: str, directory: str, filename: str = "", headers: dict | None = None,
            connections: int | None = None, start: bool = True, sort: bool | None = None,
            subs: list | None = None, auto_subs: bool = True, kind: str = "http", opts: dict | None = None) -> Item:
        it = self._new_item(url, directory, filename, headers, connections, start, sort, subs, auto_subs, kind, opts)
        self.items[it.id] = it
        self.added.emit(it.id)
        self.save()
        self._schedule()
        return it

    def _new_item(self, url, directory, filename, headers, connections, start, sort, subs, auto_subs, kind, opts) -> Item:
        return Item(url=url, directory=directory, filename=filename, headers=dict(headers or {}),
                    connections=connections or self.default_connections,
                    sort=self.sort_by_category if sort is None else sort, subs=list(subs or []),
                    auto_subs=auto_subs, kind=kind, opts=dict(opts or {}), status=QUEUED if start else PAUSED)

    def add_many(self, urls: list[str], directory: str, headers: dict | None = None, connections: int | None = None,
                 start: bool = True, sort: bool | None = None) -> list[Item]:
        """Add a whole batch (e.g. links picked from a web page) with ONE save and ONE refresh, not one per item."""
        items = [self._new_item(u, directory, "", headers, connections, start, sort, None, False, "http", None)
                 for u in urls]
        for it in items:
            self.items[it.id] = it
        if items:
            self.added.emit(items[-1].id)
            self.save()
            self._schedule()
        return items

    def pause(self, iid: str) -> None:
        it = self.items.get(iid)
        if not it or it.status in (COMPLETED,):
            return
        eng = self.engines.get(iid)
        if eng:
            eng.stop(wait=False)
        it.status, it.speed = PAUSED, 0.0
        self.changed.emit(iid)
        self.save()

    def resume(self, iid: str) -> None:
        it = self.items.get(iid)
        if it and it.status in (PAUSED, ERROR):
            it.status, it.error = QUEUED, ""
            self.changed.emit(iid)
            self._schedule()

    def remove(self, iid: str, delete_files: bool = False) -> None:
        it = self.items.pop(iid, None)
        if not it:
            return
        eng = self.engines.pop(iid, None)
        if eng:
            eng.stop(wait=True)
        if it.filename:
            part = Path(str(it.path) + ".part")
            for p in (part, Path(str(part) + ".warpdown"), Path(str(part) + ".kudm")):
                p.unlink(missing_ok=True)
            if delete_files:
                it.path.unlink(missing_ok=True)
        self.removed.emit(iid)
        self.save()
        self._schedule()

    def set_speed_limit(self, bytes_per_s: int) -> None:
        self.limiter.rate = max(0, bytes_per_s)

    def shutdown(self) -> None:
        for eng in list(self.engines.values()):
            eng.stop(wait=True)
        for it in self.items.values():
            if it.status in (DOWNLOADING, PROBING, QUEUED):
                it.status = PAUSED
        self.save()

    # ---- scheduling -------------------------------------------------
    def _active_count(self) -> int:
        return sum(1 for i in self.items.values() if i.status in (DOWNLOADING, PROBING))

    def _schedule(self) -> None:
        for it in sorted(self.items.values(), key=lambda i: i.added):
            if self._active_count() >= self.max_active:
                break
            if it.status == QUEUED and it.id not in self.engines:
                self._begin(it)

    def _begin(self, it: Item) -> None:
        if it.kind == "ytdlp":
            if not it.prepared:
                err = self._prepare_destination(it, it.filename or "video.mp4")
                if err:
                    it.status, it.error = ERROR, err
                    self.changed.emit(it.id)
                    self._schedule()
                    return
                it.prepared = True
            self._launch(it)
            return
        if it.filename and (it.size > 0 or it.kind == "hls") and it.url:
            self._launch(it)
            return
        it.status = PROBING
        self.changed.emit(it.id)

        threading.Thread(target=_probe_job, args=(self._inbox.queue, it.id, it.url, self._browser_headers(it)),
                         daemon=True).start()

    @staticmethod
    def _browser_headers(it: Item) -> dict:
        m = {"referer": "Referer", "cookie": "Cookie", "user_agent": "User-Agent"}
        out = {m[k]: v for k, v in it.headers.items() if k in m and v}
        origin = it.headers.get("cookie_origin")
        # Links grabbed from a page can point at any site. The page's cookies are only for the page's own site,
        # so they are dropped for everything else (also after a redirect to another host).
        if origin and "Cookie" in out and urlsplit(it.url).netloc.lower() != origin.lower():
            del out["Cookie"]
        return out

    def _on_probed(self, iid: str, res, err: str) -> None:
        it = self.items.get(iid)
        if not it or it.status != PROBING:
            return
        if res is None:
            it.status, it.error = ERROR, err
            self.changed.emit(iid)
            self._schedule()
            return
        it.url, it.size, it.resumable = res.url, res.size, res.resumable
        it.kind = "hls" if res.hls else "http"
        name = it.filename or res.filename
        if it.filename:
            name += guess_extension(name, res.content_type, res.head)  # e.g. a page title with no extension
        err = self._prepare_destination(it, name)
        if err:
            it.status, it.error = ERROR, err
            self.changed.emit(iid)
            self._schedule()
            return
        self._launch(it)

    def _prepare_destination(self, it: Item, name: str) -> str:
        """Pick category folder + unique filename. Returns an error message, or '' on success."""
        it.category = category_for(name)
        if it.sort:
            it.directory = str(Path(it.directory) / it.category)   # IDM-style folder per file type
        try:
            Path(it.directory).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return str(e)
        it.sort = False  # already applied; don't nest again on retry
        it.filename = unique_path(Path(it.directory), name).name
        return ""

    def _launch(self, it: Item) -> None:
        job = Job(it.url, it.path, it.size, it.resumable, it.connections, self._browser_headers(it), it.opts)
        cls = {"hls": HlsDownloader, "ytdlp": YtdlpDownloader}.get(it.kind, Downloader)
        eng = cls(job, self.limiter, on_finish=lambda d, i=it.id, q=self._inbox.queue: q.put(("finished", (i, d))))
        self.engines[it.id] = eng
        it.status = DOWNLOADING
        self.changed.emit(it.id)
        eng.start()

    def _on_finished(self, iid: str, eng) -> None:
        if self.engines.get(iid) is eng:
            self.engines.pop(iid, None)
        it = self.items.get(iid)
        if not it:
            return
        if it.status == PAUSED and not eng.done:
            self._schedule()
            return
        it.speed = 0.0
        if eng.done:
            if it.kind in ("hls", "ytdlp"):
                it.filename, it.size = eng.job.dest.name, eng.downloaded()
                it.category = category_for(it.filename)
            it.status, it.downloaded = COMPLETED, max(it.size, it.downloaded)
            if it.kind == "ytdlp":
                it.sub_files = list(eng.sub_files)    # yt-dlp already saved them next to the video
            else:
                self._start_subtitles(it, getattr(eng, "sub_tracks", []))
        elif eng.error:
            it.status, it.error = ERROR, eng.error
        else:
            it.status = PAUSED
        self.changed.emit(iid)
        self.save()
        self._schedule()

    def _start_subtitles(self, it: Item, found: list) -> None:
        tracks, seen = [], set()
        found = [] if not it.auto_subs else [t for t in found if matches_langs(tag_for(t.get("lang", ""), t.get("label", ""), t["url"]), self.sub_langs)]
        for t in [*it.subs, *found]:    # it.subs were picked by the user in the dialog; `found` are auto-detected
            if t.get("url") and t["url"] not in seen:
                seen.add(t["url"])
                tracks.append(t)
        if not tracks:
            return
        threading.Thread(target=_subtitle_job, args=(self._inbox.queue, it.id, tracks, self._browser_headers(it), it.path),
                         daemon=True).start()

    def _on_subs_done(self, iid: str, saved: list) -> None:
        it = self.items.get(iid)
        if it:
            it.sub_files = saved
            self.changed.emit(iid)
            self.save()

    def _tick(self) -> None:
        for iid, eng in list(self.engines.items()):
            it = self.items.get(iid)
            if it and it.status == DOWNLOADING:
                it.downloaded, it.speed = eng.snapshot()
                if it.kind in ("hls", "ytdlp"):
                    it.size = eng.estimated_size()
                self.changed.emit(iid)

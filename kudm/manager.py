"""Download queue/manager: persistence, scheduling, progress for the UI."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import Downloader, Job, RateLimiter, is_hls, probe
from .hls import HlsDownloader
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
    kind: str = "http"   # or "hls"
    sort: bool = False  # file into <directory>/<Category>/ once the file type is known
    headers: dict = field(default_factory=dict)  # referer, cookie, user-agent from the browser

    @property
    def path(self) -> Path:
        return Path(self.directory) / self.filename

    @property
    def progress(self) -> float:
        return self.downloaded / self.size if self.size > 0 else 0.0


class DownloadManager(QObject):
    changed = Signal(str)       # item id
    added = Signal(str)
    removed = Signal(str)
    _finished = Signal(str, object)  # emitted from worker threads
    _probed = Signal(str, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: dict[str, Item] = {}
        self.engines: dict[str, Downloader] = {}
        self.limiter = RateLimiter(0)
        self.max_active = 3
        self.default_connections = 8
        self.sort_by_category = True
        self._file = data_dir() / "downloads.json"
        self._finished.connect(self._on_finished)
        self._probed.connect(self._on_probed)
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
        tmp.write_text(json.dumps([asdict(i) for i in self.items.values()], indent=1))
        tmp.replace(self._file)

    # ---- API --------------------------------------------------------
    def add(self, url: str, directory: str, filename: str = "", headers: dict | None = None,
            connections: int | None = None, start: bool = True, sort: bool | None = None) -> Item:
        it = Item(url=url, directory=directory, filename=filename, headers=headers or {},
                  connections=connections or self.default_connections,
                  sort=self.sort_by_category if sort is None else sort,
                  status=QUEUED if start else PAUSED)
        self.items[it.id] = it
        self.added.emit(it.id)
        self.save()
        self._schedule()
        return it

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
            for p in (part, Path(str(part) + ".kudm")):
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
        if it.filename and (it.size > 0 or it.kind == "hls") and it.url:
            self._launch(it)
            return
        it.status = PROBING
        self.changed.emit(it.id)

        def work():
            try:
                res = probe(it.url, self._browser_headers(it))
                self._probed.emit(it.id, res, "")
            except Exception as e:  # noqa: BLE001
                self._probed.emit(it.id, None, str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _browser_headers(it: Item) -> dict:
        m = {"referer": "Referer", "cookie": "Cookie", "user_agent": "User-Agent"}
        return {m[k]: v for k, v in it.headers.items() if k in m and v}

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
        it.category = category_for(name)
        if it.sort:
            it.directory = str(Path(it.directory) / it.category)   # IDM-style folder per file type
        try:
            Path(it.directory).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            it.status, it.error = ERROR, str(e)
            self.changed.emit(iid)
            self._schedule()
            return
        it.sort = False  # already applied; don't nest again on retry
        it.filename = unique_path(Path(it.directory), name).name
        self._launch(it)

    def _launch(self, it: Item) -> None:
        job = Job(it.url, it.path, it.size, it.resumable, it.connections, self._browser_headers(it))
        cls = HlsDownloader if it.kind == "hls" else Downloader
        eng = cls(job, self.limiter, on_finish=lambda d, i=it.id: self._finished.emit(i, d))
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
            if it.kind == "hls":
                it.filename, it.size = eng.job.dest.name, eng.downloaded()
            it.status, it.downloaded = COMPLETED, max(it.size, it.downloaded)
        elif eng.error:
            it.status, it.error = ERROR, eng.error
        else:
            it.status = PAUSED
        self.changed.emit(iid)
        self.save()
        self._schedule()

    def _tick(self) -> None:
        for iid, eng in list(self.engines.items()):
            it = self.items.get(iid)
            if it and it.status == DOWNLOADING:
                it.downloaded, it.speed = eng.snapshot()
                if it.kind == "hls":
                    it.size = eng.estimated_size()
                self.changed.emit(iid)

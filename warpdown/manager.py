"""Download queue/manager: persistence, scheduling, progress for the UI."""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from urllib.parse import urlsplit
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import checksum as checksummod
from . import retry as retrymod
from . import torrent as torrentmod
from .engine import Downloader, Job, RateLimiter, is_hls, probe
from .limits import ChainedLimiter, LimitSchedule
from .stats import Stats
from .vpn import vpn_connected
from .inbox import Inbox
from .hls import HlsDownloader
from .scheduler import Schedule
from .subtitles import fetch_subtitle, matches_langs, tag_for
from .ytdlp import YtdlpDownloader
from .osutil import IS_WINDOWS
from .util import sanitize_filename, category_for, data_dir, ensure_category_dirs, guess_extension, unique_path

QUEUED, PROBING, DOWNLOADING, PAUSED, COMPLETED, ERROR, RETRYING = (
    "Queued", "Connecting", "Downloading", "Paused", "Completed", "Error", "Retrying")


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
    retries: int = 0         # automatic retries used so far (reset when the download succeeds or the user resumes it)
    retry_at: float = 0.0    # when the next automatic retry happens (epoch seconds), while status == "Retrying"
    held: bool = False       # paused by the scheduler (not by the user): resumed when the schedule allows it again
    sort: bool = False  # file into <directory>/<Category>/ once the file type is known
    headers: dict = field(default_factory=dict)  # referer, cookie, user-agent from the browser
    limit_kb: int = 0        # this download's own speed limit in KB/s (0 = only the global limit applies)
    priority: int = 0        # -1 low, 0 normal, 1 high: higher priority starts first
    order: float = 0.0       # position in the queue (smaller = earlier); 0 = use the time it was added
    checksum: str = ""       # expected checksum (given by the user or found next to the file)
    verified: str = ""       # "", "checking", "ok" or "bad"
    verify_algo: str = ""    # "sha256" etc. of the last check

    @property
    def path(self) -> Path:
        return Path(self.directory) / self.filename

    @property
    def position(self) -> float:
        return self.order or self.added

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


def _verify_job(q, iid: str, path: str, expected: str, url: str, filename: str, look_for_published: bool) -> None:
    """Hash the finished file and compare. Without a given checksum, look for one published next to the download."""
    try:
        found_published = False
        if not expected and look_for_published:
            import httpx
            for cand in checksummod.sidecar_candidates(url):
                try:
                    r = httpx.get(cand, timeout=6, follow_redirects=True)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and len(r.content) < 200_000:
                    expected = checksummod.find_in_text(r.text, filename) or ""
                    if expected:
                        found_published = True
                        break
        if not expected:
            q.put(("verified", (iid, "", "", "")))
            return
        ok, algo = checksummod.verify(Path(path), expected)
        parsed = checksummod.parse_expected(expected)
        q.put(("verified", (iid, "ok" if ok else "bad", algo, parsed[1] if found_published and parsed else "")))
    except Exception:  # noqa: BLE001  (a failed check must never disturb the download)
        q.put(("verified", (iid, "", "", "")))


class DownloadManager(QObject):
    changed = Signal(str)       # item id
    queue_finished = Signal()   # everything that was running has finished (at least one download completed)
    schedule_changed = Signal()  # the scheduler opened or closed the gate
    added = Signal(str)
    removed = Signal(str)
    vpn_changed = Signal(bool)   # True = downloads are paused because the VPN is not connected

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: dict[str, Item] = {}
        self.engines: dict[str, Downloader] = {}
        self.limiter = RateLimiter(0)
        self.base_limit = 0                       # bytes/s outside the limit schedule's hours
        self.limit_schedule = LimitSchedule()
        self._own_limiters: dict[str, RateLimiter] = {}
        self.stats = Stats()
        self.require_vpn = False                  # pause everything while no VPN is connected
        self._vpn_blocked = False
        self._vpn_checked = 0.0
        self.find_published_checksums = True      # look for NAME.sha256 etc. next to a finished download
        self.max_active = 3
        self.default_connections = 8
        self.sort_by_category = True
        self.sub_langs: list[str] = []   # wanted subtitle languages for auto-found tracks; [] = all
        self.auto_retry = True           # retry failed downloads automatically (network problems only)
        self.max_retries = 5
        self.schedule = Schedule()       # when downloads may run
        self._gate = True                # False while the scheduler holds downloads back
        self._last_check = datetime.now()
        self._phase_active = False       # something has been downloading since the queue was last idle
        self._phase_completed = 0
        self._file = data_dir() / "downloads.json"
        # Worker threads talk to this object only through the inbox (plain data in a queue), never via Qt signals.
        self._inbox = Inbox({"finished": self._on_finished, "probed": self._on_probed, "subs": self._on_subs_done,
                                "verified": self._on_verified}, self)
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
                if it.status in (DOWNLOADING, PROBING, QUEUED, RETRYING):
                    it.status = PAUSED
                if it.verified == "checking":
                    it.verified = ""
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
        if kind == "http" and torrentmod.is_torrent_source(url):
            kind = "torrent"
            filename = filename or torrentmod.magnet_name(url)
        return Item(url=url, directory=directory, filename=filename, headers=dict(headers or {}),
                    connections=connections or self.default_connections,
                    sort=self.sort_by_category if sort is None else sort, subs=list(subs or []),
                    auto_subs=auto_subs, kind=kind, opts=dict(opts or {}), status=QUEUED if start else PAUSED,
                    order=time.time())

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
        it.status, it.speed, it.held = PAUSED, 0.0, False      # a pause by the user is never undone by the scheduler
        self.changed.emit(iid)
        self.save()

    def resume(self, iid: str) -> None:
        it = self.items.get(iid)
        if it and it.status in (PAUSED, ERROR, RETRYING):
            it.status, it.error, it.held, it.retries, it.retry_at = QUEUED, "", False, 0, 0.0
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
                if it.kind == "torrent" and it.path.is_dir():
                    shutil.rmtree(it.path, ignore_errors=True)
                else:
                    it.path.unlink(missing_ok=True)
            Path(str(it.path) + ".aria2").unlink(missing_ok=True)      # torrent resume file
        self.removed.emit(iid)
        self.save()
        self._schedule()

    def set_speed_limit(self, bytes_per_s: int) -> None:
        self.base_limit = max(0, bytes_per_s)
        self._apply_limit(datetime.now())

    def set_limit_schedule(self, sched: LimitSchedule) -> None:
        self.limit_schedule = sched
        self._apply_limit(datetime.now())

    def _apply_limit(self, now: datetime) -> None:
        s = self.limit_schedule
        self.limiter.rate = max(0, s.kb) * 1024 if s.active(now) else self.base_limit

    def set_item_limit(self, iid: str, kb: int) -> None:
        it = self.items.get(iid)
        if not it:
            return
        it.limit_kb = max(0, kb)
        own = self._own_limiters.get(iid)
        if own:
            own.rate = it.limit_kb * 1024
        self.changed.emit(iid)
        self.save()

    # ---- queue order and priority --------------------------------------
    def queue_order(self) -> list[Item]:
        return sorted(self.items.values(), key=lambda i: (-i.priority, i.position))

    def set_priority(self, iid: str, priority: int) -> None:
        it = self.items.get(iid)
        if it:
            it.priority = max(-1, min(1, priority))
            self.changed.emit(iid)
            self.save()
            self.added.emit(iid)         # the list is shown in queue order: redraw it
            self._schedule()

    def move(self, iids: list[str], where: str) -> None:
        """Move downloads inside the queue: 'up', 'down', 'top' or 'bottom' (within their priority)."""
        chosen = set(iids)
        order = [i.id for i in self.queue_order()]
        if where in ("top", "bottom"):
            picked = [i for i in order if i in chosen]
            rest = [i for i in order if i not in chosen]
            order = picked + rest if where == "top" else rest + picked
        else:
            seq = range(len(order)) if where == "up" else range(len(order) - 1, -1, -1)
            step = -1 if where == "up" else 1
            for n in seq:
                m = n + step
                if order[n] in chosen and 0 <= m < len(order) and order[m] not in chosen:
                    order[n], order[m] = order[m], order[n]
        for pos, iid in enumerate(order, start=1):
            self.items[iid].order = float(pos)
        self.save()
        self.added.emit(order[0] if order else "")
        self._schedule()

    # ---- checksums -------------------------------------------------------
    def set_checksum(self, iid: str, text: str) -> None:
        """Remember the expected checksum; a finished download is checked at once. Raises ValueError if unusable."""
        it = self.items.get(iid)
        if not it:
            return
        if text.strip() and not checksummod.parse_expected(text):
            raise ValueError("That is not a valid MD5, SHA-1, SHA-256 or SHA-512 checksum.")
        it.checksum, it.verified = text.strip(), ""
        self.changed.emit(iid)
        self.save()
        if it.status == COMPLETED and it.checksum:
            self._start_verify(it)

    def _start_verify(self, it: Item, look_for_published: bool = False) -> None:
        if not it.path.is_file():
            return
        if it.checksum:
            it.verified = "checking"
            self.changed.emit(it.id)
        threading.Thread(target=_verify_job, args=(self._inbox.queue, it.id, str(it.path), it.checksum, it.url,
                                                   it.filename, look_for_published), daemon=True).start()

    def _on_verified(self, iid: str, state: str, algo: str, published: str) -> None:
        it = self.items.get(iid)
        if not it:
            return
        it.verified, it.verify_algo = state, algo
        if published:
            it.checksum = published
        self.changed.emit(iid)
        self.save()

    # ---- VPN ---------------------------------------------------------------
    @property
    def vpn_blocked(self) -> bool:
        return self._vpn_blocked

    def set_require_vpn(self, on: bool) -> None:
        self.require_vpn = on
        self._vpn_checked = 0.0
        if on:
            self._set_vpn_blocked(not vpn_connected())
        else:
            self._set_vpn_blocked(False)

    def _set_vpn_blocked(self, blocked: bool) -> None:
        if blocked == self._vpn_blocked:
            return
        self._vpn_blocked = blocked
        if blocked:
            self._hold_active()
        elif self._gate:
            self._release_held()
        self.vpn_changed.emit(blocked)
        self.schedule_changed.emit()

    def shutdown(self) -> None:
        for eng in list(self.engines.values()):
            eng.stop(wait=True)
        for it in self.items.values():
            if it.status in (DOWNLOADING, PROBING, QUEUED, RETRYING):
                it.status = PAUSED
        self.save()

    # ---- scheduling -------------------------------------------------
    def _active_count(self) -> int:
        return sum(1 for i in self.items.values() if i.status in (DOWNLOADING, PROBING))

    def _schedule(self) -> None:
        if not self._gate or self._vpn_blocked:
            return
        for it in self.queue_order():
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
        if it.kind == "torrent":
            if not torrentmod.available():
                it.status, it.error = ERROR, torrentmod.INSTALL_HINT
                self.changed.emit(it.id)
                self._schedule()
                return
            if not it.prepared:
                if it.sort:
                    it.directory = str(Path(it.directory) / "Torrents")
                    it.sort = False
                try:
                    Path(it.directory).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    it.status, it.error = ERROR, str(e)
                    self.changed.emit(it.id)
                    self._schedule()
                    return
                it.filename = it.filename or torrentmod.magnet_name(it.url) or "torrent"
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
            self._fail(it, err)
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
        if IS_WINDOWS:
            name = sanitize_filename(name)           # page titles contain characters Windows file names cannot have
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
        cls = {"hls": HlsDownloader, "ytdlp": YtdlpDownloader, "torrent": torrentmod.Aria2Downloader}.get(it.kind, Downloader)
        own = self._own_limiters.setdefault(it.id, RateLimiter(0))
        own.rate = it.limit_kb * 1024
        eng = cls(job, ChainedLimiter(self.limiter, own), on_finish=lambda d, i=it.id, q=self._inbox.queue: q.put(("finished", (i, d))))
        self.engines[it.id] = eng
        self._phase_active = True
        it.status = DOWNLOADING
        self.changed.emit(it.id)
        eng.start()

    def _on_finished(self, iid: str, eng) -> None:
        if self.engines.get(iid) is eng:
            self.engines.pop(iid, None)
        it = self.items.get(iid)
        if not it:
            return
        if not eng.done and not eng.error and it.status != DOWNLOADING:
            # The engine stopped because of a pause/hold. If the item was resumed meanwhile it is QUEUED again and
            # must simply be started afresh (never flipped back to "Paused" by this late message).
            self._schedule()
            return
        it.speed = 0.0
        if eng.done:
            if it.kind in ("hls", "ytdlp", "torrent"):
                it.filename, it.size = eng.job.dest.name, eng.downloaded()
                it.category = category_for(it.filename)
            it.status, it.downloaded = COMPLETED, max(it.size, it.downloaded)
            it.retries, self._phase_completed = 0, self._phase_completed + 1
            self.stats.record(it.size, it.category)
            if it.kind in ("http", "hls") and (it.checksum or (self.find_published_checksums and it.kind == "http"
                                                              and it.size >= 1_000_000)):
                self._start_verify(it, look_for_published=self.find_published_checksums and not it.checksum)
            if it.kind == "ytdlp":
                it.sub_files = list(eng.sub_files)    # yt-dlp already saved them next to the video
            else:
                self._start_subtitles(it, getattr(eng, "sub_tracks", []))
        elif eng.error:
            self._fail(it, eng.error)
        else:
            it.status = PAUSED
        self.changed.emit(iid)
        self.save()
        self._schedule()
        self._check_queue_finished()

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

    # ---- automatic retry ----------------------------------------------
    def _fail(self, it: Item, error: str) -> None:
        """A download failed: wait and try again if it looks like a temporary problem, else show the error."""
        if self.auto_retry and it.retries < self.max_retries and retrymod.is_transient(error):
            it.retries += 1
            it.retry_at = time.time() + retrymod.delay_for(it.retries)
            it.status, it.error = RETRYING, error
        else:
            it.status, it.error = ERROR, error

    # ---- the scheduler --------------------------------------------------
    @property
    def gate_open(self) -> bool:
        return self._gate

    def set_schedule(self, schedule: Schedule) -> None:
        self.schedule = schedule
        self._update_gate(datetime.now(), force=True)

    def _update_gate(self, now: datetime, force: bool = False) -> None:
        """Open or close the gate according to the schedule, holding or releasing downloads as it changes."""
        s = self.schedule
        prev, self._last_check = self._last_check, now
        if force:
            prev = now                               # (re)configured: don't react to start times in the past
        if not s.enabled:
            new = True
        elif s.window_mode:
            new = bool(s.gate(now))
        else:                                        # trigger mode: opens at the start time, closes when finished
            new = False if force else self._gate
            if s.start_due(prev, now):
                new = True
        if new == self._gate:
            return
        self._gate = new
        if new:
            self._release_held()
        else:
            self._hold_active()
        self.schedule_changed.emit()

    def _hold_active(self) -> None:
        for it in self.items.values():
            if it.status in (DOWNLOADING, PROBING):
                eng = self.engines.get(it.id)
                if eng:
                    eng.stop(wait=False)
                it.status, it.speed, it.held = PAUSED, 0.0, True
                self.changed.emit(it.id)
        self.save()

    def _release_held(self) -> None:
        if self._vpn_blocked:
            return                                   # the VPN is down: they stay held until it is back
        for it in self.items.values():
            if it.held:
                it.status, it.held = QUEUED, False
                self.changed.emit(it.id)
        self._schedule()

    def _check_queue_finished(self) -> None:
        """Emit queue_finished once, when the last running download is done and nothing is waiting."""
        busy = any(i.status in (DOWNLOADING, PROBING) for i in self.items.values())
        waiting = any(i.status in (QUEUED, RETRYING) or i.held for i in self.items.values())
        if busy:
            self._phase_active = True
            return
        if waiting or not self._phase_active:
            return
        completed, self._phase_active, self._phase_completed = self._phase_completed, False, 0
        if self.schedule.enabled and not self.schedule.window_mode and self._gate:
            self._gate = False                       # trigger mode: closed again until the next start time
            self.schedule_changed.emit()
        if completed:
            self.queue_finished.emit()

    def _tick(self) -> None:
        now = time.time()
        promoted = False
        for it in self.items.values():
            if it.status == RETRYING:
                if it.retry_at <= now:
                    it.status, promoted = QUEUED, True
                self.changed.emit(it.id)            # also refreshes the "retrying in 30 s" countdown
        self._update_gate(datetime.now())
        self._apply_limit(datetime.now())
        if self.require_vpn and time.monotonic() - self._vpn_checked >= 3:
            self._vpn_checked = time.monotonic()
            self._set_vpn_blocked(not vpn_connected())
        if promoted:
            self._schedule()
        self._check_queue_finished()
        for iid, eng in list(self.engines.items()):
            it = self.items.get(iid)
            if it and it.status == DOWNLOADING:
                it.downloaded, it.speed = eng.snapshot()
                if it.kind in ("hls", "ytdlp", "torrent"):
                    it.size = eng.estimated_size()
                if it.kind == "torrent" and getattr(eng, "name", ""):
                    it.filename = eng.name
                self.changed.emit(iid)

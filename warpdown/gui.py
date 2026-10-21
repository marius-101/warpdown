"""Qt main window."""

from __future__ import annotations

import html
import os
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QRectF, QSettings, QSize, Qt, QTime, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPalette, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QGroupBox, QVBoxLayout, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QTimeEdit, QTreeWidget, QTreeWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle, QStyledItemDelegate,
    QStyleOptionProgressBar, QSplitter, QSystemTrayIcon, QTableView, QToolBar, QWidget,
)

from . import __version__
from . import batch, browsers, linkgrab, themes, ytdlp
from .clipboard import ClipboardWatcher
from .scheduler import (AFTER_LABELS, AFTER_NOTHING, AFTER_POWEROFF, AFTER_QUIT, AFTER_SLEEP, DAY_NAMES, Schedule,
                        power_command)
from .inbox import Inbox
from .engine import probe
from .subtitles import default_checked, matches_langs, parse_langs, peek_subtitle, tag_for
from .manager import COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED, RETRYING, DownloadManager, Item
from .util import (CATEGORIES, category_folder, default_download_dir, ensure_category_dirs, fmt_eta, fmt_size, fmt_speed,
                   sanitize_filename)

_PEEK_SLOTS = threading.Semaphore(3)


# ---- background workers -------------------------------------------------------------------------------------
# Module-level on purpose: they get only plain data and a queue, so they cannot capture (and later release or emit on)
# a dialog from a worker thread. Results reach the dialog through its Inbox, in the GUI thread.
def _probe_worker(q, url: str) -> None:
    try:
        q.put(("probed", (probe(url), "")))
    except Exception as e:  # noqa: BLE001
        q.put(("probed", (None, str(e))))


def _ytdlp_info_worker(q, url: str, headers: dict) -> None:
    try:
        q.put(("yt", (ytdlp.fetch_info(url, headers), "")))
    except Exception as e:  # noqa: BLE001
        q.put(("yt", (None, str(e) or e.__class__.__name__)))


def _peek_worker(q, index: int, url: str, headers: dict) -> None:
    with _PEEK_SLOTS:                          # a few at a time: servers may throttle bursts
        for attempt in (1, 2):
            try:
                q.put(("sub", (index, peek_subtitle(url, headers))))
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    q.put(("sub", (index, {"error": str(e) or e.__class__.__name__})))
                else:
                    time.sleep(1)


def _ytdlp_update_worker(q) -> None:
    try:
        ok, msg = ytdlp.update()
        q.put(("yt_updated", (ok, msg)))
    except Exception as e:  # noqa: BLE001
        q.put(("yt_updated", (False, str(e))))

FONT_SIZES = [8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24]   # offered in Settings / by Ctrl +/-
_BASE_FONT: QFont | None = None                                 # the system's own default font, captured once


def base_font_pt() -> float:
    pt = _BASE_FONT.pointSizeF() if _BASE_FONT is not None else QApplication.font().pointSizeF()
    return pt if pt > 0 else 10.0


def apply_font(size_pt: int) -> float:
    """Set the application's text size in points (0 = the system default). Returns the scale vs. the default."""
    global _BASE_FONT
    app = QApplication.instance()
    if _BASE_FONT is None:
        _BASE_FONT = QFont(app.font())
    f = QFont(_BASE_FONT)
    if size_pt > 0:
        f.setPointSizeF(float(size_pt))
    app.setFont(f)
    return font_scale()


def font_scale() -> float:
    pt = QApplication.font().pointSizeF()
    return (pt if pt > 0 else base_font_pt()) / base_font_pt()


def saved_theme() -> tuple[str, str]:
    s = QSettings()
    style, mode = str(s.value("ui_style", "default")), str(s.value("ui_mode", "auto"))
    return (style if style in themes.STYLES else "default"), (mode if mode in themes.MODES else "auto")


def saved_font_size() -> int:
    try:
        return int(QSettings().value("font_size", 0) or 0)
    except (TypeError, ValueError):
        return 0

COLUMNS = ["Name", "Size", "Progress", "Speed", "ETA", "Status", "Added"]
CATS = ["All Downloads", "Unfinished", "Finished", "Video", "Music", "Compressed",
        "Documents", "Programs", "Images", "Other"]


class Model(QAbstractTableModel):
    def __init__(self, mgr: DownloadManager):
        super().__init__()
        self.mgr = mgr
        self.filter = "All Downloads"
        self.rows: list[str] = []
        self.rebuild()
        mgr.added.connect(lambda _: self.rebuild())
        mgr.removed.connect(lambda _: self.rebuild())
        mgr.changed.connect(self._changed)
        mgr.schedule_changed.connect(self._all_changed)

    def matches(self, it: Item) -> bool:
        f = self.filter
        if f == "All Downloads":
            return True
        if f == "Unfinished":
            return it.status != COMPLETED
        if f == "Finished":
            return it.status == COMPLETED
        return it.category == f

    def rebuild(self) -> None:
        self.beginResetModel()
        self.rows = [i.id for i in sorted(self.mgr.items.values(), key=lambda i: -i.added)
                     if self.matches(i)]
        self.endResetModel()

    def _all_changed(self) -> None:
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMNS) - 1))

    def _changed(self, iid: str) -> None:
        if iid in self.rows:
            r = self.rows.index(iid)
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))

    def item(self, row: int) -> Item:
        return self.mgr.items[self.rows[row]]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.item(index.row())
        c = index.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return it.filename or it.url
            if c == 1:
                return fmt_size(it.size) if it.size > 0 else ""
            if c == 2:
                return f"{it.progress * 100:.1f}%" if it.size > 0 else fmt_size(it.downloaded)
            if c == 3:
                return fmt_speed(it.speed) if it.status == DOWNLOADING else ""
            if c == 4:
                if it.status == DOWNLOADING and it.speed > 0 and it.size > 0:
                    return fmt_eta((it.size - it.downloaded) / it.speed)
                return ""
            if c == 5:
                if it.status == RETRYING:
                    left = max(0, int(it.retry_at - time.time()))
                    return f"Retrying in {fmt_eta(left) or '0s'} (attempt {it.retries}/{self.mgr.max_retries}) · {explain(it.error).short}"
                if it.held:
                    return "Paused by the schedule"
                if it.status == QUEUED and not self.mgr.gate_open:
                    return "Scheduled (waiting)"
                if it.error:
                    return f"{it.status}: {explain(it.error).short}"
                n = len(it.sub_files)
                return f"{it.status} · {n} subtitle{'s' if n != 1 else ''}" if n else it.status
            if c == 6:
                from datetime import datetime
                return datetime.fromtimestamp(it.added).strftime("%Y-%m-%d %H:%M")
        if role == Qt.UserRole and c == 2:
            return int(it.progress * 1000)
        if role == Qt.ToolTipRole:
            return error_tooltip(it.error, it.url) if (c == 5 and it.error) else it.url
        return None


from .app_icon import app_icon
from .errors import explain, tooltip as error_tooltip


class ProgressDelegate(QStyledItemDelegate):
    """Hand-drawn progress bar: always fills left -> right, independent of the Qt style."""

    def paint(self, painter, option, index):
        val = index.data(Qt.UserRole)
        if val is None:
            return super().paint(painter, option, index)
        pal = QApplication.palette()      # the application's palette: the table's own copy is altered by stylesheets
        rect = QRectF(option.rect.adjusted(4, 5, -4, -5))
        fill = QRectF(rect.left(), rect.top(), rect.width() * max(0, min(val, 1000)) / 1000.0, rect.height())
        painter.save()
        painter.setFont(option.font)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        groove = QColor(pal.color(QPalette.Mid)); groove.setAlpha(110)
        painter.setBrush(groove)
        radius = themes.corner_radius()
        painter.drawRoundedRect(rect, radius, radius)
        if fill.width() > 0:
            painter.setClipRect(fill)
            painter.setBrush(pal.color(QPalette.Highlight))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setClipping(False)
        text = index.data(Qt.DisplayRole) or ""
        painter.setClipRect(fill)
        painter.setPen(pal.color(QPalette.HighlightedText))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.setClipRect(QRectF(fill.right(), rect.top(), rect.width(), rect.height()))
        painter.setPen(pal.color(QPalette.Text))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()


class AddDialog(QDialog):
    def __init__(self, parent, url: str = "", headers: dict | None = None, use_ytdlp: bool = False):
        super().__init__(parent)
        self._inbox = Inbox({"probed": self._on_probed, "yt": self._on_yt_ready, "sub": self._on_sub_info}, self)
        self._hdrs = {{"referer": "Referer", "cookie": "Cookie", "user_agent": "User-Agent"}[k]: v
                      for k, v in (headers or {}).items() if k in ("referer", "cookie", "user_agent") and v}
        self._yt_user_set = use_ytdlp            # don't auto-toggle if the browser already told us the mode
        self.setWindowTitle("Add Download")
        self.setMinimumWidth(int(520 * font_scale()))
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://example.com/file.zip")
        self.info = QLabel("")
        self.name = QLineEdit()
        self.name.setPlaceholderText("(automatic)")
        settings = QSettings()
        self.dir = QLineEdit(settings.value("download_dir", str(default_download_dir())))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.dir)
        row.addWidget(browse)
        self.conns = QSpinBox()
        self.conns.setRange(1, 32)
        self.conns.setValue(int(settings.value("connections", 8)))
        self.sort = QCheckBox("Sort into category folder (Video, Music, …)")
        self.sort.setChecked(str(settings.value("sort_by_category", "true")).lower() == "true")
        self.start_now = QCheckBox("Start immediately")
        self.start_now.setChecked(True)
        self.use_ytdlp = QCheckBox("Video site (YouTube, Vimeo, …): use yt-dlp")
        self.use_ytdlp.setToolTip("Lets yt-dlp find the best video + audio and merge them.")
        self.use_ytdlp.setEnabled(ytdlp.available())
        self.quality = QComboBox()
        self.quality.addItems(list(ytdlp.QUALITIES))
        self.quality.currentTextChanged.connect(self._on_quality)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.addRow("URL:", self.url)
        form.addRow("", self.info)
        form.addRow("Save as:", self.name)
        form.addRow("Save to:", row)
        form.addRow("Connections:", self.conns)
        form.addRow("", self.use_ytdlp)
        form.addRow("Quality:", self.quality)
        self._form = form
        form.setRowVisible(self.quality, False)
        form.addRow("", self.sort)
        form.addRow("", self.start_now)
        self.subs_label = QLabel("Subtitles found on the page (checked ones are saved next to the video):")
        self.subs_list = QListWidget()
        self.subs_list.setMinimumHeight(90)
        self._sub_tracks: list = []
        self._sub_headers: dict = {}
        self._sub_touched = False
        self._sub_invalid: set = set()
        self._sub_loading = False
        self.subs_list.itemChanged.connect(self._on_sub_changed)
        self.subs_label.hide(); self.subs_list.hide()
        outer.addLayout(form)
        outer.addWidget(self.subs_label)
        outer.addWidget(self.subs_list, 3)       # extra height goes here, not into gaps between the form rows
        outer.addStretch(1)                      # ...or below the form when there is no subtitle list
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)
        self.use_ytdlp.clicked.connect(self._on_ytdlp_clicked)
        self.url.editingFinished.connect(self._probe)
        if use_ytdlp and ytdlp.available():
            self._set_ytdlp(True)
        if url:
            self._probe()

    # ---- yt-dlp mode ----
    def _set_ytdlp(self, on: bool) -> None:
        self.use_ytdlp.setChecked(on)
        self._form.setRowVisible(self.quality, on)

    def _on_ytdlp_clicked(self, checked: bool) -> None:
        self._yt_user_set = True
        self._form.setRowVisible(self.quality, checked)
        self._probe()

    def _on_quality(self, text: str) -> None:
        name = self.name.text()
        base, ext = os.path.splitext(name)
        if ext.lower() in (".mp4", ".mp3"):
            self.name.setText(base + (".mp3" if text == ytdlp.AUDIO_ONLY else ".mp4"))

    def _probe_ytdlp(self, u: str) -> None:
        self.info.setText("Asking yt-dlp… (can take a few seconds)")

        threading.Thread(target=_ytdlp_info_worker, args=(self._inbox.queue, u, dict(self._hdrs)), daemon=True).start()

    def _on_yt_ready(self, info, err: str) -> None:
        if info is None:
            self.info.setText(f"yt-dlp couldn't use this page: {err}")
            return
        if info["live"]:
            self.info.setText("This is a live stream; live streams aren't supported.")
            return
        mins, secs = divmod(int(info["duration"]), 60)
        h = f" · up to {info['max_height']}p" if info["max_height"] else ""
        text = f"{info['title'][:60]} — {mins}:{secs:02d}{h}"
        if ytdlp.is_youtube(self.url.text()) and not ytdlp.js_runtime():
            text += "\nTip: install Node.js (sudo apt install nodejs) so YouTube offers every quality."
        self.info.setText(text)
        ext = ".mp3" if self.quality.currentText() == ytdlp.AUDIO_ONLY else ".mp4"
        if not self.name.text() or self.name.text().endswith((".mp4", ".mp3")):
            self.name.setText(sanitize_filename(info["title"]) + ext)
        wanted = parse_langs(str(QSettings().value("sub_langs", "eng,en")))
        tracks = []
        for t in info["subs"]:
            if t["auto"] and not (wanted and matches_langs(t["lang"], wanted)):
                continue          # auto-captions exist in ~150 languages: only offer the wanted ones
            tracks.append({"url": f"ytdlp://{t['lang']}{'-auto' if t['auto'] else ''}", "lang": t["lang"],
                           "label": t["label"], "auto": t["auto"]})
        self.add_subs(tracks)

    def ytdlp_options(self) -> dict:
        q = self.quality.currentText()
        return {"format": ytdlp.QUALITIES[q], "audio_only": q == ytdlp.AUDIO_ONLY,
                "subs": [{"lang": t["lang"], "auto": bool(t.get("auto"))} for t in self.selected_subs()]}

    def set_subs(self, subs: list, headers: dict) -> None:
        """Checklist of subtitle tracks (page tracks now, HLS tracks once the stream is probed)."""
        self._sub_headers = {{"referer": "Referer", "cookie": "Cookie", "user_agent": "User-Agent"}[k]: v
                             for k, v in (headers or {}).items()
                             if k in ("referer", "cookie", "user_agent") and v}
        self.add_subs(subs)

    def add_subs(self, new: list) -> None:
        have = {t["url"] for t in self._sub_tracks}
        new = [t for t in new if t["url"] not in have]
        if not new:
            return
        self.subs_label.show(); self.subs_list.show()
        for t in new:
            i = len(self._sub_tracks)
            tag = tag_for(t.get("lang", ""), t.get("label", ""), t["url"])
            tail = t.get("label") or "/".join(t["url"].split("?")[0].split("/")[-2:])
            it = QListWidgetItem(f"{tag}  ·  {tail}  ·  checking…")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setData(Qt.UserRole + 1, it.checkState().value)
            it.setToolTip(t["url"])
            self._sub_loading = True
            self.subs_list.addItem(it)
            self._sub_loading = False
            self._sub_tracks.append(t)

            if t["url"].startswith("ytdlp://"):     # yt-dlp downloads these itself; nothing to preview
                it.setText(f"{tag}  ·  {t.get('label') or tag}")
                continue

            threading.Thread(target=_peek_worker, args=(self._inbox.queue, i, t["url"], dict(self._sub_headers)),
                             daemon=True).start()
        self._apply_default_checks()

    def _apply_default_checks(self) -> None:
        """One tick per language (plain dialogue preferred), recomputed until the user touches a checkbox."""
        if self._sub_touched:
            return
        wanted = parse_langs(str(QSettings().value("sub_langs", "eng,en")))
        valid = [i for i in range(len(self._sub_tracks)) if i not in self._sub_invalid]
        pre = default_checked([self._sub_tracks[i] for i in valid], wanted)
        ticked = {i for i, c in zip(valid, pre) if c}
        self._sub_loading = True
        for i in range(len(self._sub_tracks)):
            it = self.subs_list.item(i)
            it.setCheckState(Qt.Checked if i in ticked else Qt.Unchecked)
            it.setData(Qt.UserRole + 1, it.checkState().value)
        self._sub_loading = False

    def _on_sub_changed(self, item) -> None:
        """Only a real tick/untick by the user counts as 'touched' (text updates must not)."""
        if self._sub_loading:
            return
        cur = item.checkState().value
        if item.data(Qt.UserRole + 1) != cur:
            self._sub_touched = True
            item.setData(Qt.UserRole + 1, cur)

    def _on_sub_info(self, i: int, info) -> None:
        it = self.subs_list.item(i)
        if it is None:
            return
        base = it.text().rsplit("  ·  checking…", 1)[0]
        if info is None:                                  # fetched fine, but it isn't subtitle text
            it.setText(f"{base}  ·  not a subtitle file")
            it.setFlags(it.flags() & ~Qt.ItemIsUserCheckable)
            self._sub_invalid.add(i)
            self._apply_default_checks()
        elif "error" in info:                             # couldn't fetch: say why, keep it selectable
            it.setText(f"{base}  ·  couldn't check ({info['error'][:50]})")
        else:
            it.setText(f"{base}  ·  {info['cues']} cues, {fmt_size(info['bytes'])}")

    def selected_subs(self) -> list:
        return [self._sub_tracks[i] for i in range(self.subs_list.count())
                if self.subs_list.item(i).checkState() == Qt.Checked]

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir.text())
        if d:
            self.dir.setText(d)

    def _probe(self):
        u = self.url.text().strip()
        if not u.startswith(("http://", "https://")):
            return
        if not self._yt_user_set and ytdlp.available() and ytdlp.looks_like_video_site(u):
            self._set_ytdlp(True)
        if self.use_ytdlp.isChecked():
            return self._probe_ytdlp(u)
        self.info.setText("Checking…")

        threading.Thread(target=_probe_worker, args=(self._inbox.queue, u), daemon=True).start()

    def _on_probed(self, res, err):
        if res is None:
            self.info.setText(f"Could not check: {err}")
            return
        if (res.content_type or "").lower().startswith("text/html") and not self._yt_user_set and ytdlp.available():
            self._set_ytdlp(True)                          # a web page, not a file: let yt-dlp look for the video
            return self._probe_ytdlp(self.url.text().strip())
        if getattr(res, "sub_tracks", None):
            self.add_subs(res.sub_tracks)
        size = fmt_size(res.size) if res.size > 0 else "unknown size"
        shown = res.filename if len(res.filename) <= 70 else res.filename[:34] + "…" + res.filename[-34:]
        self.info.setText(f"{shown} — {size} — {'resumable' if res.resumable else 'not resumable'}")
        self.info.setToolTip(res.filename)
        if not self.name.text():
            self.name.setText(res.filename)


class LinksDialog(QDialog):
    """"Download all links on this page": choose which of the links, images and media found there to download."""

    def __init__(self, parent, links: list, page_title: str = "", existing: set | None = None):
        super().__init__(parent)
        self.setWindowTitle("Download all links")
        k = font_scale()
        self.resize(int(940 * k), int(640 * k))
        self._existing = existing or set()
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{len(links)} links</b> found on {html.escape(page_title or 'this page')}")
        head.setWordWrap(True)
        lay.addWidget(head)

        # ---- filters ----
        row = QHBoxLayout()
        self.kind = QComboBox()
        counts = linkgrab.category_counts(links)
        self.kind.addItem("All files", linkgrab.KIND_ALL)
        for cat in (*CATEGORIES, "Other", linkgrab.KIND_PAGES):
            if counts.get(cat):
                self.kind.addItem(f"{cat} ({counts[cat]})", cat)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Only links containing these words (in the address or the link text)…")
        self.filter.setClearButtonEnabled(True)
        row.addWidget(QLabel("Show:")); row.addWidget(self.kind); row.addWidget(self.filter, 1)
        lay.addLayout(row)
        self.hide_pages = QCheckBox("Hide links without a file type (usually other web pages)")
        self.hide_pages.setChecked(True)
        lay.addWidget(self.hide_pages)

        # ---- list ----
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Found as", "Address"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rows: list[tuple] = []
        for l in links:
            it = QTreeWidgetItem([l.name, l.category, linkgrab.SOURCE_LABELS.get(l.source, "Link"), l.url])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Unchecked)
            it.setToolTip(0, l.text or l.name)
            it.setToolTip(3, l.url)
            if l.url.split("#", 1)[0] in self._existing:
                it.setText(0, f"{l.name}  (already in your list)")
            self._rows.append((it, l))
            self.tree.addTopLevelItem(it)
        for col, w in enumerate((int(300 * k), int(100 * k), int(80 * k), int(380 * k))):
            self.tree.setColumnWidth(col, w)
        lay.addWidget(self.tree, 1)

        # ---- selection buttons + counter ----
        row = QHBoxLayout()
        for text, fn in (("Select all shown", lambda: self._set_all(Qt.Checked)),
                         ("Select none", lambda: self._set_all(Qt.Unchecked)), ("Invert", self._invert)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.count = QLabel()
        row.addWidget(self.count)
        lay.addLayout(row)

        # ---- where to save ----
        settings = QSettings()
        form = QFormLayout()
        self.dir = QLineEdit(str(settings.value("download_dir", str(default_download_dir()))))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        drow = QHBoxLayout()
        drow.addWidget(self.dir, 1); drow.addWidget(browse)
        form.addRow("Save to:", drow)
        self.sort = QCheckBox("Sort into category folders (Video, Music, Images, …)")
        self.sort.setChecked(str(settings.value("sort_by_category", "true")).lower() == "true")
        self.start_now = QCheckBox("Start immediately")
        self.start_now.setChecked(True)
        form.addRow("", self.sort)
        form.addRow("", self.start_now)
        lay.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self.kind.currentIndexChanged.connect(self._apply_filter)
        self.filter.textChanged.connect(self._apply_filter)
        self.hide_pages.toggled.connect(self._apply_filter)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        self._apply_filter()
        # A short list is pre-ticked (links already in your list stay unticked); a long one is left for you to filter.
        visible = self._visible()
        if len(visible) <= 100:
            self._set_all(Qt.Checked)

    # ---- helpers ----
    def _visible(self) -> list:
        return [(it, l) for it, l in self._rows if not it.isHidden()]

    def _is_known(self, l) -> bool:
        return l.url.split("#", 1)[0] in self._existing

    def _apply_filter(self) -> None:
        kind, text, hide = self.kind.currentData(), self.filter.text(), self.hide_pages.isChecked()
        self.tree.setUpdatesEnabled(False)
        for it, l in self._rows:
            it.setHidden(not linkgrab.matches(l, kind, text, hide))
        self.tree.setUpdatesEnabled(True)
        self._update_count()

    def _set_all(self, state) -> None:
        self.tree.blockSignals(True)
        for it, l in self._visible():
            it.setCheckState(0, Qt.Unchecked if (state == Qt.Checked and self._is_known(l)) else state)
        self.tree.blockSignals(False)
        self._update_count()

    def _invert(self) -> None:
        self.tree.blockSignals(True)
        for it, _l in self._visible():
            it.setCheckState(0, Qt.Unchecked if it.checkState(0) == Qt.Checked else Qt.Checked)
        self.tree.blockSignals(False)
        self._update_count()

    def selected_links(self) -> list:
        """Ticked links that are also shown by the current filter."""
        return [l for it, l in self._visible() if it.checkState(0) == Qt.Checked]

    def _update_count(self) -> None:
        n, shown = len(self.selected_links()), len(self._visible())
        self.count.setText(f"{n} selected of {shown} shown")
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Download {n} file{'s' if n != 1 else ''}")
        ok.setEnabled(n > 0)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir.text())
        if d:
            self.dir.setText(d)


def run_power_command(cmd: list[str]) -> None:
    """Sleep / shut down the computer (a function of its own so tests can replace it)."""
    subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class BatchDialog(QDialog):
    """Add many downloads at once: paste addresses (one per line); ranges like photo[01-20].jpg are expanded."""

    def __init__(self, parent, prefill: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Batch download")
        k = font_scale()
        self.resize(int(780 * k), int(640 * k))
        lay = QVBoxLayout(self)
        intro = QLabel("Paste web addresses, <b>one per line</b>. Ranges are expanded: "
                       "<code>photo[01-20].jpg</code> &nbsp; <code>file[1-100:5].zip</code> (every 5th) &nbsp; "
                       "<code>part_[a-f].rar</code>. Lines starting with # are ignored.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.text = QPlainTextEdit()
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setPlaceholderText("https://example.org/files/report.pdf\nhttps://example.org/photos/img[001-050].jpg")
        self.text.setPlainText(prefill)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        paste = QPushButton("Paste from clipboard")
        paste.clicked.connect(lambda: self.text.appendPlainText(QGuiApplication.clipboard().text().strip()))
        clear = QPushButton("Clear")
        clear.clicked.connect(self.text.clear)
        row.addWidget(paste); row.addWidget(clear); row.addStretch(1)
        lay.addLayout(row)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)

        settings = QSettings()
        form = QFormLayout()
        self.dir = QLineEdit(str(settings.value("download_dir", str(default_download_dir()))))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        drow = QHBoxLayout()
        drow.addWidget(self.dir, 1); drow.addWidget(browse)
        form.addRow("Save to:", drow)
        self.sort = QCheckBox("Sort into category folders (Video, Music, Images, …)")
        self.sort.setChecked(str(settings.value("sort_by_category", "true")).lower() == "true")
        self.start_now = QCheckBox("Start immediately")
        self.start_now.setChecked(True)
        form.addRow("", self.sort)
        form.addRow("", self.start_now)
        lay.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        self._debounce = QTimer(self)                  # re-read the text 0.2 s after typing stops
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._update)
        self.text.textChanged.connect(self._debounce.start)
        self._update()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir.text())
        if d:
            self.dir.setText(d)

    def result_info(self):
        return batch.parse_text(self.text.toPlainText())

    def urls(self) -> list:
        return self.result_info().urls

    def _update(self) -> None:
        res = self.result_info()
        n = len(res.urls)
        lines = [f"<b>{n} download{'s' if n != 1 else ''}</b> from {res.lines_used} line{'s' if res.lines_used != 1 else ''}"]
        if n:
            sample = [res.urls[0]] + ([res.urls[-1]] if n > 1 else [])
            lines.append("e.g. " + "<br>&nbsp;&nbsp;&nbsp;&nbsp;…&nbsp;" .join(html.escape(u) for u in sample))
        if res.truncated:
            lines.append(f"<b>Only the first {batch.LIMIT:,} addresses are used</b> (that is the limit for one batch).")
        if res.skipped:
            lines.append(f"{len(res.skipped)} line{'s' if len(res.skipped) != 1 else ''} without a web address ignored.")
        self.preview.setText("<br>".join(lines))
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText(f"Download {n} file{'s' if n != 1 else ''}")
        ok.setEnabled(n > 0)


class SchedulerDialog(QDialog):
    """When may downloads run, and what happens when they are all finished."""

    def __init__(self, parent, schedule: Schedule, after_action: str = AFTER_NOTHING):
        super().__init__(parent)
        self.setWindowTitle("Scheduler")
        self.setMinimumWidth(int(520 * font_scale()))
        lay = QVBoxLayout(self)
        self.enabled = QCheckBox("Hold downloads and run them on a schedule")
        self.enabled.setChecked(schedule.enabled)
        lay.addWidget(self.enabled)
        form = QFormLayout()
        self.start = QTimeEdit(QTime(*schedule.start))
        self.start.setDisplayFormat("HH:mm")
        self.use_stop = QCheckBox("Stop at")
        self.use_stop.setChecked(schedule.stop is not None)
        self.stop = QTimeEdit(QTime(*(schedule.stop or (6, 0))))
        self.stop.setDisplayFormat("HH:mm")
        srow = QHBoxLayout()
        srow.addWidget(self.use_stop); srow.addWidget(self.stop); srow.addStretch(1)
        form.addRow("Start at:", self.start)
        form.addRow("", srow)
        drow = QHBoxLayout()
        self.days = []
        for i, name in enumerate(DAY_NAMES):
            c = QCheckBox(name)
            c.setChecked(i in schedule.days)
            self.days.append(c)
            drow.addWidget(c)
        form.addRow("Days:", drow)
        lay.addLayout(form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        note = QLabel("<small>With a stop time, downloads run only in that window (it may cross midnight). Without one, "
                      "the queue is released at the start time and runs until everything is finished. Downloads "
                      "you add outside the window wait; ones you paused yourself stay paused.</small>")
        note.setWordWrap(True)
        lay.addWidget(note)
        form2 = QFormLayout()
        self.after = QComboBox()
        for key, label in AFTER_LABELS.items():
            self.after.addItem(label, key)
        self.after.setCurrentIndex(max(0, self.after.findData(after_action)))
        form2.addRow("When all downloads are finished:", self.after)
        lay.addLayout(form2)
        warn = QLabel("<small>Before the computer sleeps or shuts down you get 60 seconds to cancel.</small>")
        lay.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.enabled, self.use_stop, *self.days):
            w.toggled.connect(self._refresh)
        for w in (self.start, self.stop):
            w.timeChanged.connect(self._refresh)
        self._refresh()

    def schedule(self) -> Schedule:
        st, sp = self.start.time(), self.stop.time()
        return Schedule(enabled=self.enabled.isChecked(), start=(st.hour(), st.minute()),
                        stop=(sp.hour(), sp.minute()) if self.use_stop.isChecked() else None,
                        days=frozenset(i for i, c in enumerate(self.days) if c.isChecked()))

    def after_action(self) -> str:
        return str(self.after.currentData())

    def _refresh(self) -> None:
        self.stop.setEnabled(self.use_stop.isChecked())
        self.summary.setText(self.schedule().describe())


class CountdownDialog(QDialog):
    """Shown when everything is finished and Warpdown is about to quit / sleep / shut down: a chance to cancel."""

    WHAT = {AFTER_QUIT: "Warpdown will quit", AFTER_SLEEP: "The computer will go to sleep",
            AFTER_POWEROFF: "The computer will shut down"}

    def __init__(self, parent, action: str, seconds: int = 60):
        super().__init__(parent)
        self.setWindowTitle("All downloads finished")
        self.left = seconds
        self.what = self.WHAT.get(action, "Warpdown will continue")
        lay = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        lay.addWidget(self.label)
        bb = QDialogButtonBox()
        now = bb.addButton("Do it now", QDialogButtonBox.AcceptRole)
        bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        now.setDefault(False)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._show()

    def _show(self) -> None:
        self.label.setText(f"All downloads are finished.\n\n{self.what} in {self.left} second{'s' if self.left != 1 else ''}.")

    def _tick(self) -> None:
        self.left -= 1
        if self.left <= 0:
            self.accept()
        else:
            self._show()


class ExtensionDialog(QDialog):
    """Help for adding the Warpdown extension to Firefox / Chrome and registering the browser connector."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browser extension")
        self.setMinimumWidth(int(580 * font_scale()))
        lay = QVBoxLayout(self)
        intro = QLabel("The browser extension takes over your browser's downloads and adds a <b>Download this "
                       "video</b> button to web videos. It talks to Warpdown through a small connector that has to "
                       "be registered once.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        # ---- Firefox ----
        ff = QGroupBox("Firefox")
        fl = QVBoxLayout(ff)
        steps = QLabel("1. Press <b>Install the Firefox add-on</b>. Firefox asks for permission: choose "
                       "<b>Allow</b>, then <b>Add</b>.<br>"
                       "2. Click the Warpdown toolbar icon and press <b>Grant access</b>.<br>"
                       "<small>The add-on is signed by Mozilla, so it stays installed after Firefox restarts. If "
                       "Firefox saves the file instead of installing it, open it with "
                       "<i>File → Open File</i>.</small>")
        steps.setWordWrap(True)
        fl.addWidget(steps)
        row = QHBoxLayout()
        self.btn_ff_install = QPushButton("Install the Firefox add-on")
        self.btn_ff_install.clicked.connect(self._install_firefox)
        row.addWidget(self.btn_ff_install)
        if browsers.FIREFOX_ADDON_URL:
            b = QPushButton("Install from addons.mozilla.org")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(browsers.FIREFOX_ADDON_URL)))
            row.addWidget(b)
        row.addStretch(1)
        fl.addLayout(row)
        dev = QLabel("<small><b>Developers:</b> to test a build from source, load it as a temporary add-on "
                     "(forgotten when Firefox restarts).</small>")
        dev.setWordWrap(True)
        fl.addWidget(dev)
        row = QHBoxLayout()
        self.btn_ff = QPushButton("Open Firefox debugging page")
        self.btn_ff.clicked.connect(self._open_firefox)
        self.btn_xpi = QPushButton("Show extension file")
        self.btn_xpi.clicked.connect(self._show_xpi)
        row.addWidget(self.btn_ff); row.addWidget(self.btn_xpi); row.addStretch(1)
        fl.addLayout(row)
        lay.addWidget(ff)

        # ---- Chrome / Chromium / Brave ----
        ch = QGroupBox("Chrome, Chromium, Brave")
        cl = QVBoxLayout(ch)
        csteps = QLabel("1. Press <b>Open extensions page</b> and turn on <i>Developer mode</i>.<br>"
                        "2. Press <b>Load unpacked</b> and choose the folder (<i>Show extension folder</i>).<br>"
                        "3. Copy the extension's <b>ID</b> from that page, paste it below and press "
                        "<b>Register</b>.")
        csteps.setWordWrap(True)
        cl.addWidget(csteps)
        row = QHBoxLayout()
        self.btn_ch = QPushButton("Open extensions page")
        self.btn_ch.clicked.connect(self._open_chrome)
        self.btn_dir = QPushButton("Show extension folder")
        self.btn_dir.clicked.connect(self._show_folder)
        row.addWidget(self.btn_ch); row.addWidget(self.btn_dir)
        if browsers.CHROME_STORE_URL:
            b = QPushButton("Install from Chrome Web Store")
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(browsers.CHROME_STORE_URL)))
            row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        row = QHBoxLayout()
        self.chrome_id = QLineEdit()
        self.chrome_id.setPlaceholderText("Extension ID, e.g. abcdefghijklmnopabcdefghijklmnop")
        self.btn_reg = QPushButton("Register")
        self.btn_reg.clicked.connect(self._register_chrome)
        row.addWidget(self.chrome_id, 1); row.addWidget(self.btn_reg)
        cl.addLayout(row)
        lay.addWidget(ch)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.result)
        bottom = QHBoxLayout()
        self.btn_fix = QPushButton("Install / repair connector")
        self.btn_fix.setToolTip("Registers Warpdown's browser connector for Firefox and any Chrome-family browser "
                                "that already has the ID saved.")
        self.btn_fix.clicked.connect(lambda: self._run_installer(""))
        proj = QPushButton("Project page")
        proj.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(browsers.PROJECT_URL)))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_fix); bottom.addWidget(proj); bottom.addStretch(1); bottom.addWidget(close)
        lay.addLayout(bottom)

        have_ext = browsers.extension_dir() is not None
        for b in (self.btn_xpi, self.btn_dir):
            b.setEnabled(have_ext)
        self.btn_reg.setEnabled(browsers.installer_script() is not None)
        self.btn_fix.setEnabled(browsers.installer_script() is not None)
        if not have_ext:
            self.result.setText("The extension files aren't part of this installation. Download them from the "
                                "project page.")
        self._refresh_status()
        # word-wrapped labels need more height at a given width: size the dialog for that, or lines get clipped
        w = self.minimumWidth()
        self.resize(w, lay.totalHeightForWidth(w))

    def _refresh_status(self) -> None:
        parts = [f"{name} {'✓' if ok else '✗'}" for name, ok in browsers.registration_status()]
        self.status.setText("<b>Connector registered:</b> " + "   ".join(parts))

    def _say(self, text: str) -> None:
        self.result.setText(text)

    def _install_firefox(self) -> None:
        cmd = browsers.firefox_command()
        if cmd is None:
            return self._say("Firefox wasn't found on this computer.")
        ok = browsers.open_in_browser(cmd, browsers.FIREFOX_XPI_URL)
        self._say("Asked Firefox to install the add-on. Allow it, then press Add." if ok else
                  "Couldn't start Firefox.")

    def _open_firefox(self) -> None:
        cmd = browsers.firefox_command()
        if cmd is None:
            return self._say("Firefox wasn't found on this computer.")
        ok = browsers.open_in_browser(cmd, "about:debugging#/runtime/this-firefox")
        self._say("Opened Firefox's add-on debugging page." if ok else "Couldn't start Firefox.")

    def _open_chrome(self) -> None:
        cmd = browsers.chromium_command()
        if cmd is None:
            return self._say("No Chrome, Chromium or Brave was found on this computer.")
        ok = browsers.open_in_browser(cmd, "chrome://extensions")
        self._say("Opened the browser's extensions page." if ok else "Couldn't start the browser.")

    def _show_xpi(self) -> None:
        xpi = browsers.build_xpi()
        if xpi is None:
            return self._say("Couldn't create warpdown.xpi.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(xpi.parent)))
        self._say(f"Extension file: {xpi}")

    def _show_folder(self) -> None:
        d = browsers.extension_dir()
        if d is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
            self._say(f"Extension folder: {d}")

    def _register_chrome(self) -> None:
        cid = self.chrome_id.text().strip()
        if len(cid) != 32 or not cid.isalpha() or not cid.islower():
            return self._say("That doesn't look like an extension ID (32 lowercase letters). Copy it from the "
                             "browser's extensions page.")
        self._run_installer(cid)

    def _run_installer(self, chrome_id: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, out = browsers.run_installer(chrome_id)
        finally:
            QApplication.restoreOverrideCursor()
        self._say(("Done. " if ok else "Failed: ") + out)
        self._refresh_status()


class SettingsDialog(QDialog):
    font_preview = Signal(int)       # emitted while choosing, so the whole app resizes live
    theme_preview = Signal(str, str)  # (style, mode), likewise

    def __init__(self, parent, mgr: DownloadManager):
        super().__init__(parent)
        self._inbox = Inbox({"yt_updated": self._on_yt_updated}, self)
        self.setWindowTitle("Settings")
        s = QSettings()
        self.dir = QLineEdit(s.value("download_dir", str(default_download_dir())))
        self.conns = QSpinBox(); self.conns.setRange(1, 32)
        self.conns.setValue(int(s.value("connections", 8)))
        self.sort = QCheckBox("Sort downloads into category folders")
        self.sort.setChecked(str(s.value("sort_by_category", "true")).lower() == "true")
        self.sublangs = QLineEdit(str(s.value("sub_langs", "eng,en")))
        self.sublangs.setPlaceholderText("e.g. eng,en  (empty = every language)")
        self.active = QSpinBox(); self.active.setRange(1, 20)
        self.active.setValue(mgr.max_active)
        self.limit = QSpinBox(); self.limit.setRange(0, 1_000_000); self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("Unlimited")
        self.limit.setValue(int(s.value("speed_limit_kb", 0)))
        self.retry = QCheckBox("Retry failed downloads automatically (temporary network or server problems)")
        self.retry.setChecked(str(s.value("auto_retry", "true")).lower() == "true")
        self.retries = QSpinBox(); self.retries.setRange(1, 20)
        self.retries.setValue(int(s.value("max_retries", 5)))
        self.clip = QCheckBox("Watch the clipboard for download links")
        self.clip.setChecked(str(s.value("clipboard_watch", "false")).lower() == "true")
        self.fontsize = QComboBox()
        self.fontsize.addItem(f"System default ({base_font_pt():.0f} pt)", 0)
        for pt in FONT_SIZES:
            self.fontsize.addItem(f"{pt} pt", pt)
        idx = self.fontsize.findData(saved_font_size())
        self.fontsize.setCurrentIndex(idx if idx >= 0 else 0)
        self.fontsize.currentIndexChanged.connect(lambda _i: self.font_preview.emit(self.fontsize.currentData()))
        self.style_box = QComboBox()
        for key, label in themes.STYLES.items():
            self.style_box.addItem(label, key)
        self.mode_box = QComboBox()
        for key, label in themes.MODES.items():
            self.mode_box.addItem(label, key)
        style, mode = saved_theme()
        self.style_box.setCurrentIndex(self.style_box.findData(style))
        self.mode_box.setCurrentIndex(self.mode_box.findData(mode))
        for box in (self.style_box, self.mode_box):
            box.currentIndexChanged.connect(
                lambda _i: self.theme_preview.emit(self.style_box.currentData(), self.mode_box.currentData()))
        form = QFormLayout(self)
        form.addRow("Style:", self.style_box)
        form.addRow("Mode:", self.mode_box)
        form.addRow("Text size:", self.fontsize)
        form.addRow("Default folder:", self.dir)
        form.addRow("", self.sort)
        form.addRow("Connections per download:", self.conns)
        form.addRow("Simultaneous downloads:", self.active)
        form.addRow("Speed limit:", self.limit)
        form.addRow("", self.retry)
        form.addRow("Maximum retries:", self.retries)
        form.addRow("", self.clip)
        form.addRow("Subtitle languages:", self.sublangs)
        self.ytlabel = QLabel(f"yt-dlp {ytdlp.version()}" if ytdlp.available() else "yt-dlp is not installed")
        self.ytbtn = QPushButton("Update yt-dlp")
        self.ytbtn.setEnabled(ytdlp.available())
        self.ytbtn.clicked.connect(self._update_ytdlp)
        yrow = QHBoxLayout()
        yrow.addWidget(self.ytlabel, 1)
        yrow.addWidget(self.ytbtn)
        form.addRow("Video sites:", yrow)
        extbtn = QPushButton("Set up browser extension…")
        extbtn.clicked.connect(self._open_extension_dialog)
        form.addRow("Browser:", extbtn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


def _settings_update_methods():
    def _update_ytdlp(self):
        self.ytbtn.setEnabled(False)
        self.ytlabel.setText("Updating yt-dlp…")

        threading.Thread(target=_ytdlp_update_worker, args=(self._inbox.queue,), daemon=True).start()

    def _open_extension_dialog(self):
        d = ExtensionDialog(self)
        try:
            d.exec()
        finally:
            d.deleteLater()

    def _on_yt_updated(self, ok: bool, msg: str):
        self.ytbtn.setEnabled(True)
        self.ytlabel.setText(f"yt-dlp {ytdlp.version()}" + ("" if ok else f" — update failed: {msg[:60]}"))
    return _update_ytdlp, _on_yt_updated, _open_extension_dialog


SettingsDialog._update_ytdlp, SettingsDialog._on_yt_updated, SettingsDialog._open_extension_dialog = \
    _settings_update_methods()


class MainWindow(QMainWindow):
    def __init__(self, mgr: DownloadManager):
        super().__init__()
        self.mgr = mgr
        self.setWindowTitle(f"Warpdown — fast download manager {__version__}")
        self.resize(1200, 620)
        self.model = Model(mgr)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(2, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        self._base_widths = [300, 75, 140, 85, 65, 150, 125]
        self.table.doubleClicked.connect(self._open_file)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        self.cats = QListWidget()
        self.cats.addItems(CATS)
        self.cats.setCurrentRow(0)
        self.cats.setMaximumWidth(180)
        self.cats.currentTextChanged.connect(self._filter)
        self.cats.itemDoubleClicked.connect(self.open_category_folder)
        self.cats.setToolTip("Double-click an entry to open its folder")
        split = QSplitter()
        split.addWidget(self.cats)
        split.addWidget(self.table)
        split.setStretchFactor(0, 0)       # the list keeps its width when the window is resized...
        split.setStretchFactor(1, 1)       # ...and the table takes all the extra space
        self.split = split
        self.setCentralWidget(split)

        st = self.style()
        tb = QToolBar("Main"); tb.setMovable(False)
        self.toolbar = tb
        self._base_icon = tb.iconSize()
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        self._act(tb, "Add URL", QIcon.fromTheme("list-add", st.standardIcon(QStyle.SP_FileDialogNewFolder)),
                  self.add_dialog, "Ctrl+N")
        a = self._act(tb, "Batch", QIcon.fromTheme("view-list-details", st.standardIcon(QStyle.SP_FileDialogListView)),
                      lambda: self.batch_dialog(), "Ctrl+Shift+N")
        a.setToolTip("Add many downloads at once: paste a list, or use ranges like photo[01-20].jpg")
        self._act(tb, "Resume", QIcon.fromTheme("media-playback-start", st.standardIcon(QStyle.SP_MediaPlay)),
                  lambda: self._each(mgr.resume))
        self._act(tb, "Pause", QIcon.fromTheme("media-playback-pause", st.standardIcon(QStyle.SP_MediaPause)),
                  lambda: self._each(mgr.pause))
        self._act(tb, "Delete", QIcon.fromTheme("edit-delete", st.standardIcon(QStyle.SP_TrashIcon)),
                  self.delete_selected, "Del")
        tb.addSeparator()
        a = self._act(tb, "Open Folder", QIcon.fromTheme("folder-open", st.standardIcon(QStyle.SP_DirOpenIcon)),
                      self.open_folder)
        a.setToolTip("Open the folder of the selected download (or the main downloads folder)")
        a = self._act(tb, "Schedule", QIcon.fromTheme("chronometer", st.standardIcon(QStyle.SP_BrowserReload)),
                      self.scheduler_dialog, "Ctrl+Shift+S")
        a.setToolTip("Run downloads at night or at set times, and choose what happens when they are finished")
        a = self._act(tb, "Browser", QIcon.fromTheme("preferences-plugin", st.standardIcon(QStyle.SP_ComputerIcon)),
                      self.extension_dialog)
        a.setToolTip("Add the Warpdown extension to Firefox or Chrome")
        tb.addSeparator()
        self._act(tb, "Settings", QIcon.fromTheme("configure", st.standardIcon(QStyle.SP_FileDialogDetailedView)),
                  self.settings_dialog)

        for keys, delta in ((QKeySequence.ZoomIn, 1), (QKeySequence("Ctrl+="), 1), (QKeySequence.ZoomOut, -1)):
            QShortcut(keys, self, activated=lambda d=delta: self.zoom(d))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.zoom(0))
        self.refresh_icons()
        self.rescale()

        self.status = self.statusBar()
        mgr.changed.connect(self._update_status)
        mgr.added.connect(self._update_status)
        mgr.removed.connect(self._update_status)
        self._update_status()

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Warpdown")
        tm = QMenu()
        tm.addAction("Show", self.show_raise)
        tm.addAction("Add URL…", self.add_dialog)
        tm.addAction("Batch download…", lambda: self.batch_dialog())
        self.tray_clip = tm.addAction("Watch clipboard for downloads")
        self.tray_clip.setCheckable(True)
        self.tray_clip.toggled.connect(self._toggle_clipboard)
        tm.addSeparator()
        tm.addAction("Quit", QApplication.instance().quit)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(lambda r: self.show_raise() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        mgr.changed.connect(self._notify_done)
        self._notified: set[str] = set()
        self.clip = ClipboardWatcher(self)
        self.clip.urls_found.connect(self._on_clipboard_urls)
        self.apply_clipboard_setting()
        mgr.queue_finished.connect(lambda: QTimer.singleShot(0, self._on_queue_finished))
        mgr.schedule_changed.connect(self._update_status)

    # ---- themes & icons ----
    _TOOLBAR_KEYS = {"Add URL": "add", "Batch": "batch", "Schedule": "schedule", "Resume": "resume", "Pause": "pause", "Delete": "delete",
                     "Open Folder": "folder", "Browser": "browser", "Settings": "settings"}
    _CATEGORY_KEYS = {"All Downloads": "all", "Unfinished": "unfinished", "Finished": "finished", "Video": "video",
                      "Music": "music", "Compressed": "compressed", "Documents": "documents",
                      "Programs": "programs", "Images": "images", "Other": "other"}

    def refresh_icons(self) -> None:
        """Toolbar + category icons for the current style (the System default keeps the desktop's own icons)."""
        if not hasattr(self, "_default_icons"):
            self._default_icons = {a.text(): a.icon() for a in self.toolbar.actions() if a.text()}
        style = themes.current_style()
        color = QApplication.palette().color(QPalette.WindowText)
        for a in self.toolbar.actions():
            key = self._TOOLBAR_KEYS.get(a.text())
            if key:
                a.setIcon(themes.icon(key, style, color) or self._default_icons[a.text()])
        for i in range(self.cats.count()):
            it = self.cats.item(i)
            it.setIcon(themes.icon(self._CATEGORY_KEYS.get(it.text(), "other"), style, color) or QIcon())

    def set_theme(self, style: str, mode: str) -> None:
        themes.apply_theme(style, mode)
        self.refresh_icons()
        self.rescale()

    # ---- folders & browser extension ----
    def download_root(self) -> Path:
        return Path(str(QSettings().value("download_dir", str(default_download_dir()))))

    def open_folder(self) -> None:
        """Toolbar: the selected download's folder, else the main downloads folder."""
        path = None
        ids = self._selected_ids()
        if ids and ids[0] in self.mgr.items:
            path = Path(self.mgr.items[ids[0]].directory)
        if path is None or not path.is_dir():
            path = self.download_root()
            path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_category_folder(self, item) -> None:
        """Double-click on the left-hand list: open that category's folder (Unfinished/Finished aren't folders)."""
        name = item.text()
        folder = category_folder(self.download_root(), name)
        if folder is None:
            self.status.showMessage(f"“{name}” is a filter, not a folder", 3000)
            return
        if not folder.is_dir():
            if name != "All Downloads":
                self.status.showMessage(f"No {name} folder yet — it is created with the first download", 4000)
                return
            folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def links_dialog(self, raw_links: list, headers: dict | None = None, page: str = "", title: str = "") -> None:
        """Browser extension: "Download all links on this page"."""
        links = linkgrab.build_links(raw_links)
        if not links:
            QMessageBox.information(self, "Download all links", "No downloadable links were found.")
            return
        existing = {it.url.split("#", 1)[0] for it in self.mgr.items.values()}
        d = LinksDialog(self, links, title, existing)
        try:
            if d.exec() == QDialog.Accepted:
                chosen = d.selected_links()
                hdrs = dict(headers or {})
                if page and not hdrs.get("referer"):
                    hdrs["referer"] = page
                QSettings().setValue("download_dir", d.dir.text())
                self.mgr.add_many([l.url for l in chosen], d.dir.text(), hdrs, None, d.start_now.isChecked(),
                                  sort=d.sort.isChecked())
                self.status.showMessage(f"Added {len(chosen)} download{'s' if len(chosen) != 1 else ''}", 6000)
        finally:
            d.deleteLater()

    def batch_dialog(self, prefill: str = "") -> None:
        """Toolbar / tray / clipboard: add many downloads from a pasted list (with ranges)."""
        d = BatchDialog(self, prefill)
        try:
            if d.exec() == QDialog.Accepted:
                urls = d.urls()
                if urls:
                    QSettings().setValue("download_dir", d.dir.text())
                    self.mgr.add_many(urls, d.dir.text(), None, None, d.start_now.isChecked(), sort=d.sort.isChecked())
                    self.status.showMessage(f"Added {len(urls)} download{'s' if len(urls) != 1 else ''}", 6000)
        finally:
            d.deleteLater()

    def scheduler_dialog(self) -> None:
        s = QSettings()
        keys = ("sched_enabled", "sched_start", "sched_stop", "sched_days")
        current = Schedule.from_settings({k: s.value(k) for k in keys if s.contains(k)})
        d = SchedulerDialog(self, current, str(s.value("after_action", AFTER_NOTHING)))
        try:
            if d.exec() == QDialog.Accepted:
                new = d.schedule()
                for k, v in new.to_settings().items():
                    s.setValue(k, v)
                s.setValue("after_action", d.after_action())
                self.mgr.set_schedule(new)
                self._update_status()
        finally:
            d.deleteLater()

    def _on_queue_finished(self) -> None:
        """Everything is downloaded: maybe quit / sleep / shut down, after a 60-second chance to cancel."""
        action = str(QSettings().value("after_action", AFTER_NOTHING))
        if action not in (AFTER_QUIT, AFTER_SLEEP, AFTER_POWEROFF):
            return
        self.tray.showMessage("Downloads finished", f"{CountdownDialog.WHAT[action]} in 60 seconds.",
                              QSystemTrayIcon.Information, 5000)
        d = CountdownDialog(self, action)
        try:
            go = d.exec() == QDialog.Accepted
        finally:
            d.deleteLater()
        if go:
            self.run_after_action(action)

    def run_after_action(self, action: str) -> None:
        self.mgr.save()
        if action == AFTER_QUIT:
            self.mgr.shutdown()
            QApplication.instance().quit()
            return
        cmd = power_command(action)
        if cmd:
            run_power_command(cmd)

    def apply_clipboard_setting(self) -> None:
        on = str(QSettings().value("clipboard_watch", "false")).lower() == "true"
        self.clip.set_enabled(on)
        self.tray_clip.blockSignals(True)
        self.tray_clip.setChecked(on)
        self.tray_clip.blockSignals(False)

    def _toggle_clipboard(self, on: bool) -> None:
        QSettings().setValue("clipboard_watch", on)
        self.apply_clipboard_setting()

    def _on_clipboard_urls(self, urls: list) -> None:
        """A copied address looks like a download: offer it (one address: Add dialog; several: Batch dialog)."""
        known = {i.url.split("#", 1)[0] for i in self.mgr.items.values()}
        urls = [u for u in urls if u.split("#", 1)[0] not in known]
        if not urls:
            return

        def show():
            self.show_raise()
            if len(urls) == 1:
                self.add_dialog(urls[0])
            else:
                self.batch_dialog("\n".join(urls))
        QTimer.singleShot(0, show)

    def extension_dialog(self) -> None:
        d = ExtensionDialog(self)
        try:
            d.exec()
        finally:
            d.deleteLater()

    def rescale(self) -> None:
        """Make columns, row height and toolbar icons follow the current text size."""
        k = font_scale()
        for i, w in enumerate(self._base_widths):
            self.table.setColumnWidth(i, int(w * k))
        # use the application font, not the table's own: widgets pick up a new app font a moment later
        self.table.verticalHeader().setDefaultSectionSize(QFontMetrics(QApplication.font()).height() + 10)
        self.toolbar.setIconSize(QSize(int(self._base_icon.width() * k), int(self._base_icon.height() * k)))
        cats_w = int(180 * min(k, 1.4))
        self.cats.setMaximumWidth(cats_w)
        self.cats.setIconSize(QSize(int(18 * k), int(18 * k)))
        # Give the list exactly its (maximum) width. Without this the splitter starts with a default share that is
        # wider than the list may grow, and the difference shows up as an empty bar next to the divider.
        total = max(self.split.width(), self.width(), 1)
        self.split.setSizes([cats_w, max(total - cats_w - self.split.handleWidth(), 1)])
        # grow the window (never shrink it) so the Name column keeps a usable width, up to the screen width
        needed = sum(int(w * k) for w in self._base_widths[1:]) + cats_w + int(260 * k) + 40
        scr = self.screen()
        if scr is not None:
            needed = min(needed, scr.availableGeometry().width())
        if self.width() < needed:
            self.resize(needed, self.height())

    def set_font_size(self, size_pt: int) -> None:
        apply_font(size_pt)
        self.rescale()

    def zoom(self, delta: int) -> None:
        """Ctrl+ / Ctrl- / Ctrl+0: step through the text sizes (0 resets to the system default)."""
        if delta == 0:
            new = 0
        else:
            sizes = sorted(set(FONT_SIZES + [round(base_font_pt())]))
            cur = round(QApplication.font().pointSizeF())
            i = min(range(len(sizes)), key=lambda j: abs(sizes[j] - cur))
            new = sizes[max(0, min(len(sizes) - 1, i + delta))]
            if new == round(base_font_pt()):
                new = 0
        QSettings().setValue("font_size", new)
        self.set_font_size(new)

    def _act(self, tb, text, icon, slot, shortcut=None):
        a = QAction(icon, text, self)
        a.triggered.connect(lambda *_: slot())
        if shortcut:
            a.setShortcut(shortcut)
        tb.addAction(a)
        return a

    def show_raise(self):
        self.show(); self.raise_(); self.activateWindow()

    def closeEvent(self, e):
        if self.tray.isVisible():
            self.hide()
            e.ignore()
        else:
            e.accept()

    def _filter(self, name):
        self.model.filter = name
        self.model.rebuild()

    def _selected_ids(self):
        rows = {i.row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.rows[r] for r in sorted(rows)]

    def _each(self, fn):
        for iid in self._selected_ids():
            fn(iid)

    def add_dialog(self, url: str = "", headers: dict | None = None, filename: str = "", subs: list | None = None,
                   mode: str = ""):
        if not url:
            clip = QGuiApplication.clipboard().text().strip()
            url = clip if clip.startswith(("http://", "https://")) and "\n" not in clip else ""
        d = AddDialog(self, url, headers or {}, use_ytdlp=(mode == "ytdlp"))
        try:
            d.set_subs(subs or [], headers or {})
            if filename:
                d.name.setText(filename)
            if d.exec() == QDialog.Accepted and d.url.text().strip():
                s = QSettings()
                s.setValue("download_dir", d.dir.text())
                s.setValue("connections", d.conns.value())
                if d.use_ytdlp.isChecked():
                    self.mgr.add(d.url.text().strip(), d.dir.text(), d.name.text().strip(), headers, d.conns.value(),
                                 d.start_now.isChecked(), sort=d.sort.isChecked(), auto_subs=False, kind="ytdlp",
                                 opts=d.ytdlp_options())
                    return
                self.mgr.add(d.url.text().strip(), d.dir.text(), d.name.text().strip(), headers,
                             d.conns.value(), d.start_now.isChecked(), sort=d.sort.isChecked(), subs=d.selected_subs(),
                             auto_subs=False)
        finally:
            d.deleteLater()      # gone for good once the dialog is closed (nothing may keep it alive)

    def delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        box = QMessageBox(QMessageBox.Question, "Delete", f"Remove {len(ids)} download(s)?", parent=self)
        keep = box.addButton("Remove from list", QMessageBox.AcceptRole)
        wipe = box.addButton("Also delete files", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() in (keep, wipe):
            for iid in ids:
                self.mgr.remove(iid, delete_files=box.clickedButton() is wipe)

    def settings_dialog(self):
        original_font = saved_font_size()
        original_theme = saved_theme()
        d = SettingsDialog(self, self.mgr)
        d.font_preview.connect(self.set_font_size)
        d.theme_preview.connect(self.set_theme)
        if d.exec() == QDialog.Accepted:
            s = QSettings()
            s.setValue("download_dir", d.dir.text())
            s.setValue("connections", d.conns.value())
            s.setValue("speed_limit_kb", d.limit.value())
            s.setValue("max_active", d.active.value())
            s.setValue("sort_by_category", d.sort.isChecked())
            s.setValue("sub_langs", d.sublangs.text().strip())
            s.setValue("auto_retry", d.retry.isChecked())
            s.setValue("max_retries", d.retries.value())
            s.setValue("clipboard_watch", d.clip.isChecked())
            s.setValue("font_size", int(d.fontsize.currentData()))
            s.setValue("ui_style", d.style_box.currentData())
            s.setValue("ui_mode", d.mode_box.currentData())
            self.set_theme(d.style_box.currentData(), d.mode_box.currentData())
            self.set_font_size(int(d.fontsize.currentData()))
            apply_settings(self.mgr)
            self.apply_clipboard_setting()
        else:
            self.set_font_size(original_font)          # cancelled: undo the live previews
            self.set_theme(*original_theme)
        d.deleteLater()

    def _open_file(self, idx):
        it = self.model.item(idx.row())
        if it.status == COMPLETED:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(it.path)))

    def _menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        it = self.model.item(idx.row())
        m = QMenu(self)
        if it.status == COMPLETED:
            m.addAction("Open", lambda: self._open_file(idx))
            m.addAction("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(it.directory)))
        m.addAction("Resume", lambda: self._each(self.mgr.resume))
        m.addAction("Pause", lambda: self._each(self.mgr.pause))
        m.addAction("Copy URL", lambda: (self.clip.remember(it.url), QGuiApplication.clipboard().setText(it.url)))
        if it.error:
            m.addAction("Copy error details", lambda: QGuiApplication.clipboard().setText(f"{it.error}\n{it.url}"))
        m.addSeparator()
        m.addAction("Delete…", self.delete_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _update_status(self, *_):
        its = self.mgr.items.values()
        active = [i for i in its if i.status == DOWNLOADING]
        total = sum(i.speed for i in active)
        sched = ""
        if self.mgr.schedule.enabled:
            sched = " · scheduler: " + ("running" if self.mgr.gate_open else "waiting")
        self.status.showMessage(f"{len(active)} active · {fmt_speed(total) or '0 B/s'} · {len(its)} total{sched}")
        if hasattr(self, "tray"):          # hovering the tray icon: name plus what is going on
            self.tray.setToolTip("Warpdown" + (f"\n{len(active)} downloading · {fmt_speed(total)}" if active else ""))

    def _notify_done(self, iid):
        it = self.mgr.items.get(iid)
        if it and it.status == COMPLETED and iid not in self._notified:
            self._notified.add(iid)
            self.tray.showMessage("Download complete", it.filename, QSystemTrayIcon.Information, 4000)


def apply_settings(mgr: DownloadManager) -> None:
    s = QSettings()
    mgr.default_connections = int(s.value("connections", 8))
    mgr.max_active = int(s.value("max_active", 3))
    mgr.sort_by_category = str(s.value("sort_by_category", "true")).lower() == "true"
    mgr.sub_langs = parse_langs(str(s.value("sub_langs", "eng,en")))
    if mgr.sort_by_category:
        try:
            ensure_category_dirs(Path(s.value("download_dir", str(default_download_dir()))))
        except OSError:
            pass
    mgr.set_speed_limit(int(s.value("speed_limit_kb", 0)) * 1024)
    mgr.auto_retry = str(s.value("auto_retry", "true")).lower() == "true"
    mgr.max_retries = int(s.value("max_retries", 5))
    sched = Schedule.from_settings({k: s.value(k) for k in ("sched_enabled", "sched_start", "sched_stop", "sched_days")
                                    if s.contains(k)})
    if sched != mgr.schedule:                       # only when it really changed: re-applying would reset a trigger
        mgr.set_schedule(sched)

"""Qt main window."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QVBoxLayout, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle, QStyledItemDelegate,
    QStyleOptionProgressBar, QSplitter, QSystemTrayIcon, QTableView, QToolBar, QWidget,
)

from . import __version__
from . import ytdlp
from .engine import probe
from .subtitles import default_checked, matches_langs, parse_langs, peek_subtitle, tag_for
from .manager import COMPLETED, DOWNLOADING, ERROR, PAUSED, DownloadManager, Item
from .util import default_download_dir, ensure_category_dirs, fmt_eta, fmt_size, fmt_speed, sanitize_filename

_PEEK_SLOTS = threading.Semaphore(3)

COLUMNS = ["Name", "Size", "Progress", "Speed", "ETA", "Status", "Added"]
CATS = ["All Downloads", "Unfinished", "Finished", "Video", "Music", "Compressed",
        "Documents", "Programs", "Other"]


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
                if it.error:
                    return f"{it.status}: {it.error}"
                n = len(it.sub_files)
                return f"{it.status} · {n} subtitle{'s' if n != 1 else ''}" if n else it.status
            if c == 6:
                from datetime import datetime
                return datetime.fromtimestamp(it.added).strftime("%Y-%m-%d %H:%M")
        if role == Qt.UserRole and c == 2:
            return int(it.progress * 1000)
        if role == Qt.ToolTipRole:
            return it.url
        return None


class ProgressDelegate(QStyledItemDelegate):
    """Hand-drawn progress bar: always fills left -> right, independent of the Qt style."""

    def paint(self, painter, option, index):
        val = index.data(Qt.UserRole)
        if val is None:
            return super().paint(painter, option, index)
        pal = option.palette
        rect = QRectF(option.rect.adjusted(4, 5, -4, -5))
        fill = QRectF(rect.left(), rect.top(), rect.width() * max(0, min(val, 1000)) / 1000.0, rect.height())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        groove = QColor(pal.color(QPalette.Mid)); groove.setAlpha(110)
        painter.setBrush(groove)
        painter.drawRoundedRect(rect, 3, 3)
        if fill.width() > 0:
            painter.setClipRect(fill)
            painter.setBrush(pal.color(QPalette.Highlight))
            painter.drawRoundedRect(rect, 3, 3)
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
    probed = Signal(object, str)
    sub_info = Signal(int, object)
    yt_ready = Signal(object, str)

    def __init__(self, parent, url: str = "", headers: dict | None = None, use_ytdlp: bool = False):
        super().__init__(parent)
        self._hdrs = {{"referer": "Referer", "cookie": "Cookie", "user_agent": "User-Agent"}[k]: v
                      for k, v in (headers or {}).items() if k in ("referer", "cookie", "user_agent") and v}
        self._yt_user_set = use_ytdlp            # don't auto-toggle if the browser already told us the mode
        self.setWindowTitle("Add Download")
        self.setMinimumWidth(520)
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
        self.probed.connect(self._on_probed)
        self.sub_info.connect(self._on_sub_info)
        self.yt_ready.connect(self._on_yt_ready)
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

        def work():
            try:
                self.yt_ready.emit(ytdlp.fetch_info(u, self._hdrs), "")
            except Exception as e:  # noqa: BLE001
                self.yt_ready.emit(None, str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True).start()

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

            def work(i=i, url=t["url"]):
                with _PEEK_SLOTS:                      # a few at a time: servers may throttle bursts
                    for attempt in (1, 2):
                        try:
                            self.sub_info.emit(i, peek_subtitle(url, self._sub_headers))
                            return
                        except Exception as e:  # noqa: BLE001
                            if attempt == 2:
                                self.sub_info.emit(i, {"error": str(e) or e.__class__.__name__})
                            else:
                                time.sleep(1)
            threading.Thread(target=work, daemon=True).start()
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

        def work():
            try:
                self.probed.emit(probe(u), "")
            except Exception as e:  # noqa: BLE001
                self.probed.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

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


class SettingsDialog(QDialog):
    yt_updated = Signal(bool, str)

    def __init__(self, parent, mgr: DownloadManager):
        super().__init__(parent)
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
        form = QFormLayout(self)
        form.addRow("Default folder:", self.dir)
        form.addRow("", self.sort)
        form.addRow("Connections per download:", self.conns)
        form.addRow("Simultaneous downloads:", self.active)
        form.addRow("Speed limit:", self.limit)
        form.addRow("Subtitle languages:", self.sublangs)
        self.ytlabel = QLabel(f"yt-dlp {ytdlp.version()}" if ytdlp.available() else "yt-dlp is not installed")
        self.ytbtn = QPushButton("Update yt-dlp")
        self.ytbtn.setEnabled(ytdlp.available())
        self.ytbtn.clicked.connect(self._update_ytdlp)
        self.yt_updated.connect(self._on_yt_updated)
        yrow = QHBoxLayout()
        yrow.addWidget(self.ytlabel, 1)
        yrow.addWidget(self.ytbtn)
        form.addRow("Video sites:", yrow)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


def _settings_update_methods():
    def _update_ytdlp(self):
        self.ytbtn.setEnabled(False)
        self.ytlabel.setText("Updating yt-dlp…")

        def work():
            try:
                self.yt_updated.emit(*ytdlp.update())
            except Exception as e:  # noqa: BLE001
                self.yt_updated.emit(False, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_yt_updated(self, ok: bool, msg: str):
        self.ytbtn.setEnabled(True)
        self.ytlabel.setText(f"yt-dlp {ytdlp.version()}" + ("" if ok else f" — update failed: {msg[:60]}"))
    return _update_ytdlp, _on_yt_updated


SettingsDialog._update_ytdlp, SettingsDialog._on_yt_updated = _settings_update_methods()


class MainWindow(QMainWindow):
    def __init__(self, mgr: DownloadManager):
        super().__init__()
        self.mgr = mgr
        self.setWindowTitle(f"KUDM — KUbuntu Download Manager {__version__}")
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
        for i, w in enumerate([300, 75, 140, 85, 65, 150, 125]):
            self.table.setColumnWidth(i, w)
        self.table.doubleClicked.connect(self._open_file)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)

        self.cats = QListWidget()
        self.cats.addItems(CATS)
        self.cats.setCurrentRow(0)
        self.cats.setMaximumWidth(180)
        self.cats.currentTextChanged.connect(self._filter)
        split = QSplitter()
        split.addWidget(self.cats)
        split.addWidget(self.table)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        st = self.style()
        tb = QToolBar("Main"); tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        self._act(tb, "Add URL", QIcon.fromTheme("list-add", st.standardIcon(QStyle.SP_FileDialogNewFolder)),
                  self.add_dialog, "Ctrl+N")
        self._act(tb, "Resume", QIcon.fromTheme("media-playback-start", st.standardIcon(QStyle.SP_MediaPlay)),
                  lambda: self._each(mgr.resume))
        self._act(tb, "Pause", QIcon.fromTheme("media-playback-pause", st.standardIcon(QStyle.SP_MediaPause)),
                  lambda: self._each(mgr.pause))
        self._act(tb, "Delete", QIcon.fromTheme("edit-delete", st.standardIcon(QStyle.SP_TrashIcon)),
                  self.delete_selected, "Del")
        tb.addSeparator()
        self._act(tb, "Settings", QIcon.fromTheme("configure", st.standardIcon(QStyle.SP_FileDialogDetailedView)),
                  self.settings_dialog)

        self.status = self.statusBar()
        mgr.changed.connect(self._update_status)
        mgr.added.connect(self._update_status)
        mgr.removed.connect(self._update_status)
        self._update_status()

        self.tray = QSystemTrayIcon(QIcon.fromTheme("folder-download", st.standardIcon(QStyle.SP_ArrowDown)), self)
        tm = QMenu()
        tm.addAction("Show", self.show_raise)
        tm.addAction("Add URL…", self.add_dialog)
        tm.addSeparator()
        tm.addAction("Quit", QApplication.instance().quit)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(lambda r: self.show_raise() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        mgr.changed.connect(self._notify_done)
        self._notified: set[str] = set()

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
        d = SettingsDialog(self, self.mgr)
        if d.exec() == QDialog.Accepted:
            s = QSettings()
            s.setValue("download_dir", d.dir.text())
            s.setValue("connections", d.conns.value())
            s.setValue("speed_limit_kb", d.limit.value())
            s.setValue("max_active", d.active.value())
            s.setValue("sort_by_category", d.sort.isChecked())
            s.setValue("sub_langs", d.sublangs.text().strip())
            apply_settings(self.mgr)

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
        m.addAction("Copy URL", lambda: QGuiApplication.clipboard().setText(it.url))
        m.addSeparator()
        m.addAction("Delete…", self.delete_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _update_status(self, *_):
        its = self.mgr.items.values()
        active = [i for i in its if i.status == DOWNLOADING]
        total = sum(i.speed for i in active)
        self.status.showMessage(f"{len(active)} active · {fmt_speed(total) or '0 B/s'} · {len(its)} total")

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

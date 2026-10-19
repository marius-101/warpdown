"""Qt main window."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle, QStyledItemDelegate,
    QStyleOptionProgressBar, QSplitter, QSystemTrayIcon, QTableView, QToolBar, QWidget,
)

from . import __version__
from .engine import probe
from .manager import COMPLETED, DOWNLOADING, ERROR, PAUSED, DownloadManager, Item
from .util import default_download_dir, ensure_category_dirs, fmt_eta, fmt_size, fmt_speed

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
                return it.status if not it.error else f"{it.status}: {it.error}"
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

    def __init__(self, parent, url: str = ""):
        super().__init__(parent)
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
        form = QFormLayout(self)
        form.addRow("URL:", self.url)
        form.addRow("", self.info)
        form.addRow("Save as:", self.name)
        form.addRow("Save to:", row)
        form.addRow("Connections:", self.conns)
        form.addRow("", self.sort)
        form.addRow("", self.start_now)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.probed.connect(self._on_probed)
        self.url.editingFinished.connect(self._probe)
        if url:
            self._probe()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save to", self.dir.text())
        if d:
            self.dir.setText(d)

    def _probe(self):
        u = self.url.text().strip()
        if not u.startswith(("http://", "https://")):
            return
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
        size = fmt_size(res.size) if res.size > 0 else "unknown size"
        self.info.setText(f"{res.filename} — {size} — {'resumable' if res.resumable else 'not resumable'}")
        if not self.name.text():
            self.name.setText(res.filename)


class SettingsDialog(QDialog):
    def __init__(self, parent, mgr: DownloadManager):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        s = QSettings()
        self.dir = QLineEdit(s.value("download_dir", str(default_download_dir())))
        self.conns = QSpinBox(); self.conns.setRange(1, 32)
        self.conns.setValue(int(s.value("connections", 8)))
        self.sort = QCheckBox("Sort downloads into category folders")
        self.sort.setChecked(str(s.value("sort_by_category", "true")).lower() == "true")
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
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


class MainWindow(QMainWindow):
    def __init__(self, mgr: DownloadManager):
        super().__init__()
        self.mgr = mgr
        self.setWindowTitle(f"KUDM — KUbuntu Download Manager {__version__}")
        self.resize(1100, 600)
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
        for i, w in enumerate([340, 80, 160, 90, 70, 180, 130]):
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

    def add_dialog(self, url: str = "", headers: dict | None = None, filename: str = ""):
        if not url:
            clip = QGuiApplication.clipboard().text().strip()
            url = clip if clip.startswith(("http://", "https://")) and "\n" not in clip else ""
        d = AddDialog(self, url)
        if filename:
            d.name.setText(filename)
        if d.exec() == QDialog.Accepted and d.url.text().strip():
            s = QSettings()
            s.setValue("download_dir", d.dir.text())
            s.setValue("connections", d.conns.value())
            self.mgr.add(d.url.text().strip(), d.dir.text(), d.name.text().strip(), headers,
                         d.conns.value(), d.start_now.isChecked(), sort=d.sort.isChecked())

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
    if mgr.sort_by_category:
        try:
            ensure_category_dirs(Path(s.value("download_dir", str(default_download_dir()))))
        except OSError:
            pass
    mgr.set_speed_limit(int(s.value("speed_limit_kb", 0)) * 1024)

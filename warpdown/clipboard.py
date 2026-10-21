"""Clipboard watcher: tells the window when a copied web address looks like something to download.

Under Wayland, Qt only hears about clipboard changes while its own window has focus. KDE's clipboard manager (Klipper)
can be asked over D-Bus instead, which works while Warpdown sits in the tray, so that is polled as well. Both paths run
in the GUI thread (no worker threads, no Qt objects in threads).
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from . import clipwatch

REPEAT_QUIET_SECONDS = 60          # the same address is not offered again within this time


class ClipboardWatcher(QObject):
    urls_found = Signal(list)      # addresses that look like downloads, in the order they were copied

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._enabled = False
        self._last = ""
        self._handled: dict[str, float] = {}
        self._iface = None
        self._polls_since_lookup = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        QGuiApplication.clipboard().dataChanged.connect(self._on_qt_changed)

    # ---- control ----
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            self._last = self._current_text()          # don't react to what is already on the clipboard
            self._timer.start()
        else:
            self._timer.stop()

    def remember(self, text: str) -> None:
        """Text that Warpdown itself puts on the clipboard (e.g. "Copy URL") must not trigger a download offer."""
        self._last = text

    # ---- reading ----
    def _current_text(self) -> str:
        text = self._read_klipper()
        return text if text is not None else QGuiApplication.clipboard().text()

    def _read_klipper(self) -> str | None:
        """The clipboard according to KDE's Klipper, or None if it isn't available."""
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface
        except ImportError:
            return None
        if self._iface is None:
            self._polls_since_lookup += 1
            if self._polls_since_lookup > 1 and self._polls_since_lookup % 30:     # look again every ~30 polls
                return None
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected() or not bus.interface().isServiceRegistered("org.kde.klipper").value():
                return None
            iface = QDBusInterface("org.kde.klipper", "/klipper", "org.kde.klipper.klipper", bus)
            if not iface.isValid():
                return None
            iface.setTimeout(500)                       # never let a stuck clipboard manager freeze the window
            self._iface = iface
        reply = self._iface.call("getClipboardContents")
        args = reply.arguments()
        return args[0] if args and isinstance(args[0], str) else None

    # ---- reacting ----
    def _on_qt_changed(self) -> None:
        if self._enabled:
            self._handle(QGuiApplication.clipboard().text())

    def _poll(self) -> None:
        text = self._read_klipper()
        if text is not None:
            self._handle(text)

    def _handle(self, text: str) -> None:
        if not text or text == self._last:
            return
        self._last = text
        now = time.monotonic()
        fresh = [u for u in clipwatch.pick(text) if now - self._handled.get(u, -1e9) > REPEAT_QUIET_SECONDS]
        if fresh:
            for u in fresh:
                self._handled[u] = now
            self.urls_found.emit(fresh)

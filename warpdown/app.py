"""Entry point: `python -m warpdown [URL ...]`."""

from __future__ import annotations

import faulthandler
import os
import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from . import ipc
from . import themes
from .inbox import Inbox
from .gui import MainWindow, apply_font, apply_settings, saved_font_size, saved_theme
from .manager import DownloadManager
from .util import data_dir, default_download_dir, migrate_legacy_data


_CRASH_LOG = None     # kept open for the life of the process


def dispatch(win: MainWindow, msg: dict) -> None:
    """What to do with a message from the browser extension (or a second Warpdown start)."""
    action = msg.get("action")
    win.show_raise()
    if action == "add" and msg.get("url"):
        win.add_dialog(msg["url"], msg.get("headers"), msg.get("filename", ""), msg.get("subs"), msg.get("mode", ""))
    elif action == "links" and msg.get("links"):
        win.links_dialog(msg["links"], msg.get("headers"), msg.get("page", ""), msg.get("title", ""))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    urls = [a for a in argv[1:] if a.startswith(("http://", "https://", "ftp://"))]

    # Already running? Hand the URLs over (or just raise the window) and exit. No Qt needed for this.
    if ipc.send({"action": "add", "url": urls[0]} if urls else {"action": "show"}):
        for u in urls[1:]:
            ipc.send({"action": "add", "url": u})
        return 0

    migrate_legacy_data()      # KUDM -> Warpdown (0.5): carry over the download list and settings
    global _CRASH_LOG
    try:                       # on a crash, Python writes the stack of every thread here (helps find rare bugs)
        _CRASH_LOG = open(data_dir() / "crash.log", "a", buffering=1)
        faulthandler.enable(file=_CRASH_LOG, all_threads=True)
    except OSError:
        pass
    app = QApplication(argv)
    app.setApplicationName("warpdown")
    app.setOrganizationName("warpdown")
    app.setQuitOnLastWindowClosed(False)
    apply_font(saved_font_size())          # text size chosen in Settings (0 = system default)
    themes.apply_theme(*saved_theme())    # style + mode chosen in Settings

    mgr = DownloadManager()
    apply_settings(mgr)
    win = MainWindow(mgr)
    inbox = Inbox({"ipc": lambda msg: handle(msg)})      # messages from the browser helper arrive here, in the GUI thread
    server = ipc.IpcServer(inbox.queue)
    server.listen()

    def handle(msg: dict) -> None:
        # A dialog runs a nested event loop: start it from a fresh event, not from inside the inbox's timer callback.
        QTimer.singleShot(0, lambda: handle_now(msg))

    def handle_now(msg: dict) -> None:
        dispatch(win, msg)

    app.aboutToQuit.connect(mgr.shutdown)
    app.aboutToQuit.connect(server.close)
    if os.environ.get("WARPDOWN_QUIT_AFTER_MS"):           # test hook: lets a test start the real app and watch it exit
        QTimer.singleShot(int(os.environ["WARPDOWN_QUIT_AFTER_MS"]), app.quit)
    win.show()
    for u in urls:
        win.add_dialog(u)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

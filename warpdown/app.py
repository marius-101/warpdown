"""Entry point: `python -m warpdown [URL ...]`."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from . import ipc
from .gui import MainWindow, apply_font, apply_settings, saved_font_size
from .manager import DownloadManager
from .util import default_download_dir, migrate_legacy_data


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    urls = [a for a in argv[1:] if a.startswith(("http://", "https://", "ftp://"))]

    migrate_legacy_data()      # KUDM -> Warpdown (0.5): carry over the download list and settings
    app = QApplication(argv)
    app.setApplicationName("warpdown")
    app.setOrganizationName("warpdown")
    app.setQuitOnLastWindowClosed(False)
    apply_font(saved_font_size())          # text size chosen in Settings (0 = system default)

    # Already running? Hand the URLs over (or just raise the window) and exit.
    if ipc.send({"action": "add", "url": urls[0]} if urls else {"action": "show"}):
        for u in urls[1:]:
            ipc.send({"action": "add", "url": u})
        return 0

    mgr = DownloadManager()
    apply_settings(mgr)
    win = MainWindow(mgr)
    server = ipc.IpcServer()
    server.listen()

    def handle(msg: dict) -> None:
        # Never run a modal dialog (nested event loop) inside the socket's readyRead callback:
        # the peer disconnects meanwhile and the socket would be freed under Qt's feet.
        QTimer.singleShot(0, lambda: handle_now(msg))

    def handle_now(msg: dict) -> None:
        if msg.get("action") == "add" and msg.get("url"):
            win.show_raise()
            win.add_dialog(msg["url"], msg.get("headers"), msg.get("filename", ""), msg.get("subs"), msg.get("mode", ""))
        else:
            win.show_raise()

    server.message.connect(handle)
    app.aboutToQuit.connect(mgr.shutdown)
    app.aboutToQuit.connect(server.close)
    win.show()
    for u in urls:
        win.add_dialog(u)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

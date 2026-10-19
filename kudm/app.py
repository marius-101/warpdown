"""Entry point: `python -m kudm [URL ...]`."""

from __future__ import annotations

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from . import ipc
from .gui import MainWindow, apply_settings
from .manager import DownloadManager
from .util import default_download_dir


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    urls = [a for a in argv[1:] if a.startswith(("http://", "https://", "ftp://"))]

    app = QApplication(argv)
    app.setApplicationName("kudm")
    app.setOrganizationName("kudm")
    app.setQuitOnLastWindowClosed(False)

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
        if msg.get("action") == "add" and msg.get("url"):
            win.show_raise()
            win.add_dialog(msg["url"], msg.get("headers"), msg.get("filename", ""))
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

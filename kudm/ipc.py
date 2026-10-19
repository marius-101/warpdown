"""Single-instance IPC: the browser native host / CLI talk to the running GUI."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def socket_name() -> str:
    return f"kudm-{os.getuid()}"


def send(msg: dict, timeout_ms: int = 1500) -> bool:
    """Send a message to a running instance. Returns False if none is running."""
    sock = QLocalSocket()
    sock.connectToServer(socket_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(msg).encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class IpcServer(QObject):
    message = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_conn)

    def listen(self) -> bool:
        QLocalServer.removeServer(socket_name())
        return self.server.listen(socket_name())

    def _on_conn(self) -> None:
        sock = self.server.nextPendingConnection()
        buf = bytearray()

        def ready():
            buf.extend(sock.readAll().data())
            while b"\n" in buf:
                line, _, rest = bytes(buf).partition(b"\n")
                buf[:] = rest
                try:
                    self.message.emit(json.loads(line))
                except ValueError:
                    pass

        sock.readyRead.connect(ready)
        sock.disconnected.connect(lambda: (ready(), sock.deleteLater()))

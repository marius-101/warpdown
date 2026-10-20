"""Single-instance IPC: the browser native host / CLI talk to the running GUI."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def socket_name() -> str:
    return f"warpdown-{os.getuid()}"


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
        self._socks: set[QLocalSocket] = set()
        self.server.newConnection.connect(self._on_conn)

    def close(self) -> None:
        self.server.close()
        for sock in list(self._socks):
            sock.blockSignals(True)
            sock.abort()
            sock.deleteLater()
        self._socks.clear()

    def listen(self) -> bool:
        QLocalServer.removeServer(socket_name())
        return self.server.listen(socket_name())

    def _on_conn(self) -> None:
        while self.server.hasPendingConnections():
            self._serve(self.server.nextPendingConnection())

    def _serve(self, sock: QLocalSocket) -> None:
        sock.setParent(self)           # keep the C++ object alive and owned by Qt
        self._socks.add(sock)          # ...and the Python wrapper too
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

        def closed():
            ready()
            self._socks.discard(sock)
            sock.deleteLater()

        sock.readyRead.connect(ready)
        sock.disconnected.connect(closed)

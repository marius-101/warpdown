"""Single-instance IPC: the browser's native host (or a second start of Warpdown) talks to the running app.

Plain Python sockets, no Qt, on purpose. The earlier version used a Qt socket object per connection, and those could be
freed twice (random crashes right after a hand-over from the browser). Here a background thread accepts connections and
puts every message into a thread-safe queue that the GUI reads (see inbox.py); the thread never touches a Qt object.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time

MAX_MESSAGE = 64 * 1024 * 1024      # a page with tens of thousands of links is a few MB; refuse anything absurd


def socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, f"warpdown-{os.getuid()}.sock")


def send(msg: dict, timeout_ms: int = 1500) -> bool:
    """Send a message to a running instance. Returns False if none is running or it could not be delivered."""
    data = json.dumps(msg).encode() + b"\n"
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(timeout_ms / 1000)
            s.connect(socket_path())
            break
        except (BlockingIOError, InterruptedError):               # the server is busy (its queue is full): wait a moment
            s.close()
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)
        except OSError:                                           # no server (missing / refused): nothing to wait for
            s.close()
            return False
    try:
        s.settimeout(timeout_ms / 1000 + len(data) / 200_000)     # +5 s per MB for very big messages
        s.sendall(data)                                           # sendall: the whole message, however big
        s.shutdown(socket.SHUT_WR)                                # tells the server "that was everything"
        return True
    except OSError:
        return False
    finally:
        s.close()


def _alive(path: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def _read_client(conn: socket.socket) -> bytes:
    data = bytearray()
    try:
        conn.settimeout(5)
        while len(data) < MAX_MESSAGE:
            chunk = conn.recv(1 << 16)
            if not chunk:
                break
            data += chunk
    except OSError:                       # includes a timeout: use whatever arrived
        pass
    finally:
        conn.close()
    return bytes(data)


def _accept_loop(server: socket.socket, q, stop: threading.Event) -> None:
    """Runs in a background thread: plain data in, plain data out (`q` is a queue.SimpleQueue)."""
    while not stop.is_set():
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:                   # the listening socket was closed
            return
        for line in _read_client(conn).split(b"\n"):   # one client at a time, so messages keep their order
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                q.put(("ipc", (msg,)))


class IpcServer:
    """Listens on a Unix socket; every received message is put on `out_queue` as ("ipc", (message_dict,))."""

    def __init__(self, out_queue, path: str | None = None):
        self._queue = out_queue
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def listen(self) -> bool:
        """False if another live instance already owns the socket. A stale file from a crash is replaced."""
        if os.path.exists(self.path):
            if _alive(self.path):
                return False
            os.unlink(self.path)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(self.path)
            os.chmod(self.path, 0o600)    # only you may talk to Warpdown
            s.listen(128)
        except OSError:
            s.close()
            return False
        s.settimeout(0.3)
        self._sock = s
        self._stop.clear()
        q, stop = self._queue, self._stop
        self._thread = threading.Thread(target=_accept_loop, args=(s, q, stop), daemon=True, name="warpdown-ipc")
        self._thread.start()
        return True

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        try:
            os.unlink(self.path)
        except OSError:
            pass

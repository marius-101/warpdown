import os
import queue
import socket
import stat
import tempfile
import time

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from warpdown import ipc
from warpdown.inbox import Inbox


@pytest.fixture()
def path(monkeypatch):
    d = tempfile.mkdtemp(prefix="wd")                       # short: unix socket paths are limited to ~100 characters
    p = os.path.join(d, "s.sock")
    monkeypatch.setattr(ipc, "socket_path", lambda: p)
    yield p
    try:
        os.unlink(p)
    except OSError:
        pass
    os.rmdir(d)


def collect(q, n, timeout=10):
    out = []
    end = time.time() + timeout
    while len(out) < n and time.time() < end:
        try:
            out.append(q.get(timeout=0.1))
        except queue.Empty:
            pass
    return out


def test_roundtrip_many_messages_keep_their_order(path):
    q = queue.SimpleQueue()
    srv = ipc.IpcServer(q)
    assert srv.listen()
    for i in range(200):                                    # a burst, like many hand-overs in a row
        assert ipc.send({"action": "add", "url": f"http://x/{i}"})
    got = collect(q, 200)
    srv.close()
    assert [kind for kind, _ in got] == ["ipc"] * 200
    assert [args[0]["url"] for _, args in got] == [f"http://x/{i}" for i in range(200)]


def test_send_without_a_running_instance_returns_false(path):
    assert ipc.send({"action": "show"}) is False


def test_socket_is_private_and_removed_on_close(path):
    srv = ipc.IpcServer(queue.SimpleQueue())
    assert srv.listen()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    srv.close()
    assert not os.path.exists(path)


def test_stale_socket_file_is_replaced_but_a_live_instance_is_not_stolen(path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(path)
    stale.close()                                           # a file left behind by a crashed instance
    assert os.path.exists(path)
    q = queue.SimpleQueue()
    first = ipc.IpcServer(q)
    assert first.listen()                                   # replaced the dead file
    second = ipc.IpcServer(queue.SimpleQueue())
    assert second.listen() is False                         # another live instance owns it: don't steal
    assert ipc.send({"action": "show"}) and collect(q, 1)[0][1][0] == {"action": "show"}
    first.close()


def test_garbage_and_non_dict_messages_are_ignored(path):
    q = queue.SimpleQueue()
    srv = ipc.IpcServer(q)
    assert srv.listen()
    for raw in (b"not json\n", b"[1, 2]\n", b"\n", b'{"action": "ok"}\n', b"\xff\xfe\n"):
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(path); c.sendall(raw); c.close()
    got = collect(q, 1)
    time.sleep(0.3)
    srv.close()
    assert [args[0] for _, args in got] == [{"action": "ok"}] and q.empty()


def test_a_stuck_client_does_not_block_the_next_one(path):
    q = queue.SimpleQueue()
    srv = ipc.IpcServer(q)
    assert srv.listen()
    stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stuck.connect(path)
    stuck.sendall(b'{"action": "half"')                    # never finishes, never closes
    t0 = time.time()
    assert ipc.send({"action": "after-stuck"})
    got = collect(q, 1, timeout=12)
    stuck.close()
    srv.close()
    assert [args[0]["action"] for _, args in got] == ["after-stuck"] and time.time() - t0 < 10


# ---------------------------------------------------------------- delivery to the GUI thread
def test_messages_reach_a_handler_in_the_gui_thread_even_if_it_opens_a_nested_event_loop(qapp, path):
    """app.py's pattern: Inbox -> handler -> QTimer.singleShot -> nested event loop (what a modal dialog does)."""
    import threading
    main = threading.get_ident()
    handled, threads = [], []

    def run(msg):
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)                   # stands in for dialog.exec()
        loop.exec()
        handled.append(msg["url"])
        threads.append(threading.get_ident())

    inbox = Inbox({"ipc": lambda msg: QTimer.singleShot(0, lambda: run(msg))})
    srv = ipc.IpcServer(inbox.queue)
    assert srv.listen()
    for i in range(3):
        assert ipc.send({"action": "add", "url": f"http://x/{i}"})
    end = time.time() + 10
    while len(handled) < 3 and time.time() < end:
        qapp.processEvents(); time.sleep(0.01)
    srv.close()
    assert sorted(handled) == ["http://x/0", "http://x/1", "http://x/2"] and set(threads) == {main}


def test_tcp_transport_used_on_windows_needs_the_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "USE_TCP", True)
    p = str(tmp_path / "ipc.json")
    monkeypatch.setattr(ipc, "socket_path", lambda: p)
    q = queue.SimpleQueue()
    server = ipc.IpcServer(q)
    assert server.listen()
    try:
        assert ipc.send({"action": "add", "url": "http://x/a"})
        assert collect(q, 1) == [("ipc", ({"action": "add", "url": "http://x/a"},))]
        info = __import__("json").load(open(p))
        raw = socket.create_connection(("127.0.0.1", info["port"]))     # a stranger without the token
        raw.sendall(b'{"action": "add", "url": "http://evil/x"}\n')
        raw.close()
        assert ipc.send({"action": "show"})
        assert collect(q, 2, timeout=1.5) == [("ipc", ({"action": "show"},))]
        assert not ipc.IpcServer(queue.SimpleQueue()).listen()          # a second instance is refused
    finally:
        server.close()
    assert not os.path.exists(p)

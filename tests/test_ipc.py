import time


from warpdown import ipc


def test_ipc_roundtrip_many_messages(qapp, monkeypatch):
    app = qapp
    monkeypatch.setattr(ipc, "socket_name", lambda: "warpdown-test-ipc")
    srv = ipc.IpcServer()
    assert srv.listen()
    got = []
    srv.message.connect(got.append)
    for i in range(20):
        assert ipc.send({"action": "add", "url": f"http://x/{i}"})
    t = time.time()
    while len(got) < 20 and time.time() - t < 5:
        app.processEvents()
        time.sleep(0.01)
    time.sleep(0.05); app.processEvents()
    srv.close()
    for _ in range(5):
        app.processEvents()
    assert [m["url"] for m in got] == [f"http://x/{i}" for i in range(20)]


def test_nested_event_loop_in_handler_does_not_crash(qapp, monkeypatch):
    """Regression: a modal dialog opened directly in the handler crashed Qt."""
    from PySide6.QtCore import QEventLoop, QTimer
    monkeypatch.setattr(ipc, "socket_name", lambda: "warpdown-test-ipc2")
    srv = ipc.IpcServer()
    assert srv.listen()
    handled = []

    def handle(msg):
        QTimer.singleShot(0, lambda: run(msg))   # same pattern as app.handle

    def run(msg):
        loop = QEventLoop()
        QTimer.singleShot(150, loop.quit)        # stands in for dialog.exec()
        loop.exec()
        handled.append(msg["url"])

    srv.message.connect(handle)
    assert ipc.send({"action": "add", "url": "http://x/1"})
    t = time.time()
    while not handled and time.time() - t < 5:
        qapp.processEvents()
        time.sleep(0.01)
    srv.close()
    for _ in range(5):
        qapp.processEvents()
    assert handled == ["http://x/1"]

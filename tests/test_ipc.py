import time


from kudm import ipc


def test_ipc_roundtrip_many_messages(qapp, monkeypatch):
    app = qapp
    monkeypatch.setattr(ipc, "socket_name", lambda: "kudm-test-ipc")
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

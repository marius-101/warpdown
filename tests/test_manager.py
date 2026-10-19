import os
import time

from PySide6.QtWidgets import QApplication

from kudm.manager import COMPLETED, DownloadManager
from tests.rangeserver import make_server

PAYLOAD = os.urandom(5 * 1024 * 1024 + 7)


def test_manager_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    app = QApplication.instance() or QApplication([])
    srv, url = make_server(PAYLOAD)
    m = DownloadManager()
    it = m.add(url, str(tmp_path))
    t0 = time.time()
    while it.status != COMPLETED and time.time() - t0 < 30:
        app.processEvents()
        time.sleep(0.05)
    assert it.status == COMPLETED, (it.status, it.error)
    assert (tmp_path / "test file.bin").read_bytes() == PAYLOAD
    m.shutdown()
    assert DownloadManager().items[it.id].status == COMPLETED
    srv.shutdown()

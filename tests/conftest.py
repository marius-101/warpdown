import gc

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def _isolated_home(tmp_path_factory):
    """Tests must never touch the real download list, statistics or settings of whoever runs them."""
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    for var, sub in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                     ("XDG_DOWNLOAD_DIR", "Downloads")):
        mp.setenv(var, str(home / sub))
    yield
    mp.undo()


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _close_all_windows():
    """Windows and dialogs left behind by a test must not outlive it: PySide can crash when it tears down objects whose
    C++ side is long gone (seen at interpreter exit). Close and delete them, and let Qt finish, after every test."""
    yield
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()

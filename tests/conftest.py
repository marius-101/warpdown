import gc

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


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

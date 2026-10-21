"""Regression tests for the crashes seen after downloads: worker threads must never touch Qt objects."""

import ast
import gc
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from warpdown import gui
from warpdown.inbox import Inbox
from warpdown.manager import DownloadManager

PKG = Path(__file__).resolve().parent.parent / "warpdown"


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------- the rule, enforced by reading the source
@pytest.mark.parametrize("module", ["gui.py", "manager.py", "app.py", "ipc.py"])
def test_threads_only_run_module_level_functions_with_plain_arguments(module):
    """threading.Thread(target=...) must name a module-level function and must not be handed `self`: a closure over a
    widget/QObject is exactly what let worker threads release dialogs (double delete) or emit on dead ones."""
    tree = ast.parse((PKG / module).read_text())
    top_level = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Thread":
            kw = {k.arg: k.value for k in node.keywords}
            target = kw.get("target")
            if not (isinstance(target, ast.Name) and target.id in top_level):
                problems.append(f"{module}:{node.lineno}: target is not a module-level function")
            args = kw.get("args")
            for el in (args.elts if isinstance(args, ast.Tuple) else []):
                # Calls such as dict(self._hdrs) or self._browser_headers(it) run in the GUI thread before the thread
                # starts and yield plain data; `self._inbox.queue` is a plain queue. What must never be passed is
                # `self` itself, a widget/method attribute of it (`self.something`), or a lambda closing over one.
                bare_self = isinstance(el, ast.Name) and el.id == "self"
                self_attr = isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name) and el.value.id == "self"
                if bare_self or self_attr or isinstance(el, ast.Lambda):
                    problems.append(f"{module}:{node.lineno}: a Qt object or closure is passed to a thread")
    assert problems == []


# ---------------------------------------------------------------- the mailbox
class Owner:
    def __init__(self):
        self.got = []

    def handle(self, *a):
        self.got.append(a)


def test_inbox_delivers_in_order_in_the_gui_thread_and_survives_bad_handlers(qapp):
    owner = Owner()
    main = threading.get_ident()
    seen_threads = []

    def boom(*a):
        raise ValueError("a bad message")

    def spy(*a):
        seen_threads.append(threading.get_ident())

    box = Inbox({"a": owner.handle, "boom": boom, "spy": spy})
    q = box.queue
    t = threading.Thread(target=lambda: [q.put(("a", (i,))) for i in range(5)] + [q.put(("boom", ())), q.put(("spy", ())),
                                                                                 q.put(("nobody", ())), q.put(("a", ("last",)))])
    t.start(); t.join()
    pump(qapp, 0.4)
    assert [x[0] for x in owner.got] == [0, 1, 2, 3, 4, "last"]          # in order, even after a failing handler
    assert seen_threads == [main]                                          # delivered in the GUI thread


def test_inbox_does_not_keep_its_owner_alive(qapp):
    import weakref
    owner = Owner()
    ref = weakref.ref(owner)
    box = Inbox({"a": owner.handle})
    del owner
    gc.collect()
    assert ref() is None                                                   # no strong reference, no cycle
    box.queue.put(("a", (1,)))
    pump(qapp, 0.3)                                                        # message for a dead owner is just dropped


# ---------------------------------------------------------------- the scenario that crashed
@pytest.fixture()
def win(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    qapp.setOrganizationName("warpdown-test4")
    qapp.setApplicationName("warpdown-test4")
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "conf"))
    QSettings().clear()
    m = DownloadManager()
    w = gui.MainWindow(m)
    w.show()
    yield w
    m.shutdown()


def test_slow_lookups_finishing_after_the_dialog_is_gone_are_harmless(qapp, win, monkeypatch):
    """Subtitle lookups run for up to a minute. Their results arriving after the dialog was closed and deleted must not
    raise, crash, or touch the deleted dialog."""
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args))

    def slow_peek(url, headers):
        time.sleep(0.6)
        return {"ext": ".vtt", "cues": 3, "bytes": 99}
    monkeypatch.setattr(gui, "peek_subtitle", slow_peek)

    for round_no in range(4):                                     # like several videos handed over in a row
        d = gui.AddDialog(win, "", {})
        d.set_subs([{"url": f"http://x/{round_no}-{i}.vtt", "lang": "eng", "label": ""} for i in range(7)], {})
        d.show(); pump(qapp, 0.1)
        d.reject()
        d.deleteLater()                                           # what add_dialog() now does after closing
        del d
        gc.collect()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
        pump(qapp, 0.1)
    pump(qapp, 4.5)                                               # every worker finishes into the void
    gc.collect(); pump(qapp, 0.2)
    assert errors == []                                           # no exception in any worker thread
    assert win.isVisible()                                        # and, of course, no crash


def test_add_dialog_cleans_itself_up(qapp, win, monkeypatch):
    """add_dialog() must delete the dialog it created once it is closed."""
    created = []
    real = gui.AddDialog

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)
    monkeypatch.setattr(gui, "AddDialog", Spy)
    monkeypatch.setattr(real, "exec", lambda self: 0)             # "user pressed Cancel" immediately
    import shiboken6
    win.add_dialog("http://127.0.0.1:9/x.bin")
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(created) == 1 and not shiboken6.isValid(created[0])    # the C++ dialog is gone


def test_manager_background_results_after_removal_are_ignored(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from tests.rangeserver import make_server
    srv, url = make_server(b"x" * 100_000, delay=0.0)
    m = DownloadManager()
    it = m.add(url, str(tmp_path / "dl"))
    m.remove(it.id)                                               # removed while the probe thread is still running
    pump(qapp, 1.0)
    assert it.id not in m.items
    m.shutdown(); srv.shutdown()

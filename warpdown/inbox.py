"""Thread-safe hand-off from worker threads to the GUI thread.

Worker threads must never touch Qt objects. A signal emitted from a thread, or a Python wrapper of a widget released by
one, can make PySide delete an object twice or call into a dead one (seen as random crashes a minute after a download).
So workers only put plain Python data into a queue; a timer in the GUI thread picks it up and calls the handlers.
"""

from __future__ import annotations

import queue
import traceback
import weakref
from typing import Callable

from PySide6.QtCore import QObject, QTimer


class Inbox(QObject):
    """Create it in the GUI thread. Give worker threads `inbox.queue` (a plain object), never the inbox itself:

        threading.Thread(target=module_level_worker, args=(inbox.queue, ...)).start()
        # in the worker:  q.put(("kind", (arg1, arg2)))   ->   handlers["kind"](arg1, arg2) in the GUI thread
    """

    def __init__(self, handlers: dict[str, Callable], parent: QObject | None = None, interval_ms: int = 100):
        super().__init__(parent)
        self.queue: queue.SimpleQueue = queue.SimpleQueue()
        # weak references: the inbox must not keep its owner alive (or form a reference cycle with it)
        self._handlers = {k: weakref.WeakMethod(h) if hasattr(h, "__self__") else (lambda h=h: h)
                          for k, h in handlers.items()}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain)
        self._timer.start(interval_ms)

    def _drain(self) -> None:
        while True:
            try:
                kind, args = self.queue.get_nowait()
            except queue.Empty:
                return
            ref = self._handlers.get(kind)
            handler = ref() if ref is not None else None
            if handler is None:                 # unknown kind, or the owner is gone: drop the message
                continue
            try:
                handler(*args)
            except Exception:                   # noqa: BLE001  one bad message must not block the others
                traceback.print_exc()

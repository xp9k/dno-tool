import threading
from PySide6.QtCore import QObject, Signal, QMetaObject, Qt


class BaseWorker(QObject):
    """Base class for all workers"""
    finished = Signal()
    error = Signal(str)
    started = Signal()

    def __init__(self):
        super().__init__()
        self._abort_event = threading.Event()

    def execute(self):
        raise NotImplementedError

    def abort(self):
        self._abort_event.set()

    @property
    def is_aborting(self) -> bool:
        return self._abort_event.is_set()

    def abort_wait(self, timeout: float = 0.1) -> bool:
        return self._abort_event.wait(timeout)

    @staticmethod
    def emit_signal_safe(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            pass
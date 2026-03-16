"""Threading helper for background tasks in the GUI."""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(tuple)
    progress = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    """Run a callable in a background thread.

    Signal emissions are guarded: if the parent widget (and therefore
    the WorkerSignals QObject) is destroyed before the worker
    finishes, the emit() calls are silently skipped instead of
    raising ``RuntimeError: Signal source has been deleted``.
    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def _safe_emit(self, signal, *args):
        """Emit a signal, silently ignoring if QObject already deleted."""
        try:
            signal.emit(*args)
        except RuntimeError:
            # WorkerSignals QObject was destroyed (app closing) — ignore
            pass

    @Slot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception:
            self._safe_emit(self.signals.error, sys.exc_info())
        else:
            self._safe_emit(self.signals.result, result)
        finally:
            self._safe_emit(self.signals.finished)

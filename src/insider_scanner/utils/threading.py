"""Threading helper for background tasks in the GUI."""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


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

    def __init__(self, fn, *args, emit_progress: bool = False, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.emit_progress = emit_progress
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def _safe_emit(self, signal, *args):
        """Emit a signal, silently ignoring if QObject already deleted."""
        try:
            signal.emit(*args)
        except RuntimeError:
            # WorkerSignals QObject was destroyed (app closing) — ignore
            pass

    def _emit_progress(self, value: object) -> None:
        """Forward a worker progress update onto the ``progress`` signal."""
        self._safe_emit(self.signals.progress, value)

    @Slot()
    def run(self):
        # Only inject ``progress_callback`` when progress emission was requested,
        # so functions that don't expect the kwarg keep working unchanged. A new
        # dict is built rather than mutating ``self.kwargs``.
        kwargs = self.kwargs
        if self.emit_progress:
            kwargs = {**self.kwargs, "progress_callback": self._emit_progress}
        try:
            result = self.fn(*self.args, **kwargs)
        except Exception:
            self._safe_emit(self.signals.error, sys.exc_info())
        else:
            self._safe_emit(self.signals.result, result)
        finally:
            self._safe_emit(self.signals.finished)


def dispatch(
    thread_pool: QThreadPool,
    fn: Callable,
    *,
    on_result: Callable | None = None,
    on_error: Callable | None = None,
    on_progress: Callable | None = None,
    on_finished: Callable | None = None,
    args: tuple = (),
    kwargs: dict | None = None,
) -> Worker:
    """Create a :class:`Worker`, connect its signals, and start it.

    Collapses the repeated create-Worker / connect-signals / start
    boilerplate used by the GUI tabs into a single call. Returns the
    worker so callers can keep a reference if needed.
    """
    worker = Worker(fn, *args, emit_progress=on_progress is not None, **(kwargs or {}))
    if on_result is not None:
        worker.signals.result.connect(on_result)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    thread_pool.start(worker)
    return worker

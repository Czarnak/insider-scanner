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

    Signals are independent of any receiving widget. Emission guards handle
    an already deleted signal object; use :class:`LatestRequest` to tie
    callbacks to an owner's lifetime and discard superseded results.
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
        except Exception:  # noqa: BLE001 - arbitrary worker failures must reach the GUI error signal
            self._safe_emit(self.signals.error, sys.exc_info())
        else:
            self._safe_emit(self.signals.result, result)
        finally:
            self._safe_emit(self.signals.finished)


class _RequestRelay(QObject):
    """Receive worker signals in the owning object's thread and lifetime."""

    def __init__(
        self,
        requests: LatestRequest,
        request_id: int,
        *,
        on_result: Callable | None,
        on_error: Callable | None,
        on_progress: Callable | None,
        on_finished: Callable | None,
    ):
        super().__init__(requests)
        self._requests = requests
        self._request_id = request_id
        self._on_result = on_result
        self._on_error = on_error
        self._on_progress = on_progress
        self._on_finished = on_finished

    def _deliver(self, callback: Callable | None, *args) -> None:
        if self._request_id == self._requests.request_id and callback is not None:
            callback(*args)

    @Slot(object)
    def result(self, value: object) -> None:
        self._deliver(self._on_result, value)

    @Slot(tuple)
    def error(self, error_info: tuple) -> None:
        self._deliver(self._on_error, error_info)

    @Slot(object)
    def progress(self, value: object) -> None:
        self._deliver(self._on_progress, value)

    @Slot()
    def finished(self) -> None:
        callback = self._on_finished
        self._requests._pending.pop(self._request_id, None)
        self._on_result = self._on_error = self._on_progress = self._on_finished = None
        self.deleteLater()
        self._deliver(callback)


class LatestRequest(QObject):
    """Own one stream of GUI work whose latest request alone may update UI.

    Each request keeps its worker and a child QObject relay until completion.
    The relay's bound slots run in the owner's thread, and Qt disconnects them
    when the owner is destroyed. Invalidating a request suppresses callbacks;
    callers remain responsible for any cooperative cancellation their work
    supports. Separate independent activities should use separate instances.
    """

    def __init__(self, owner: QObject, thread_pool: QThreadPool):
        super().__init__(owner)
        self._thread_pool = thread_pool
        self._request_id = 0
        self._pending: dict[int, tuple[Worker, _RequestRelay]] = {}

    @property
    def request_id(self) -> int:
        """Return the generation used to recognize the current request."""
        return self._request_id

    def invalidate(self) -> None:
        """Suppress every callback belonging to already submitted work."""
        self._request_id += 1

    def start(
        self,
        fn: Callable,
        *,
        args: tuple = (),
        kwargs: dict | None = None,
        on_result: Callable | None = None,
        on_error: Callable | None = None,
        on_progress: Callable | None = None,
        on_finished: Callable | None = None,
    ) -> Worker:
        """Start work, superseding callbacks from the previous request."""
        self.invalidate()
        worker = Worker(
            fn, *args, emit_progress=on_progress is not None, **(kwargs or {})
        )
        relay = _RequestRelay(
            self,
            self._request_id,
            on_result=on_result,
            on_error=on_error,
            on_progress=on_progress,
            on_finished=on_finished,
        )
        worker.signals.result.connect(relay.result)
        worker.signals.error.connect(relay.error)
        worker.signals.progress.connect(relay.progress)
        worker.signals.finished.connect(relay.finished)
        self._pending[self._request_id] = worker, relay
        self._thread_pool.start(worker)
        return worker


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

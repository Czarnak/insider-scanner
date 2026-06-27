"""Tests for background worker helpers."""

from __future__ import annotations

from insider_scanner.utils.threading import Worker, dispatch


class InlinePool:
    """Minimal QThreadPool stand-in that runs the worker synchronously."""

    def start(self, worker):
        worker.run()


class TestWorker:
    def test_emits_result_and_finished(self):
        worker = Worker(lambda: 42)
        results = []
        finished = []
        worker.signals.result.connect(results.append)
        worker.signals.finished.connect(lambda: finished.append(True))

        worker.run()

        assert results == [42]
        assert finished == [True]

    def test_emits_error(self):
        worker = Worker(lambda: 1 / 0)
        errors = []
        worker.signals.error.connect(errors.append)

        worker.run()

        assert len(errors) == 1
        error_info = errors[0]
        assert error_info[0] is ZeroDivisionError

    def test_emit_progress_injects_callback_that_drives_progress_signal(self):
        def work(progress_callback):
            progress_callback("tick")
            progress_callback("tock")
            return "done"

        worker = Worker(work, emit_progress=True)
        progress = []
        results = []
        worker.signals.progress.connect(progress.append)
        worker.signals.result.connect(results.append)

        worker.run()

        assert progress == ["tick", "tock"]
        assert results == ["done"]

    def test_default_worker_injects_no_progress_callback(self):
        def work(**kwargs):
            return kwargs

        worker = Worker(work)
        results = []
        worker.signals.result.connect(results.append)

        worker.run()

        assert results == [{}]


class TestDispatch:
    def test_enables_progress_emission_when_on_progress_supplied(self):
        def work(progress_callback):
            progress_callback(7)
            return "ok"

        progress = []
        results = []
        dispatch(
            InlinePool(),
            work,
            on_result=results.append,
            on_progress=progress.append,
        )

        assert progress == [7]
        assert results == ["ok"]

    def test_without_on_progress_injects_no_callback(self):
        def work(**kwargs):
            return kwargs

        results = []
        dispatch(InlinePool(), work, on_result=results.append)

        assert results == [{}]

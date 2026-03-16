"""Tests for background worker helpers."""

from __future__ import annotations

from insider_scanner.utils.threading import Worker


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

"""Cooperative window close must keep processing GUI events until work drains."""

from __future__ import annotations

from threading import Event

import pytest
from PySide6.QtCore import QRunnable, QTimer

from insider_scanner.gui.main_window import MainWindow, MainWindowInitializationError


class HeldWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.release = Event()

    def run(self):
        self.started.set()
        self.release.wait(5)


def test_close_never_waits_on_gui_thread_and_cancels_only_once(
    qtbot, tmp_path, monkeypatch
):
    window = MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    window.show()
    worker = HeldWorker()
    window._thread_pool.start(worker)
    assert worker.started.wait(1)
    cancellations = []
    original_cancel = window.request_cancellation

    def cancel():
        cancellations.append(True)
        original_cancel()

    def forbidden_wait(*args):
        raise AssertionError("window close must not block the GUI thread")

    monkeypatch.setattr(window, "request_cancellation", cancel)
    monkeypatch.setattr(window._thread_pool, "waitForDone", forbidden_wait)
    heartbeat = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: heartbeat.append(True))
    timer.start(5)
    try:
        assert not window.close()
        assert not window.close()
        assert cancellations == [True]
        assert window.isVisible()
        assert not window.centralWidget().isEnabled()
        assert "Stopping background tasks" in window.status_bar.currentMessage()
        qtbot.waitUntil(lambda: len(heartbeat) >= 2)
        worker.release.set()
        qtbot.waitUntil(lambda: not window.isVisible())
        assert cancellations == [True]
    finally:
        worker.release.set()
        monkeypatch.undo()
        window._thread_pool.waitForDone(1_000)


def test_delayed_shutdown_reports_wait_and_closes_after_release(
    qtbot, tmp_path, caplog
):
    window = MainWindow(state_path=tmp_path / "feed_state.json", shutdown_timeout_ms=10)
    qtbot.addWidget(window)
    window.show()
    worker = HeldWorker()
    window._thread_pool.start(worker)
    assert worker.started.wait(1)
    try:
        assert not window.close()
        qtbot.waitUntil(lambda: "Still waiting" in window.status_bar.currentMessage())
        assert window.isVisible()
        assert "Timed out waiting for GUI workers to stop" in caplog.text
        worker.release.set()
        qtbot.waitUntil(lambda: not window.isVisible())
    finally:
        worker.release.set()
        window._thread_pool.waitForDone(1_000)


def test_idle_window_closes_immediately(qtbot, tmp_path):
    window = MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    window.show()
    assert window.close()
    assert not window.isVisible()


def test_partial_window_initialization_can_close_safely(qtbot, tmp_path, monkeypatch):
    def fail_recovery(_data_dir):
        raise PermissionError("locked rename files")

    monkeypatch.setattr(
        "insider_scanner.gui.user_state.recover_watchlist_rename", fail_recovery
    )
    window = MainWindow.__new__(MainWindow)
    with pytest.raises(MainWindowInitializationError, match="rename"):
        window.__init__(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)

    assert window.centralWidget() is None
    assert window.close()

"""Ownership and thread-affinity regressions for latest GUI requests."""

from __future__ import annotations

import gc
import weakref
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QThreadPool
from PySide6.QtWidgets import QLabel


class QueuedPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.fixture
def requests(qapp):
    from insider_scanner.utils.threading import LatestRequest

    owner = QObject()
    pool = QueuedPool()
    helper = LatestRequest(owner, pool)
    yield helper, pool
    owner.deleteLater()


@pytest.mark.parametrize("old_fails", [False, True])
def test_only_latest_request_delivers_result_error_progress_and_finish(
    requests, old_fails
):
    helper, pool = requests
    calls = []

    def work(label, *, fail=False, progress_callback):
        progress_callback(label)
        if fail:
            raise ValueError(label)
        return label

    def start(label, *, fail=False):
        return helper.start(
            work,
            args=(label,),
            kwargs={"fail": fail},
            on_result=lambda value: calls.append(("result", value)),
            on_error=lambda error: calls.append(("error", str(error[1]))),
            on_progress=lambda value: calls.append(("progress", value)),
            on_finished=lambda: calls.append(("finished", label)),
        )

    start("old", fail=old_fails)
    first_id = helper.request_id
    start("new")
    assert helper.request_id > first_id
    pool.workers[1].run()
    pool.workers[0].run()

    assert calls == [("progress", "new"), ("result", "new"), ("finished", "new")]


def test_invalidate_suppresses_all_callbacks_and_can_start_again(requests):
    helper, pool = requests
    calls = []
    helper.start(
        lambda: "old", on_result=calls.append, on_finished=lambda: calls.append("done")
    )
    request_id = helper.request_id
    helper.invalidate()
    assert helper.request_id > request_id
    pool.workers[0].run()
    assert calls == []

    helper.start(lambda: "new", on_result=calls.append)
    pool.workers[1].run()
    assert calls == ["new"]


def test_retains_worker_until_it_can_finish(qapp):
    from insider_scanner.utils.threading import LatestRequest

    class WeakPool:
        def start(self, worker):
            self.worker = weakref.ref(worker)

    owner = QObject()
    pool = WeakPool()
    helper = LatestRequest(owner, pool)
    calls = []
    helper.start(lambda: 42, on_result=calls.append)
    gc.collect()

    assert pool.worker() is not None
    pool.worker().run()
    assert calls == [42]
    owner.deleteLater()


def test_real_pool_delivers_callbacks_on_gui_thread_and_discards_old_completion(
    qtbot, qapp
):
    from insider_scanner.utils.threading import LatestRequest

    owner = QObject()
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    helper = LatestRequest(owner, pool)
    started, release = Event(), Event()
    calls = []
    threads = []

    def old_work():
        started.set()
        assert release.wait(5)
        return "old"

    def record(kind, value=None):
        calls.append((kind, value))
        threads.append(QThread.currentThread())

    def new_work(*, progress_callback):
        progress_callback("tick")
        return "new"

    try:
        helper.start(old_work, on_result=lambda value: record("old", value))
        qtbot.waitUntil(started.is_set)
        helper.start(
            new_work,
            on_progress=lambda value: record("progress", value),
            on_result=lambda value: record("result", value),
            on_finished=lambda: record("finished"),
        )
        qtbot.waitUntil(lambda: ("finished", None) in calls)
        assert calls == [("progress", "tick"), ("result", "new"), ("finished", None)]
        assert all(thread == qapp.thread() for thread in threads)
    finally:
        release.set()
        assert pool.waitForDone(5_000)
        qapp.processEvents()
        owner.deleteLater()

    assert not any(kind == "old" for kind, _ in calls)


@pytest.mark.parametrize("fail", [False, True])
def test_destroyed_widget_drops_real_worker_callbacks(qtbot, qapp, fail):
    from insider_scanner.utils.threading import LatestRequest

    owner = QLabel("live")
    pool = QThreadPool()
    helper = LatestRequest(owner, pool)
    started, release = Event(), Event()
    callbacks = []

    def work(*, progress_callback):
        started.set()
        assert release.wait(5)
        progress_callback("late")
        if fail:
            raise ValueError("late")
        return "late"

    def touch_deleted_widget(value=None):
        owner.setText(str(value))
        callbacks.append(value)

    try:
        helper.start(
            work,
            on_result=touch_deleted_widget,
            on_progress=touch_deleted_widget,
            on_error=touch_deleted_widget,
            on_finished=touch_deleted_widget,
        )
        qtbot.waitUntil(started.is_set)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        release.set()
        assert pool.waitForDone(5_000)
        qapp.processEvents()

    assert callbacks == []

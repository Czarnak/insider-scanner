"""Alert coordination keeps only current results and stops at shutdown."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QThreadPool

from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.alerts import AlertHit


class HeldPool(QThreadPool):
    """Hold real workers so tests can choose their completion order."""

    def __init__(self):
        super().__init__()
        self.workers = []

    def start(self, worker, priority=0):
        self.workers.append(worker)


@pytest.fixture
def controller(qapp):
    from insider_scanner.gui.alert_controller import AlertController

    pool = HeldPool()
    instance = AlertController(object(), thread_pool=pool)
    yield instance, pool
    instance.request_cancellation()


def _store(name="Current", *, enabled=True):
    return AlertStore(rules=(AlertRule(name, enabled=enabled),))


def test_check_evaluates_snapshots_and_publishes_hits_and_badge(qapp, monkeypatch):
    from insider_scanner.gui import alert_controller as module

    repository, pool = object(), HeldPool()
    instance = module.AlertController(repository, thread_pool=pool)
    store, watchlists = _store(), WatchlistStore()
    hits = (AlertHit("Current", (), 12),)
    calls, results, counts = [], [], []

    def evaluate(repository, alert_store, watchlist_store):
        calls.append((repository, alert_store, watchlist_store))
        return hits

    monkeypatch.setattr(module, "evaluate_alerts", evaluate)
    instance.hitsChanged.connect(results.append)
    instance.badgeChanged.connect(counts.append)

    instance.check(store, watchlists)
    assert results == [()]
    assert len(pool.workers) == 1
    pool.workers[0].run()

    assert calls == [(repository, store, watchlists)]
    assert results == [(), hits]
    assert counts == [12]


def test_new_check_clears_previous_hits_and_badge_before_completing(
    controller, monkeypatch
):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller
    hits = (AlertHit("Current", (), 4),)
    monkeypatch.setattr(module, "evaluate_alerts", lambda *_: hits)
    results, counts = [], []
    instance.hitsChanged.connect(results.append)
    instance.badgeChanged.connect(counts.append)
    instance.check(_store(), WatchlistStore())
    pool.workers[0].run()

    instance.check(_store(), WatchlistStore())

    assert results == [(), hits, ()]
    assert counts == [4, 0]


def test_late_result_cannot_replace_newer_rules_or_badge(controller, monkeypatch):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller
    monkeypatch.setattr(
        module,
        "evaluate_alerts",
        lambda _repo, store, _lists: (AlertHit(store.rules[0].name, (), 3),),
    )
    results, counts = [], []
    instance.hitsChanged.connect(results.append)
    instance.badgeChanged.connect(counts.append)
    instance.check(_store("Old"), WatchlistStore())
    instance.check(_store("New"), WatchlistStore())
    pool.workers[1].run()
    pool.workers[0].run()

    assert results == [(), (), (AlertHit("New", (), 3),)]
    assert counts == [3]


@pytest.mark.parametrize("store", [AlertStore(), _store(enabled=False)])
def test_empty_or_disabled_rules_invalidate_pending_work(
    controller, monkeypatch, store
):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller
    monkeypatch.setattr(module, "evaluate_alerts", lambda *_: (AlertHit("Old", (), 9),))
    results = []
    instance.hitsChanged.connect(results.append)
    instance.check(_store(), WatchlistStore())
    instance.check(store, WatchlistStore())
    pool.workers[0].run()

    assert results == [(), ()]
    assert len(pool.workers) == 1


def test_missing_repository_clears_without_dispatch(qapp):
    from insider_scanner.gui.alert_controller import AlertController

    pool = HeldPool()
    instance = AlertController(None, thread_pool=pool)
    results, counts = [], []
    instance.hitsChanged.connect(results.append)
    instance.badgeChanged.connect(counts.append)
    instance.update_match_count(7)
    instance.check(_store(), WatchlistStore())

    assert results == [()]
    assert counts == [7, 0]
    assert not pool.workers


def test_shutdown_blocks_pending_results_and_future_checks(controller, monkeypatch):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller
    monkeypatch.setattr(module, "evaluate_alerts", lambda *_: (AlertHit("Old", (), 9),))
    results, counts = [], []
    instance.hitsChanged.connect(results.append)
    instance.badgeChanged.connect(counts.append)
    instance.check(_store(), WatchlistStore())
    instance.request_cancellation()
    pool.workers[0].run()
    instance.check(_store(), WatchlistStore())
    instance.update_match_count(4)

    assert results == [()]
    assert not counts
    assert len(pool.workers) == 1


def test_failed_current_evaluation_logs_and_keeps_empty_display(
    controller, monkeypatch, caplog
):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller

    def fail(*_):
        raise RuntimeError("Cannot query local feed")

    monkeypatch.setattr(module, "evaluate_alerts", fail)
    results = []
    instance.hitsChanged.connect(results.append)
    instance.check(_store(), WatchlistStore())
    pool.workers[0].run()

    assert results == [()]
    assert "Alert evaluation failed" in caplog.text
    assert "Cannot query local feed" in caplog.text


def test_stale_evaluation_error_does_not_log_as_current(
    controller, monkeypatch, caplog
):
    from insider_scanner.gui import alert_controller as module

    instance, pool = controller

    def evaluate(_repository, store, _watchlists):
        if store.rules[0].name == "Old":
            raise RuntimeError("Obsolete error")
        return ()

    monkeypatch.setattr(module, "evaluate_alerts", evaluate)
    instance.check(_store("Old"), WatchlistStore())
    instance.check(_store("New"), WatchlistStore())
    pool.workers[0].run()
    pool.workers[1].run()

    assert "Obsolete error" not in caplog.text


def test_page_match_count_updates_badge_without_duplicate_notifications(controller):
    instance, _pool = controller
    counts = []
    instance.badgeChanged.connect(counts.append)

    instance.update_match_count(6)
    instance.update_match_count(6)
    instance.update_match_count(0)

    assert counts == [6, 0]

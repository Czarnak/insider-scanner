"""Request ownership regressions for Congress and European scan workflows."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade


class CapturingPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.mark.parametrize("market", ["congress", "european"])
def test_stop_scan_retains_partial_result_finishes_and_allows_restart(qtbot, market):
    from insider_scanner.gui import congress_tab, european_tab

    if market == "congress":
        cls = congress_tab.CongressTab
        partial_rows = [CongressTrade(official_name="Partial Person", ticker="AAPL")]
        complete_rows = [CongressTrade(official_name="Next Person", ticker="MSFT")]
    else:
        cls = european_tab.EuropeanTab
        partial_rows = [EuropeanInsiderTrade(isin="GB0002875804", source="rns")]
        complete_rows = [EuropeanInsiderTrade(isin="GB0007980591", source="rns")]

    def scan(_identifier, **kwargs):
        return partial_rows if kwargs["cancelled"]() else complete_rows

    pool = CapturingPool()
    tab = cls(SimpleNamespace(scan=scan), thread_pool=pool)
    qtbot.addWidget(tab)
    if market == "european":
        tab.isin_edit.setText("GB0002875804")
    changes = []
    tab.dataChanged.connect(lambda: changes.append(True))

    tab._run_scan()
    stopped_event = tab._cancel_event
    tab._stop_scan()
    assert stopped_event.is_set()
    pool.workers[0].run()

    assert tab._trades == partial_rows
    assert tab.btn_scan.isEnabled()
    assert not tab.btn_stop.isEnabled() or tab.btn_stop.isHidden()
    assert tab.progress.isHidden()
    assert changes == [True]

    tab._run_scan()
    assert tab._cancel_event is not stopped_event
    assert not tab._cancel_event.is_set()
    pool.workers[1].run()

    assert tab._trades == complete_rows
    assert tab.btn_scan.isEnabled()
    assert tab.progress.isHidden()
    assert changes == [True, True]


@pytest.mark.parametrize("market", ["congress", "european"])
def test_superseded_scan_keeps_its_cancellation_and_drops_late_callbacks(
    qtbot, monkeypatch, market
):
    from insider_scanner.gui import congress_tab, european_tab

    module = congress_tab if market == "congress" else european_tab
    cls = module.CongressTab if market == "congress" else module.EuropeanTab
    pool = CapturingPool()
    service = SimpleNamespace(scan=lambda *_args, **_kwargs: [])
    tab = cls(service, thread_pool=pool)
    qtbot.addWidget(tab)
    if market == "european":
        tab.isin_edit.setText("GB0002875804")
        old_rows = [EuropeanInsiderTrade(isin="GB0002875804", source="rns")]
        new_rows = [EuropeanInsiderTrade(isin="GB0007980591", source="rns")]
    else:
        old_rows = [CongressTrade(official_name="Old Person", ticker="AAPL")]
        new_rows = [CongressTrade(official_name="New Person", ticker="MSFT")]
    errors = []
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *_: errors.append(True))

    tab._run_scan()
    previous_cancel = tab._cancel_event
    tab._run_scan()
    stale, current = pool.workers

    assert previous_cancel.is_set()
    assert tab._cancel_event is not previous_cancel
    current.signals.result.emit(new_rows)
    current.signals.finished.emit()
    stale.signals.result.emit(old_rows)
    stale.signals.error.emit((RuntimeError, RuntimeError("old failure"), None))
    stale.signals.finished.emit()

    assert tab._trades == new_rows
    assert not errors
    assert tab.btn_scan.isEnabled()


@pytest.mark.parametrize("market", ["congress", "european"])
def test_cancelled_scan_drops_callbacks(qtbot, monkeypatch, market):
    from insider_scanner.gui import congress_tab, european_tab

    module = congress_tab if market == "congress" else european_tab
    cls = module.CongressTab if market == "congress" else module.EuropeanTab
    pool = CapturingPool()
    tab = cls(SimpleNamespace(scan=lambda *_args, **_kwargs: []), thread_pool=pool)
    qtbot.addWidget(tab)
    if market == "european":
        tab.isin_edit.setText("GB0002875804")
    errors = []
    changes = []
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *_: errors.append(True))
    tab.dataChanged.connect(lambda: changes.append(True))
    tab._run_scan()
    tab.request_cancellation()
    worker = pool.workers[0]

    worker.signals.result.emit([])
    worker.signals.error.emit((RuntimeError, RuntimeError("late failure"), None))
    worker.signals.finished.emit()

    assert tab._cancel_event.is_set()
    assert not errors
    assert not changes


def test_european_watchlist_uses_worker_progress_and_drops_stale_progress(
    qtbot, monkeypatch
):
    from insider_scanner.gui import european_tab

    pool = CapturingPool()
    calls = []

    def scan(isin, **kwargs):
        calls.append((isin, kwargs))
        return []

    monkeypatch.setattr(
        european_tab, "load_eu_watchlist", lambda: ["GB0002875804", "GB0007980591"]
    )
    tab = european_tab.EuropeanTab(SimpleNamespace(scan=scan), thread_pool=pool)
    qtbot.addWidget(tab)
    tab._run_watchlist()
    stale = pool.workers[0]
    assert stale.emit_progress
    tab._run_watchlist()
    stale.signals.progress.emit(2)
    stale.signals.finished.emit()
    assert tab.progress.value() == 0
    assert not tab.btn_scan.isEnabled()

    pool.workers[1].run()

    assert [isin for isin, _ in calls] == ["GB0002875804", "GB0007980591"]
    assert tab.progress.value() == 2
    assert tab.btn_scan.isEnabled()

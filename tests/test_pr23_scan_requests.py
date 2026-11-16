"""Scan requests stay off the GUI thread and own their current results."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from insider_scanner.core.models import InsiderTrade
from insider_scanner.gui.scan_tab import ScanTab


class QueuedPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.fixture
def scan_widget(qtbot, monkeypatch):
    service = SimpleNamespace(
        resolve_cik=Mock(return_value=("0000000320", "https://example.test/aapl")),
        latest=Mock(return_value=[]),
        scan=Mock(return_value=[]),
    )
    pool = QueuedPool()
    opened = []
    monkeypatch.setattr("insider_scanner.gui.scan_tab.webbrowser.open", opened.append)
    # Keep the old synchronous implementation deterministic during RED verification.
    monkeypatch.setattr("insider_scanner.core.edgar.resolve_cik", lambda ticker: "320")
    monkeypatch.setattr("insider_scanner.core.edgar.get_filing_url", lambda cik: "old")
    tab = ScanTab(service, thread_pool=pool)
    qtbot.addWidget(tab)
    tab.ticker_edit.setText("AAPL")
    return tab, service, pool, opened


def test_cik_lookup_queues_service_work_and_restores_button(scan_widget):
    tab, service, pool, opened = scan_widget
    tab._resolve_cik()

    service.resolve_cik.assert_not_called()
    assert not tab.btn_resolve_cik.isEnabled()
    assert len(pool.workers) == 1
    pool.workers[0].run()

    service.resolve_cik.assert_called_once_with("AAPL")
    assert "0000000320" in tab.detail_label.text()
    assert opened == ["https://example.test/aapl"]
    assert tab.btn_resolve_cik.isEnabled()


def test_ticker_edit_invalidates_pending_cik_result(scan_widget):
    tab, _service, pool, opened = scan_widget
    tab._resolve_cik()
    tab.ticker_edit.setText("MSFT")
    tab.detail_label.setText("Current selection")
    assert tab.btn_resolve_cik.isEnabled()

    pool.workers[0].run()

    assert tab.detail_label.text() == "Current selection"
    assert opened == []


def test_old_cik_finish_cannot_unlock_or_replace_new_lookup(scan_widget):
    tab, service, pool, opened = scan_widget
    tab._resolve_cik()
    tab.ticker_edit.setText("MSFT")
    tab._resolve_cik()
    pool.workers[0].run()
    assert not tab.btn_resolve_cik.isEnabled()
    assert opened == []

    service.resolve_cik.return_value = ("0000789019", "https://example.test/msft")
    pool.workers[1].run()
    assert "MSFT" in tab.detail_label.text()
    assert opened == ["https://example.test/msft"]
    assert tab.btn_resolve_cik.isEnabled()


@pytest.mark.parametrize("failure", [None, OSError("SEC unavailable")])
def test_cik_miss_or_error_restores_button_and_reports_failure(scan_widget, failure):
    tab, service, pool, opened = scan_widget
    service.resolve_cik.return_value = None
    service.resolve_cik.side_effect = failure
    tab._resolve_cik()
    pool.workers[0].run()

    assert tab.btn_resolve_cik.isEnabled()
    assert "AAPL" in tab.detail_label.text()
    assert "Could not resolve CIK" in tab.detail_label.text()
    assert opened == []


def test_shutdown_invalidates_cik_and_scan_callbacks(scan_widget):
    tab, _service, pool, opened = scan_widget
    tab._resolve_cik()
    tab._run_latest()
    tab.request_cancellation()
    tab.detail_label.setText("Closing")
    tab.status_label.setText("Closing")
    for worker in pool.workers:
        worker.run()

    assert tab.detail_label.text() == "Closing"
    assert tab.status_label.text() == "Closing"
    assert opened == []


def test_replacement_scan_keeps_old_cancellation_token_set(scan_widget):
    tab, service, pool, _opened = scan_widget
    cancelled = []
    service.latest.side_effect = lambda **kwargs: (
        cancelled.append(kwargs["cancelled"]()) or []
    )
    tab._run_latest()
    tab._run_latest()
    for worker in pool.workers:
        worker.run()

    assert cancelled == [True, False]


def test_stale_scan_cannot_replace_newer_results(scan_widget):
    tab, service, pool, _opened = scan_widget
    tab._run_latest()
    tab._run_latest()
    service.latest.return_value = [InsiderTrade(ticker="MSFT")]
    pool.workers[1].run()
    service.latest.return_value = [InsiderTrade(ticker="AAPL")]
    pool.workers[0].run()

    assert [trade.ticker for trade in tab._trades] == ["MSFT"]


def test_stop_scan_keeps_partial_results_callback(scan_widget):
    tab, service, pool, _opened = scan_widget
    service.latest.return_value = [InsiderTrade(ticker="AAPL")]
    tab._run_latest()
    tab._stop_scan()
    pool.workers[0].run()

    assert [trade.ticker for trade in tab._trades] == ["AAPL"]
    assert "cancelled" in tab.status_label.text()

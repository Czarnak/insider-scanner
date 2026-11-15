from datetime import date

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult
from insider_scanner.core.models import InsiderTrade
from insider_scanner.gui import scan_tab
from insider_scanner.gui.analysis_tab import AnalysisTab
from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import WatchEntry, Watchlist, WatchlistStore
from insider_scanner.services.common import ScanError


class InlinePool:
    def start(self, worker):
        worker.run()


def _watchlists(*symbols):
    return WatchlistStore(
        watchlists=(
            Watchlist(
                "Companies",
                tuple(
                    WatchEntry("company", FeedMarket.US, symbol) for symbol in symbols
                ),
            ),
        )
    )


def test_watchlist_scan_keeps_successes_and_continues_after_ticker_failure(
    qtbot, monkeypatch
):
    calls = []

    class Service:
        def scan(self, ticker, **kwargs):
            calls.append(ticker)
            if ticker == "BAD":
                raise ScanError("provider unavailable")
            return FetchResult(
                [InsiderTrade(ticker=ticker, trade_date=date(2026, 1, 1))],
                issues=[FetchIssue("result_limit", "openinsider", ticker)]
                if ticker == "AAPL"
                else (),
            )

    monkeypatch.setattr(scan_tab.QMessageBox, "critical", lambda *args: None)
    tab = scan_tab.ScanTab(
        Service(),
        thread_pool=InlinePool(),
        watchlists=_watchlists("AAPL", "BAD", "MSFT"),
    )
    qtbot.addWidget(tab)

    tab._run_watchlist()

    assert calls == ["AAPL", "BAD", "MSFT"]
    assert {trade.ticker for trade in tab._trades} == {"AAPL", "MSFT"}
    assert tab.trades_model.rowCount() == 2
    assert not tab._trades.complete
    assert "1 ticker failed: BAD" in tab.status_label.text()
    assert "result limit" in tab.status_label.text()
    tab._apply_filters()
    assert "1 ticker failed: BAD" in tab.status_label.text()


def test_analysis_symbols_read_the_managed_watchlists(qtbot):
    tab = AnalysisTab(watchlists=_watchlists("AAPL", "MSFT"))
    qtbot.addWidget(tab)

    assert [tab.symbol_combo.itemText(i) for i in range(tab.symbol_combo.count())] == [
        "AAPL",
        "MSFT",
    ]

    tab.symbol_combo.setCurrentText("MSFT")
    tab.set_watchlists(_watchlists("MSFT", "NVDA"))

    assert [tab.symbol_combo.itemText(i) for i in range(tab.symbol_combo.count())] == [
        "MSFT",
        "NVDA",
    ]
    assert tab.symbol_combo.currentText() == "MSFT"


def test_scan_uses_updated_watchlists_for_next_run(qtbot):
    calls = []

    class Service:
        def scan(self, ticker, **kwargs):
            calls.append(ticker)
            return []

    tab = scan_tab.ScanTab(
        Service(), thread_pool=InlinePool(), watchlists=_watchlists("AAPL")
    )
    qtbot.addWidget(tab)
    tab.set_watchlists(_watchlists("MSFT"))

    tab._run_watchlist()

    assert calls == ["MSFT"]

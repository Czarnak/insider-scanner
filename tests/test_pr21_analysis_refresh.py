from datetime import date
from types import SimpleNamespace

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.gui.analysis_tab import AnalysisTab
from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import WatchEntry, Watchlist, WatchlistStore


class CapturingPool:
    def start(self, worker):
        self.worker = worker


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


def _loading_tab(qtbot):
    bars = [PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10)]
    services = SimpleNamespace(
        prices=SimpleNamespace(get_history=lambda *args: bars),
        persistence=SimpleNamespace(
            us_trades=SimpleNamespace(query=lambda **kwargs: []),
            congress_trades=SimpleNamespace(query=lambda **kwargs: []),
        ),
    )
    pool = CapturingPool()
    tab = AnalysisTab(
        services, thread_pool=pool, watchlists=_watchlists("AAPL", "MSFT")
    )
    qtbot.addWidget(tab)
    tab._load_selected()
    return tab, pool


def test_unrelated_watchlist_edit_keeps_inflight_chart_load(qtbot):
    tab, pool = _loading_tab(qtbot)

    tab.set_watchlists(_watchlists("MSFT", "AAPL", "NVDA"))

    assert not tab.btn_load.isEnabled()
    assert tab.symbol_combo.currentText() == "AAPL"
    pool.worker.run()
    assert tab.status_label.text() == "AAPL: 1 bars, 0 trades"
    assert list(tab.chart.price_curve.getData()[1]) == [1.5]
    assert tab.btn_load.isEnabled()


def test_removing_selected_symbol_invalidates_inflight_chart_load(qtbot):
    tab, pool = _loading_tab(qtbot)

    tab.set_watchlists(_watchlists("MSFT"))

    assert tab.symbol_combo.currentText() == "MSFT"
    assert tab.btn_load.isEnabled()
    tab.status_label.setText("Select MSFT")
    pool.worker.run()
    assert tab.status_label.text() == "Select MSFT"
    assert tab.chart.price_curve is None

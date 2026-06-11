import pytest

from insider_scanner.gui import analysis_tab as mod
from insider_scanner.gui.analysis_tab import AnalysisTab, load_trades_for_ticker
from datetime import date

from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.core.prices.model import PriceBar


class CapturingPool:
    def __init__(self):
        self.worker = None

    def start(self, worker):
        self.worker = worker


@pytest.fixture
def tab(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL", "MSFT", "NVDA"])
    w = AnalysisTab()
    qtbot.addWidget(w)
    return w


def test_symbol_combo_populated_from_watchlist(tab):
    items = [tab.symbol_combo.itemText(i) for i in range(tab.symbol_combo.count())]
    assert items == ["AAPL", "MSFT", "NVDA"]


def test_has_chart_widget(tab):
    from insider_scanner.gui.price_chart import PriceChartWidget

    assert isinstance(tab.chart, PriceChartWidget)


def test_load_selected_updates_chart(tab, monkeypatch, qtbot):
    bars = [
        PriceBar("AAPL", date(2026, 1, 5), 1, 15, 0.5, 10.0, 10),
        PriceBar("AAPL", date(2026, 1, 7), 1, 15, 0.5, 12.0, 10),
    ]
    trades = [
        InsiderTrade(
            ticker="AAPL",
            trade_date=date(2026, 1, 5),
            trade_type="Buy",
            insider_name="A",
            shares=10,
            value=100,
        ),
    ]
    monkeypatch.setattr(mod, "get_price_history", lambda *a, **k: bars)
    monkeypatch.setattr(
        mod, "load_trades_for_ticker", lambda ticker: trades, raising=False
    )

    tab.symbol_combo.setCurrentText("AAPL")
    # Call the synchronous render path directly to avoid threading in the test
    tab._render(bars, trades)

    _, ys = tab.chart.price_curve.getData()
    assert list(ys) == [10.0, 12.0]
    assert len(tab.chart.buy_scatter.data) == 1


def test_load_trades_for_ticker_bounds_queries_and_trade_dates(monkeypatch):
    start = date(2026, 1, 1)
    end = date(2026, 12, 31)
    us_in_range = InsiderTrade(
        ticker="AAPL",
        filing_date=date(2027, 1, 2),
        trade_date=date(2026, 6, 1),
    )
    congress_in_range = CongressTrade(
        ticker="AAPL",
        filing_date=date(2027, 1, 2),
        trade_date=date(2026, 7, 1),
    )
    us_on_start = InsiderTrade(ticker="AAPL", trade_date=start)
    congress_on_end = CongressTrade(ticker="AAPL", trade_date=end)

    class Repository:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def query(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return self.rows

    us_repository = Repository(
        [
            us_in_range,
            us_on_start,
            InsiderTrade(
                ticker="AAPL",
                filing_date=start,
                trade_date=date(2025, 12, 31),
            ),
            InsiderTrade(ticker="AAPL", trade_date=None),
        ]
    )
    congress_repository = Repository(
        [
            congress_in_range,
            congress_on_end,
            CongressTrade(
                ticker="AAPL",
                filing_date=start,
                trade_date=date(2025, 12, 31),
            ),
            CongressTrade(
                ticker="AAPL",
                trade_date=date(2027, 1, 1),
            ),
            CongressTrade(
                ticker="MSFT",
                filing_date=start,
                trade_date=date(2026, 7, 1),
            ),
        ]
    )

    class Context:
        us_trades = us_repository
        congress_trades = congress_repository

        def close(self):
            pass

    monkeypatch.setattr(
        "insider_scanner.services.context.open_persistence",
        lambda: Context(),
    )

    trades = load_trades_for_ticker("aapl", start, end)

    assert trades == [us_in_range, us_on_start, congress_in_range, congress_on_end]
    assert us_repository.calls == [
        (
            (),
            {
                "ticker": "AAPL",
                "trade_start_date": start,
                "trade_end_date": end,
            },
        )
    ]
    assert congress_repository.calls == [
        ((), {"trade_start_date": start, "trade_end_date": end})
    ]


def test_analysis_tab_uses_injected_thread_pool(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])

    tab._load_selected()

    assert pool.worker is not None


def test_analysis_tab_discards_result_after_symbol_changes(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL", "MSFT"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    bars = [PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10)]
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: bars)
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])
    rendered = []
    monkeypatch.setattr(tab, "_render", lambda *args: rendered.append(args))

    tab.symbol_combo.setCurrentText("AAPL")
    tab._load_selected()
    payload = pool.worker.fn()
    tab.symbol_combo.setCurrentText("MSFT")
    tab.status_label.setText("Waiting for MSFT")
    tab._on_loaded(payload)

    assert rendered == []
    assert tab.status_label.text() == "Waiting for MSFT"


def test_analysis_tab_keeps_result_stale_after_switching_away_and_back(
    qtbot, monkeypatch
):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL", "MSFT"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    bars = [PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10)]
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: bars)
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])
    rendered = []
    monkeypatch.setattr(tab, "_render", lambda *args: rendered.append(args))

    tab.symbol_combo.setCurrentText("AAPL")
    tab._load_selected()
    payload = pool.worker.fn()
    tab.symbol_combo.setCurrentText("MSFT")
    tab.symbol_combo.setCurrentText("AAPL")
    tab._on_loaded(payload)

    assert rendered == []


def test_analysis_tab_discards_error_after_symbol_changes(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL", "MSFT"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])

    tab.symbol_combo.setCurrentText("AAPL")
    tab._load_selected()
    request_id = tab._request_id
    tab.symbol_combo.setCurrentText("MSFT")
    tab._on_error(
        request_id,
        "AAPL",
        (RuntimeError, RuntimeError("old request failed"), None),
    )

    assert "old request failed" not in tab.status_label.text()


def test_analysis_tab_keeps_error_stale_after_switching_away_and_back(
    qtbot, monkeypatch
):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL", "MSFT"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])

    tab.symbol_combo.setCurrentText("AAPL")
    tab._load_selected()
    request_id = tab._request_id
    tab.symbol_combo.setCurrentText("MSFT")
    tab.symbol_combo.setCurrentText("AAPL")
    tab.status_label.setText("Current selection is AAPL")
    tab._on_error(
        request_id,
        "AAPL",
        (RuntimeError, RuntimeError("old request failed"), None),
    )

    assert tab.status_label.text() == "Current selection is AAPL"


def test_request_cancellation_suppresses_late_analysis_result(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "load_watchlist", lambda: ["AAPL"])
    pool = CapturingPool()
    tab = AnalysisTab(thread_pool=pool)
    qtbot.addWidget(tab)
    bars = [PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10)]
    monkeypatch.setattr(mod, "get_price_history", lambda *args, **kwargs: bars)
    monkeypatch.setattr(mod, "load_trades_for_ticker", lambda *args: [])
    rendered = []
    monkeypatch.setattr(tab, "_render", lambda *args: rendered.append(args))

    tab._load_selected()
    payload = pool.worker.fn()
    tab.status_label.setText("Closing")
    tab.request_cancellation()
    tab._on_loaded(payload)

    assert rendered == []
    assert tab.status_label.text() == "Closing"
    assert tab._active_request is None

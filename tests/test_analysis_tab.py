import pytest

from insider_scanner.gui import analysis_tab as mod
from insider_scanner.gui.analysis_tab import AnalysisTab
from datetime import date

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.prices.model import PriceBar


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

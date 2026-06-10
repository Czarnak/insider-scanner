import pytest

from insider_scanner.gui import analysis_tab as mod
from insider_scanner.gui.analysis_tab import AnalysisTab


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

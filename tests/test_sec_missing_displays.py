"""SEC missing numbers stay distinct from reported zeros in user displays."""

from datetime import date
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from insider_scanner import cli
from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.prices.model import PriceBar
from insider_scanner.gui.price_chart import PriceChartWidget, build_marker_tooltip
from insider_scanner.gui.scan_tab import ScanTab


def _trade(missing: bool) -> InsiderTrade:
    trade = InsiderTrade(ticker="TEST", trade_date=date(2026, 1, 5))
    trade.shares_missing = missing
    trade.price_missing = missing
    trade.value_missing = missing
    return trade


@pytest.mark.parametrize("command", ["scan", "latest"])
def test_cli_labels_missing_numbers_and_preserves_zero(command, capsys):
    missing = _trade(True)
    zero = _trade(False)
    services = SimpleNamespace(
        us=SimpleNamespace(
            scan=lambda *a, **k: [missing, zero], latest=lambda **k: [missing, zero]
        )
    )
    args = cli.build_parser().parse_args(
        [command, "TEST"] if command == "scan" else [command]
    )
    args.func(args, services)

    lines = capsys.readouterr().out.splitlines()
    rows = [line for line in lines if "Other" in line]
    assert "N/A" in rows[0]
    assert "$" not in rows[0]
    assert "$" in rows[1]
    assert "N/A" not in rows[1]


def test_scan_table_and_details_distinguish_unknown_from_zero(qtbot):
    tab = ScanTab()
    qtbot.addWidget(tab)
    tab._trades = [_trade(True), _trade(False)]
    tab._display_trades(tab._trades)
    frame = tab.trades_model.dataframe
    for column in ("shares", "price", "value"):
        col = frame.columns.get_loc(column)
        assert (
            tab.trades_model.data(
                tab.trades_model.index(0, col), Qt.ItemDataRole.DisplayRole
            )
            == "N/A"
        )
        assert (
            tab.trades_model.data(
                tab.trades_model.index(1, col), Qt.ItemDataRole.DisplayRole
            )
            == "0.00"
        )
    tab._on_row_double_click(tab.trades_model.index(0, 0))
    assert "Shares: N/A" in tab.detail_label.text()
    assert "Price: N/A" in tab.detail_label.text()
    assert "Value: N/A" in tab.detail_label.text()


def test_chart_omits_unknown_fallback_price_and_labels_missing_tooltip(qtbot):
    chart = PriceChartWidget()
    qtbot.addWidget(chart)
    missing = _trade(True)
    zero = _trade(False)
    chart.set_trade_markers([missing, zero])
    _, ys = chart.other_scatter.getData()
    assert list(ys) == [0]
    assert "Shares: N/A" in build_marker_tooltip(missing)
    assert "Value: N/A" in build_marker_tooltip(missing)
    assert "Shares: 0" in build_marker_tooltip(zero)
    assert "Value: $0" in build_marker_tooltip(zero)

    chart.set_price_data([PriceBar("TEST", date(2026, 1, 5), 1, 2, 1, 2, 10)])
    chart.set_trade_markers([missing])
    _, ys = chart.other_scatter.getData()
    assert list(ys) == [2]

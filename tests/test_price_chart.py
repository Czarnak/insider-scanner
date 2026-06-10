from datetime import date, datetime, timezone

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.gui.price_chart import bars_to_xy


def _ts(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def test_bars_to_xy_uses_posix_timestamps_and_plot_close():
    bars = [
        PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10, adjusted_close=1.4),
        PriceBar("AAPL", date(2026, 1, 6), 1, 2, 0.5, 1.8, 10),
    ]
    xs, ys = bars_to_xy(bars)
    assert xs == [_ts(date(2026, 1, 5)), _ts(date(2026, 1, 6))]
    assert ys == [1.4, 1.8]  # adj_close then raw close


def test_bars_to_xy_empty():
    assert bars_to_xy([]) == ([], [])


import pytest

from insider_scanner.gui.price_chart import PriceChartWidget


@pytest.fixture
def chart(qtbot):
    w = PriceChartWidget()
    qtbot.addWidget(w)
    return w


def test_set_price_data_draws_one_line(chart):
    bars = [
        PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10),
        PriceBar("AAPL", date(2026, 1, 6), 1, 2, 0.5, 1.8, 10),
    ]
    chart.set_price_data(bars)
    # exactly one price curve is present after setting data
    assert chart.price_curve is not None
    xs, ys = chart.price_curve.getData()
    assert list(ys) == [1.5, 1.8]


def test_set_price_data_replaces_previous(chart):
    chart.set_price_data([PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10)])
    chart.set_price_data([PriceBar("AAPL", date(2026, 1, 6), 1, 2, 0.5, 1.8, 10)])
    _, ys = chart.price_curve.getData()
    assert list(ys) == [1.8]  # not appended to the old curve


from insider_scanner.core.models import InsiderTrade
from insider_scanner.gui.price_chart import build_marker_tooltip, marker_y_for_trade


def _bars():
    return [
        PriceBar("AAPL", date(2026, 1, 5), 1, 15, 0.5, 10.0, 10),
        PriceBar("AAPL", date(2026, 1, 7), 1, 15, 0.5, 12.0, 10),
    ]


def test_marker_y_uses_close_on_trade_date():
    t = InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 5), price=9.9)
    assert marker_y_for_trade(t, _bars()) == 10.0


def test_marker_y_falls_back_to_prior_trading_day():
    # trade on a non-trading day (Jan 6) -> use prior bar's close (Jan 5)
    t = InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 6), price=9.9)
    assert marker_y_for_trade(t, _bars()) == 10.0


def test_marker_y_falls_back_to_trade_price_when_no_bars():
    t = InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 5), price=9.9)
    assert marker_y_for_trade(t, []) == 9.9


def test_build_marker_tooltip_contains_key_fields():
    t = InsiderTrade(
        ticker="AAPL", trade_date=date(2026, 1, 5), trade_type="Buy",
        insider_name="Jane Doe", insider_title="CFO", shares=1000, value=10000.0,
    )
    tip = build_marker_tooltip(t)
    assert "Jane Doe" in tip
    assert "CFO" in tip
    assert "Buy" in tip
    assert "1,000" in tip      # shares formatted with thousands sep
    assert "10,000" in tip     # value formatted
    assert "2026-01-05" in tip


def test_set_trade_markers_splits_buy_and_sell(chart):
    chart.set_price_data(_bars())
    trades = [
        InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 5), trade_type="Buy",
                     insider_name="A", shares=10, value=100),
        InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 7), trade_type="Sell",
                     insider_name="B", shares=20, value=200),
    ]
    chart.set_trade_markers(trades)
    assert len(chart.buy_scatter.data) == 1
    assert len(chart.sell_scatter.data) == 1


def test_set_trade_markers_replaces_previous(chart):
    chart.set_price_data(_bars())
    chart.set_trade_markers([
        InsiderTrade(ticker="AAPL", trade_date=date(2026, 1, 5), trade_type="Buy"),
    ])
    chart.set_trade_markers([])  # clear
    assert len(chart.buy_scatter.data) == 0
    assert len(chart.sell_scatter.data) == 0

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

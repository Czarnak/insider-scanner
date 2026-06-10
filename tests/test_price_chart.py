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

"""pyqtgraph price chart with insider-trade markers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from insider_scanner.core.prices.model import PriceBar


def _to_timestamp(d: date) -> float:
    """Convert a date to a UTC POSIX timestamp for pyqtgraph's DateAxisItem."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def bars_to_xy(bars: list[PriceBar]) -> tuple[list[float], list[float]]:
    """Return (x_timestamps, y_plot_close) parallel lists for the price line."""
    xs = [_to_timestamp(b.date) for b in bars]
    ys = [b.plot_close for b in bars]
    return xs, ys

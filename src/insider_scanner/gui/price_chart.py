"""pyqtgraph price chart with insider-trade markers."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

from insider_scanner.core.models import InsiderTrade, CongressTrade
from insider_scanner.core.prices.model import PriceBar


def _to_timestamp(d: date) -> float:
    """Convert a date to a UTC POSIX timestamp for pyqtgraph's DateAxisItem."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def bars_to_xy(bars: list[PriceBar]) -> tuple[list[float], list[float]]:
    """Return (x_timestamps, y_plot_close) parallel lists for the price line."""
    xs = [_to_timestamp(b.date) for b in bars]
    ys = [b.plot_close for b in bars]
    return xs, ys


class PriceChartWidget(QWidget):
    """Date-axis price line with pan/zoom and a crosshair readout."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        pg.setConfigOptions(antialias=True)

        self._bars: list[PriceBar] = []
        self.price_curve: pg.PlotDataItem | None = None

        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.plot_widget.setLabel("left", "Price")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.addLegend()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot_widget)

        self._vline = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#888", width=1)
        )
        self._hline = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen("#888", width=1)
        )
        self.plot_widget.addItem(self._vline, ignoreBounds=True)
        self.plot_widget.addItem(self._hline, ignoreBounds=True)
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        self.buy_scatter = pg.ScatterPlotItem(
            size=12,
            symbol="t1",
            brush=pg.mkBrush("#2ca02c"),
            pen=pg.mkPen("#155b15"),
            hoverable=True,
            tip=lambda x, y, data: data.get("tip", "") if data else "",
        )
        self.sell_scatter = pg.ScatterPlotItem(
            size=12,
            symbol="t",
            brush=pg.mkBrush("#d62728"),
            pen=pg.mkPen("#7a1718"),
            hoverable=True,
            tip=lambda x, y, data: data.get("tip", "") if data else "",
        )
        self.plot_widget.addItem(self.buy_scatter)
        self.plot_widget.addItem(self.sell_scatter)

    def set_trade_markers(self, trades: list[InsiderTrade | CongressTrade]) -> None:
        """Overlay buy/sell markers on the current price line."""
        buys, sells = [], []
        for t in trades:
            if t.trade_date is None:
                continue
            spot = {
                "pos": (_to_timestamp(t.trade_date), marker_y_for_trade(t, self._bars)),
                "data": {"tip": build_marker_tooltip(t)},
            }
            (buys if is_buy(t) else sells).append(spot)
        self.buy_scatter.setData(
            [b["pos"][0] for b in buys],
            [b["pos"][1] for b in buys],
            data=[b["data"] for b in buys],
        )
        self.sell_scatter.setData(
            [s["pos"][0] for s in sells],
            [s["pos"][1] for s in sells],
            data=[s["data"] for s in sells],
        )

    def set_price_data(self, bars: list[PriceBar]) -> None:
        """Render the price line, replacing any previous curve."""
        self._bars = list(bars)
        xs, ys = bars_to_xy(self._bars)
        if self.price_curve is None:
            self.price_curve = self.plot_widget.plot(
                xs, ys, pen=pg.mkPen("#1f77b4", width=2), name="Price"
            )
        else:
            self.price_curve.setData(xs, ys)
        self.plot_widget.enableAutoRange()

    def _on_mouse_moved(self, pos) -> None:
        vb = self.plot_widget.getPlotItem().vb
        if not self.plot_widget.sceneBoundingRect().contains(pos):
            return
        point = vb.mapSceneToView(pos)
        self._vline.setPos(point.x())
        self._hline.setPos(point.y())


BUY_TYPES = {"Buy", "Purchase", "Exercise"}


def marker_y_for_trade(
    trade: InsiderTrade | CongressTrade, bars: list[PriceBar]
) -> float:
    """Y position for a trade marker: close on the trade date, else the
    most recent prior trading day's close, else the trade's own price."""
    price = getattr(trade, "price", 0.0)
    if not bars or trade.trade_date is None:
        return price
    best: float | None = None
    for b in bars:  # bars are date-sorted ascending
        if b.date <= trade.trade_date:
            best = b.plot_close
        else:
            break
    if best is not None:
        return best
    return bars[0].plot_close if bars else price


def is_buy(trade: InsiderTrade | CongressTrade) -> bool:
    return trade.trade_type in BUY_TYPES


def build_marker_tooltip(trade: InsiderTrade | CongressTrade) -> str:
    """Multi-line hover text for an insider trade marker."""
    who = (
        getattr(trade, "insider_name", None)
        or getattr(trade, "congress_member", None)
        or getattr(trade, "official_name", "Unknown")
    )
    lines = [
        f"{trade.trade_type}  {trade.ticker}",
        f"{who}"
        + (
            f" — {trade.insider_title}" if getattr(trade, "insider_title", None) else ""
        ),
        f"Date: {trade.trade_date.isoformat() if trade.trade_date else 'n/a'}",
    ]
    if hasattr(trade, "shares"):
        lines.append(f"Shares: {trade.shares:,.0f}")
        lines.append(f"Value: ${trade.value:,.0f}")
    elif hasattr(trade, "amount_range"):
        lines.append(f"Value: {trade.amount_range}")
    return "\n".join(lines)

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


import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget


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

        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#888", width=1))
        self.plot_widget.addItem(self._vline, ignoreBounds=True)
        self.plot_widget.addItem(self._hline, ignoreBounds=True)
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

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

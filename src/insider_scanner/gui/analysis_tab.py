"""Analysis tab: price timeline with insider-trade overlay (US tickers)."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QThreadPool, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.core.prices import get_price_history
from insider_scanner.gui.price_chart import PriceChartWidget
from insider_scanner.utils.config import load_watchlist
from insider_scanner.utils.threading import Worker


from insider_scanner.core.models import InsiderTrade, CongressTrade
from insider_scanner.core.prices.model import PriceBar


def load_trades_for_ticker(ticker: str) -> list[InsiderTrade | CongressTrade]:
    """Load stored insider trades for a US ticker from the Phase 1 DB."""
    from insider_scanner.services.context import open_persistence

    ctx = open_persistence()
    try:
        us_trades = list(ctx.us_trades.query(ticker=ticker.upper()))
        
        # Load Congress trades and filter by ticker
        all_congress = ctx.congress_trades.query()
        congress = [t for t in all_congress if t.ticker == ticker.upper()]
        
        # Return a unified list
        return us_trades + congress
    finally:
        ctx.close()


class AnalysisTab(QWidget):
    """Pick a US ticker -> price line + insider markers from local data."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._refresh_symbols()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Symbol:"))
        self.symbol_combo = QComboBox()
        self.symbol_combo.setMinimumWidth(120)
        controls.addWidget(self.symbol_combo)

        self.btn_load = QPushButton("Load Chart")
        self.btn_load.clicked.connect(self._load_selected)
        controls.addWidget(self.btn_load)

        self.btn_refresh = QPushButton("Refresh Symbols")
        self.btn_refresh.clicked.connect(self._refresh_symbols)
        controls.addWidget(self.btn_refresh)

        controls.addStretch(1)
        self.status_label = QLabel("Select a symbol")
        controls.addWidget(self.status_label)
        layout.addLayout(controls)

        self.chart = PriceChartWidget()
        layout.addWidget(self.chart, stretch=1)

    def _refresh_symbols(self) -> None:
        current = self.symbol_combo.currentText()
        self.symbol_combo.clear()
        self.symbol_combo.addItems(load_watchlist())
        if current:
            idx = self.symbol_combo.findText(current)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)

    def _load_selected(self) -> None:
        symbol = self.symbol_combo.currentText().strip().upper()
        if not symbol:
            self.status_label.setText("No symbol selected")
            return
        self.btn_load.setEnabled(False)
        self.status_label.setText(f"Loading {symbol}…")

        end = date.today()
        start = end - timedelta(days=365 * 2)

        def work():
            bars = get_price_history(symbol, start, end)
            trades = load_trades_for_ticker(symbol)
            return bars, trades

        worker = Worker(work)
        worker.signals.result.connect(self._on_loaded)
        worker.signals.error.connect(self._on_error)
        worker.signals.finished.connect(self._on_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _on_finished(self) -> None:
        self.btn_load.setEnabled(True)

    @Slot(object)
    def _on_loaded(self, payload) -> None:
        bars, trades = payload
        self._render(bars, trades)
        self.status_label.setText(
            f"{self.symbol_combo.currentText()}: {len(bars)} bars, {len(trades)} trades"
        )

    @Slot(tuple)
    def _on_error(self, error_info) -> None:
        self.status_label.setText(f"Load failed: {error_info[1]}")

    def _render(self, bars: list[PriceBar], trades: list[InsiderTrade | CongressTrade]) -> None:
        self.chart.set_price_data(bars)
        self.chart.set_trade_markers(trades)

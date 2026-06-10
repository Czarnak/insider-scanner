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
        pass

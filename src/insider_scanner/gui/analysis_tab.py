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

from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.core.prices.model import PriceBar
from insider_scanner.gui.price_chart import PriceChartWidget
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.application import ApplicationServices
from insider_scanner.services.context import PersistenceContext
from insider_scanner.utils.threading import LatestRequest


def load_trades_for_ticker(
    ticker: str, start: date, end: date, *, persistence: PersistenceContext
) -> list[InsiderTrade | CongressTrade]:
    """Load ticker-filtered overlay trades using shared application persistence."""
    normalized_ticker = ticker.strip().upper()
    us_trades = persistence.us_trades.query(
        ticker=normalized_ticker,
        trade_start_date=start,
        trade_end_date=end,
    )
    congress_trades = persistence.congress_trades.query(
        ticker=normalized_ticker,
        trade_start_date=start,
        trade_end_date=end,
    )
    return [
        trade
        for trade in [*us_trades, *congress_trades]
        if trade.ticker == normalized_ticker
        and trade.trade_date is not None
        and start <= trade.trade_date <= end
    ]


class AnalysisTab(QWidget):
    """Pick a US ticker -> price line + insider markers from local data."""

    def __init__(
        self,
        services: ApplicationServices | None = None,
        parent=None,
        *,
        thread_pool: QThreadPool | None = None,
        watchlists: WatchlistStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._services = services
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._watchlists = watchlists if watchlists is not None else WatchlistStore()
        self._requests = LatestRequest(self, self._thread_pool)
        self._active_symbol: str | None = None
        self._build_ui()
        self._refresh_symbols()

    def set_watchlists(self, store: WatchlistStore) -> None:
        """Refresh chart symbols from the application's managed watchlists."""
        self._watchlists = store
        self._refresh_symbols()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Symbol:"))
        self.symbol_combo = QComboBox()
        self.symbol_combo.setMinimumWidth(120)
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
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
        signals_blocked = self.symbol_combo.blockSignals(True)
        try:
            self.symbol_combo.clear()
            self.symbol_combo.addItems(self._watchlists.us_symbols())
            if current:
                idx = self.symbol_combo.findText(current)
                if idx >= 0:
                    self.symbol_combo.setCurrentIndex(idx)
        finally:
            self.symbol_combo.blockSignals(signals_blocked)
        selected = self.symbol_combo.currentText()
        if selected != current:
            self._on_symbol_changed(selected)

    def _load_selected(self) -> None:
        if self._services is None:
            self.status_label.setText("Price analysis requires application services")
            return
        symbol = self.symbol_combo.currentText().strip().upper()
        if not symbol:
            self.status_label.setText("No symbol selected")
            return
        self.btn_load.setEnabled(False)
        self.status_label.setText(f"Loading {symbol}…")

        end = date.today()
        start = end - timedelta(days=365 * 2)
        self._active_symbol = symbol
        services = self._services

        def work():
            bars = services.prices.get_history(symbol, start, end)
            trades = load_trades_for_ticker(
                symbol, start, end, persistence=services.persistence
            )
            return symbol, bars, trades

        self._requests.start(
            work,
            on_result=self._on_loaded,
            on_error=self._on_error,
            on_finished=self._on_finished,
        )

    def request_cancellation(self) -> None:
        """Invalidate pending callbacks during window shutdown."""
        self._invalidate_active_request()

    @Slot(str)
    def _on_symbol_changed(self, _symbol: str) -> None:
        if self._active_symbol is None:
            return
        self._invalidate_active_request()
        self.btn_load.setEnabled(True)

    @Slot(object)
    def _on_loaded(self, payload) -> None:
        symbol, bars, trades = payload
        self._render(bars, trades)
        self.status_label.setText(f"{symbol}: {len(bars)} bars, {len(trades)} trades")

    def _on_error(self, error_info) -> None:
        self.status_label.setText(f"Load failed: {error_info[1]}")

    def _on_finished(self) -> None:
        self._active_symbol = None
        self.btn_load.setEnabled(True)

    def _invalidate_active_request(self) -> None:
        self._requests.invalidate()
        self._active_symbol = None

    def _render(
        self, bars: list[PriceBar], trades: list[InsiderTrade | CongressTrade]
    ) -> None:
        self.chart.set_price_data(bars)
        self.chart.set_trade_markers(trades)

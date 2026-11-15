"""Analysis tab: price timeline with insider-trade overlay (US tickers)."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial

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
from insider_scanner.utils.threading import Worker


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
        self._request_id = 0
        self._active_request: tuple[int, str] | None = None
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
        self._request_id += 1
        request_id = self._request_id
        self._active_request = (request_id, symbol)
        services = self._services

        def work():
            bars = services.prices.get_history(symbol, start, end)
            trades = load_trades_for_ticker(
                symbol, start, end, persistence=services.persistence
            )
            return request_id, symbol, bars, trades

        worker = Worker(work)
        worker.signals.result.connect(self._on_loaded)
        worker.signals.error.connect(partial(self._on_error, request_id, symbol))
        worker.signals.finished.connect(partial(self._on_finished, request_id))
        self._thread_pool.start(worker)

    def request_cancellation(self) -> None:
        """Invalidate pending callbacks during window shutdown."""
        self._invalidate_active_request()

    @Slot(str)
    def _on_symbol_changed(self, _symbol: str) -> None:
        if self._active_request is None:
            return
        self._invalidate_active_request()
        self.btn_load.setEnabled(True)

    @Slot(object)
    def _on_loaded(self, payload) -> None:
        request_id, symbol, bars, trades = payload
        if not self._is_current_request(request_id, symbol):
            return
        self._render(bars, trades)
        self.status_label.setText(f"{symbol}: {len(bars)} bars, {len(trades)} trades")

    def _on_error(self, request_id: int, symbol: str, error_info) -> None:
        if not self._is_current_request(request_id, symbol):
            return
        self.status_label.setText(f"Load failed: {error_info[1]}")

    def _on_finished(self, request_id: int) -> None:
        if self._active_request is None or self._active_request[0] != request_id:
            return
        self._active_request = None
        self.btn_load.setEnabled(True)

    def _is_current_request(self, request_id: int, symbol: str) -> bool:
        current_symbol = self.symbol_combo.currentText().strip().upper()
        return self._active_request == (request_id, symbol) and current_symbol == symbol

    def _invalidate_active_request(self) -> None:
        self._request_id += 1
        self._active_request = None

    def _render(
        self, bars: list[PriceBar], trades: list[InsiderTrade | CongressTrade]
    ) -> None:
        self.chart.set_price_data(bars)
        self.chart.set_trade_markers(trades)

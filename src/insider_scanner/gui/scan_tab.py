"""Scan tab: ticker search, source selection, filters, results table, EDGAR links."""

from __future__ import annotations

import webbrowser
from datetime import date
from threading import Event

import pandas as pd
from PySide6.QtCore import QDate, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.core.fetch_outcome import (
    FetchIssue,
    FetchResult,
    FetchStatus,
    as_fetch_result,
    describe_incomplete,
)
from insider_scanner.gui.sec_backfill_info import build_sec_backfill_info_group
from insider_scanner.gui.widgets import SortableTableModel
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.common import ScanError
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import LatestRequest
from insider_scanner.utils.trade_display import format_reported_number

log = get_logger("scan_tab")


class ScanTab(QWidget):
    """Full scan workflow: enter ticker → select sources → scan → view → EDGAR."""

    dataChanged = Signal()

    def __init__(
        self,
        service=None,
        parent=None,
        *,
        thread_pool: QThreadPool | None = None,
        watchlists: WatchlistStore | None = None,
    ):
        super().__init__(parent)
        self._service = service
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._scan_requests = LatestRequest(self, self._thread_pool)
        self._cik_requests = LatestRequest(self, self._thread_pool)
        self._watchlists = watchlists if watchlists is not None else WatchlistStore()
        self._trades: list = []
        self._cancel_event = Event()
        self._build_ui()
        self.ticker_edit.textChanged.connect(self._invalidate_cik)

    def set_watchlists(self, store: WatchlistStore) -> None:
        """Use the application's current managed watchlists for the next scan."""
        self._watchlists = store

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.addWidget(self._build_search_group())
        root.addWidget(build_sec_backfill_info_group())
        root.addLayout(self._build_filter_row())
        root.addLayout(self._build_progress_row())
        root.addWidget(self._build_results_splitter(), stretch=1)

    def _build_search_group(self) -> QGroupBox:
        search_grp = QGroupBox("Search")
        search_l = QHBoxLayout(search_grp)

        search_l.addWidget(QLabel("Ticker:"))
        self.ticker_edit = QLineEdit()
        self.ticker_edit.setPlaceholderText("AAPL")
        self.ticker_edit.setMaximumWidth(120)
        self.ticker_edit.returnPressed.connect(self._run_scan)
        search_l.addWidget(self.ticker_edit)

        self.btn_scan = QPushButton("Scan Ticker")
        self.btn_scan.clicked.connect(self._run_scan)
        search_l.addWidget(self.btn_scan)

        self.btn_latest = QPushButton("Latest Trades (all)")
        self.btn_latest.clicked.connect(self._run_latest)
        search_l.addWidget(self.btn_latest)

        search_l.addWidget(QLabel("Count:"))
        self.latest_count_spin = QSpinBox()
        self.latest_count_spin.setRange(10, 500)
        self.latest_count_spin.setValue(100)
        self.latest_count_spin.setSingleStep(10)
        self.latest_count_spin.setMaximumWidth(80)
        self.latest_count_spin.setToolTip("Number of latest trades to fetch")
        search_l.addWidget(self.latest_count_spin)

        self.btn_watchlist = QPushButton("Watchlist Scan")
        self.btn_watchlist.clicked.connect(self._run_watchlist)
        self.btn_watchlist.setToolTip("Scan US companies from your Watchlists")
        search_l.addWidget(self.btn_watchlist)

        search_l.addStretch()
        return search_grp

    def _build_filter_row(self) -> QHBoxLayout:
        filter_row = QHBoxLayout()
        filter_row.addWidget(self._build_sources_group())
        filter_row.addWidget(self._build_date_range_group())
        filter_row.addWidget(self._build_filters_group())
        return filter_row

    def _build_sources_group(self) -> QGroupBox:
        src_grp = QGroupBox("Sources")
        src_l = QHBoxLayout(src_grp)
        self.chk_secform4 = QCheckBox("secform4.com")
        self.chk_secform4.setChecked(True)
        self.chk_openinsider = QCheckBox("openinsider.com")
        self.chk_openinsider.setChecked(True)
        src_l.addWidget(self.chk_secform4)
        src_l.addWidget(self.chk_openinsider)
        return src_grp

    def _build_date_range_group(self) -> QGroupBox:
        date_grp = QGroupBox("Filing Date Range")
        date_l = QHBoxLayout(date_grp)

        self.chk_use_dates = QCheckBox("Enable")
        self.chk_use_dates.setChecked(False)
        self.chk_use_dates.toggled.connect(self._on_date_toggle)
        date_l.addWidget(self.chk_use_dates)

        date_l.addWidget(QLabel("Start:"))
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_date.setDate(QDate.currentDate().addMonths(-6))
        self.start_date.setEnabled(False)
        date_l.addWidget(self.start_date)

        date_l.addWidget(QLabel("End:"))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date.setDate(QDate.currentDate())
        self.end_date.setEnabled(False)
        date_l.addWidget(self.end_date)

        return date_grp

    def _build_filters_group(self) -> QGroupBox:
        filt_grp = QGroupBox("Filters")
        filt_l = QHBoxLayout(filt_grp)

        filt_l.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All", "Buy", "Sell", "Exercise", "Other"])
        filt_l.addWidget(self.type_combo)

        filt_l.addWidget(QLabel("Min value ($):"))
        self.min_value_spin = QDoubleSpinBox()
        self.min_value_spin.setRange(0, 1_000_000_000)
        self.min_value_spin.setDecimals(0)
        self.min_value_spin.setSingleStep(10_000)
        self.min_value_spin.setValue(0)
        filt_l.addWidget(self.min_value_spin)

        self.chk_congress = QCheckBox("Congress only")
        filt_l.addWidget(self.chk_congress)

        self.btn_filter = QPushButton("Apply Filters")
        self.btn_filter.clicked.connect(self._apply_filters)
        filt_l.addWidget(self.btn_filter)

        return filt_grp

    def _build_progress_row(self) -> QHBoxLayout:
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        progress_row.addWidget(self.progress)

        self.btn_stop = QPushButton("Stop Scan")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._stop_scan)
        self.btn_stop.setMaximumWidth(100)
        progress_row.addWidget(self.btn_stop)

        return progress_row

    def _build_results_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        table_widget = QWidget()
        table_l = QVBoxLayout(table_widget)
        table_l.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("No scan results yet")
        table_l.addWidget(self.status_label)

        self.trades_model = SortableTableModel()
        self.trades_table = QTableView()
        self.trades_table.setModel(self.trades_model)
        self.trades_table.setSortingEnabled(True)
        self.trades_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.trades_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.trades_table.doubleClicked.connect(self._on_row_double_click)
        table_l.addWidget(self.trades_table)

        splitter.addWidget(table_widget)

        # Bottom: action buttons + detail
        bottom = QWidget()
        bottom_l = QHBoxLayout(bottom)

        self.btn_edgar = QPushButton("Open EDGAR Filing")
        self.btn_edgar.clicked.connect(self._open_edgar)
        self.btn_edgar.setEnabled(False)
        bottom_l.addWidget(self.btn_edgar)

        self.btn_save = QPushButton("Save Results")
        self.btn_save.clicked.connect(self._save_results)
        self.btn_save.setEnabled(False)
        bottom_l.addWidget(self.btn_save)

        self.btn_resolve_cik = QPushButton("Resolve CIK")
        self.btn_resolve_cik.clicked.connect(self._resolve_cik)
        bottom_l.addWidget(self.btn_resolve_cik)

        bottom_l.addStretch()

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        bottom_l.addWidget(self.detail_label, stretch=1)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)

        return splitter

    # ------------------------------------------------------------------
    # Date range helpers
    # ------------------------------------------------------------------

    def _on_date_toggle(self, checked: bool):
        self.start_date.setEnabled(checked)
        self.end_date.setEnabled(checked)

    def _get_start_date(self) -> date | None:
        if not self.chk_use_dates.isChecked():
            return None
        qd = self.start_date.date()
        return date(qd.year(), qd.month(), qd.day())

    def _get_end_date(self) -> date | None:
        if not self.chk_use_dates.isChecked():
            return None
        qd = self.end_date.date()
        return date(qd.year(), qd.month(), qd.day())

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _set_scan_buttons_enabled(self, enabled: bool):
        """Enable or disable all scan-triggering buttons."""
        self.btn_scan.setEnabled(enabled)
        self.btn_latest.setEnabled(enabled)
        self.btn_watchlist.setEnabled(enabled)
        # Stop button is opposite: visible when scanning
        self.btn_stop.setVisible(not enabled)

    def _stop_scan(self):
        """Signal the running watchlist scan to stop."""
        self._cancel_event.set()
        self.btn_stop.setEnabled(False)
        self.progress.setFormat("Stopping...")

    def request_cancellation(self) -> None:
        """Request cooperative cancellation without blocking the GUI thread."""
        self._cancel_event.set()
        self._scan_requests.invalidate()
        self._cik_requests.invalidate()

    def _run_scan(self):
        ticker = self.ticker_edit.text().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Input", "Enter a ticker symbol.")
            return

        self._cancel_event.set()
        self._cancel_event = Event()
        cancel = self._cancel_event
        service = self._service
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat(f"Scanning {ticker}...")

        use_sf4 = self.chk_secform4.isChecked()
        use_oi = self.chk_openinsider.isChecked()
        sources = tuple(
            source
            for source, enabled in (
                ("secform4", use_sf4),
                ("openinsider", use_oi),
            )
            if enabled
        )
        if not sources:
            self._set_scan_buttons_enabled(True)
            self.progress.setVisible(False)
            QMessageBox.warning(self, "Sources", "Select at least one source.")
            return
        sd = self._get_start_date()
        ed = self._get_end_date()

        def work():
            return service.scan(
                ticker,
                sources=sources,
                start_date=sd,
                end_date=ed,
                use_cache=True,
                cancelled=cancel.is_set,
            )

        self._scan_requests.start(
            work,
            on_result=self._on_scan_done,
            on_error=self._on_scan_error,
        )

    def _run_latest(self):
        self._cancel_event.set()
        self._cancel_event = Event()
        cancel = self._cancel_event
        service = self._service
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Fetching latest trades...")

        sd = self._get_start_date()
        ed = self._get_end_date()
        count = self.latest_count_spin.value()

        def work():
            return service.latest(
                count=count,
                sources=("openinsider",),
                start_date=sd,
                end_date=ed,
                use_cache=True,
                cancelled=cancel.is_set,
            )

        self._scan_requests.start(
            work,
            on_result=self._on_scan_done,
            on_error=self._on_scan_error,
        )

    def _run_watchlist(self):
        tickers = self._watchlists.us_symbols()
        if not tickers:
            QMessageBox.warning(
                self,
                "Watchlist",
                "Add US companies on the Watchlists page before scanning.",
            )
            return

        self._cancel_event.set()
        self._cancel_event = Event()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(tickers))
        self.progress.setValue(0)
        self.progress.setFormat("Watchlist: %v/%m tickers scanned")

        use_sf4 = self.chk_secform4.isChecked()
        use_oi = self.chk_openinsider.isChecked()
        sd = self._get_start_date()
        ed = self._get_end_date()
        cancel = self._cancel_event
        service = self._service
        sources = tuple(
            source
            for source, enabled in (
                ("secform4", use_sf4),
                ("openinsider", use_oi),
            )
            if enabled
        )
        if not sources:
            self.progress.setVisible(False)
            self._set_scan_buttons_enabled(True)
            QMessageBox.warning(self, "Sources", "Select at least one source.")
            return

        def work(progress_callback):
            from insider_scanner.core.merger import merge_trades

            all_lists = []
            issues: list[FetchIssue] = []
            for i, ticker in enumerate(tickers):
                if cancel.is_set():
                    break
                try:
                    result = as_fetch_result(
                        service.scan(
                            ticker,
                            sources=sources,
                            start_date=sd,
                            end_date=ed,
                            use_cache=True,
                            cancelled=cancel.is_set,
                        )
                    )
                except ScanError:
                    log.exception("Watchlist scan failed for %s", ticker)
                    issues.append(FetchIssue("scan_failed", identifier=ticker))
                    progress_callback(i + 1)
                    continue
                all_lists.append(result)
                issues.extend(result.issues)
                if not result.complete and not result.issues:
                    issues.append(FetchIssue(result.status.value, identifier=ticker))
                progress_callback(i + 1)

            if cancel.is_set():
                issues.append(FetchIssue("cancelled"))
            trades = merge_trades(*all_lists)
            status = (
                FetchStatus.UNSUPPORTED
                if not trades
                and issues
                and all(not issue.retryable for issue in issues)
                else None
            )
            return FetchResult(trades, status=status, issues=issues)

        self._scan_requests.start(
            work,
            on_result=self._on_scan_done,
            on_error=self._on_scan_error,
            on_progress=self.progress.setValue,
        )

    @Slot(object)
    def _on_scan_done(self, trades):
        cancelled = self._cancel_event.is_set()
        self._cancel_event.clear()
        self._trades = trades
        self.progress.setVisible(False)
        self.btn_stop.setEnabled(True)
        self._set_scan_buttons_enabled(True)
        self.btn_save.setEnabled(True)
        self._display_trades(trades)
        if cancelled:
            self.status_label.setText(
                self.status_label.text() + "  (scan was cancelled)"
            )
        self.dataChanged.emit()

    @Slot(tuple)
    def _on_scan_error(self, error_info):
        self._cancel_event.clear()
        self.progress.setVisible(False)
        self.btn_stop.setEnabled(True)
        self._set_scan_buttons_enabled(True)
        exc_type, exc_value, _ = error_info
        log.error(
            "Scan failed",
            exc_info=(exc_type, exc_value, error_info[2]),
        )
        self.dataChanged.emit()
        QMessageBox.critical(
            self,
            "Scan Error",
            "Scan failed. See logs for details.",
        )

    # ------------------------------------------------------------------
    # Display + filter
    # ------------------------------------------------------------------

    def _display_trades(self, trades):
        from insider_scanner.core.edgar import build_edgar_url_for_trade
        from insider_scanner.core.merger import trades_to_dataframe

        # Auto-generate edgar_url for trades that don't have one
        for trade in trades:
            if not trade.edgar_url:
                trade.edgar_url = build_edgar_url_for_trade(trade)

        df = trades_to_dataframe(trades)
        if df.empty:
            self._set_results_status("No trades found.")
            self.trades_model.set_dataframe(pd.DataFrame())
            return

        for column in ("shares", "price", "value"):
            df[column] = (
                df[column].astype(object).where(~df[f"{column}_missing"], "N/A")
            )

        # Select display columns
        display_cols = [
            c
            for c in [
                "filing_date",
                "trade_date",
                "ticker",
                "insider_name",
                "insider_title",
                "trade_type",
                "shares",
                "price",
                "value",
                "source",
                "edgar_url",
            ]
            if c in df.columns
        ]
        self.trades_model.set_dataframe(df[display_cols])
        congress_count = sum(1 for t in trades if t.is_congress)
        self._set_results_status(
            f"{len(trades)} trades found  |  {congress_count} congress-flagged"
        )

    def _set_results_status(self, text):
        if warning := describe_incomplete(self._trades):
            text += f"  |  {warning}"
        if isinstance(self._trades, FetchResult):
            failed = tuple(
                dict.fromkeys(
                    issue.identifier
                    for issue in self._trades.issues
                    if issue.reason == "scan_failed"
                )
            )
            if failed:
                noun = "ticker" if len(failed) == 1 else "tickers"
                text += f"  |  {len(failed)} {noun} failed: {', '.join(failed)}"
        self.status_label.setText(text)

    def _apply_filters(self):
        if not self._trades:
            return

        from insider_scanner.core.merger import filter_trades

        trade_type = self.type_combo.currentText()
        if trade_type == "All":
            trade_type = None

        min_val = self.min_value_spin.value()
        if min_val == 0:
            min_val = None

        filtered = filter_trades(
            self._trades,
            trade_type=trade_type,
            min_value=min_val,
            congress_only=self.chk_congress.isChecked(),
            since=self._get_start_date(),
            until=self._get_end_date(),
        )
        self._display_trades(filtered)

    # ------------------------------------------------------------------
    # EDGAR + details
    # ------------------------------------------------------------------

    def _on_row_double_click(self, index):
        row = index.row()
        source_index = self.trades_model.mapToSource(index)
        if row < len(self._trades):
            trade = self._trades[source_index.row()]
            shares = format_reported_number(trade.shares, missing=trade.shares_missing)
            price = format_reported_number(
                trade.price, missing=trade.price_missing, decimals=2, currency=True
            )
            value = format_reported_number(
                trade.value, missing=trade.value_missing, currency=True
            )
            detail = (
                f"Name: {trade.insider_name}  |  Title: {trade.insider_title}\n"
                f"Type: {trade.trade_type}  |  Shares: {shares}  |  "
                f"Price: {price}  |  Value: {value}\n"
                f"Source: {trade.source}"
            )
            if trade.is_congress:
                detail += f"  |  Congress: {trade.congress_member}"
            self.detail_label.setText(detail)
            self.btn_edgar.setEnabled(True)

    def _open_edgar(self):
        # Get selected row
        indexes = self.trades_table.selectionModel().selectedRows()
        if not indexes:
            return

        source_index = self.trades_model.mapToSource(indexes[0])
        row = source_index.row()
        if row < len(self._trades):
            trade = self._trades[row]
            if trade.edgar_url:
                webbrowser.open(trade.edgar_url)
            else:
                from insider_scanner.core.edgar import build_edgar_url_for_trade

                url = build_edgar_url_for_trade(trade)
                webbrowser.open(url)

    def _invalidate_cik(self) -> None:
        self._cik_requests.invalidate()
        self.btn_resolve_cik.setEnabled(True)

    def _resolve_cik(self):
        ticker = self.ticker_edit.text().strip().upper()
        if not ticker:
            return

        self.btn_resolve_cik.setEnabled(False)
        self.detail_label.setText(f"Resolving CIK for {ticker}...")

        def show_result(result):
            if result is None:
                self.detail_label.setText(f"Could not resolve CIK for {ticker}")
                return
            cik, url = result
            self.detail_label.setText(f"{ticker} → CIK {cik}\nFilings: {url}")
            webbrowser.open(url)

        def show_error(error_info):
            log.error("CIK lookup failed for %s", ticker, exc_info=error_info)
            self.detail_label.setText(
                f"Could not resolve CIK for {ticker}. See logs for details."
            )

        self._cik_requests.start(
            self._service.resolve_cik,
            args=(ticker,),
            on_result=show_result,
            on_error=show_error,
            on_finished=lambda: self.btn_resolve_cik.setEnabled(True),
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_results(self):
        if not self._trades:
            return

        from insider_scanner.core.merger import save_scan_results

        ticker = self.ticker_edit.text().strip().upper() or "latest"
        out = save_scan_results(self._trades, label=f"{ticker}_scan")
        QMessageBox.information(self, "Saved", f"Results saved to:\n{out}")

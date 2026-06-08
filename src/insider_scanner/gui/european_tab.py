"""European Insider Scan tab — GUI for UK, DE, FR, NL disclosures.

Provides a full scan workflow:
  - Country selector (All / UK / DE / FR / NL)
  - ISIN input field
  - Date range with enable checkbox
  - Trade type and minimum value filters
  - Single-ISIN scan and EU watchlist scan
  - Results table with double-click detail panel
  - Save to CSV + JSON
"""

from __future__ import annotations

import webbrowser
from datetime import date
from threading import Event

import pandas as pd
from PySide6.QtCore import Qt, QDate, QThreadPool, Slot
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.eu_merger import (
    DISPLAY_COLUMNS,
    eu_trades_to_dataframe,
    filter_eu_trades,
    merge_eu_trades,
    save_eu_results,
)
from insider_scanner.gui.widgets import SortableTableModel
from insider_scanner.utils.config import EU_WATCHLIST_FILE, load_eu_watchlist
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("european_tab")

_COUNTRIES = ["All", "UK", "DE", "FR", "NL"]

# Human-readable column headers matching DISPLAY_COLUMNS
_HEADERS = [
    "ISIN",
    "Issuer",
    "Country",
    "Reg. Body",
    "Insider",
    "Position",
    "Trade Date",
    "Filing Date",
    "Type",
    "Instrument",
    "Volume",
    "Price",
    "Currency",
    "Total Value",
    "Source",
    "Source URL",
]


class EuropeanTab(QWidget):
    """European insider scan tab."""

    def __init__(
        self,
        service=None,
        parent=None,
        *,
        thread_pool: QThreadPool | None = None,
    ):
        super().__init__(parent)
        self._service = service
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._trades: list[EuropeanInsiderTrade] = []
        self._filtered_trades: list[EuropeanInsiderTrade] | None = None
        self._cancel_event = Event()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- Search controls ---
        search_grp = QGroupBox("Search")
        search_l = QHBoxLayout(search_grp)

        search_l.addWidget(QLabel("Country:"))
        self.country_combo = QComboBox()
        self.country_combo.addItems(_COUNTRIES)
        self.country_combo.setMaximumWidth(80)
        search_l.addWidget(self.country_combo)

        search_l.addWidget(QLabel("ISIN:"))
        self.isin_edit = QLineEdit()
        self.isin_edit.setPlaceholderText("GB0002875804")
        self.isin_edit.setMaximumWidth(160)
        self.isin_edit.returnPressed.connect(self._run_scan)
        search_l.addWidget(self.isin_edit)

        self.btn_scan = QPushButton("Scan ISIN")
        self.btn_scan.clicked.connect(self._run_scan)
        search_l.addWidget(self.btn_scan)

        self.btn_watchlist = QPushButton("Watchlist Scan")
        self.btn_watchlist.setToolTip(f"Scan all ISINs in {EU_WATCHLIST_FILE}")
        self.btn_watchlist.clicked.connect(self._run_watchlist)
        search_l.addWidget(self.btn_watchlist)

        self.btn_latest = QPushButton("Latest Trades")
        self.btn_latest.setToolTip(
            "Fetch the N most recent trades from each EU source globally (no ISIN filter)"
        )
        self.btn_latest.clicked.connect(self._run_latest)
        search_l.addWidget(self.btn_latest)

        search_l.addWidget(QLabel("Count:"))
        self.latest_count_spin = QSpinBox()
        self.latest_count_spin.setRange(10, 500)
        self.latest_count_spin.setValue(50)
        self.latest_count_spin.setSingleStep(10)
        self.latest_count_spin.setMaximumWidth(70)
        self.latest_count_spin.setToolTip(
            "Maximum number of most-recent trades to show"
        )
        search_l.addWidget(self.latest_count_spin)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip("Cancel running scan")
        self.btn_stop.clicked.connect(self._stop_scan)
        self.btn_stop.hide()
        search_l.addWidget(self.btn_stop)

        search_l.addStretch()
        root.addWidget(search_grp)

        # --- Date range + filters ---
        filter_row = QHBoxLayout()

        date_grp = QGroupBox("Date Range")
        date_l = QHBoxLayout(date_grp)
        self.chk_use_dates = QCheckBox("Enable")
        # stateChanged passes Qt.CheckState (an enum) in PySide6 6.4+, not a
        # plain int. Using isChecked() inside the slot is version-agnostic and
        # always correct regardless of what the signal argument type happens to be.
        self.chk_use_dates.stateChanged.connect(self._toggle_dates)
        date_l.addWidget(self.chk_use_dates)
        date_l.addWidget(QLabel("From:"))
        self.start_date = QDateEdit(QDate.currentDate().addMonths(-3))
        self.start_date.setCalendarPopup(True)
        self.start_date.setEnabled(False)
        date_l.addWidget(self.start_date)
        date_l.addWidget(QLabel("To:"))
        self.end_date = QDateEdit(QDate.currentDate())
        self.end_date.setCalendarPopup(True)
        self.end_date.setEnabled(False)
        date_l.addWidget(self.end_date)
        filter_row.addWidget(date_grp)

        flt_grp = QGroupBox("Filters")
        flt_l = QHBoxLayout(flt_grp)
        flt_l.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All", "Buy", "Sell", "Other"])
        self.type_combo.setMaximumWidth(80)
        flt_l.addWidget(self.type_combo)
        flt_l.addWidget(QLabel("Min Value (€/£):"))
        self.min_value_spin = QDoubleSpinBox()
        self.min_value_spin.setRange(0, 1_000_000_000)
        self.min_value_spin.setValue(0)
        self.min_value_spin.setSingleStep(10_000)
        self.min_value_spin.setMaximumWidth(120)
        flt_l.addWidget(self.min_value_spin)
        self.btn_filter = QPushButton("Apply Filters")
        self.btn_filter.clicked.connect(self._apply_filters)
        flt_l.addWidget(self.btn_filter)
        filter_row.addWidget(flt_grp)

        root.addLayout(filter_row)

        # --- Progress bar ---
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # --- Splitter: table + detail ---
        splitter = QSplitter(Qt.Vertical)

        # Results table
        self.trades_model = SortableTableModel(headers=_HEADERS)
        self.trades_model.set_dataframe(pd.DataFrame(columns=DISPLAY_COLUMNS))
        self.trades_table = QTableView()
        self.trades_table.setModel(self.trades_model)
        self.trades_table.setSortingEnabled(True)
        self.trades_table.setSelectionBehavior(QTableView.SelectRows)
        self.trades_table.setEditTriggers(QTableView.NoEditTriggers)
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.trades_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.trades_table.doubleClicked.connect(self._show_detail)
        splitter.addWidget(self.trades_table)

        # Detail panel
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(140)
        self.detail_text.setPlaceholderText("Double-click a row to view trade details.")
        splitter.addWidget(self.detail_text)

        splitter.setSizes([450, 140])
        root.addWidget(splitter)

        # --- Bottom action bar ---
        action_row = QHBoxLayout()
        self.lbl_count = QLabel("No results.")
        action_row.addWidget(self.lbl_count)
        action_row.addStretch()
        self.btn_open_source = QPushButton("Open Source URL")
        self.btn_open_source.setEnabled(False)
        self.btn_open_source.clicked.connect(self._open_source_url)
        action_row.addWidget(self.btn_open_source)
        self.btn_save = QPushButton("Save Results")
        self.btn_save.clicked.connect(self._save_results)
        action_row.addWidget(self.btn_save)
        root.addLayout(action_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _toggle_dates(self, state) -> None:
        # Use isChecked() rather than comparing against Qt.Checked.
        # In PySide6 6.4+ the stateChanged signal emits Qt.CheckState (an
        # enum), not a plain int. Direct int comparison is unreliable across
        # PySide6 versions; isChecked() always returns the correct bool.
        enabled = self.chk_use_dates.isChecked()
        self.start_date.setEnabled(enabled)
        self.end_date.setEnabled(enabled)

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

    def _set_scan_buttons_enabled(self, enabled: bool):
        self.btn_scan.setEnabled(enabled)
        self.btn_watchlist.setEnabled(enabled)
        self.btn_latest.setEnabled(enabled)
        if enabled:
            self.btn_stop.hide()
        else:
            self.btn_stop.show()

    def _update_table(self, trades: list[EuropeanInsiderTrade]):
        self._trades = trades
        self._filtered_trades = None
        df = eu_trades_to_dataframe(trades)
        self.trades_model.update_data(df)
        self.lbl_count.setText(f"{len(trades)} trade(s) found.")
        self.detail_text.clear()
        self.btn_open_source.setEnabled(False)

    # ------------------------------------------------------------------
    # Scan actions
    # ------------------------------------------------------------------

    def _run_scan(self):
        isin = self.isin_edit.text().strip().upper()
        if not isin:
            QMessageBox.warning(self, "Input Required", "Please enter an ISIN.")
            return

        country = self.country_combo.currentText()
        date_from = self._get_start_date()
        date_to = self._get_end_date()

        self._cancel_event.clear()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate

        def task():
            return self._service.scan(
                isin,
                country=country,
                start_date=date_from,
                end_date=date_to,
                use_cache=True,
                cancelled=self._cancel_event.is_set,
            )

        worker = Worker(task)
        worker.signals.result.connect(self._on_scan_result)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.finished.connect(self._on_scan_finished)
        self._thread_pool.start(worker)

    def _run_watchlist(self):
        isins = load_eu_watchlist()
        if not isins:
            QMessageBox.information(
                self,
                "Watchlist Empty",
                f"No ISINs found in {EU_WATCHLIST_FILE}.\n"
                "Add ISINs (one per line) to that file and try again.",
            )
            return

        country = self.country_combo.currentText()
        date_from = self._get_start_date()
        date_to = self._get_end_date()

        self._cancel_event.clear()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(isins))
        self.progress.setValue(0)

        def task():
            all_trades: list[EuropeanInsiderTrade] = []
            for i, isin in enumerate(isins):
                if self._cancel_event.is_set():
                    break
                batch = self._service.scan(
                    isin,
                    country=country,
                    start_date=date_from,
                    end_date=date_to,
                    use_cache=True,
                    cancelled=self._cancel_event.is_set,
                )
                all_trades.extend(batch)
                worker.signals.progress.emit(i + 1)
            return merge_eu_trades(all_trades)

        worker = Worker(task)
        worker.signals.result.connect(self._on_scan_result)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.progress.connect(self._on_watchlist_progress)
        worker.signals.finished.connect(self._on_scan_finished)
        self._thread_pool.start(worker)

    def _run_latest(self):
        """Fetch the N most recent trades from each EU source globally (no ISIN filter).

        Each scraper fetches its own N most recent disclosures. Results are
        merged and deduplicated. N per source is controlled by the Count spinbox.
        """
        count = self.latest_count_spin.value()
        country = self.country_combo.currentText()
        date_from = self._get_start_date()
        date_to = self._get_end_date()

        self._cancel_event.clear()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(
            0, 0
        )  # indeterminate — scraper runs in one blocking call

        def task():
            return self._service.latest(
                count=count,
                country=country,
                start_date=date_from,
                end_date=date_to,
                use_cache=True,
                cancelled=self._cancel_event.is_set,
            )

        worker = Worker(task)
        worker.signals.result.connect(self._on_scan_result)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.finished.connect(self._on_scan_finished)
        self._thread_pool.start(worker)

    def _stop_scan(self):
        self.request_cancellation()
        log.info("European scan cancellation requested")

    def request_cancellation(self) -> None:
        """Request cooperative cancellation without blocking the GUI thread."""
        self._cancel_event.set()

    # ------------------------------------------------------------------
    # Worker slots
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_scan_result(self, trades: list[EuropeanInsiderTrade]):
        self._update_table(trades)

    @Slot(tuple)
    def _on_scan_error(self, error: tuple):
        exc_type = error[0]
        exc = error[1]
        log.error("Scan failed", exc_info=(exc_type, exc, error[2]))
        QMessageBox.critical(
            self,
            "Scan Error",
            "Scan failed. See logs for details.",
        )

    @Slot()
    def _on_scan_finished(self):
        self._set_scan_buttons_enabled(True)
        self.progress.setVisible(False)

    @Slot(object)
    def _on_watchlist_progress(self, value: int):
        self.progress.setValue(value)

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _apply_filters(self):
        country = self.country_combo.currentText()
        trade_type = self.type_combo.currentText()
        min_val = self.min_value_spin.value() or None
        since = self._get_start_date()
        until = self._get_end_date()

        filtered = filter_eu_trades(
            self._trades,
            country=country if country != "All" else None,
            trade_type=trade_type if trade_type != "All" else None,
            min_value=min_val,
            since=since,
            until=until,
        )
        self._filtered_trades = filtered
        df = eu_trades_to_dataframe(filtered)
        self.trades_model.update_data(df)
        self.lbl_count.setText(
            f"{len(filtered)} trade(s) shown (of {len(self._trades)} total)."
        )

    # ------------------------------------------------------------------
    # Detail & source URL
    # ------------------------------------------------------------------

    def _show_detail(self, index):
        proxy = self.trades_table.model()
        source_index = proxy.mapToSource(index)
        row = source_index.row()
        trades = (
            self._filtered_trades if self._filtered_trades is not None else self._trades
        )
        if row >= len(trades):
            return
        trade = trades[row]
        lines = [
            f"ISIN:           {trade.isin}",
            f"Issuer:         {trade.issuer_name}",
            f"Country:        {trade.country}  ({trade.regulatory_body})",
            f"Insider:        {trade.insider_name}",
            f"Position:       {trade.position}",
            f"Trade Date:     {trade.trade_date}",
            f"Filing Date:    {trade.filing_date}",
            f"Type:           {trade.trade_type}",
            f"Instrument:     {trade.instrument_type}",
            f"Volume:         {trade.volume}",
            f"Price:          {trade.price} {trade.currency}",
            f"Total Value:    {trade.total_value} {trade.currency}",
            f"Source:         {trade.source}",
            f"Source URL:     {trade.source_url or '—'}",
        ]
        self.detail_text.setPlainText("\n".join(lines))
        self.btn_open_source.setEnabled(bool(trade.source_url))

    def _open_source_url(self):
        proxy = self.trades_table.model()
        indexes = self.trades_table.selectionModel().selectedRows()
        if not indexes:
            return
        source_index = proxy.mapToSource(indexes[0])
        row = source_index.row()
        trades = (
            self._filtered_trades if self._filtered_trades is not None else self._trades
        )
        if row < len(trades) and trades[row].source_url:
            webbrowser.open(trades[row].source_url)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_results(self):
        trades = (
            self._filtered_trades if self._filtered_trades is not None else self._trades
        )
        if not trades:
            QMessageBox.information(self, "Nothing to Save", "No trades to save.")
            return
        isin = self.isin_edit.text().strip().upper() or "watchlist"
        country = self.country_combo.currentText().lower()
        label = f"{isin}_{country}_eu_scan"
        out = save_eu_results(trades, label=label)
        QMessageBox.information(self, "Saved", f"Results saved to:\n{out}")

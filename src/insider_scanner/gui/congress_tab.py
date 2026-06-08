"""Congress Scan tab: scan trades by Congress member with sector filtering."""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date
from pathlib import Path

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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.gui.widgets import SortableTableModel
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("congress_tab")

# Sector categories — matches committee→sector mapping in update_congress.py
SECTORS = [
    "All",
    "Defense",
    "Energy",
    "Finance",
    "Technology",
    "Healthcare",
    "Industrials",
    "Other",
]

# Congress trade table columns for display
DISPLAY_COLUMNS = [
    "filing_date",
    "trade_date",
    "official_name",
    "chamber",
    "ticker",
    "asset_description",
    "trade_type",
    "owner",
    "amount_range",
    "source",
]


def _load_congress_names() -> list[str]:
    """Load Congress member names from the JSON data file.

    Returns a sorted list of display names (e.g. "Pelosi Nancy").
    The first entry is always "All" to allow scanning all members.
    """
    from insider_scanner.utils.config import CONGRESS_FILE

    names = []
    if CONGRESS_FILE.exists():
        try:
            data = json.loads(CONGRESS_FILE.read_text(encoding="utf-8"))
            for entry in data:
                # Support both simple {"name": ...} and extended formats
                name = entry.get("official_name") or entry.get("name", "")
                if name:
                    names.append(name)
        except (json.JSONDecodeError, KeyError):
            pass

    names.sort()
    return ["All"] + names


def _load_member_sectors() -> dict[str, list[str]]:
    """Load official_name → sector list mapping from congress_members.json.

    Returns a dict like {"Pelosi Nancy": ["Finance"], ...}.
    """
    from insider_scanner.utils.config import CONGRESS_FILE

    mapping: dict[str, list[str]] = {}
    if CONGRESS_FILE.exists():
        try:
            data = json.loads(CONGRESS_FILE.read_text(encoding="utf-8"))
            for entry in data:
                name = entry.get("official_name") or entry.get("name", "")
                sector = entry.get("sector", ["Other"])
                if isinstance(sector, str):
                    sector = [sector]
                if name:
                    mapping[name] = sector
        except (json.JSONDecodeError, KeyError):
            pass
    return mapping


def congress_trades_to_dataframe(trades: list) -> pd.DataFrame:
    """Convert a list of CongressTrade to a pandas DataFrame."""
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([t.to_dict() for t in trades])


def filter_congress_trades(
    trades: list,
    *,
    trade_type: str | None = None,
    min_value: float | None = None,
    since: date | None = None,
    until: date | None = None,
    sector: str | None = None,
    member_sectors: dict[str, list[str]] | None = None,
) -> list:
    """Filter CongressTrade records.

    Parameters
    ----------
    trades : list of CongressTrade
    trade_type : str or None
        "Purchase", "Sale", "Exchange", or None for all.
    min_value : float or None
        Minimum amount_low value.
    since, until : date or None
        Filing date range.
    sector : str or None
        Sector to filter by (matches official's sectors from
        congress_members.json).
    member_sectors : dict or None
        Mapping of official_name → list of sector strings.
    """
    result = trades

    if trade_type:
        result = [t for t in result if t.trade_type == trade_type]

    if min_value is not None and min_value > 0:
        result = [t for t in result if t.amount_low >= min_value]

    if since:
        result = [t for t in result if t.filing_date and t.filing_date >= since]

    if until:
        result = [t for t in result if t.filing_date and t.filing_date <= until]

    if sector and sector != "All" and member_sectors:
        result = [
            t
            for t in result
            if sector in member_sectors.get(t.official_name, ["Other"])
        ]

    return result


def save_congress_results(
    trades: list,
    label: str = "congress_scan",
) -> Path:
    """Save Congress scan results as CSV and JSON.

    Returns the output directory.
    """
    from insider_scanner.utils.config import SCAN_OUTPUTS_DIR, ensure_dirs

    ensure_dirs()
    out = SCAN_OUTPUTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    # CSV
    df = congress_trades_to_dataframe(trades)
    csv_path = out / f"{label}.csv"
    df.to_csv(csv_path, index=False)

    # JSON
    json_path = out / f"{label}.json"
    with open(json_path, "w") as f:
        json.dump([t.to_dict() for t in trades], f, indent=2, default=str)

    return out


class CongressTab(QWidget):
    """Congress trade scanner: select official → scan sources → filter → view."""

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
        self._trades: list = []
        self._filtered_trades: list = []
        self._cancel_event = threading.Event()
        self._member_sectors: dict[str, list[str]] = {}
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- Official selection ---
        official_grp = QGroupBox("Congress Member")
        official_l = QHBoxLayout(official_grp)

        official_l.addWidget(QLabel("Official:"))
        self.official_combo = QComboBox()
        self.official_combo.addItems(_load_congress_names())
        self.official_combo.setMinimumWidth(250)
        self.official_combo.setEditable(True)
        self.official_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.official_combo.setToolTip(
            "Select a Congress member or type to search. 'All' scans all members."
        )
        official_l.addWidget(self.official_combo)

        self.btn_scan = QPushButton("Scan Trades")
        self.btn_scan.clicked.connect(self._run_scan)
        official_l.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setMaximumWidth(60)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_scan)
        official_l.addWidget(self.btn_stop)

        self.btn_refresh_list = QPushButton("Refresh List")
        self.btn_refresh_list.setToolTip("Reload congress_members.json")
        self.btn_refresh_list.clicked.connect(self._refresh_member_list)
        self.btn_refresh_list.setMaximumWidth(100)
        official_l.addWidget(self.btn_refresh_list)

        official_l.addStretch()
        root.addWidget(official_grp)

        # --- Source + date range + filter controls ---
        filter_row = QHBoxLayout()

        # Sources — House and Senate disclosure systems
        src_grp = QGroupBox("Sources")
        src_l = QHBoxLayout(src_grp)
        self.chk_house = QCheckBox("House")
        self.chk_house.setChecked(True)
        self.chk_house.setToolTip("Scan disclosures-clerk.house.gov")
        self.chk_senate = QCheckBox("Senate")
        self.chk_senate.setChecked(True)
        self.chk_senate.setToolTip("Scan efdsearch.senate.gov")
        src_l.addWidget(self.chk_house)
        src_l.addWidget(self.chk_senate)
        filter_row.addWidget(src_grp)

        # Filing date range
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

        filter_row.addWidget(date_grp)

        # Filters
        filt_grp = QGroupBox("Filters")
        filt_l = QHBoxLayout(filt_grp)

        filt_l.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All", "Purchase", "Sale", "Exchange", "Other"])
        filt_l.addWidget(self.type_combo)

        filt_l.addWidget(QLabel("Min value ($):"))
        self.min_value_spin = QDoubleSpinBox()
        self.min_value_spin.setRange(0, 1_000_000_000)
        self.min_value_spin.setDecimals(0)
        self.min_value_spin.setSingleStep(10_000)
        self.min_value_spin.setValue(0)
        filt_l.addWidget(self.min_value_spin)

        filt_l.addWidget(QLabel("Sector:"))
        self.sector_combo = QComboBox()
        self.sector_combo.addItems(SECTORS)
        self.sector_combo.setToolTip(
            "Filter by official's committee sector (from congress_members.json)"
        )
        filt_l.addWidget(self.sector_combo)

        self.btn_filter = QPushButton("Apply Filters")
        self.btn_filter.clicked.connect(self._apply_filters)
        filt_l.addWidget(self.btn_filter)

        filter_row.addWidget(filt_grp)
        root.addLayout(filter_row)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        # --- Results ---
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        table_widget = QWidget()
        table_l = QVBoxLayout(table_widget)
        table_l.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("Select a Congress member and click Scan Trades")
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

        self.btn_open_filing = QPushButton("Open Filing")
        self.btn_open_filing.setToolTip("Open the disclosure filing in browser")
        self.btn_open_filing.clicked.connect(self._open_filing)
        self.btn_open_filing.setEnabled(False)
        bottom_l.addWidget(self.btn_open_filing)

        self.btn_save = QPushButton("Save Results")
        self.btn_save.clicked.connect(self._save_results)
        self.btn_save.setEnabled(False)
        bottom_l.addWidget(self.btn_save)

        bottom_l.addStretch()

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        bottom_l.addWidget(self.detail_label, stretch=1)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

    # ------------------------------------------------------------------
    # Helpers
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

    def _refresh_member_list(self):
        """Reload congress_members.json and repopulate the dropdown."""
        current = self.official_combo.currentText()
        self.official_combo.clear()
        names = _load_congress_names()
        self.official_combo.addItems(names)
        idx = self.official_combo.findText(current)
        if idx >= 0:
            self.official_combo.setCurrentIndex(idx)

    def _set_scan_buttons_enabled(self, enabled: bool):
        """Toggle scan-related buttons."""
        self.btn_scan.setEnabled(enabled)
        self.btn_refresh_list.setEnabled(enabled)
        self.btn_stop.setEnabled(not enabled)

    def _stop_scan(self):
        """Signal the background scan to stop."""
        self.request_cancellation()
        self.btn_stop.setEnabled(False)
        self.status_label.setText("Cancelling scan...")

    def request_cancellation(self) -> None:
        """Request cooperative cancellation without blocking the GUI thread."""
        self._cancel_event.set()

    # ------------------------------------------------------------------
    # Scan — wire to House + Senate backends
    # ------------------------------------------------------------------

    def _run_scan(self):
        selected = self.official_combo.currentText().strip()
        if not selected:
            QMessageBox.warning(self, "Input", "Select a Congress member.")
            return

        use_house = self.chk_house.isChecked()
        use_senate = self.chk_senate.isChecked()

        if not use_house and not use_senate:
            QMessageBox.warning(
                self,
                "Sources",
                "Select at least one source (House or Senate).",
            )
            return

        # Load sectors for filtering
        self._member_sectors = _load_member_sectors()

        self._cancel_event.clear()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate

        sd = self._get_start_date()
        ed = self._get_end_date()
        cancel = self._cancel_event
        sources = tuple(
            source
            for source, enabled in (
                ("house", use_house),
                ("senate", use_senate),
            )
            if enabled
        )

        self.progress.setFormat(
            f"Scanning {'all officials' if selected == 'All' else selected}..."
        )

        def work():
            return self._service.scan(
                selected,
                sources=sources,
                start_date=sd,
                end_date=ed,
                use_cache=True,
                cancelled=cancel.is_set,
            )

        worker = Worker(work)
        worker.signals.result.connect(self._on_scan_done)
        worker.signals.error.connect(self._on_scan_error)
        self._thread_pool.start(worker)

    @Slot(object)
    def _on_scan_done(self, trades):
        cancelled = self._cancel_event.is_set()
        self._cancel_event.clear()
        self._trades = trades
        self._filtered_trades = trades
        self.progress.setVisible(False)
        self._set_scan_buttons_enabled(True)
        self.btn_save.setEnabled(bool(trades))
        self._display_trades(trades)
        if cancelled:
            self.status_label.setText(
                self.status_label.text() + "  (scan was cancelled)"
            )

    @Slot(tuple)
    def _on_scan_error(self, error_info):
        self._cancel_event.clear()
        self.progress.setVisible(False)
        self._set_scan_buttons_enabled(True)
        exc_type, exc_value, _ = error_info
        log.error(
            "Congress scan failed",
            exc_info=(exc_type, exc_value, error_info[2]),
        )
        QMessageBox.critical(
            self,
            "Scan Error",
            "Scan failed. See logs for details.",
        )

    # ------------------------------------------------------------------
    # Display + filter
    # ------------------------------------------------------------------

    def _display_trades(self, trades):
        if not trades:
            self.status_label.setText("No trades found.")
            self.trades_model.set_dataframe(pd.DataFrame())
            self.btn_open_filing.setEnabled(False)
            return

        df = congress_trades_to_dataframe(trades)

        # Select display columns that exist
        cols = [c for c in DISPLAY_COLUMNS if c in df.columns]
        self.trades_model.set_dataframe(df[cols])
        self.status_label.setText(f"{len(trades)} congress trades found")

    def _apply_filters(self):
        if not self._trades:
            return

        trade_type = self.type_combo.currentText()
        if trade_type == "All":
            trade_type = None

        min_val = self.min_value_spin.value()

        sector = self.sector_combo.currentText()
        if sector == "All":
            sector = None

        self._filtered_trades = filter_congress_trades(
            self._trades,
            trade_type=trade_type,
            min_value=min_val if min_val > 0 else None,
            since=self._get_start_date(),
            until=self._get_end_date(),
            sector=sector,
            member_sectors=self._member_sectors,
        )
        self._display_trades(self._filtered_trades)
        self.btn_save.setEnabled(bool(self._filtered_trades))

    # ------------------------------------------------------------------
    # Filing details + open
    # ------------------------------------------------------------------

    def _on_row_double_click(self, index):
        source_index = self.trades_model.mapToSource(index)
        row = source_index.row()
        trades = self._filtered_trades or self._trades
        if row < len(trades):
            trade = trades[row]
            sectors = self._member_sectors.get(trade.official_name, ["Other"])
            detail = (
                f"Official: {trade.official_name}  |  "
                f"Chamber: {trade.chamber}  |  "
                f"Sector: {', '.join(sectors)}\n"
                f"Asset: {trade.asset_description}\n"
                f"Type: {trade.trade_type}  |  Owner: {trade.owner}  |  "
                f"Amount: {trade.amount_range}  |  "
                f"Source: {trade.source}"
            )
            self.detail_label.setText(detail)
            self.btn_open_filing.setEnabled(bool(trade.source_url))

    def _open_filing(self):
        indexes = self.trades_table.selectionModel().selectedRows()
        if not indexes:
            return

        source_index = self.trades_model.mapToSource(indexes[0])
        row = source_index.row()
        trades = self._filtered_trades or self._trades
        if row < len(trades):
            trade = trades[row]
            if trade.source_url:
                webbrowser.open(trade.source_url)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_results(self):
        trades = self._filtered_trades or self._trades
        if not trades:
            return

        selected = (
            self.official_combo.currentText().strip().replace(" ", "_") or "congress"
        )
        out = save_congress_results(trades, label=f"{selected}_congress_scan")
        QMessageBox.information(self, "Saved", f"Results saved to:\n{out}")

"""Congress Scan tab: scan trades by Congress member with sector filtering."""

from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from insider_scanner.core.congress_identity import identity_for
from insider_scanner.core.fetch_outcome import describe_incomplete
from insider_scanner.gui.scan_workflow import BaseScanTab
from insider_scanner.services.trade_tables import (
    _sectors_for,
    congress_trades_to_dataframe,
    filter_congress_trades,
    load_congress_members,
    save_congress_results,
)
from insider_scanner.utils.logging import get_logger

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
    """Compatibility access to the service-owned member loader."""
    return load_congress_members().names


def _load_member_sectors() -> dict[str, list[str]]:
    """Compatibility access to the service-owned member loader."""
    return load_congress_members().sectors


class CongressTab(BaseScanTab):
    """Congress trade scanner: select official → scan sources → filter → view."""

    initial_status = "Select a Congress member and click Scan Trades"

    def __init__(
        self,
        service=None,
        parent=None,
        *,
        thread_pool: QThreadPool | None = None,
    ):
        super().__init__(service, parent, thread_pool=thread_pool)
        self._requests = self._scan_requests
        self._members = load_congress_members()
        self._member_sectors = self._members.sectors
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.addWidget(self._build_official_group())
        root.addLayout(self._build_filter_row())
        root.addWidget(self._build_progress())
        root.addWidget(self._build_results_splitter(), stretch=1)

    def _build_official_group(self) -> QGroupBox:
        official_grp = QGroupBox("Congress Member")
        official_l = QHBoxLayout(official_grp)

        official_l.addWidget(QLabel("Official:"))
        self.official_combo = QComboBox()
        self.official_combo.addItems(self._members.names)
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
        return official_grp

    def _build_filter_row(self) -> QHBoxLayout:
        filter_row = QHBoxLayout()
        filter_row.addWidget(self._build_sources_group())
        filter_row.addWidget(self._build_date_range_group())
        filter_row.addWidget(self._build_filters_group())
        return filter_row

    def _build_sources_group(self) -> QGroupBox:
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
        return src_grp

    def _build_filters_group(self) -> QGroupBox:
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

        return filt_grp

    def _build_progress(self) -> QProgressBar:
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        return self.progress

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_member_list(self):
        """Refresh names and sectors together from one seed read."""
        self._members = load_congress_members()
        self._member_sectors = self._members.sectors
        current = self.official_combo.currentText()
        self.official_combo.clear()
        self.official_combo.addItems(self._members.names)
        idx = self.official_combo.findText(current)
        if idx >= 0:
            self.official_combo.setCurrentIndex(idx)

    def _set_scan_buttons_enabled(self, enabled: bool):
        """Toggle scan-related buttons."""
        self.btn_scan.setEnabled(enabled)
        self.btn_refresh_list.setEnabled(enabled)
        self.btn_stop.setEnabled(not enabled)

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

        self._cancel_event.set()
        self._cancel_event = threading.Event()
        self._set_scan_buttons_enabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate

        sd = self._get_start_date()
        ed = self._get_end_date()
        cancel = self._cancel_event
        service = self._service
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

        member = selected if selected == "All" else identity_for(selected)

        def work():
            return service.scan(
                member,
                sources=sources,
                start_date=sd,
                end_date=ed,
                use_cache=True,
                cancelled=cancel.is_set,
            )

        self._requests.start(
            work,
            on_result=self._on_scan_done,
            on_error=self._on_scan_error,
        )

    # ------------------------------------------------------------------
    # Display + filter
    # ------------------------------------------------------------------

    def _display_trades(self, trades):
        df = congress_trades_to_dataframe(trades)
        columns = [c for c in DISPLAY_COLUMNS if c in df.columns]
        self._set_displayed_trades(trades, df, columns)
        self._set_results_status(
            f"{len(trades)} congress trades found" if trades else "No trades found."
        )

    def _set_results_status(self, text):
        if warning := describe_incomplete(self._trades):
            text += f"  |  {warning}"
        self.status_label.setText(text)

    def _apply_filters(self):
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

    # ------------------------------------------------------------------
    # Filing details + open
    # ------------------------------------------------------------------

    def _on_row_double_click(self, index):
        trade = self._trade_at(index)
        if trade is None:
            return
        sectors = _sectors_for(trade.official_name, self._member_sectors)
        detail = (
            f"Official: {trade.official_name}  |  Chamber: {trade.chamber}  |  "
            f"Sector: {', '.join(sectors)}\nAsset: {trade.asset_description}\n"
            f"Type: {trade.trade_type}  |  Owner: {trade.owner}  |  "
            f"Amount: {trade.amount_range}  |  Source: {trade.source}"
        )
        self.detail_label.setText(detail)
        self.btn_open_filing.setEnabled(bool(trade.source_url))

    def _open_filing(self):
        trade = self._selected_trade()
        if trade is not None and trade.source_url:
            webbrowser.open(trade.source_url)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_results(self):
        selected = (
            self.official_combo.currentText().strip().replace(" ", "_") or "congress"
        )
        self._start_export(save_congress_results, f"{selected}_congress_scan")

    def _build_result_actions(self, layout):
        self.btn_open_filing = QPushButton("Open Filing")
        self.btn_open_filing.setToolTip("Open the disclosure filing in browser")
        self.btn_open_filing.clicked.connect(self._open_filing)
        self.btn_open_filing.setEnabled(False)
        self._filing_button = self.btn_open_filing
        layout.addWidget(self.btn_open_filing)
        self.btn_save = QPushButton("Save Results")
        self.btn_save.clicked.connect(self._save_results)
        self.btn_save.setEnabled(False)
        layout.addWidget(self.btn_save)

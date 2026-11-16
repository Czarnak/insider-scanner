"""Common result, date and background-export workflow for trade scan tabs."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from threading import Event

from PySide6.QtCore import QDate, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
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

from insider_scanner.gui.widgets import ROW_OBJECT_ROLE, SortableTableModel
from insider_scanner.services.trade_tables import safe_export_label
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import LatestRequest

log = get_logger("scan_workflow")


class DateRangeGroup(QGroupBox):
    """Optional inclusive filing-date range used by both scan workflows."""

    def __init__(self, parent=None):
        super().__init__("Filing Date Range", parent)
        layout = QHBoxLayout(self)
        self.enabled = QCheckBox("Enable")
        layout.addWidget(self.enabled)
        self.start = QDateEdit()
        self.end = QDateEdit()
        for label, widget, value in (
            ("Start:", self.start, QDate.currentDate().addMonths(-6)),
            ("End:", self.end, QDate.currentDate()),
        ):
            layout.addWidget(QLabel(label))
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.setDate(value)
            widget.setEnabled(False)
            layout.addWidget(widget)
        self.enabled.toggled.connect(self.start.setEnabled)
        self.enabled.toggled.connect(self.end.setEnabled)

    def start_date(self) -> date | None:
        value = self.start.date()
        return (
            date(value.year(), value.month(), value.day())
            if self.enabled.isChecked()
            else None
        )

    def end_date(self) -> date | None:
        value = self.end.date()
        return (
            date(value.year(), value.month(), value.day())
            if self.enabled.isChecked()
            else None
        )


class BaseScanTab(QWidget):
    """Keep table identity, cancellation and exports consistent across scans."""

    dataChanged = Signal()
    initial_status = "No scan results yet"
    btn_save: QPushButton
    btn_stop: QPushButton
    progress: QProgressBar
    _filing_button: QPushButton

    def __init__(self, service=None, parent=None, *, thread_pool=None):
        super().__init__(parent)
        self._service = service
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._scan_requests = LatestRequest(self, self._thread_pool)
        self._export_requests = LatestRequest(self, self._thread_pool)
        self._cancel_event = Event()
        self._trades: list = []
        self._displayed_trades: list = []
        self._filtered_trades: list = []
        self._saving = False
        self._sized_columns: tuple[str, ...] = ()

    def _build_result_actions(self, layout):
        raise NotImplementedError

    def _on_row_double_click(self, index):
        raise NotImplementedError

    def _set_scan_buttons_enabled(self, enabled: bool):
        raise NotImplementedError

    def _apply_filters(self):
        raise NotImplementedError

    def _build_date_range_group(self):
        self.date_range = DateRangeGroup()
        self.chk_use_dates = self.date_range.enabled
        self.start_date = self.date_range.start
        self.end_date = self.date_range.end
        return self.date_range

    def _get_start_date(self):
        return self.date_range.start_date()

    def _get_end_date(self):
        return self.date_range.end_date()

    def _build_results_splitter(self):
        splitter = QSplitter(Qt.Orientation.Vertical)
        table_widget = QWidget()
        layout = QVBoxLayout(table_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel(self.initial_status)
        layout.addWidget(self.status_label)
        self.trades_model = SortableTableModel()
        self.trades_table = QTableView()
        self.trades_table.setModel(self.trades_model)
        self.trades_table.setSortingEnabled(True)
        self.trades_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        header = self.trades_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setResizeContentsPrecision(100)
        header.setStretchLastSection(True)
        self.trades_table.doubleClicked.connect(self._on_row_double_click)
        layout.addWidget(self.trades_table)
        splitter.addWidget(table_widget)
        bottom = QWidget()
        actions = QHBoxLayout(bottom)
        self._build_result_actions(actions)
        actions.addStretch()
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        actions.addWidget(self.detail_label, stretch=1)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _set_displayed_trades(self, trades, dataframe, columns):
        self._displayed_trades = list(trades)
        self._filtered_trades = self._displayed_trades
        self.detail_label.clear()
        self._filing_button.setEnabled(False)
        self.trades_model.set_dataframe(
            dataframe, visible_columns=columns, row_objects=self._displayed_trades
        )
        # Size one bounded sample per schema; sorting/filtering keeps user widths.
        schema = tuple(columns)
        if trades and schema != self._sized_columns:
            self.trades_table.resizeColumnsToContents()
            self._sized_columns = schema
        self._update_save_enabled()

    def _trade_at(self, index):
        return index.data(ROW_OBJECT_ROLE) if index.isValid() else None

    def _selected_trade(self):
        indexes = self.trades_table.selectionModel().selectedRows()
        return self._trade_at(indexes[0]) if indexes else None

    def _update_save_enabled(self):
        self.btn_save.setEnabled(bool(self._displayed_trades) and not self._saving)

    def _start_export(self, save, label):
        if not self._displayed_trades or self._saving:
            return
        # Later filters/scans cannot change the pending export's domain records.
        trades = deepcopy(self._displayed_trades)
        self._saving = True
        self._update_save_enabled()
        self._export_requests.start(
            save,
            args=(trades,),
            kwargs={"label": safe_export_label(label)},
            on_result=lambda out: QMessageBox.information(
                self, "Saved", f"Results saved to:\n{out}"
            ),
            on_error=self._on_export_error,
            on_finished=self._on_export_finished,
        )

    def _on_export_error(self, error_info):
        log.error("Export failed", exc_info=error_info)
        QMessageBox.critical(self, "Save Error", "Saving failed. See logs for details.")

    def _on_export_finished(self):
        self._saving = False
        self._update_save_enabled()

    def _stop_scan(self):
        self._cancel_event.set()
        self.btn_stop.setEnabled(False)
        self.progress.setFormat("Stopping...")
        self.status_label.setText("Cancelling scan...")

    def request_cancellation(self):
        self._cancel_event.set()
        self._scan_requests.invalidate()
        self._export_requests.invalidate()

    @Slot(object)
    def _on_scan_done(self, trades):
        cancelled = self._cancel_event.is_set()
        self._cancel_event.clear()
        self._trades = trades
        self.progress.hide()
        self.btn_stop.setEnabled(True)
        self._set_scan_buttons_enabled(True)
        self._apply_filters()
        if cancelled:
            self.status_label.setText(
                self.status_label.text() + "  (scan was cancelled)"
            )
        self.dataChanged.emit()

    @Slot(tuple)
    def _on_scan_error(self, error_info):
        self._cancel_event.clear()
        self.progress.hide()
        self.btn_stop.setEnabled(True)
        self._set_scan_buttons_enabled(True)
        log.error("Scan failed", exc_info=error_info)
        self.dataChanged.emit()
        QMessageBox.critical(self, "Scan Error", "Scan failed. See logs for details.")

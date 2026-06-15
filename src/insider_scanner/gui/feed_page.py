"""Unified local transaction Feed page."""

from __future__ import annotations

import dataclasses
import webbrowser
from datetime import datetime
from functools import partial

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.gui.feed_filters import FeedFilterChips, FeedFilterPanel
from insider_scanner.gui.investigation_drawer import InvestigationDrawer
from insider_scanner.gui.theme import get_theme_manager
from insider_scanner.persistence.feed import (
    DEFAULT_PAGE_SIZE,
    FeedCriteria,
    FeedMarket,
    FeedPage,
    FeedRecord,
    FeedRepository,
    FeedSortField,
    TransactionDirection,
)
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("gui.feed_page")

_COLUMNS = (
    ("Market", "market"),
    ("Transaction", "transaction_type"),
    ("Company / Issuer", "issuer"),
    ("Ticker / ISIN", "identifier"),
    ("Insider / Official", "person"),
    ("Role", "role"),
    ("Trade Date", "transaction_date"),
    ("Filing Date", "filing_date"),
    ("Quantity", "quantity"),
    ("Price", "price"),
    ("Reported Value", "value"),
    ("Source", "source"),
)

_SORTABLE_COLUMNS = {
    0: FeedSortField.MARKET,
    2: FeedSortField.ISSUER,
    4: FeedSortField.PERSON,
    6: FeedSortField.TRANSACTION_DATE,
    7: FeedSortField.FILING_DATE,
    10: FeedSortField.VALUE,
}


class FeedTableModel(QAbstractTableModel):
    """Immutable-record table model with server-side sort requests."""

    sortRequested = Signal(object, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: tuple[FeedRecord, ...] = ()
        self._palette = get_theme_manager().palette()
        self._mono_font = QFont("JetBrains Mono")
        self._mono_font.setStyleHint(QFont.StyleHint.Monospace)
        get_theme_manager().paletteChanged.connect(self._on_palette_changed)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(_COLUMNS):
            return _COLUMNS[section][0]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(record, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in {8, 9, 10}:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.FontRole and column in {3, 6, 7, 8, 9, 10}:
            return self._mono_font
        if role == Qt.ItemDataRole.ForegroundRole and column == 1:
            normalized = record.transaction_type.casefold()
            if normalized in {"buy", "purchase"}:
                return QBrush(self._palette.purchase)
            if normalized in {"sell", "sale"}:
                return QBrush(self._palette.sale)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._display_value(record, column)
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder) -> None:
        field = _SORTABLE_COLUMNS.get(column)
        if field is not None:
            self.sortRequested.emit(field, order == Qt.SortOrder.DescendingOrder)

    def replace_records(self, records: tuple[FeedRecord, ...]) -> None:
        self.beginResetModel()
        self._records = tuple(records)
        self.endResetModel()

    def append_records(self, records: tuple[FeedRecord, ...]) -> None:
        if not records:
            return
        start = len(self._records)
        self.beginInsertRows(QModelIndex(), start, start + len(records) - 1)
        self._records = (*self._records, *records)
        self.endInsertRows()

    def record_at(self, row: int) -> FeedRecord:
        return self._records[row]

    def records(self) -> tuple[FeedRecord, ...]:
        return self._records

    def _on_palette_changed(self, palette) -> None:
        self._palette = palette
        if self._records:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._records) - 1, len(_COLUMNS) - 1),
            )

    @staticmethod
    def _display_value(record: FeedRecord, column: int) -> str:
        if column == 0:
            return record.market.value
        if column == 1:
            return record.transaction_type
        if column == 2:
            return record.issuer or "—"
        if column == 3:
            return record.identifier or "—"
        if column == 4:
            return record.person or "—"
        if column == 5:
            return record.role or "—"
        if column == 6:
            return str(record.transaction_date) if record.transaction_date else "—"
        if column == 7:
            return str(record.filing_date) if record.filing_date else "—"
        if column == 8:
            return f"{record.quantity:,.2f}" if record.quantity is not None else "—"
        if column == 9:
            if record.price is None:
                return "—"
            suffix = f" {record.currency}" if record.currency else ""
            return f"{record.price:,.2f}{suffix}"
        if column == 10:
            if record.value_display:
                return record.value_display
            if record.value_sort is None:
                return "—"
            suffix = f" {record.currency}" if record.currency else ""
            return f"{record.value_sort:,.2f}{suffix}"
        return record.source or "—"


class FeedPageWidget(QWidget):
    """Paged, searchable projection of all locally persisted transactions."""

    resultCountChanged = Signal(int)
    freshnessChanged = Signal(str)
    criteriaChanged = Signal(object)
    resetRequested = Signal()
    openCompanyRequested = Signal(object, object)
    openInsiderRequested = Signal(object, object)

    def __init__(
        self,
        repository: FeedRepository | None,
        parent=None,
        *,
        thread_pool: QThreadPool | None = None,
        initial_criteria: FeedCriteria | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._request_id = 0
        self._workers: dict[int, Worker] = {}
        self._criteria: FeedCriteria = initial_criteria or FeedCriteria()
        self._total_count = 0
        self._freshness_text = "Local data unavailable"
        self._build_ui()
        self.filter_panel.set_filters(self._criteria)
        self.chips.set_criteria(self._criteria)
        if repository is None:
            self._show_state("Local transaction data is unavailable.")
        else:
            self.reload()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Loading transactions")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.state_label = QLabel()
        self.state_label.setObjectName("feedState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setAccessibleName("Feed status")
        self.state_label.hide()
        layout.addWidget(self.state_label)

        self.filter_panel = FeedFilterPanel()
        self.filter_panel.hide()
        self.filter_panel.filtersApplied.connect(self._on_filters_applied)
        self.filter_panel.resetRequested.connect(self._on_reset)
        layout.addWidget(self.filter_panel)

        self.chips = FeedFilterChips()
        self.chips.chipRemoved.connect(self._on_chip_removed)
        self.chips.resetRequested.connect(self._on_reset)
        layout.addWidget(self.chips)

        self.model = FeedTableModel(self)
        self.table = QTableView()
        self.table.setAccessibleName("Unified transaction feed")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate(
            (70, 90, 170, 100, 150, 100, 90, 90, 90, 90, 120, 80)
        ):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSortIndicator(
            6,
            Qt.SortOrder.DescendingOrder,
        )
        self.table.doubleClicked.connect(self._open_selected_source)
        self.table.clicked.connect(self._on_row_clicked)
        self.table.selectionModel().selectionChanged.connect(self._on_row_selected)

        self.drawer = InvestigationDrawer(
            self._repository, thread_pool=self._thread_pool
        )
        self.drawer.hide()
        self.drawer.openCompanyRequested.connect(self.openCompanyRequested)
        self.drawer.openInsiderRequested.connect(self.openInsiderRequested)
        self.drawer.closeRequested.connect(self._close_drawer)
        self._drawer_origin_row = -1

        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(self.table)
        self._content_splitter.addWidget(self.drawer)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 0)
        self._content_splitter.setCollapsible(0, False)
        layout.addWidget(self._content_splitter, stretch=1)

        self.load_more_button = QPushButton("Load More")
        self.load_more_button.setAccessibleName("Load more transactions")
        self.load_more_button.clicked.connect(self.load_more)
        self.load_more_button.hide()
        layout.addWidget(
            self.load_more_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        self.table.setSortingEnabled(True)
        self.model.sortRequested.connect(self._on_sort_requested)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def criteria(self) -> FeedCriteria:
        """Return the current FeedCriteria (single source of truth)."""
        return self._criteria

    def set_criteria(self, criteria: FeedCriteria) -> None:
        """Canonical apply path: store, sync UI, emit criteriaChanged, re-query."""
        self._criteria = criteria
        self.filter_panel.set_filters(criteria)
        self.chips.set_criteria(criteria)
        self.criteriaChanged.emit(criteria)
        self._start_query(append=False)

    def toggle_filter_panel(self) -> None:
        """Show or hide the filter panel."""
        self.filter_panel.setVisible(not self.filter_panel.isVisible())

    def reload(self) -> None:
        self._start_query(append=False)

    def set_search(self, search: str) -> None:
        normalized = search.strip()
        if normalized == self._criteria.search and self.model.rowCount() > 0:
            return
        self.set_criteria(dataclasses.replace(self._criteria, search=normalized))

    def load_more(self) -> None:
        if self.load_more_button.isEnabled():
            self._start_query(append=True)

    def freshness_text(self) -> str:
        return self._freshness_text

    def request_cancellation(self) -> None:
        self._request_id += 1
        self.drawer.request_cancellation()

    def column_layout(self) -> str | None:
        """Return the current column layout as a base64-encoded string."""
        from insider_scanner.persistence.feed_state import encode_column_layout

        return encode_column_layout(bytes(self.table.horizontalHeader().saveState()))

    def restore_column_layout(self, text: str | None) -> None:
        """Restore column layout from a base64-encoded string; no-op on falsy input."""
        if not text:
            return
        try:
            from insider_scanner.persistence.feed_state import decode_column_layout

            from PySide6.QtCore import QByteArray

            self.table.horizontalHeader().restoreState(
                QByteArray(decode_column_layout(text))
            )
        except Exception:
            log.warning("Could not restore column layout: corrupt or invalid blob")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_filters_applied(self, filters: FeedCriteria) -> None:
        """Merge filter-panel fields into current criteria (keep search + sort)."""
        merged = dataclasses.replace(
            self._criteria,
            markets=filters.markets,
            directions=filters.directions,
            transaction_date_from=filters.transaction_date_from,
            transaction_date_to=filters.transaction_date_to,
            value_min=filters.value_min,
            value_max=filters.value_max,
        )
        self.set_criteria(merged)

    def _on_chip_removed(self, key: str) -> None:
        """Clear the single criterion identified by *key* and re-query."""
        new_criteria = self._clear_chip_key(key)
        self.set_criteria(new_criteria)

    def _on_reset(self) -> None:
        """Re-emit resetRequested upward; main_window decides whether to clear."""
        self.resetRequested.emit()

    def _on_sort_requested(self, field: FeedSortField, descending: bool) -> None:
        self._criteria = dataclasses.replace(
            self._criteria, sort_field=field, descending=descending
        )
        self.criteriaChanged.emit(self._criteria)
        self._start_query(append=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_chip_key(self, key: str) -> FeedCriteria:
        """Return a new FeedCriteria with the criterion identified by *key* cleared."""
        if key.startswith("market:"):
            value = key[len("market:") :]
            market = FeedMarket(value)
            new_markets = tuple(m for m in self._criteria.markets if m != market)
            return dataclasses.replace(self._criteria, markets=new_markets)
        if key.startswith("direction:"):
            value = key[len("direction:") :]
            direction = TransactionDirection(value)
            new_dirs = tuple(d for d in self._criteria.directions if d != direction)
            return dataclasses.replace(self._criteria, directions=new_dirs)
        if key == "date_from":
            return dataclasses.replace(self._criteria, transaction_date_from=None)
        if key == "date_to":
            return dataclasses.replace(self._criteria, transaction_date_to=None)
        if key == "value_min":
            return dataclasses.replace(self._criteria, value_min=None)
        if key == "value_max":
            return dataclasses.replace(self._criteria, value_max=None)
        if key == "search":
            return dataclasses.replace(self._criteria, search="")
        return self._criteria

    def _start_query(self, *, append: bool) -> None:
        if self._repository is None:
            self._show_state("Local transaction data is unavailable.")
            return
        self._request_id += 1
        request_id = self._request_id
        offset = self.model.rowCount() if append else 0
        try:
            query = self._criteria.to_query(limit=DEFAULT_PAGE_SIZE, offset=offset)
        except ValueError as exc:
            self._show_state(str(exc))
            return

        self.progress.show()
        self.load_more_button.setEnabled(False)
        if not append:
            self._show_state("Loading transactions...")
        worker = Worker(self._repository.query, query)
        worker.signals.result.connect(
            lambda page: self._on_result(request_id, append, page)
        )
        worker.signals.error.connect(lambda error: self._on_error(request_id, error))
        worker.signals.finished.connect(partial(self._release_worker, request_id))
        self._workers[request_id] = worker
        self._thread_pool.start(worker)

    def _release_worker(self, request_id: int) -> None:
        self._workers.pop(request_id, None)

    def _on_result(self, request_id: int, append: bool, page: FeedPage) -> None:
        if request_id != self._request_id:
            return
        if append:
            self.model.append_records(page.records)
        else:
            self.model.replace_records(page.records)
        self._total_count = page.total_count
        self.resultCountChanged.emit(page.total_count)
        self._set_freshness(page)
        self.progress.hide()
        self.load_more_button.setEnabled(True)
        self.load_more_button.setVisible(page.has_more)
        if page.total_count == 0:
            message = (
                "No transactions match your search."
                if self._criteria.search
                else "No local transactions yet. Run a scan from Tools."
            )
            self._show_state(message)
        else:
            self.state_label.hide()

    def _on_error(self, request_id: int, error_info) -> None:
        if request_id != self._request_id:
            return
        log.error("Unified feed query failed: %s", error_info[1])
        self.progress.hide()
        self.load_more_button.setEnabled(True)
        self.load_more_button.hide()
        self._show_state("Could not load local transactions.")

    def _set_freshness(self, page: FeedPage) -> None:
        if page.freshness_at is None:
            text = "No local data"
        else:
            timestamp = self._format_timestamp(page.freshness_at)
            text = f"Local data stale · {timestamp}" if page.is_stale else timestamp
        self._freshness_text = text
        self.freshnessChanged.emit(text)

    def _show_state(self, message: str) -> None:
        self.state_label.setText(message)
        self.state_label.show()

    def _open_selected_source(self, index) -> None:
        if not index.isValid():
            return
        record = self.model.record_at(index.row())
        if record.source_url.startswith(("https://", "http://")):
            webbrowser.open(record.source_url)

    def _on_row_clicked(self, index) -> None:
        # Reopen-only affordance: after the drawer is dismissed the row stays
        # selected, so selectionChanged will not re-fire. A click then reopens
        # it. Normal selection changes are handled by _on_row_selected, so this
        # guard removes any dependency on click/selection signal ordering.
        if index.isValid() and self.drawer.isHidden():
            self._show_drawer_for_row(index.row())

    def _on_row_selected(self, selected, deselected) -> None:
        selection = self.table.selectionModel()
        if selection is None:
            return
        indexes = selection.selectedRows()
        if not indexes:
            self.drawer.hide()
            self._drawer_origin_row = -1
            return
        self._show_drawer_for_row(indexes[0].row())

    def _show_drawer_for_row(self, row: int) -> None:
        if not 0 <= row < self.model.rowCount():
            return
        if row == self._drawer_origin_row and not self.drawer.isHidden():
            return
        self._drawer_origin_row = row
        self.drawer.show_record(self.model.record_at(row))
        self.drawer.show()

    def _close_drawer(self) -> None:
        self.drawer.hide()
        self.table.setFocus()

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return f"Updated {value.astimezone().strftime('%Y-%m-%d %H:%M')}"

"""Watchlist management page.

Left: the list of watchlists with create / rename / delete actions.
Right: the entries of the selected watchlist, with add / remove and one-click
navigation to the feed, company, or insider research views.

The page owns an immutable :class:`WatchlistStore`; every mutation produces a new
store and is announced via the ``watchlistsChanged`` signal so the host window can
persist it.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import (
    WatchEntry,
    WatchlistStore,
)

_DEFAULT_LIST_NAME = "Watchlist"
_ENTRY_HEADERS = ("Kind", "Market", "Identifier / Person", "Label", "Note")


class WatchlistPage(QWidget):
    """Manage named watchlists of companies and insiders."""

    watchlistsChanged = Signal(object)  # WatchlistStore
    openCompanyRequested = Signal(object, object)  # (identifier, market)
    openInsiderRequested = Signal(object, object)  # (person, market)
    feedSearchRequested = Signal(object)  # (text)

    def __init__(
        self, store: WatchlistStore | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._store: WatchlistStore = store or WatchlistStore()
        self._current_name: str | None = None
        self._current_entries: tuple[WatchEntry, ...] = ()
        self._build_ui()
        self._refresh_lists()
        names = self._store.names()
        if names:
            self._select_watchlist(names[0])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self) -> WatchlistStore:
        return self._store

    def set_store(self, store: WatchlistStore) -> None:
        """Replace the store without emitting (used to load persisted state)."""
        self._store = store
        self._current_name = None
        self._refresh_lists()
        names = store.names()
        if names:
            self._select_watchlist(names[0])
        else:
            self._refresh_entries()

    def add_entry(self, entry: WatchEntry) -> None:
        """Append *entry* to the current list, creating a default one if needed."""
        target = self._current_name
        if target is None:
            target = (
                self._store.names()[0] if self._store.names() else _DEFAULT_LIST_NAME
            )
        new_store = self._store.with_entry(target, entry)
        self._current_name = target
        self._apply(new_store)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(16)
        layout.addLayout(self._build_left_pane(), stretch=0)
        layout.addLayout(self._build_right_pane(), stretch=1)

    def _build_left_pane(self) -> QVBoxLayout:
        pane = QVBoxLayout()
        pane.setSpacing(8)

        header = QLabel("Watchlists")
        header.setObjectName("watchlistHeader")
        bold = QFont()
        bold.setBold(True)
        header.setFont(bold)
        pane.addWidget(header)

        self.list_widget = QListWidget()
        self.list_widget.setAccessibleName("Watchlists")
        self.list_widget.setFixedWidth(200)
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        pane.addWidget(self.list_widget, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.new_button = QPushButton("New")
        self.new_button.setAccessibleName("New watchlist")
        self.new_button.clicked.connect(self._create_watchlist)
        self.rename_button = QPushButton("Rename")
        self.rename_button.setAccessibleName("Rename watchlist")
        self.rename_button.clicked.connect(self._rename_watchlist)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setAccessibleName("Delete watchlist")
        self.delete_button.clicked.connect(self._delete_watchlist)
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.rename_button)
        buttons.addWidget(self.delete_button)
        pane.addLayout(buttons)
        return pane

    def _build_right_pane(self) -> QVBoxLayout:
        pane = QVBoxLayout()
        pane.setSpacing(8)

        # Add-entry controls
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.kind_combo = QComboBox()
        self.kind_combo.setAccessibleName("Entry kind")
        self.kind_combo.addItems(["Company", "Insider"])
        self.market_combo = QComboBox()
        self.market_combo.setAccessibleName("Entry market")
        self.market_combo.addItems([m.value for m in FeedMarket])
        self.entry_input = QLineEdit()
        self.entry_input.setAccessibleName("Entry identifier or person")
        self.entry_input.setPlaceholderText("Ticker / ISIN or insider name")
        self.entry_input.setMaxLength(120)
        self.entry_input.returnPressed.connect(self._add_entry)
        self.add_button = QPushButton("Add")
        self.add_button.setAccessibleName("Add entry")
        self.add_button.clicked.connect(self._add_entry)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setAccessibleName("Remove selected entry")
        self.remove_button.clicked.connect(self._remove_selected_entry)
        add_row.addWidget(self.kind_combo)
        add_row.addWidget(self.market_combo)
        add_row.addWidget(self.entry_input, stretch=1)
        add_row.addWidget(self.add_button)
        add_row.addWidget(self.remove_button)
        pane.addLayout(add_row)

        # Entries table
        self.entries_table = QTableWidget(0, len(_ENTRY_HEADERS))
        self.entries_table.setAccessibleName("Watchlist entries")
        self.entries_table.setHorizontalHeaderLabels(list(_ENTRY_HEADERS))
        self.entries_table.verticalHeader().setVisible(False)
        self.entries_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.entries_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.entries_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.entries_table.horizontalHeader().setStretchLastSection(True)
        self.entries_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        pane.addWidget(self.entries_table, stretch=1)

        # Navigation actions
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self.open_feed_button = QPushButton("Open in feed")
        self.open_feed_button.setAccessibleName("Open selected entry in feed")
        self.open_feed_button.clicked.connect(self._open_in_feed)
        self.open_company_button = QPushButton("Open company")
        self.open_company_button.setAccessibleName("Open company page")
        self.open_company_button.clicked.connect(self._open_company)
        self.open_insider_button = QPushButton("Open insider")
        self.open_insider_button.setAccessibleName("Open insider page")
        self.open_insider_button.clicked.connect(self._open_insider)
        nav_row.addWidget(self.open_feed_button)
        nav_row.addWidget(self.open_company_button)
        nav_row.addWidget(self.open_insider_button)
        nav_row.addStretch()
        pane.addLayout(nav_row)
        return pane

    # ------------------------------------------------------------------
    # Watchlist CRUD
    # ------------------------------------------------------------------

    def _create_watchlist(self) -> None:
        name, ok = QInputDialog.getText(self, "New watchlist", "Name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self._current_name = name
        self._apply(self._store.with_list(name))

    def _rename_watchlist(self) -> None:
        if self._current_name is None:
            return
        old = self._current_name
        name, ok = QInputDialog.getText(self, "Rename watchlist", "Name:", text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        new_name = name.strip()
        self._current_name = new_name
        self._apply(self._store.renamed(old, new_name))

    def _delete_watchlist(self) -> None:
        if self._current_name is None:
            return
        name = self._current_name
        answer = QMessageBox.question(
            self, "Delete watchlist", f"Delete watchlist '{name}'?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._current_name = None
        self._apply(self._store.without_list(name))

    # ------------------------------------------------------------------
    # Entry operations
    # ------------------------------------------------------------------

    def _add_entry(self) -> None:
        if self._current_name is None:
            return
        text = self.entry_input.text().strip()
        if not text:
            return
        kind = self.kind_combo.currentText().casefold()
        market = FeedMarket(self.market_combo.currentText())
        if kind == "company":
            entry = WatchEntry(
                kind="company",
                market=market,
                identifier=text.upper(),
                label=text.upper(),
            )
        else:
            entry = WatchEntry(kind="insider", market=market, person=text, label=text)
        self.entry_input.clear()
        self._apply(self._store.with_entry(self._current_name, entry))

    def _remove_selected_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None or self._current_name is None:
            return
        self._apply(self._store.without_entry(self._current_name, entry.key()))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _open_in_feed(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        text = entry.identifier or entry.person
        if text:
            self.feedSearchRequested.emit(text)

    def _open_company(self) -> None:
        entry = self._selected_entry()
        if entry is not None and entry.kind == "company" and entry.identifier:
            self.openCompanyRequested.emit(entry.identifier, entry.market)

    def _open_insider(self) -> None:
        entry = self._selected_entry()
        if entry is not None and entry.kind == "insider" and entry.person:
            self.openInsiderRequested.emit(entry.person, entry.market)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply(self, new_store: WatchlistStore) -> None:
        self._store = new_store
        self.watchlistsChanged.emit(new_store)
        self._refresh_lists()
        if self._current_name is not None and self._store.get(self._current_name):
            self._select_watchlist(self._current_name)
        else:
            self._current_name = None
            self._refresh_entries()

    def _refresh_lists(self) -> None:
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.addItems(list(self._store.names()))
        if self._current_name is not None:
            for row in range(self.list_widget.count()):
                if self.list_widget.item(row).text() == self._current_name:
                    self.list_widget.setCurrentRow(row)
                    break
        self.list_widget.blockSignals(False)

    def _select_watchlist(self, name: str) -> None:
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).text() == name:
                self.list_widget.setCurrentRow(row)
                self._current_name = name
                self._refresh_entries()
                return

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        self._current_name = current.text()
        self._refresh_entries()

    def _refresh_entries(self) -> None:
        watchlist = (
            self._store.get(self._current_name)
            if self._current_name is not None
            else None
        )
        self._current_entries = watchlist.entries if watchlist is not None else ()
        self.entries_table.setRowCount(len(self._current_entries))
        for row, entry in enumerate(self._current_entries):
            who = entry.identifier or entry.person
            values = (
                entry.kind.capitalize(),
                entry.market.value,
                who,
                entry.label,
                entry.note,
            )
            for col, value in enumerate(values):
                self.entries_table.setItem(row, col, QTableWidgetItem(value))

    def _selected_entry(self) -> WatchEntry | None:
        row = self.entries_table.currentRow()
        if 0 <= row < len(self._current_entries):
            return self._current_entries[row]
        return None

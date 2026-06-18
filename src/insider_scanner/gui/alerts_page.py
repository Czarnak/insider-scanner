"""Alerts page.

Top: the saved alert rules, each toggleable, with create / delete actions and a
"Check now" trigger. Bottom: the new matching transactions from the last check,
grouped by the rule that produced them so the user can see *why* each row fired.

The page owns an immutable :class:`AlertStore`; every mutation produces a new store
and is announced via ``rulesChanged`` so the host window can persist it. Evaluating
rules against the feed is the host window's job — the page only emits
``checkRequested`` and renders the resulting :class:`AlertHit` list.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.gui.theme import get_theme_manager
from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import FeedCriteria, FeedRecord

_PURCHASE_TYPES: frozenset[str] = frozenset({"buy", "purchase"})
_SALE_TYPES: frozenset[str] = frozenset({"sell", "sale"})

_RULE_HEADERS = ("On", "Name", "Matches")
_RESULT_HEADERS = (
    "Alert",
    "Trade date",
    "Type",
    "Company",
    "Identifier",
    "Insider",
    "Value",
)


def _format_value(record: FeedRecord) -> str:
    if record.value_display:
        return record.value_display
    if record.value_sort is None:
        return "—"
    suffix = f" {record.currency}" if record.currency else ""
    return f"{record.value_sort:,.2f}{suffix}"


def _criteria_summary(rule: AlertRule) -> str:
    if rule.watchlist is not None:
        return f"Watchlist: {rule.watchlist}"
    c = rule.criteria
    parts: list[str] = []
    if c.search:
        parts.append(f'"{c.search}"')
    if c.markets:
        parts.append("/".join(m.value for m in c.markets))
    if c.directions:
        parts.append("/".join(d.value for d in c.directions))
    if c.value_min is not None:
        parts.append(f"≥ {c.value_min:,.0f}")
    if c.value_max is not None:
        parts.append(f"≤ {c.value_max:,.0f}")
    return ", ".join(parts) if parts else "All transactions"


class AlertsPage(QWidget):
    """Manage in-app alert rules and review their latest matches."""

    rulesChanged = Signal(object)  # AlertStore
    checkRequested = Signal()
    openCompanyRequested = Signal(object, object)
    openInsiderRequested = Signal(object, object)

    def __init__(
        self,
        store: AlertStore | None = None,
        *,
        criteria_provider=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store: AlertStore = store or AlertStore()
        self._criteria_provider = criteria_provider or (lambda: FeedCriteria())
        self._hits: tuple = ()
        self._result_rows: list[tuple[str, FeedRecord]] = []
        self._palette = get_theme_manager().palette()
        self._build_ui()
        self._refresh_rules()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self) -> AlertStore:
        return self._store

    def set_store(self, store: AlertStore) -> None:
        """Replace the store without emitting (used to load persisted state)."""
        self._store = store
        self._refresh_rules()

    def set_criteria_provider(self, provider) -> None:
        self._criteria_provider = provider

    def new_match_count(self) -> int:
        return sum(hit.match_count for hit in self._hits)

    def display_hits(self, hits: tuple) -> None:
        """Render *hits* (an AlertHit tuple) into the results table."""
        self._hits = tuple(hits)
        self._result_rows = [
            (hit.rule_name, record) for hit in self._hits for record in hit.records
        ]
        self._refresh_results()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        header = QLabel("Alerts")
        bold = QFont()
        bold.setBold(True)
        header.setFont(bold)
        layout.addWidget(header)

        # Rule actions
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.new_button = QPushButton("New from filters")
        self.new_button.setAccessibleName("Create alert from current feed filters")
        self.new_button.clicked.connect(self._create_from_filters)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setAccessibleName("Delete selected alert")
        self.delete_button.clicked.connect(self._delete_selected_rule)
        self.check_button = QPushButton("Check now")
        self.check_button.setAccessibleName("Check alerts against local data")
        self.check_button.clicked.connect(self.checkRequested)
        self.mark_seen_button = QPushButton("Mark all seen")
        self.mark_seen_button.setAccessibleName("Mark all alert matches as seen")
        self.mark_seen_button.clicked.connect(self._mark_all_seen)
        action_row.addWidget(self.new_button)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.check_button)
        action_row.addWidget(self.mark_seen_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Rules table
        self.rules_table = QTableWidget(0, len(_RULE_HEADERS))
        self.rules_table.setAccessibleName("Alert rules")
        self.rules_table.setHorizontalHeaderLabels(list(_RULE_HEADERS))
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.rules_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.rules_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rules_table.horizontalHeader().setStretchLastSection(True)
        self.rules_table.itemChanged.connect(self._on_rule_item_changed)
        layout.addWidget(self.rules_table, stretch=1)

        # Results
        self.summary_label = QLabel("No matches yet. Use Check now.")
        self.summary_label.setAccessibleName("Alert match summary")
        layout.addWidget(self.summary_label)

        self.results_table = QTableWidget(0, len(_RESULT_HEADERS))
        self.results_table.setAccessibleName("Alert matches")
        self.results_table.setHorizontalHeaderLabels(list(_RESULT_HEADERS))
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.results_table.doubleClicked.connect(self._open_selected_result)
        layout.addWidget(self.results_table, stretch=2)

    # ------------------------------------------------------------------
    # Rule operations
    # ------------------------------------------------------------------

    def _create_from_filters(self) -> None:
        name, ok = QInputDialog.getText(self, "New alert", "Alert name:")
        if not ok or not name.strip():
            return
        rule = AlertRule(name=name.strip(), criteria=self._criteria_provider())
        self._apply(self._store.with_rule(rule))

    def _delete_selected_rule(self) -> None:
        name = self._selected_rule_name()
        if name is None:
            return
        answer = QMessageBox.question(self, "Delete alert", f"Delete alert '{name}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._apply(self._store.without_rule(name))

    def _mark_all_seen(self) -> None:
        self._apply(self._store.marked_all_seen(datetime.now(UTC)))
        self.display_hits(())

    def _on_rule_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        row = item.row()
        name_item = self.rules_table.item(row, 1)
        if name_item is None:
            return
        name = name_item.text()
        enabled = item.checkState() == Qt.CheckState.Checked
        rule = self._store.get(name)
        if rule is not None and rule.enabled != enabled:
            self._apply(self._store.toggled(name, enabled))

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _open_selected_result(self, index) -> None:
        if not index.isValid():
            return
        row = index.row()
        if not 0 <= row < len(self._result_rows):
            return
        _, record = self._result_rows[row]
        if record.identifier:
            self.openCompanyRequested.emit(record.identifier, record.market)
        elif record.person:
            self.openInsiderRequested.emit(record.person, record.market)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply(self, new_store: AlertStore) -> None:
        self._store = new_store
        self.rulesChanged.emit(new_store)
        self._refresh_rules()

    def _selected_rule_name(self) -> str | None:
        row = self.rules_table.currentRow()
        if 0 <= row < len(self._store.rules):
            return self._store.rules[row].name
        return None

    def _refresh_rules(self) -> None:
        self.rules_table.blockSignals(True)
        self.rules_table.setRowCount(len(self._store.rules))
        for row, rule in enumerate(self._store.rules):
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked
            )
            self.rules_table.setItem(row, 0, check_item)
            self.rules_table.setItem(row, 1, QTableWidgetItem(rule.name))
            self.rules_table.setItem(row, 2, QTableWidgetItem(_criteria_summary(rule)))
        self.rules_table.blockSignals(False)

    def _refresh_results(self) -> None:
        self.results_table.setRowCount(len(self._result_rows))
        for row, (rule_name, record) in enumerate(self._result_rows):
            date_str = str(record.transaction_date) if record.transaction_date else "—"
            values = (
                rule_name,
                date_str,
                record.transaction_type,
                record.issuer or "—",
                record.identifier or "—",
                record.person or "—",
                _format_value(record),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 2:
                    brush = self._type_brush(record.transaction_type)
                    if brush is not None:
                        item.setForeground(brush)
                self.results_table.setItem(row, col, item)
        total = len(self._result_rows)
        rules = len(self._hits)
        if total == 0:
            self.summary_label.setText("No new matches.")
        else:
            noun = "match" if total == 1 else "matches"
            alert_noun = "alert" if rules == 1 else "alerts"
            self.summary_label.setText(
                f"{total} new {noun} across {rules} {alert_noun}"
            )

    def _type_brush(self, transaction_type: str) -> QBrush | None:
        normalized = transaction_type.casefold()
        if normalized in _PURCHASE_TYPES:
            return QBrush(self._palette.purchase)
        if normalized in _SALE_TYPES:
            return QBrush(self._palette.sale)
        return None

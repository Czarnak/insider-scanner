"""Shared transaction presentation for feed, alerts and research views."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from insider_scanner.persistence.feed import FeedRecord


def format_value(record: FeedRecord) -> str:
    """Prefer the source's reported range; otherwise format its numeric value."""
    if record.value_display:
        return record.value_display
    if record.value_sort is None:
        return "—"
    suffix = f" {record.currency}" if record.currency else ""
    return f"{record.value_sort:,.2f}{suffix}"


def make_feed_table(model: QAbstractTableModel, accessible_name: str) -> QTableView:
    """Build the shared feed/history table with consistent interaction and widths."""
    table = QTableView()
    table.setAccessibleName(accessible_name)
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    for column, width in enumerate(
        (70, 90, 170, 100, 150, 100, 90, 90, 90, 90, 120, 80)
    ):
        table.setColumnWidth(column, width)
    return table

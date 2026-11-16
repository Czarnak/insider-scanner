"""Displayed trades remain authoritative through filters, selection and export."""

from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView

from insider_scanner.gui.congress_tab import CongressTab
from insider_scanner.gui.scan_tab import ScanTab
from tests.factories import congress_trade, us_trade


class QueuedPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.fixture(params=["scan", "congress"])
def workflow(request, qtbot, monkeypatch):
    pool = QueuedPool()
    if request.param == "scan":
        tab = ScanTab(thread_pool=pool)
        trades = [
            us_trade(insider_name="Excluded", trade_type="Sell", value=1),
            us_trade(
                insider_name="Small",
                trade_type="Buy",
                value=95,
                edgar_url="https://example.test/small",
            ),
            us_trade(
                insider_name="Large",
                trade_type="Buy",
                value=1000000,
                edgar_url="https://example.test/large",
                is_congress=True,
            ),
        ]
        kind, column, name = "Buy", "value", "insider_name"
        save_path = "insider_scanner.core.merger.save_scan_results"
    else:
        tab = CongressTab(thread_pool=pool)
        trades = [
            congress_trade(official_name="Excluded", trade_type="Sale", amount_low=1),
            congress_trade(
                official_name="Small",
                trade_type="Purchase",
                amount_low=95,
                amount_range="$95 - $100",
                source_url="https://example.test/small",
            ),
            congress_trade(
                official_name="Large",
                trade_type="Purchase",
                amount_low=1000000,
                amount_range="$1,000,000 - $2,000,000",
                source_url="https://example.test/large",
            ),
        ]
        kind, column, name = "Purchase", "amount_range", "official_name"
        save_path = "insider_scanner.gui.congress_tab.save_congress_results"
    qtbot.addWidget(tab)
    saved = Mock(return_value="test-output")
    monkeypatch.setattr(save_path, saved)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *a: None)
    return tab, trades, kind, column, name, pool, saved


def test_new_results_obey_existing_filters(workflow):
    tab, trades, kind, _, name, _, _ = workflow
    tab.type_combo.setCurrentText(kind)
    tab.min_value_spin.setValue(100)
    tab._on_scan_done(trades)
    col = next(
        i
        for i in range(tab.trades_model.columnCount())
        if tab.trades_model.headerData(i, Qt.Orientation.Horizontal) == name
    )
    assert tab.trades_model.rowCount() == 1
    assert tab.trades_model.index(0, col).data() == "Large"
    tab._on_scan_done([])
    assert tab.trades_model.rowCount() == 0
    assert not tab.btn_save.isEnabled()


def test_filtered_sorted_detail_and_filing_use_displayed_trade(workflow, monkeypatch):
    tab, trades, kind, column, _, _, _ = workflow
    tab._on_scan_done(trades)
    tab.type_combo.setCurrentText(kind)
    tab._apply_filters()
    col = next(
        i
        for i in range(tab.trades_model.columnCount())
        if tab.trades_model.headerData(i, Qt.Orientation.Horizontal) == column
    )
    tab.trades_model.sort(col, Qt.SortOrder.DescendingOrder)
    tab.trades_table.selectRow(0)
    tab._on_row_double_click(tab.trades_model.index(0, 0))
    assert "Large" in tab.detail_label.text()
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    if isinstance(tab, ScanTab):
        tab._open_edgar()
    else:
        tab._open_filing()
    assert opened == ["https://example.test/large"]
    tab.min_value_spin.setValue(2000000)
    tab._apply_filters()
    assert tab.detail_label.text() == ""
    button = tab.btn_edgar if isinstance(tab, ScanTab) else tab.btn_open_filing
    assert not button.isEnabled()


def test_export_queues_snapshot_of_displayed_trades(workflow):
    tab, trades, kind, _, _, pool, saved = workflow
    tab._on_scan_done(trades)
    tab.type_combo.setCurrentText(kind)
    tab._apply_filters()
    tab._save_results()
    saved.assert_not_called()
    assert len(pool.workers) == 1
    tab.min_value_spin.setValue(2000000)
    tab._apply_filters()
    pool.workers[0].run()
    assert saved.call_args.args[0] == trades[1:]
    assert not tab.btn_save.isEnabled()


def test_empty_filtered_results_cannot_export_original_rows(workflow):
    tab, trades, _, _, _, pool, saved = workflow
    tab._on_scan_done(trades)
    tab.min_value_spin.setValue(2000000)
    tab._apply_filters()
    assert tab.trades_model.rowCount() == 0
    assert not tab.btn_save.isEnabled()
    tab._save_results()
    saved.assert_not_called()
    assert pool.workers == []


def test_headers_remain_interactive_after_filter_and_sort(workflow):
    tab, trades, _, _, _, _, _ = workflow
    tab._on_scan_done(trades)
    header = tab.trades_table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.resizeContentsPrecision() <= 100
    assert header.stretchLastSection()


def test_scan_highlighting_retains_congress_metadata(qtbot):
    tab = ScanTab()
    qtbot.addWidget(tab)
    tab._display_trades([us_trade(is_congress=True, trade_type="Buy")])
    foreground = tab.trades_model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole)
    from insider_scanner.gui.theme import get_theme_manager

    assert foreground is not None
    assert foreground.name() == get_theme_manager().palette().warning.lower()

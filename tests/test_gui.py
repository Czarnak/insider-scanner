"""GUI tests using pytest-qt for widget creation and basic interactions."""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt


class TestPandasTableModel:
    def test_set_dataframe(self, qtbot):
        from insider_scanner.gui.widgets import PandasTableModel

        model = PandasTableModel()
        df = pd.DataFrame({"ticker": ["AAPL", "MSFT"], "value": [1000.0, 2000.0]})
        model.set_dataframe(df)
        assert model.rowCount() == 2
        assert model.columnCount() == 2

    def test_data_formatting(self, qtbot):
        from insider_scanner.gui.widgets import PandasTableModel

        model = PandasTableModel()
        df = pd.DataFrame({"val": [1234567.89]})
        model.set_dataframe(df)
        idx = model.index(0, 0)
        assert model.data(idx) == "1,234,567.89"

    def test_empty(self, qtbot):
        from insider_scanner.gui.widgets import PandasTableModel

        model = PandasTableModel()
        assert model.rowCount() == 0


class TestSortableTableModel:
    def test_set_and_sort(self, qtbot):
        from insider_scanner.gui.widgets import SortableTableModel

        model = SortableTableModel()
        df = pd.DataFrame({"name": ["B", "A", "C"], "val": [2, 1, 3]})
        model.set_dataframe(df)
        assert model.rowCount() == 3
        model.sort(1, Qt.SortOrder.AscendingOrder)
        assert model.rowCount() == 3


class TestScanTab:
    def test_create(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert tab.btn_scan is not None
        assert tab.btn_latest is not None
        assert tab.ticker_edit is not None

    def test_empty_ticker_warning(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        # Ticker is empty, scan should not crash
        tab.ticker_edit.setText("")
        # _run_scan shows a warning but doesn't crash

    def test_date_widgets_exist(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert tab.start_date is not None
        assert tab.end_date is not None
        assert tab.chk_use_dates is not None

    def test_date_toggle_enables_fields(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        # Initially disabled
        assert not tab.start_date.isEnabled()
        assert not tab.end_date.isEnabled()
        # Enable
        tab.chk_use_dates.setChecked(True)
        assert tab.start_date.isEnabled()
        assert tab.end_date.isEnabled()
        # Disable again
        tab.chk_use_dates.setChecked(False)
        assert not tab.start_date.isEnabled()
        assert not tab.end_date.isEnabled()

    def test_get_dates_disabled(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        # When dates unchecked, helpers return None
        assert tab._get_start_date() is None
        assert tab._get_end_date() is None

    def test_get_dates_enabled(self, qtbot):
        from datetime import date
        from PySide6.QtCore import QDate
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        tab.chk_use_dates.setChecked(True)
        tab.start_date.setDate(QDate(2025, 3, 15))
        tab.end_date.setDate(QDate(2025, 9, 30))
        assert tab._get_start_date() == date(2025, 3, 15)
        assert tab._get_end_date() == date(2025, 9, 30)

    def test_latest_count_spin_exists(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert tab.latest_count_spin is not None
        assert tab.latest_count_spin.value() == 100
        assert tab.latest_count_spin.minimum() == 10
        assert tab.latest_count_spin.maximum() == 500

    def test_latest_count_spin_change(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        tab.latest_count_spin.setValue(250)
        assert tab.latest_count_spin.value() == 250

    def test_watchlist_button_exists(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert tab.btn_watchlist is not None
        assert tab.btn_watchlist.isEnabled()

    def test_set_scan_buttons_enabled(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        tab._set_scan_buttons_enabled(False)
        assert not tab.btn_scan.isEnabled()
        assert not tab.btn_latest.isEnabled()
        assert not tab.btn_watchlist.isEnabled()
        assert not tab.btn_stop.isHidden()  # stop visible when scanning
        tab._set_scan_buttons_enabled(True)
        assert tab.btn_scan.isEnabled()
        assert tab.btn_latest.isEnabled()
        assert tab.btn_watchlist.isEnabled()
        assert tab.btn_stop.isHidden()  # stop hidden when idle

    def test_stop_button_exists(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert tab.btn_stop is not None
        # Initially hidden
        assert tab.btn_stop.isHidden()

    def test_stop_scan_sets_cancel_event(self, qtbot):
        from insider_scanner.gui.scan_tab import ScanTab

        tab = ScanTab()
        qtbot.addWidget(tab)
        assert not tab._cancel_event.is_set()
        tab._stop_scan()
        assert tab._cancel_event.is_set()


class TestMainWindow:
    def test_create(self, qtbot):
        from insider_scanner.gui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)
        assert win.page_stack.count() == 9
        assert win.page_stack.currentWidget() is win.feed_page

    def test_navigation_names(self, qtbot):
        from insider_scanner.gui.main_window import MainWindow
        from insider_scanner.gui.european_tab import EuropeanTab

        win = MainWindow()
        qtbot.addWidget(win)
        assert list(win.navigation_buttons) == [
            "Feed",
            "Watchlists",
            "Alerts",
            "Companies",
            "Insiders",
            "Insider Scan",
            "Congress Scan",
            "European Scan",
            "Analysis",
        ]
        assert isinstance(win.european_tab, EuropeanTab)

    def test_status_bar(self, qtbot):
        from insider_scanner.gui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)
        win.log_status("Testing")
        assert win.status_bar.currentMessage() == "Testing"

    def test_main_window_has_analysis_tab(self, qtbot):
        from insider_scanner.gui.main_window import MainWindow

        win = MainWindow()
        qtbot.addWidget(win)
        assert "Analysis" in win.navigation_buttons
        assert win.analysis_tab._thread_pool is win._thread_pool


class TestEuropeanTab:
    def _sample_trade(self, **overrides):
        from datetime import date
        from insider_scanner.core.eu_models import EuropeanInsiderTrade

        base = EuropeanInsiderTrade(
            isin="GB0002875804",
            issuer_name="Example PLC",
            country="UK",
            regulatory_body="FCA",
            insider_name="Jane Doe",
            position="Executive",
            trade_date=date(2026, 2, 1),
            filing_date=date(2026, 2, 2),
            trade_type="Buy",
            instrument_type="Share",
            volume=100.0,
            price=2.5,
            currency="GBP",
            total_value=250.0,
            source="rns",
            source_url="https://example.com/announcement",
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_create(self, qtbot):
        from insider_scanner.gui.european_tab import EuropeanTab

        tab = EuropeanTab()
        qtbot.addWidget(tab)
        assert tab.btn_scan is not None
        assert tab.btn_watchlist is not None
        assert tab.trades_table.model() is tab.trades_model

    def test_apply_filters_and_show_detail(self, qtbot):
        from insider_scanner.gui.european_tab import EuropeanTab

        tab = EuropeanTab()
        qtbot.addWidget(tab)
        trades = [
            self._sample_trade(),
            self._sample_trade(
                isin="NL0000009165",
                country="NL",
                regulatory_body="AFM",
                trade_type="Sell",
                total_value=50.0,
                source_url="",
            ),
        ]
        tab._update_table(trades)
        tab.country_combo.setCurrentText("UK")
        tab.type_combo.setCurrentText("Buy")
        tab.min_value_spin.setValue(100.0)

        tab._apply_filters()

        assert tab.trades_model.rowCount() == 1
        assert "1 trade(s) shown" in tab.lbl_count.text()

        index = tab.trades_model.index(0, 0)
        tab._show_detail(index)

        assert "Example PLC" in tab.detail_text.toPlainText()
        assert tab.btn_open_source.isEnabled()


class TestCongressTab:
    def test_create(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        assert tab.btn_scan is not None
        assert tab.btn_stop is not None
        assert tab.btn_filter is not None
        assert tab.btn_save is not None
        assert tab.btn_open_filing is not None

    def test_source_checkboxes(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        assert tab.chk_house.isChecked()
        assert tab.chk_senate.isChecked()

    def test_official_combo(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        assert tab.official_combo.count() >= 1
        assert tab.official_combo.itemText(0) == "All"
        assert tab.official_combo.isEditable()

    def test_date_toggle(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        assert not tab.start_date.isEnabled()
        tab.chk_use_dates.setChecked(True)
        assert tab.start_date.isEnabled()
        assert tab.end_date.isEnabled()

    def test_type_combo(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        items = [tab.type_combo.itemText(i) for i in range(tab.type_combo.count())]
        assert "All" in items
        assert "Purchase" in items
        assert "Sale" in items

    def test_sector_combo(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        items = [tab.sector_combo.itemText(i) for i in range(tab.sector_combo.count())]
        assert "All" in items
        assert "Defense" in items
        assert "Finance" in items

    def test_set_scan_buttons_enabled(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        tab._set_scan_buttons_enabled(False)
        assert not tab.btn_scan.isEnabled()
        assert tab.btn_stop.isEnabled()  # stop enabled when scanning
        tab._set_scan_buttons_enabled(True)
        assert tab.btn_scan.isEnabled()
        assert not tab.btn_stop.isEnabled()  # stop disabled when idle

    def test_stop_sets_cancel_event(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        assert not tab._cancel_event.is_set()
        tab._stop_scan()
        assert tab._cancel_event.is_set()

    def test_display_empty_trades(self, qtbot):
        from insider_scanner.gui.congress_tab import CongressTab

        tab = CongressTab()
        qtbot.addWidget(tab)
        tab._display_trades([])
        assert "No trades" in tab.status_label.text()

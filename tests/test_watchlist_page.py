"""WatchlistPage widget tests (pytest-qt)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from insider_scanner.gui.watchlist_page import WatchlistPage
from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import (
    WatchEntry,
    Watchlist,
    WatchlistStore,
)


def _store_with_tech() -> WatchlistStore:
    return WatchlistStore(
        watchlists=(
            Watchlist(
                name="Tech",
                entries=(
                    WatchEntry(
                        kind="company",
                        market=FeedMarket.US,
                        identifier="AAPL",
                        label="AAPL",
                    ),
                    WatchEntry(
                        kind="insider",
                        market=FeedMarket.US,
                        person="Jane Director",
                        label="Jane Director",
                    ),
                ),
            ),
        )
    )


class TestWatchlistCrud:
    def test_create_watchlist_emits_changed(self, qtbot, monkeypatch):
        page = WatchlistPage()
        qtbot.addWidget(page)
        emitted: list = []
        page.watchlistsChanged.connect(emitted.append)

        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("Energy", True),
        )
        page._create_watchlist()

        assert "Energy" in page.store().names()
        assert emitted and emitted[-1].names() == ("Energy",)

    def test_create_watchlist_cancelled_is_noop(self, qtbot, monkeypatch):
        page = WatchlistPage()
        qtbot.addWidget(page)
        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("", False),
        )
        page._create_watchlist()
        assert page.store().names() == ()

    def test_rename_watchlist(self, qtbot, monkeypatch):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("Technology", True),
        )
        page._rename_watchlist()
        assert page.store().names() == ("Technology",)

    def test_delete_watchlist_confirmed(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        page._delete_watchlist()
        assert page.store().names() == ()


class TestEntries:
    def test_selecting_list_populates_entries_table(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        assert page.entries_table.rowCount() == 2

    def test_add_entry_updates_store_and_table(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")

        page.entry_input.setText("MSFT")
        page.kind_combo.setCurrentText("Company")
        page.market_combo.setCurrentText("US")
        qtbot.mouseClick(page.add_button, Qt.MouseButton.LeftButton)

        entries = page.store().get("Tech").entries
        assert any(e.identifier == "MSFT" for e in entries)
        assert page.entries_table.rowCount() == 3

    def test_remove_selected_entry(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        page.entries_table.selectRow(0)  # the AAPL company entry
        qtbot.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)

        entries = page.store().get("Tech").entries
        assert all(e.identifier != "AAPL" for e in entries)

    def test_public_add_entry_creates_default_list_when_empty(self, qtbot):
        page = WatchlistPage()
        qtbot.addWidget(page)
        page.add_entry(
            WatchEntry(
                kind="company", market=FeedMarket.US, identifier="NVDA", label="NVDA"
            )
        )
        # A list now exists holding the entry.
        names = page.store().names()
        assert len(names) == 1
        assert page.store().get(names[0]).entries[0].identifier == "NVDA"


class TestNavigationSignals:
    def test_open_company_signal(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        page.entries_table.selectRow(0)  # AAPL company

        emitted: list = []
        page.openCompanyRequested.connect(lambda i, m: emitted.append((i, m)))
        qtbot.mouseClick(page.open_company_button, Qt.MouseButton.LeftButton)
        assert emitted == [("AAPL", FeedMarket.US)]

    def test_open_insider_signal(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        page.entries_table.selectRow(1)  # Jane Director insider

        emitted: list = []
        page.openInsiderRequested.connect(lambda p, m: emitted.append((p, m)))
        qtbot.mouseClick(page.open_insider_button, Qt.MouseButton.LeftButton)
        assert emitted == [("Jane Director", FeedMarket.US)]

    def test_open_in_feed_signal(self, qtbot):
        page = WatchlistPage(_store_with_tech())
        qtbot.addWidget(page)
        page._select_watchlist("Tech")
        page.entries_table.selectRow(0)  # AAPL company

        emitted: list = []
        page.feedSearchRequested.connect(emitted.append)
        qtbot.mouseClick(page.open_feed_button, Qt.MouseButton.LeftButton)
        assert emitted == ["AAPL"]

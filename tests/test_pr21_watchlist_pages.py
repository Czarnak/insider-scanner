"""User-visible watchlist and alert failures preserve configuration."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from insider_scanner.gui import alerts_page, main_window, watchlist_page
from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import WatchEntry, Watchlist, WatchlistStore


@pytest.fixture
def warnings(monkeypatch):
    messages = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, message: messages.append(message),
    )
    return messages


def test_collision_keeps_selected_watchlist(qtbot, monkeypatch, warnings):
    store = WatchlistStore(watchlists=(Watchlist("Tech"), Watchlist("Energy")))
    page = watchlist_page.WatchlistPage(store)
    qtbot.addWidget(page)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText", lambda *a, **kw: ("energy", True)
    )
    page._rename_watchlist()
    assert page.store() == store
    assert page.list_widget.currentItem().text() == "Tech"
    assert warnings and "exist" in warnings[0].lower()


def test_entry_cap_keeps_input_and_store(qtbot, monkeypatch, warnings):
    from insider_scanner.persistence import watchlists

    monkeypatch.setattr(watchlists, "_MAX_ENTRIES", 1)
    store = WatchlistStore().with_entry(
        "Tech", WatchEntry("company", FeedMarket.US, identifier="AAPL")
    )
    page = watchlist_page.WatchlistPage(store)
    qtbot.addWidget(page)
    page.entry_input.setText("MSFT")
    page._add_entry()
    assert page.store() == store
    assert page.entry_input.text() == "MSFT"
    assert warnings


def test_alert_cap_reports_error_without_emitting(qtbot, monkeypatch, warnings):
    from insider_scanner.persistence import alerts

    monkeypatch.setattr(alerts, "_MAX_RULES", 1)
    store = AlertStore(rules=(AlertRule("Existing"),))
    page = alerts_page.AlertsPage(store)
    qtbot.addWidget(page)
    emitted = []
    page.rulesChanged.connect(emitted.append)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText", lambda *a, **kw: ("New", True)
    )
    page._create_from_filters()
    assert page.store() == store
    assert warnings
    assert emitted == []


def test_rename_persists_alert_reference(qtbot, monkeypatch, tmp_path):
    from insider_scanner.persistence.alerts import load_alerts, save_alerts
    from insider_scanner.persistence.watchlists import load_watchlists, save_watchlists

    save_watchlists(tmp_path / "watchlists.json", WatchlistStore().with_list("Tech"))
    save_alerts(
        tmp_path / "alerts.json",
        AlertStore(rules=(AlertRule("Watching tech", watchlist="Tech"),)),
    )
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText", lambda *a, **kw: ("Technology", True)
    )
    win.watchlist_page._rename_watchlist()
    assert win.alerts_page.store().get("Watching tech").watchlist == "Technology"
    assert (
        load_alerts(tmp_path / "alerts.json").get("Watching tech").watchlist
        == "Technology"
    )
    assert load_watchlists(tmp_path / "watchlists.json").names() == ("Technology",)


def test_invalid_alert_shows_error_and_cannot_be_enabled(qtbot, warnings):
    from insider_scanner.persistence.alerts import rule_from_dict

    rule = rule_from_dict({"name": "Broken", "criteria": {"markets": ["invalid"]}})
    page = alerts_page.AlertsPage(AlertStore(rules=(rule,)))
    qtbot.addWidget(page)
    assert "invalid" in page.rules_table.item(0, 2).text().lower()
    assert not page.rules_table.item(0, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_main_window_migrates_and_shares_watchlists(qtbot, tmp_path):
    (tmp_path / "tickers_watchlist.txt").write_text("AAPL\nMSFT\n", encoding="utf-8")
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    assert win.watchlist_page.store().us_symbols() == ("AAPL", "MSFT")
    assert [
        win.analysis_tab.symbol_combo.itemText(i)
        for i in range(win.analysis_tab.symbol_combo.count())
    ] == ["AAPL", "MSFT"]
    win.watchlist_page.add_entry(
        WatchEntry("company", FeedMarket.US, identifier="NVDA")
    )
    assert win.scan_tab._watchlists.us_symbols() == ("AAPL", "MSFT", "NVDA")
    assert win.analysis_tab.symbol_combo.findText("NVDA") >= 0


def test_migration_failure_keeps_existing_watchlists_and_warns(qtbot, tmp_path):
    from insider_scanner.persistence.watchlists import load_watchlists, save_watchlists

    original = WatchlistStore().with_list("Existing")
    save_watchlists(tmp_path / "watchlists.json", original)
    (tmp_path / "tickers_watchlist.txt").write_bytes(b"\xff\xfeinvalid")
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    assert win.watchlist_page.store() == original
    assert load_watchlists(tmp_path / "watchlists.json") == original
    assert "could not import" in win.status_bar.currentMessage().lower()


def test_feed_watch_cap_does_not_claim_success(qtbot, monkeypatch, tmp_path, warnings):
    from types import SimpleNamespace

    from insider_scanner.persistence import watchlists

    monkeypatch.setattr(watchlists, "_MAX_WATCHLISTS", 0)
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    win._on_watch_requested(
        SimpleNamespace(
            identifier="AAPL", person="", issuer="Apple", market=FeedMarket.US
        )
    )
    assert warnings
    assert "added to watchlist" not in win.status_bar.currentMessage().lower()


def test_rename_recovers_alert_reference_after_interrupted_save(
    qtbot, monkeypatch, tmp_path
):
    import os
    from pathlib import Path

    from insider_scanner.persistence.alerts import save_alerts
    from insider_scanner.persistence.watchlists import save_watchlists

    save_watchlists(tmp_path / "watchlists.json", WatchlistStore().with_list("Tech"))
    save_alerts(
        tmp_path / "alerts.json",
        AlertStore(rules=(AlertRule("Watching tech", watchlist="Tech"),)),
    )
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    replace_file = os.replace

    def fail_alert_save(source, target):
        if Path(target).name == "alerts.json":
            raise PermissionError("temporarily locked")
        return replace_file(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_alert_save)
        patch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("Technology", True),
        )
        win.watchlist_page._rename_watchlist()
    restarted = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(restarted)
    assert restarted.watchlist_page.store().names() == ("Technology",)
    assert restarted.alerts_page.store().get("Watching tech").watchlist == "Technology"


def test_failed_rename_journal_keeps_original_configuration(
    qtbot, monkeypatch, tmp_path, warnings
):
    import os
    from pathlib import Path

    from insider_scanner.persistence.watchlists import save_watchlists

    original = WatchlistStore().with_list("Tech")
    save_watchlists(tmp_path / "watchlists.json", original)
    win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    replace_file = os.replace

    def fail_journal_save(source, target):
        if Path(target).name == "watchlist_rename.json":
            raise PermissionError("cannot save journal")
        return replace_file(source, target)

    monkeypatch.setattr(os, "replace", fail_journal_save)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText", lambda *a, **kw: ("Technology", True)
    )
    win.watchlist_page._rename_watchlist()
    assert win.watchlist_page.store() == original
    assert win.watchlist_page.list_widget.currentItem().text() == "Tech"
    assert warnings


def test_unrecoverable_rename_blocks_startup_before_loading_partial_state(
    qtbot, monkeypatch, tmp_path
):
    def fail_recovery(_data_dir):
        raise PermissionError("locked rename files")

    monkeypatch.setattr(main_window, "recover_watchlist_rename", fail_recovery)
    with pytest.raises(main_window.MainWindowInitializationError, match="rename"):
        win = main_window.MainWindow(state_path=tmp_path / "feed_state.json")
        qtbot.addWidget(win)

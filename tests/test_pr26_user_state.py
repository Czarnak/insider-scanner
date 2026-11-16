"""User-state coordination preserves data and reports recoverable write failures."""

from __future__ import annotations

import pytest

from insider_scanner.gui import user_state as us
from insider_scanner.persistence.alerts import AlertRule, AlertStore, load_alerts
from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.feed_state import (
    FeedState,
    SavedScreen,
    load_feed_state,
)
from insider_scanner.persistence.watchlist_rename import WatchlistRenamePendingError
from insider_scanner.persistence.watchlists import (
    WatchlistError,
    WatchlistStore,
    load_watchlists,
)


def test_loads_existing_state_and_migrates_legacy_from_supplied_profile(tmp_path):
    state_path = tmp_path / "custom-state.json"
    criteria = FeedCriteria(search="AAPL")
    us.save_feed_state(state_path, FeedState(criteria=criteria))
    us.save_alerts(tmp_path / "alerts.json", AlertStore(rules=(AlertRule("Buys"),)))
    legacy = tmp_path / "tickers_watchlist.txt"
    legacy.write_text("AAPL\nMSFT\n", encoding="utf-8")

    state = us.UserStateStore(state_path)

    assert state.state_path == state_path
    assert state.data_dir == tmp_path
    assert state.feed_state.criteria == criteria
    assert state.alerts.rules[0].name == "Buys"
    assert state.watchlists.us_symbols() == ("AAPL", "MSFT")
    assert state.watchlists.legacy_watchlist_migrated
    assert state.startup_notice is None


def test_pending_rename_recovery_failure_stops_initialization(tmp_path, monkeypatch):
    def fail(_data_dir):
        raise WatchlistError("malformed journal")

    monkeypatch.setattr(us, "recover_watchlist_rename", fail)
    with pytest.raises(
        us.UserStateInitializationError, match="pending watchlist rename"
    ):
        us.UserStateStore(tmp_path / "feed_state.json")


def test_failed_legacy_migration_keeps_existing_lists_and_requests_retry(
    tmp_path, monkeypatch
):
    existing = WatchlistStore().with_list("Existing")
    us.save_watchlists(tmp_path / "watchlists.json", existing)
    original = us.load_watchlists

    def load(path, *, legacy_path=None):
        if legacy_path is not None:
            raise PermissionError("private profile path")
        return original(path)

    monkeypatch.setattr(us, "load_watchlists", load)
    state = us.UserStateStore(tmp_path / "feed_state.json")

    assert state.watchlists == existing
    assert "retry on next startup" in state.startup_notice
    assert "private profile path" not in state.startup_notice


@pytest.mark.parametrize(
    "error,writable",
    [(PermissionError("secret"), True), (RuntimeError("secret"), False)],
)
def test_failed_feed_save_preserves_latest_memory_and_screens(
    tmp_path, monkeypatch, error, writable
):
    notices = []
    state = us.UserStateStore(
        tmp_path / "feed_state.json", notify=lambda *args: notices.append(args)
    )
    state.set_screen("Saved", FeedCriteria(search="TSLA"))

    def fail(_path, _state):
        raise error

    monkeypatch.setattr(us, "save_feed_state", fail)
    criteria = FeedCriteria(search="NVDA")
    assert not state.save_feed(criteria, "layout")
    assert state.feed_state.criteria == criteria
    assert state.feed_state.column_layout == "layout"
    assert state.feed_state.screens == (
        SavedScreen("Saved", FeedCriteria(search="TSLA")),
    )
    message, timeout = notices[-1]
    assert "Couldn't save filters" in message
    assert ("writable" in message) is writable
    assert "secret" not in message
    assert timeout == 6_000


@pytest.mark.parametrize("kind", ["watchlists", "alerts"])
def test_pending_recovery_prevents_overwriting_coordinated_files(
    tmp_path, monkeypatch, kind
):
    notices = []
    state = us.UserStateStore(
        tmp_path / "feed_state.json", notify=lambda *args: notices.append(args)
    )
    candidate = (
        WatchlistStore().with_list("Changed")
        if kind == "watchlists"
        else AlertStore(rules=(AlertRule("Changed"),))
    )
    writes = []

    def fail(_data_dir):
        raise PermissionError("journal cannot be published")

    monkeypatch.setattr(us, "recover_watchlist_rename", fail)
    monkeypatch.setattr(us, f"save_{kind}", lambda *args: writes.append(args))
    assert not getattr(state, f"save_{kind}")(candidate)
    assert getattr(state, kind) == candidate
    assert writes == []
    assert "writable" in notices[-1][0]


def test_feed_snapshot_and_screen_replacement_cap_survive_restart(tmp_path):
    state = us.UserStateStore(tmp_path / "feed_state.json")
    for i in range(201):
        state.set_screen(f"Screen {i}", FeedCriteria(search=str(i)))
    state.set_screen("Screen 100", FeedCriteria(search="replacement"))
    state.delete_screen("Screen 101")
    criteria = FeedCriteria(search="active")

    assert state.save_feed(criteria, "columns")
    restored = load_feed_state(state.state_path)
    assert len(restored.screens) == 199
    assert restored.screens[0].name == "Screen 1"
    assert restored.screens[-1] == SavedScreen(
        "Screen 100", FeedCriteria(search="replacement")
    )
    assert all(screen.name != "Screen 101" for screen in restored.screens)
    assert restored.criteria == criteria
    assert restored.column_layout == "columns"


def test_rename_uses_current_alert_watermark_and_persists_both_stores(tmp_path):
    state = us.UserStateStore(tmp_path / "feed_state.json")
    before = WatchlistStore().with_list("Old")
    assert state.save_watchlists(before)
    stale = AlertStore(
        rules=(AlertRule("Rule", watchlist="Old", last_seen_sequence=1),)
    )
    assert state.save_alerts(stale)
    current = stale.marked_seen_sequence("Rule", 42)
    renamed = before.renamed("Old", "New")

    assert state.rename_watchlist(renamed, "Old", "New", alerts=current)
    assert state.watchlists == renamed
    assert state.alerts.rules[0].watchlist == "New"
    assert state.alerts.rules[0].last_seen_sequence == 42
    assert load_watchlists(state.watchlists_path) == renamed
    assert load_alerts(state.alerts_path) == state.alerts


def test_rename_failure_before_journal_retains_existing_memory(tmp_path, monkeypatch):
    notices = []
    state = us.UserStateStore(
        tmp_path / "feed_state.json", notify=lambda *args: notices.append(args)
    )
    state.watchlists = WatchlistStore().with_list("Old")
    state.alerts = AlertStore(rules=(AlertRule("Rule", watchlist="Old"),))
    before = (state.watchlists, state.alerts)

    def fail(*_args):
        raise PermissionError("cannot create journal")

    monkeypatch.setattr(us, "save_watchlist_rename", fail)
    assert not state.rename_watchlist(
        state.watchlists.renamed("Old", "New"), "Old", "New"
    )
    assert (state.watchlists, state.alerts) == before
    assert "writable" in notices[-1][0]


def test_journaled_rename_accepts_new_memory_and_reports_pending_recovery(
    tmp_path, monkeypatch
):
    notices = []
    state = us.UserStateStore(
        tmp_path / "feed_state.json", notify=lambda *args: notices.append(args)
    )
    state.watchlists = WatchlistStore().with_list("Old")
    state.alerts = AlertStore(rules=(AlertRule("Rule", watchlist="Old"),))

    def pending(*_args):
        raise WatchlistRenamePendingError("journal saved")

    monkeypatch.setattr(us, "save_watchlist_rename", pending)
    renamed = state.watchlists.renamed("Old", "New")
    assert state.rename_watchlist(renamed, "Old", "New")
    assert state.watchlists == renamed
    assert state.alerts.rules[0].watchlist == "New"
    assert "saved for recovery" in notices[-1][0]
    assert notices[-1][1] == 10_000

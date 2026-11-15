"""Tests for the watchlist store persistence module — pure, no Qt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import (
    SCHEMA_VERSION,
    WATCHLISTS_FILENAME,
    WatchEntry,
    Watchlist,
    WatchlistError,
    WatchlistStore,
    entry_from_dict,
    entry_to_dict,
    load_watchlists,
    save_watchlists,
    store_from_dict,
    store_to_dict,
    watchlists_path,
)


def _company(
    identifier: str = "AAPL", market: FeedMarket = FeedMarket.US
) -> WatchEntry:
    return WatchEntry(
        kind="company",
        market=market,
        identifier=identifier,
        person="",
        label=identifier,
        note="",
    )


def _insider(
    person: str = "Jane Director", market: FeedMarket = FeedMarket.US
) -> WatchEntry:
    return WatchEntry(
        kind="insider",
        market=market,
        identifier="",
        person=person,
        label=person,
        note="watch closely",
    )


# ---------------------------------------------------------------------------
# entry serialization
# ---------------------------------------------------------------------------


class TestEntryDictRoundTrip:
    def test_company_round_trips(self) -> None:
        e = _company()
        assert entry_from_dict(entry_to_dict(e)) == e

    def test_insider_round_trips(self) -> None:
        e = _insider()
        assert entry_from_dict(entry_to_dict(e)) == e

    def test_market_serializes_as_value(self) -> None:
        d = entry_to_dict(_company(market=FeedMarket.EUROPE))
        assert d["market"] == "Europe"

    def test_invalid_market_raises(self) -> None:
        with pytest.raises(ValueError):
            entry_from_dict({"kind": "company", "market": "NOPE", "identifier": "X"})

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            entry_from_dict({"kind": "robot", "market": "US", "identifier": "X"})


# ---------------------------------------------------------------------------
# store serialization
# ---------------------------------------------------------------------------


class TestStoreDictRoundTrip:
    def test_empty_store_round_trips(self) -> None:
        s = WatchlistStore()
        assert store_from_dict(store_to_dict(s)) == s

    def test_populated_store_round_trips(self) -> None:
        s = WatchlistStore(
            watchlists=(
                Watchlist(name="Tech", entries=(_company("AAPL"), _company("MSFT"))),
                Watchlist(name="People", entries=(_insider(),)),
            )
        )
        assert store_from_dict(store_to_dict(s)) == s

    def test_store_dict_contains_version(self) -> None:
        assert store_to_dict(WatchlistStore())["version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# resilient load
# ---------------------------------------------------------------------------


class TestLoadWatchlists:
    def test_missing_file_returns_empty_store(self, tmp_path: Path) -> None:
        assert load_watchlists(tmp_path / WATCHLISTS_FILENAME) == WatchlistStore()

    def test_invalid_json_returns_empty_store(self, tmp_path: Path) -> None:
        p = tmp_path / WATCHLISTS_FILENAME
        p.write_text("{ bad json", encoding="utf-8")
        assert load_watchlists(p) == WatchlistStore()

    def test_bad_entry_dropped_good_survives(self, tmp_path: Path) -> None:
        p = tmp_path / WATCHLISTS_FILENAME
        data = {
            "version": SCHEMA_VERSION,
            "watchlists": [
                {
                    "name": "Mixed",
                    "entries": [
                        {"kind": "company", "market": "BOGUS", "identifier": "X"},
                        {
                            "kind": "company",
                            "market": "US",
                            "identifier": "AAPL",
                            "person": "",
                            "label": "AAPL",
                            "note": "",
                        },
                    ],
                }
            ],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_watchlists(p)
        assert len(result.watchlists) == 1
        assert result.watchlists[0].entries == (_company("AAPL"),)

    def test_unknown_version_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / WATCHLISTS_FILENAME
        p.write_text(json.dumps({"version": 999, "watchlists": []}), encoding="utf-8")
        assert isinstance(load_watchlists(p), WatchlistStore)


# ---------------------------------------------------------------------------
# save / round-trip on disk
# ---------------------------------------------------------------------------


class TestSaveWatchlists:
    def test_save_then_load_equal(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / WATCHLISTS_FILENAME
        store = WatchlistStore(
            watchlists=(Watchlist(name="W", entries=(_company(), _insider())),)
        )
        save_watchlists(p, store)
        assert load_watchlists(p) == store

    def test_no_leftover_temp_files(self, tmp_path: Path) -> None:
        p = tmp_path / WATCHLISTS_FILENAME
        save_watchlists(p, WatchlistStore())
        assert [f for f in tmp_path.iterdir() if f.suffix == ".tmp"] == []


class TestWatchlistsPath:
    def test_path(self, tmp_path: Path) -> None:
        assert watchlists_path(tmp_path) == tmp_path / WATCHLISTS_FILENAME


# ---------------------------------------------------------------------------
# immutable store operations
# ---------------------------------------------------------------------------


class TestStoreOperations:
    def test_with_list_adds_empty_and_is_immutable(self) -> None:
        store = WatchlistStore()
        updated = store.with_list("New")
        assert store.names() == ()  # original untouched
        assert updated.names() == ("New",)
        assert updated.get("New") == Watchlist(name="New", entries=())

    def test_with_list_is_noop_when_present(self) -> None:
        store = WatchlistStore().with_list("A")
        assert store.with_list("A").names() == ("A",)

    def test_without_list(self) -> None:
        store = WatchlistStore().with_list("A").with_list("B")
        assert store.without_list("A").names() == ("B",)

    def test_renamed(self) -> None:
        store = WatchlistStore().with_list("Old")
        assert store.renamed("Old", "New").names() == ("New",)

    def test_with_entry_creates_list_if_missing(self) -> None:
        store = WatchlistStore().with_entry("Tech", _company("AAPL"))
        assert store.get("Tech").entries == (_company("AAPL"),)

    def test_with_entry_dedups_by_key(self) -> None:
        store = (
            WatchlistStore()
            .with_entry("Tech", _company("AAPL"))
            .with_entry("Tech", _company("AAPL"))
        )
        assert len(store.get("Tech").entries) == 1

    def test_without_entry(self) -> None:
        store = (
            WatchlistStore()
            .with_entry("Tech", _company("AAPL"))
            .with_entry("Tech", _company("MSFT"))
        )
        reduced = store.without_entry("Tech", _company("AAPL").key())
        assert reduced.get("Tech").entries == (_company("MSFT"),)

    @pytest.mark.parametrize("name", ["", "   "])
    def test_blank_names_are_rejected(self, name: str) -> None:
        with pytest.raises(WatchlistError, match="name"):
            WatchlistStore().with_list(name)

    def test_new_names_are_trimmed(self) -> None:
        assert WatchlistStore().with_list(" Tech ").names() == ("Tech",)

    def test_create_rejects_case_insensitive_collision(self) -> None:
        store = WatchlistStore().with_list("Tech")
        with pytest.raises(WatchlistError, match="exists"):
            store.with_list("TECH")

    def test_rename_rejects_collision_without_losing_entries(self) -> None:
        store = WatchlistStore().with_entry("Tech", _company()).with_list("Other")
        with pytest.raises(WatchlistError, match="exists"):
            store.renamed("Tech", "OTHER")
        assert store.get("Tech").entries == (_company(),)

    def test_case_only_rename_keeps_list_order_and_entries(self) -> None:
        store = WatchlistStore().with_list("First").with_entry("Tech", _company())
        updated = store.renamed("Tech", "TECH")
        assert updated.names() == ("First", "TECH")
        assert updated.get("TECH").entries == (_company(),)

    def test_list_cap_raises_for_add_and_implicit_entry_list(self, monkeypatch) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_WATCHLISTS", 1)
        store = WatchlistStore().with_list("Full")
        with pytest.raises(WatchlistError, match="limit"):
            store.with_list("Overflow")
        with pytest.raises(WatchlistError, match="limit"):
            store.with_entry("Overflow", _company())
        assert store.with_list("Full") == store

    def test_entry_cap_raises_but_duplicate_remains_noop(self, monkeypatch) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_ENTRIES", 1)
        store = WatchlistStore().with_entry("Tech", _company())
        with pytest.raises(WatchlistError, match="limit"):
            store.with_entry("Tech", _company("MSFT"))
        assert store.with_entry("Tech", _company()) == store


class TestUnifiedWatchlist:
    def test_symbols_select_us_companies_and_deduplicate(self) -> None:
        store = WatchlistStore(
            watchlists=(
                Watchlist(
                    "First",
                    (_company(" aapl "), _insider(), _company("DE", FeedMarket.EUROPE)),
                ),
                Watchlist("Second", (_company("AAPL"), _company("msft"), _company(""))),
            )
        )
        assert store.us_symbols() == ("AAPL", "MSFT")

    def test_migration_merges_existing_json_and_imports_only_once(
        self, tmp_path
    ) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("# old tickers\naapl\nMSFT\nAAPL\n", encoding="utf-8")
        save_watchlists(path, WatchlistStore().with_entry("Tech", _company("AAPL")))
        original = legacy.read_bytes()
        migrated = load_watchlists(path, legacy_path=legacy)
        assert migrated.us_symbols() == ("AAPL", "MSFT")
        assert migrated.get("Tech").entries == (_company("AAPL"),)
        assert legacy.read_bytes() == original

        trimmed = migrated.without_list("Legacy US")
        save_watchlists(path, trimmed)
        legacy.write_text("TSLA\n", encoding="utf-8")
        reloaded = load_watchlists(path, legacy_path=legacy)
        assert reloaded.us_symbols() == ("AAPL",)

    def test_migration_creates_json_from_legacy_file(self, tmp_path) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("\ufeff# seed\naapl\n\nMSFT\n", encoding="utf-8")
        store = load_watchlists(path, legacy_path=legacy)
        assert store.us_symbols() == ("AAPL", "MSFT")
        assert load_watchlists(path) == store

    def test_migration_avoids_name_collision(self, tmp_path) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        save_watchlists(path, WatchlistStore().with_list("legacy us"))
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("AAPL", encoding="utf-8")
        store = load_watchlists(path, legacy_path=legacy)
        assert store.names() == ("legacy us", "Legacy US (2)")
        assert store.us_symbols() == ("AAPL",)

    def test_migration_capacity_failure_preserves_original_state(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_WATCHLISTS", 1)
        path = tmp_path / WATCHLISTS_FILENAME
        save_watchlists(path, WatchlistStore().with_list("Existing"))
        previous = path.read_bytes()
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("AAPL", encoding="utf-8")
        with pytest.raises(WatchlistError, match="limit"):
            load_watchlists(path, legacy_path=legacy)
        assert path.read_bytes() == previous

    def test_missing_legacy_file_does_not_mark_migration_complete(
        self, tmp_path
    ) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        legacy = tmp_path / "tickers_watchlist.txt"
        save_watchlists(path, WatchlistStore())
        assert load_watchlists(path, legacy_path=legacy) == WatchlistStore()
        legacy.write_text("AAPL", encoding="utf-8")
        assert load_watchlists(path, legacy_path=legacy).us_symbols() == ("AAPL",)

    def test_migration_write_failure_leaves_existing_json_and_text_intact(
        self, tmp_path, monkeypatch
    ) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        save_watchlists(path, WatchlistStore().with_list("Existing"))
        previous = path.read_bytes()
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("AAPL", encoding="utf-8")

        def fail_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            "insider_scanner.persistence.watchlists.atomic_write_json", fail_write
        )
        with pytest.raises(OSError, match="disk full"):
            load_watchlists(path, legacy_path=legacy)
        assert path.read_bytes() == previous
        assert legacy.read_text(encoding="utf-8") == "AAPL"

    def test_migration_splits_large_legacy_list_without_dropping_symbols(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_ENTRIES", 2)
        path = tmp_path / WATCHLISTS_FILENAME
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_text("AAPL\nMSFT\nTSLA", encoding="utf-8")
        store = load_watchlists(path, legacy_path=legacy)
        assert store.us_symbols() == ("AAPL", "MSFT", "TSLA")
        assert [len(w.entries) for w in store.watchlists] == [2, 1]

    def test_loaded_case_collisions_merge_entries_under_the_first_name(self) -> None:
        store = store_from_dict(
            {
                "watchlists": [
                    {"name": "Tech", "entries": [entry_to_dict(_company("AAPL"))]},
                    {"name": " tech ", "entries": [entry_to_dict(_company("MSFT"))]},
                ]
            }
        )
        assert store.names() == ("Tech",)
        assert store.get("Tech").entries == (_company("AAPL"), _company("MSFT"))

    def test_loading_preserves_historical_data_over_edit_caps(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_WATCHLISTS", 1)
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_ENTRIES", 1)
        path = tmp_path / WATCHLISTS_FILENAME
        original = WatchlistStore(
            watchlists=(
                Watchlist("First", (_company("AAPL"), _company("MSFT"))),
                Watchlist("Second", (_company("TSLA"),)),
            )
        )
        save_watchlists(path, original)
        loaded = load_watchlists(path)
        assert loaded == original
        with pytest.raises(WatchlistError, match="limit"):
            loaded.with_list("Third")
        with pytest.raises(WatchlistError, match="limit"):
            loaded.with_entry("First", _company("NVDA"))

    def test_merged_historical_lists_remain_complete_after_save_reload(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("insider_scanner.persistence.watchlists._MAX_ENTRIES", 1)
        path = tmp_path / WATCHLISTS_FILENAME
        path.write_text(
            json.dumps(
                {
                    "watchlists": [
                        {"name": "Tech", "entries": [entry_to_dict(_company("AAPL"))]},
                        {"name": "TECH", "entries": [entry_to_dict(_company("MSFT"))]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        store = load_watchlists(path)
        assert store.us_symbols() == ("AAPL", "MSFT")
        save_watchlists(path, store)
        assert load_watchlists(path).us_symbols() == ("AAPL", "MSFT")

    def test_invalid_legacy_encoding_reports_a_watchlist_error_without_save(
        self, tmp_path
    ) -> None:
        path = tmp_path / WATCHLISTS_FILENAME
        save_watchlists(path, WatchlistStore().with_list("Existing"))
        previous = path.read_bytes()
        legacy = tmp_path / "tickers_watchlist.txt"
        legacy.write_bytes(b"\xff\xfe")
        with pytest.raises(WatchlistError, match="decode"):
            load_watchlists(path, legacy_path=legacy)
        assert path.read_bytes() == previous

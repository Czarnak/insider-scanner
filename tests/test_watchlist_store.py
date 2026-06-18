"""Tests for the watchlist store persistence module — pure, no Qt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.watchlists import (
    WATCHLISTS_FILENAME,
    SCHEMA_VERSION,
    WatchEntry,
    Watchlist,
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

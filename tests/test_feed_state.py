"""Tests for feed_state persistence module — pure, no Qt."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedSortField,
    TransactionDirection,
)
from insider_scanner.persistence.feed_state import (
    FEED_STATE_FILENAME,
    SCHEMA_VERSION,
    FeedState,
    FeedStateError,
    SavedScreen,
    criteria_from_dict,
    criteria_to_dict,
    decode_column_layout,
    encode_column_layout,
    feed_state_path,
    load_feed_state,
    save_feed_state,
    state_from_dict,
    state_to_dict,
)


# ---------------------------------------------------------------------------
# criteria_to_dict / criteria_from_dict round-trip
# ---------------------------------------------------------------------------


class TestCriteriaDictRoundTrip:
    def test_default_criteria_round_trips(self) -> None:
        c = FeedCriteria()
        assert criteria_from_dict(criteria_to_dict(c)) == c

    def test_full_criteria_round_trips(self) -> None:
        c = FeedCriteria(
            search="apple",
            sort_field=FeedSortField.VALUE,
            descending=False,
            markets=(FeedMarket.US, FeedMarket.CONGRESS),
            directions=(TransactionDirection.BUY,),
            transaction_date_from=date(2024, 1, 1),
            transaction_date_to=date(2024, 6, 30),
            value_min=1000.0,
            value_max=50000.5,
        )
        assert criteria_from_dict(criteria_to_dict(c)) == c

    def test_none_dates_serialize_as_none(self) -> None:
        c = FeedCriteria()
        d = criteria_to_dict(c)
        assert d["transaction_date_from"] is None
        assert d["transaction_date_to"] is None

    def test_dates_serialize_as_isoformat(self) -> None:
        c = FeedCriteria(
            transaction_date_from=date(2023, 3, 15),
            transaction_date_to=date(2023, 12, 31),
        )
        d = criteria_to_dict(c)
        assert d["transaction_date_from"] == "2023-03-15"
        assert d["transaction_date_to"] == "2023-12-31"

    def test_enums_serialize_as_values(self) -> None:
        c = FeedCriteria(
            sort_field=FeedSortField.FILING_DATE,
            markets=(FeedMarket.EUROPE,),
            directions=(TransactionDirection.SELL,),
        )
        d = criteria_to_dict(c)
        assert d["sort_field"] == "filing_date"
        assert d["markets"] == ["Europe"]
        assert d["directions"] == ["Sell"]

    def test_float_values_survive_round_trip(self) -> None:
        c = FeedCriteria(value_min=0.01, value_max=99999.99)
        result = criteria_from_dict(criteria_to_dict(c))
        assert result.value_min == pytest.approx(0.01)
        assert result.value_max == pytest.approx(99999.99)

    def test_missing_keys_yield_defaults(self) -> None:
        result = criteria_from_dict({})
        assert result == FeedCriteria()

    def test_partial_dict_uses_defaults_for_missing(self) -> None:
        result = criteria_from_dict({"search": "tesla"})
        assert result.search == "tesla"
        assert result.sort_field == FeedSortField.TRANSACTION_DATE
        assert result.markets == ()
        assert result.directions == ()

    def test_invalid_market_raises_feed_state_error(self) -> None:
        with pytest.raises(FeedStateError):
            criteria_from_dict({"markets": ["INVALID_MARKET"]})

    def test_invalid_direction_raises_feed_state_error(self) -> None:
        with pytest.raises(FeedStateError):
            criteria_from_dict({"directions": ["INVALID_DIR"]})

    def test_invalid_sort_field_raises_feed_state_error(self) -> None:
        with pytest.raises(FeedStateError):
            criteria_from_dict({"sort_field": "nonexistent_field"})

    def test_invalid_date_format_raises_feed_state_error(self) -> None:
        with pytest.raises(FeedStateError):
            criteria_from_dict({"transaction_date_from": "not-a-date"})


# ---------------------------------------------------------------------------
# state_to_dict / state_from_dict round-trip
# ---------------------------------------------------------------------------


class TestStateDictRoundTrip:
    def test_default_state_round_trips(self) -> None:
        s = FeedState()
        assert state_from_dict(state_to_dict(s)) == s

    def test_state_with_screens_and_column_layout_round_trips(self) -> None:
        c1 = FeedCriteria(search="aapl", markets=(FeedMarket.US,))
        c2 = FeedCriteria(
            sort_field=FeedSortField.VALUE,
            directions=(TransactionDirection.SELL,),
        )
        s = FeedState(
            criteria=FeedCriteria(search="msft"),
            column_layout="dGVzdA==",
            screens=(
                SavedScreen(name="Screen A", criteria=c1),
                SavedScreen(name="Screen B", criteria=c2),
            ),
        )
        result = state_from_dict(state_to_dict(s))
        assert result == s

    def test_state_dict_contains_version(self) -> None:
        d = state_to_dict(FeedState())
        assert d["version"] == SCHEMA_VERSION

    def test_state_dict_screens_are_list_of_dicts(self) -> None:
        c = FeedCriteria(search="x")
        s = FeedState(screens=(SavedScreen(name="S", criteria=c),))
        d = state_to_dict(s)
        assert isinstance(d["screens"], list)
        assert d["screens"][0]["name"] == "S"
        assert isinstance(d["screens"][0]["criteria"], dict)

    def test_column_layout_round_trips(self) -> None:
        s = FeedState(column_layout="abc123")
        result = state_from_dict(state_to_dict(s))
        assert result.column_layout == "abc123"

    def test_none_column_layout_preserved(self) -> None:
        s = FeedState(column_layout=None)
        result = state_from_dict(state_to_dict(s))
        assert result.column_layout is None


# ---------------------------------------------------------------------------
# load_feed_state — missing / corrupt / bad-content
# ---------------------------------------------------------------------------


class TestLoadFeedState:
    def test_missing_file_returns_default_state(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent" / FEED_STATE_FILENAME
        result = load_feed_state(p)
        assert result == FeedState()

    def test_invalid_json_returns_default_state(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        p.write_text("{ not valid json }", encoding="utf-8")
        result = load_feed_state(p)
        assert result == FeedState()

    def test_invalid_json_does_not_raise(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        p.write_text("!!!!", encoding="utf-8")
        # Must never raise
        load_feed_state(p)

    def test_bad_top_level_criteria_falls_back_to_default_criteria(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        data = {
            "version": SCHEMA_VERSION,
            "criteria": {"markets": ["INVALID"]},
            "column_layout": None,
            "screens": [],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_feed_state(p)
        # Load must succeed
        assert isinstance(result, FeedState)
        # Criteria must be the default
        assert result.criteria == FeedCriteria()

    def test_bad_screen_is_dropped_good_screen_survives(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        data = {
            "version": SCHEMA_VERSION,
            "criteria": {},
            "column_layout": None,
            "screens": [
                {
                    "name": "Bad Screen",
                    "criteria": {"markets": ["BOGUS_MARKET"]},
                },
                {
                    "name": "Good Screen",
                    "criteria": {"search": "valid"},
                },
            ],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_feed_state(p)
        assert len(result.screens) == 1
        assert result.screens[0].name == "Good Screen"

    def test_unknown_version_is_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        data = {
            "version": 999,
            "criteria": {"search": "future"},
            "column_layout": None,
            "screens": [],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_feed_state(p)
        assert isinstance(result, FeedState)
        assert result.criteria.search == "future"

    def test_non_integer_version_falls_back_but_keeps_data(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        data = {
            "version": "2.0",
            "criteria": {"search": "keep me"},
            "column_layout": None,
            "screens": [],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_feed_state(p)
        assert result.version == SCHEMA_VERSION
        assert result.criteria.search == "keep me"


# ---------------------------------------------------------------------------
# save_feed_state + load_feed_state round-trip
# ---------------------------------------------------------------------------


class TestSaveFeedState:
    def test_save_then_load_produces_equal_state(self, tmp_path: Path) -> None:
        p = tmp_path / "subdir" / FEED_STATE_FILENAME
        c = FeedCriteria(
            search="test",
            markets=(FeedMarket.CONGRESS,),
            directions=(TransactionDirection.BUY,),
            transaction_date_from=date(2024, 1, 1),
            value_min=100.0,
        )
        original = FeedState(
            criteria=c,
            column_layout="bGF5b3V0",
            screens=(SavedScreen(name="My Screen", criteria=FeedCriteria()),),
        )
        save_feed_state(p, original)
        loaded = load_feed_state(p)
        assert loaded == original

    def test_parent_dir_is_created_if_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "deep" / FEED_STATE_FILENAME
        save_feed_state(p, FeedState())
        assert p.exists()

    def test_no_leftover_temp_files_after_save(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        save_feed_state(p, FeedState())
        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert tmp_files == []

    def test_saved_file_is_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        save_feed_state(p, FeedState())
        content = p.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert "version" in parsed
        assert "criteria" in parsed

    def test_file_is_replaced_atomically_on_second_save(self, tmp_path: Path) -> None:
        p = tmp_path / FEED_STATE_FILENAME
        s1 = FeedState(column_layout="first")
        s2 = FeedState(column_layout="second")
        save_feed_state(p, s1)
        save_feed_state(p, s2)
        assert load_feed_state(p).column_layout == "second"


# ---------------------------------------------------------------------------
# feed_state_path
# ---------------------------------------------------------------------------


class TestFeedStatePath:
    def test_returns_data_dir_slash_filename(self, tmp_path: Path) -> None:
        result = feed_state_path(tmp_path)
        assert result == tmp_path / FEED_STATE_FILENAME


# ---------------------------------------------------------------------------
# base64 codec
# ---------------------------------------------------------------------------


class TestBase64Codec:
    def test_encode_decode_round_trips(self) -> None:
        raw = b"\x00\x01\x02Hello\xff\xfe"
        assert decode_column_layout(encode_column_layout(raw)) == raw

    def test_empty_bytes_round_trip(self) -> None:
        assert decode_column_layout(encode_column_layout(b"")) == b""

    def test_encode_returns_ascii_string(self) -> None:
        encoded = encode_column_layout(b"test data")
        assert isinstance(encoded, str)
        encoded.encode("ascii")  # must not raise

    def test_known_value(self) -> None:
        assert encode_column_layout(b"test") == "dGVzdA=="
        assert decode_column_layout("dGVzdA==") == b"test"

"""Tests for Congress member list management and trade flagging."""

from __future__ import annotations

import json
from datetime import date

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.senate import (
    DEFAULT_CONGRESS_MEMBERS,
    _normalize_name,
    flag_congress_trades,
    init_default_congress_file,
    load_congress_members,
    save_congress_members,
)


class TestNormalizeName:
    def test_basic(self):
        assert _normalize_name("  John  Smith  ") == "john smith"

    def test_suffix_removal(self):
        assert _normalize_name("John Smith Jr") == "john smith"
        assert _normalize_name("John Smith III") == "john smith"

    def test_comma_removal(self):
        assert _normalize_name("Smith, John") == "smith john"


class TestLoadSave:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "members.json"
        members = [{"name": "Test Person", "state": "CA"}]
        save_congress_members(members, path)
        loaded = load_congress_members(path)
        assert len(loaded) == 1
        assert loaded[0]["name"] == "Test Person"

    def test_load_missing_file(self, tmp_path):
        path = tmp_path / "missing.json"
        result = load_congress_members(path)
        assert result == []

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        result = load_congress_members(path)
        assert result == []

    def test_init_default(self, tmp_path):
        path = tmp_path / "congress.json"
        init_default_congress_file(path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert len(data) == len(DEFAULT_CONGRESS_MEMBERS)

    def test_init_no_overwrite(self, tmp_path):
        path = tmp_path / "congress.json"
        save_congress_members([{"name": "Custom"}], path)
        init_default_congress_file(path)
        # Should not overwrite
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["name"] == "Custom"


class TestFlagCongressTrades:
    def _make_trade(self, name: str) -> InsiderTrade:
        return InsiderTrade(
            ticker="AAPL",
            insider_name=name,
            trade_type="Buy",
            trade_date=date(2025, 1, 1),
        )

    def test_exact_match(self):
        members = [{"name": "Pelosi Nancy"}]
        trades = [self._make_trade("Pelosi Nancy")]
        flag_congress_trades(trades, members)
        assert trades[0].is_congress
        assert trades[0].congress_member == "Pelosi Nancy"

    def test_case_insensitive(self):
        members = [{"name": "Pelosi Nancy"}]
        trades = [self._make_trade("pelosi nancy")]
        flag_congress_trades(trades, members)
        assert trades[0].is_congress

    def test_no_match(self):
        members = [{"name": "Pelosi Nancy"}]
        trades = [self._make_trade("Cook Timothy")]
        flag_congress_trades(trades, members)
        assert not trades[0].is_congress

    def test_partial_match(self):
        members = [{"name": "Tuberville Tommy"}]
        trades = [self._make_trade("Tuberville Tommy J")]
        flag_congress_trades(trades, members)
        assert trades[0].is_congress

    def test_empty_members(self):
        trades = [self._make_trade("Anyone")]
        flag_congress_trades(trades, [])
        assert not trades[0].is_congress

    def test_multiple_trades(self):
        members = [{"name": "Pelosi Nancy"}, {"name": "Tuberville Tommy"}]
        trades = [
            self._make_trade("Pelosi Nancy"),
            self._make_trade("Cook Timothy"),
            self._make_trade("Tuberville Tommy"),
        ]
        flag_congress_trades(trades, members)
        assert trades[0].is_congress
        assert not trades[1].is_congress
        assert trades[2].is_congress

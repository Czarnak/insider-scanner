"""Tests for the alert store persistence module — pure, no Qt."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from insider_scanner.persistence.alerts import (
    ALERTS_FILENAME,
    SCHEMA_VERSION,
    AlertRule,
    AlertStore,
    alerts_path,
    load_alerts,
    rule_from_dict,
    rule_to_dict,
    save_alerts,
    store_from_dict,
    store_to_dict,
)
from insider_scanner.persistence.feed import FeedCriteria, FeedMarket

_WHEN = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def _rule(name: str = "Big buys", **kw) -> AlertRule:
    defaults = dict(
        name=name,
        criteria=FeedCriteria(value_min=100_000, markets=(FeedMarket.US,)),
        watchlist=None,
        enabled=True,
        last_seen_at=None,
    )
    defaults.update(kw)
    return AlertRule(**defaults)


class TestRuleDictRoundTrip:
    def test_default_rule_round_trips(self) -> None:
        r = _rule()
        assert rule_from_dict(rule_to_dict(r)) == r

    def test_full_rule_round_trips(self) -> None:
        r = _rule(
            name="Watchlist hits",
            watchlist="Tech",
            enabled=False,
            last_seen_at=_WHEN,
        )
        assert rule_from_dict(rule_to_dict(r)) == r

    def test_last_seen_at_serializes_iso(self) -> None:
        assert (
            rule_to_dict(_rule(last_seen_at=_WHEN))["last_seen_at"] == _WHEN.isoformat()
        )

    def test_none_last_seen_serializes_none(self) -> None:
        assert rule_to_dict(_rule())["last_seen_at"] is None

    def test_bad_criteria_falls_back_to_default(self) -> None:
        d = rule_to_dict(_rule())
        d["criteria"] = {"markets": ["BOGUS"]}
        result = rule_from_dict(d)
        assert result.criteria == FeedCriteria()


class TestStoreDictRoundTrip:
    def test_empty_round_trips(self) -> None:
        s = AlertStore()
        assert store_from_dict(store_to_dict(s)) == s

    def test_populated_round_trips(self) -> None:
        s = AlertStore(rules=(_rule("A"), _rule("B", enabled=False)))
        assert store_from_dict(store_to_dict(s)) == s

    def test_version_present(self) -> None:
        assert store_to_dict(AlertStore())["version"] == SCHEMA_VERSION


class TestLoadAlerts:
    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_alerts(tmp_path / ALERTS_FILENAME) == AlertStore()

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / ALERTS_FILENAME
        p.write_text("nope", encoding="utf-8")
        assert load_alerts(p) == AlertStore()

    def test_bad_rule_dropped_good_survives(self, tmp_path: Path) -> None:
        p = tmp_path / ALERTS_FILENAME
        data = {
            "version": SCHEMA_VERSION,
            "rules": [
                {"name": "broken"},  # missing criteria entirely
                rule_to_dict(_rule("Good")),
            ],
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_alerts(p)
        names = [r.name for r in result.rules]
        assert "Good" in names


class TestSaveAlerts:
    def test_round_trip_on_disk(self, tmp_path: Path) -> None:
        p = tmp_path / "sub" / ALERTS_FILENAME
        store = AlertStore(rules=(_rule("X", last_seen_at=_WHEN),))
        save_alerts(p, store)
        assert load_alerts(p) == store

    def test_no_leftover_temp_files(self, tmp_path: Path) -> None:
        p = tmp_path / ALERTS_FILENAME
        save_alerts(p, AlertStore())
        assert [f for f in tmp_path.iterdir() if f.suffix == ".tmp"] == []


class TestAlertsPath:
    def test_path(self, tmp_path: Path) -> None:
        assert alerts_path(tmp_path) == tmp_path / ALERTS_FILENAME


class TestStoreOperations:
    def test_with_rule_adds(self) -> None:
        store = AlertStore().with_rule(_rule("A"))
        assert [r.name for r in store.rules] == ["A"]

    def test_with_rule_replaces_by_name(self) -> None:
        store = AlertStore().with_rule(_rule("A", enabled=True))
        store = store.with_rule(_rule("A", enabled=False))
        assert len(store.rules) == 1
        assert store.rules[0].enabled is False

    def test_without_rule(self) -> None:
        store = AlertStore().with_rule(_rule("A")).with_rule(_rule("B"))
        assert [r.name for r in store.without_rule("A").rules] == ["B"]

    def test_toggled(self) -> None:
        store = AlertStore().with_rule(_rule("A", enabled=True))
        assert store.toggled("A", False).rules[0].enabled is False

    def test_marked_all_seen_sets_watermark(self) -> None:
        store = AlertStore().with_rule(_rule("A")).with_rule(_rule("B"))
        seen = store.marked_all_seen(_WHEN)
        assert all(r.last_seen_at == _WHEN for r in seen.rules)

    def test_original_untouched_by_operations(self) -> None:
        store = AlertStore().with_rule(_rule("A"))
        store.marked_all_seen(_WHEN)
        assert store.rules[0].last_seen_at is None

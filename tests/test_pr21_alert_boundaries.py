"""Regression coverage for legacy alert references and timestamp boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from insider_scanner.persistence.alerts import AlertRule, AlertStore, load_alerts
from insider_scanner.persistence.watchlists import store_from_dict
from insider_scanner.services.alerts import evaluate_alerts
from tests.test_alert_evaluation import FakeRepo, _record


@pytest.mark.parametrize(
    "timestamp",
    ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"],
)
def test_overflow_watermark_disables_rule_and_preserves_other_rules(
    tmp_path, timestamp
):
    path = tmp_path / "alerts.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {"name": "Broken", "last_seen_at": timestamp},
                    {"name": "Good", "last_seen_at": "2024-06-01T12:00:00Z"},
                ]
            }
        ),
        encoding="utf-8",
    )

    store = load_alerts(path)

    assert [rule.name for rule in store.rules] == ["Broken", "Good"]
    assert store.rules[0].enabled is False
    assert "timestamp" in store.rules[0].error
    assert store.rules[1].enabled is True
    assert store.rules[1].last_seen_at == datetime(2024, 6, 1, 12, tzinfo=UTC)
    assert path.exists()


def test_merged_legacy_watchlist_keeps_case_and_whitespace_variant_alerts_working():
    watchlists = store_from_dict(
        {
            "watchlists": [
                {
                    "name": name,
                    "entries": [
                        {"kind": "company", "market": "US", "identifier": symbol}
                    ],
                }
                for name, symbol in (("Tech", "AAPL"), (" tech ", "MSFT"))
            ]
        }
    )
    store = AlertStore(rules=(AlertRule(name="Tech hits", watchlist=" tech "),))
    repo = FakeRepo(
        by_issuer={
            "aapl": (_record("us:aapl", identifier="AAPL"),),
            "msft": (_record("us:msft", identifier="MSFT"),),
        }
    )

    hits = evaluate_alerts(repo, store, watchlists)

    assert len(hits) == 1
    assert [record.key for record in hits[0].records] == ["us:aapl", "us:msft"]


def test_rename_watchlist_updates_whitespace_variant_alert_reference():
    store = AlertStore(rules=(AlertRule(name="Tech hits", watchlist=" tech "),))

    updated = store.renamed_watchlist("Tech", "Technology")

    assert updated.rules[0].watchlist == "Technology"
    assert store.rules[0].watchlist == " tech "

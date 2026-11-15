"""Alert watermarks acknowledge only bounded, presented ingestion progress."""

import pytest

from insider_scanner.persistence.alerts import (
    AlertRule,
    AlertStore,
    rule_from_dict,
    rule_to_dict,
)


def test_sequence_roundtrip_and_monotonic_acknowledgement():
    rule = rule_from_dict({"name": "New", "last_seen_sequence": 17})
    assert rule_to_dict(rule).get("last_seen_sequence") == 17
    store = AlertStore(rules=(rule, AlertRule(name="Untouched")))
    updated = store.marked_seen_sequence("New", 12)
    assert updated.get("New").last_seen_sequence == 17
    updated = updated.marked_seen_sequence("New", 23)
    assert updated.get("New").last_seen_sequence == 23
    assert updated.get("Untouched").last_seen_sequence is None
    assert store.get("New").last_seen_sequence == 17


@pytest.mark.parametrize("bad", [-1, True, 1.5, "12", {}, []])
def test_invalid_sequence_disables_rule_without_widening_matches(bad):
    rule = rule_from_dict({"name": "Broken", "last_seen_sequence": bad})
    assert rule.enabled is False
    assert "sequence" in rule.error
    assert rule_from_dict(rule_to_dict(rule)) == rule

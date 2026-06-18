"""Persistence for in-app alert rules.

An alert rule is essentially a saved screen plus a "tell me about new matches"
watermark. It reuses :class:`FeedCriteria` as its matching predicate (serialised
with the same helpers as ``feed_state``) and optionally references a watchlist by
name. ``last_seen_at`` is a UTC high-water mark used to decide which matches are
"new" since the user last acknowledged the rule.

All public store operations return **new** immutable ``AlertStore`` values.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.feed_state import criteria_from_dict, criteria_to_dict
from insider_scanner.persistence.json_state import atomic_write_json, read_json_dict
from insider_scanner.utils.logging import get_logger

_log = get_logger("alerts")

SCHEMA_VERSION = 1
ALERTS_FILENAME = "alerts.json"
_MAX_RULES = 200


class AlertStoreError(ValueError):
    """Raised when alert data cannot be parsed or coerced."""


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertRule:
    """A named alert: a feed predicate plus an acknowledgement watermark."""

    name: str
    criteria: FeedCriteria = FeedCriteria()
    watchlist: str | None = None
    enabled: bool = True
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class AlertStore:
    """Serialisable snapshot of all alert rules."""

    version: int = SCHEMA_VERSION
    rules: tuple[AlertRule, ...] = ()

    def get(self, name: str) -> AlertRule | None:
        return next((r for r in self.rules if r.name == name), None)

    def with_rule(self, rule: AlertRule) -> AlertStore:
        """Return a copy with *rule* added, replacing any rule of the same name."""
        others = tuple(r for r in self.rules if r.name != rule.name)
        return replace(self, rules=(*others, rule)[:_MAX_RULES])

    def without_rule(self, name: str) -> AlertStore:
        return replace(self, rules=tuple(r for r in self.rules if r.name != name))

    def toggled(self, name: str, enabled: bool) -> AlertStore:
        rules = tuple(
            replace(r, enabled=enabled) if r.name == name else r for r in self.rules
        )
        return replace(self, rules=rules)

    def marked_seen(self, name: str, at: datetime) -> AlertStore:
        rules = tuple(
            replace(r, last_seen_at=at) if r.name == name else r for r in self.rules
        )
        return replace(self, rules=rules)

    def marked_all_seen(self, at: datetime) -> AlertStore:
        rules = tuple(replace(r, last_seen_at=at) for r in self.rules)
        return replace(self, rules=rules)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def rule_to_dict(r: AlertRule) -> dict:
    return {
        "name": r.name,
        "criteria": criteria_to_dict(r.criteria),
        "watchlist": r.watchlist,
        "enabled": r.enabled,
        "last_seen_at": r.last_seen_at.isoformat()
        if r.last_seen_at is not None
        else None,
    }


def rule_from_dict(d: dict) -> AlertRule:
    """Reconstruct an AlertRule; raises AlertStoreError on structural failure.

    A malformed ``criteria`` block falls back to the default FeedCriteria rather
    than failing the whole rule.
    """
    try:
        name = str(d["name"])
    except (KeyError, TypeError) as exc:
        raise AlertStoreError(f"Cannot parse AlertRule from dict: {exc}") from exc

    raw_criteria = d.get("criteria", {})
    try:
        criteria = criteria_from_dict(
            raw_criteria if isinstance(raw_criteria, dict) else {}
        )
    except Exception:  # noqa: BLE001 - resilient: bad criteria → default
        criteria = FeedCriteria()

    watchlist = d.get("watchlist")
    watchlist = str(watchlist) if watchlist is not None else None

    enabled = bool(d.get("enabled", True))

    raw_seen = d.get("last_seen_at")
    try:
        last_seen_at = (
            datetime.fromisoformat(raw_seen) if isinstance(raw_seen, str) else None
        )
    except ValueError:
        last_seen_at = None

    return AlertRule(
        name=name,
        criteria=criteria,
        watchlist=watchlist,
        enabled=enabled,
        last_seen_at=last_seen_at,
    )


def store_to_dict(s: AlertStore) -> dict:
    return {"version": s.version, "rules": [rule_to_dict(r) for r in s.rules]}


def store_from_dict(d: dict) -> AlertStore:
    """Reconstruct an AlertStore resiliently; bad rules are dropped."""
    try:
        version = int(d.get("version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        version = SCHEMA_VERSION

    raw_rules = d.get("rules", [])
    rules: list[AlertRule] = []
    if isinstance(raw_rules, list):
        for raw_r in raw_rules[:_MAX_RULES]:
            try:
                if not isinstance(raw_r, dict):
                    continue
                rules.append(rule_from_dict(raw_r))
            except AlertStoreError:
                _log.warning("Dropping invalid alert rule: %r", raw_r)

    return AlertStore(version=version, rules=tuple(rules))


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def load_alerts(path: Path) -> AlertStore:
    """Load an AlertStore from *path*; returns an empty store on any error."""
    data = read_json_dict(path)
    if data is None:
        return AlertStore()
    try:
        return store_from_dict(data)
    except Exception as exc:  # noqa: BLE001 - never fail a load
        _log.warning("Failed to parse alerts from %s: %s", path, exc)
        return AlertStore()


def save_alerts(path: Path, store: AlertStore) -> None:
    """Atomically write *store* to *path* as JSON."""
    atomic_write_json(path, store_to_dict(store))


def alerts_path(data_dir: Path) -> Path:
    """Return the canonical alerts file path inside *data_dir*."""
    return data_dir / ALERTS_FILENAME

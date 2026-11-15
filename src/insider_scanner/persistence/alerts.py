"""Persistence for in-app alert rules.

An alert rule is essentially a saved screen plus a "tell me about new matches"
watermark. It reuses :class:`FeedCriteria` as its matching predicate (serialised
with the same helpers as ``feed_state``) and optionally references a watchlist by
name. ``last_seen_at`` is a UTC high-water mark used to decide which matches are
"new" since the user last acknowledged the rule.

All public store operations return **new** immutable ``AlertStore`` values.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.feed_state import (
    FeedStateError,
    criteria_from_dict,
    criteria_to_dict,
)
from insider_scanner.persistence.json_state import (
    atomic_write_json,
    preserve_json_state,
    read_json_dict,
)
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
    criteria: FeedCriteria = field(default_factory=FeedCriteria)
    watchlist: str | None = None
    enabled: bool = True
    last_seen_at: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.last_seen_at is not None:
            at = self.last_seen_at
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            object.__setattr__(self, "last_seen_at", at.astimezone(UTC))
        if self.error:
            object.__setattr__(self, "enabled", False)


@dataclass(frozen=True)
class AlertStore:
    """Serialisable snapshot of all alert rules."""

    version: int = SCHEMA_VERSION
    rules: tuple[AlertRule, ...] = ()

    def get(self, name: str) -> AlertRule | None:
        return next((r for r in self.rules if r.name == name), None)

    def with_rule(self, rule: AlertRule) -> AlertStore:
        """Return a copy with *rule* added, replacing any rule of the same name."""
        if self.get(rule.name) is not None:
            return replace(
                self,
                rules=tuple(rule if r.name == rule.name else r for r in self.rules),
            )
        if len(self.rules) >= _MAX_RULES:
            raise AlertStoreError(f"Cannot add more than {_MAX_RULES} alert rules")
        return replace(self, rules=(*self.rules, rule))

    def without_rule(self, name: str) -> AlertStore:
        return replace(self, rules=tuple(r for r in self.rules if r.name != name))

    def toggled(self, name: str, enabled: bool) -> AlertStore:
        rule = self.get(name)
        if enabled and rule is not None and rule.error:
            raise AlertStoreError(f"Cannot enable invalid alert '{name}': {rule.error}")
        rules = tuple(
            replace(r, enabled=enabled) if r.name == name else r for r in self.rules
        )
        return replace(self, rules=rules)

    def renamed_watchlist(self, old: str, new: str) -> AlertStore:
        """Keep alert references attached when a watchlist display name changes."""
        rules = tuple(
            replace(r, watchlist=new)
            if r.watchlist is not None
            and r.watchlist.strip().casefold() == old.strip().casefold()
            else r
            for r in self.rules
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
        "error": r.error,
        "last_seen_at": r.last_seen_at.isoformat()
        if r.last_seen_at is not None
        else None,
    }


def rule_from_dict(d: dict) -> AlertRule:
    """Reconstruct an AlertRule; raises AlertStoreError on structural failure.

    Malformed predicates and watermarks disable the rule with a persisted error;
    invalid state must never widen an alert to all transactions.
    """
    try:
        name = str(d["name"])
    except (KeyError, TypeError) as exc:
        raise AlertStoreError(f"Cannot parse AlertRule from dict: {exc}") from exc

    raw_error = d.get("error")
    error = str(raw_error) if raw_error else None
    raw_criteria = d.get("criteria", {})
    try:
        if not isinstance(raw_criteria, dict):
            raise FeedStateError("Alert criteria must be an object")
        criteria = criteria_from_dict(raw_criteria)
    except FeedStateError as exc:
        criteria = FeedCriteria()
        error = f"Invalid criteria: {exc}"

    watchlist = d.get("watchlist")
    watchlist = str(watchlist) if watchlist is not None else None

    enabled = bool(d.get("enabled", True))

    raw_seen = d.get("last_seen_at")
    try:
        if raw_seen is not None and not isinstance(raw_seen, str):
            raise ValueError("Alert timestamp must be an ISO timestamp")
        last_seen_at = (
            datetime.fromisoformat(raw_seen) if raw_seen is not None else None
        )
        if last_seen_at is not None:
            if last_seen_at.tzinfo is None:
                last_seen_at = last_seen_at.replace(tzinfo=UTC)
            last_seen_at = last_seen_at.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        last_seen_at = None
        error = f"Invalid last-seen timestamp: {exc}"

    return AlertRule(
        name=name,
        criteria=criteria,
        watchlist=watchlist,
        enabled=enabled,
        last_seen_at=last_seen_at,
        error=error,
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
    """Load rules, preserving unreadable state while surfacing programming errors."""
    data = read_json_dict(path)
    if data is None:
        return AlertStore()
    try:
        return store_from_dict(data)
    except AlertStoreError as exc:
        _log.warning("Failed to parse alerts from %s: %s", path, exc)
        preserve_json_state(path)
        return AlertStore()


def save_alerts(path: Path, store: AlertStore) -> None:
    """Atomically write *store* to *path* as JSON."""
    atomic_write_json(path, store_to_dict(store), protect_existing=True)


def alerts_path(data_dir: Path) -> Path:
    """Return the canonical alerts file path inside *data_dir*."""
    return data_dir / ALERTS_FILENAME

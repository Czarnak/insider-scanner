"""Persistence helpers for the unified feed UI state."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedSortField,
    TransactionDirection,
)
from insider_scanner.utils.logging import get_logger

_log = get_logger("feed_state")

SCHEMA_VERSION = 1
FEED_STATE_FILENAME = "feed_state.json"
_MAX_SCREENS = 200


class FeedStateError(ValueError):
    """Raised when feed state data cannot be parsed or coerced."""


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedScreen:
    """A named, persisted set of feed filter criteria."""

    name: str
    criteria: FeedCriteria


@dataclass(frozen=True)
class FeedState:
    """Serialisable snapshot of the feed panel UI state."""

    version: int = SCHEMA_VERSION
    criteria: FeedCriteria = field(default_factory=FeedCriteria)
    column_layout: str | None = None
    screens: tuple[SavedScreen, ...] = ()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def criteria_to_dict(c: FeedCriteria) -> dict:
    """Serialise a FeedCriteria to a plain dict (JSON-safe)."""
    return {
        "search": c.search,
        "sort_field": c.sort_field.value,
        "descending": c.descending,
        "markets": [m.value for m in c.markets],
        "directions": [d.value for d in c.directions],
        "transaction_date_from": (
            c.transaction_date_from.isoformat()
            if c.transaction_date_from is not None
            else None
        ),
        "transaction_date_to": (
            c.transaction_date_to.isoformat()
            if c.transaction_date_to is not None
            else None
        ),
        "value_min": c.value_min,
        "value_max": c.value_max,
    }


def criteria_from_dict(d: dict) -> FeedCriteria:
    """Reconstruct FeedCriteria from a plain dict.

    Raises FeedStateError on any structural or coercion failure.
    """
    try:
        search = str(d.get("search", ""))

        raw_sort = d.get("sort_field", FeedSortField.TRANSACTION_DATE.value)
        sort_field = FeedSortField(raw_sort)

        descending = bool(d.get("descending", True))

        raw_markets = d.get("markets", [])
        markets = tuple(FeedMarket(m) for m in raw_markets)

        raw_directions = d.get("directions", [])
        directions = tuple(TransactionDirection(dr) for dr in raw_directions)

        raw_date_from = d.get("transaction_date_from")
        transaction_date_from = (
            date.fromisoformat(raw_date_from) if raw_date_from is not None else None
        )

        raw_date_to = d.get("transaction_date_to")
        transaction_date_to = (
            date.fromisoformat(raw_date_to) if raw_date_to is not None else None
        )

        raw_min = d.get("value_min")
        value_min = float(raw_min) if raw_min is not None else None

        raw_max = d.get("value_max")
        value_max = float(raw_max) if raw_max is not None else None

        return FeedCriteria(
            search=search,
            sort_field=sort_field,
            descending=descending,
            markets=markets,
            directions=directions,
            transaction_date_from=transaction_date_from,
            transaction_date_to=transaction_date_to,
            value_min=value_min,
            value_max=value_max,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FeedStateError(f"Cannot parse FeedCriteria from dict: {exc}") from exc


def state_to_dict(s: FeedState) -> dict:
    """Serialise a FeedState to a plain dict (JSON-safe)."""
    return {
        "version": s.version,
        "criteria": criteria_to_dict(s.criteria),
        "column_layout": s.column_layout,
        "screens": [
            {"name": sc.name, "criteria": criteria_to_dict(sc.criteria)}
            for sc in s.screens
        ],
    }


def _migrate(data: dict) -> dict:
    """Migration seam — no-op for schema version 1."""
    return data


def state_from_dict(d: dict) -> FeedState:
    """Reconstruct FeedState from a plain dict.

    Resilient: bad top-level criteria falls back to FeedCriteria();
    individual bad screens are dropped rather than failing the whole load.
    Caps screens at _MAX_SCREENS.
    """
    try:
        version = int(d.get("version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        version = SCHEMA_VERSION
    data = _migrate(d)

    raw_criteria = data.get("criteria", {})
    try:
        criteria = criteria_from_dict(
            raw_criteria if isinstance(raw_criteria, dict) else {}
        )
    except FeedStateError:
        criteria = FeedCriteria()

    column_layout = data.get("column_layout")
    if column_layout is not None:
        column_layout = str(column_layout)

    raw_screens = data.get("screens", [])
    screens: list[SavedScreen] = []
    if isinstance(raw_screens, list):
        for raw_sc in raw_screens[:_MAX_SCREENS]:
            try:
                if not isinstance(raw_sc, dict):
                    continue
                screen_name = str(raw_sc.get("name", ""))
                screen_criteria = criteria_from_dict(raw_sc.get("criteria", {}) or {})
                screens.append(SavedScreen(name=screen_name, criteria=screen_criteria))
            except Exception:  # noqa: BLE001
                _log.warning("Dropping invalid screen entry: %r", raw_sc)

    return FeedState(
        version=version,
        criteria=criteria,
        column_layout=column_layout,
        screens=tuple(screens),
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def load_feed_state(path: Path) -> FeedState:
    """Load FeedState from *path*; never raises — returns FeedState() on any error."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FeedState()
    except OSError as exc:
        _log.warning("Could not read feed state file %s: %s", path, exc)
        return FeedState()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.warning("Feed state file %s contains invalid JSON: %s", path, exc)
        return FeedState()

    if not isinstance(data, dict):
        _log.warning("Feed state file %s has unexpected top-level type", path)
        return FeedState()

    try:
        return state_from_dict(data)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to parse feed state from %s: %s", path, exc)
        return FeedState()


def save_feed_state(path: Path, state: FeedState) -> None:
    """Atomically write *state* to *path* as indented JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state_to_dict(state), indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def feed_state_path(data_dir: Path) -> Path:
    """Return the canonical feed-state file path inside *data_dir*."""
    return data_dir / FEED_STATE_FILENAME


def encode_column_layout(raw: bytes) -> str:
    """Encode raw Qt header-state bytes as an ASCII base64 string."""
    return base64.b64encode(raw).decode("ascii")


def decode_column_layout(text: str) -> bytes:
    """Decode an ASCII base64 string back to raw bytes."""
    return base64.b64decode(text)

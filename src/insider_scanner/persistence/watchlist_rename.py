"""Recoverable publication of watchlists and the alert references they own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from insider_scanner.persistence.alerts import (
    AlertStore,
    alerts_path,
    save_alerts,
)
from insider_scanner.persistence.alerts import (
    store_from_dict as alerts_from_dict,
)
from insider_scanner.persistence.alerts import (
    store_to_dict as alerts_to_dict,
)
from insider_scanner.persistence.json_state import atomic_write_json
from insider_scanner.persistence.watchlists import (
    WatchlistError,
    WatchlistStore,
    save_watchlists,
    watchlists_path,
)
from insider_scanner.persistence.watchlists import (
    store_from_dict as watchlists_from_dict,
)
from insider_scanner.persistence.watchlists import (
    store_to_dict as watchlists_to_dict,
)

WATCHLIST_RENAME_FILENAME = "watchlist_rename.json"


class WatchlistRenamePendingError(OSError):
    """The intended rename is durable but still needs publication or cleanup."""


def _same_json(left: Any, right: Any) -> bool:
    """Compare canonical snapshots without accepting bool/number coercions."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    # Criteria parsing normalizes JSON integer bounds to floats; both retain
    # the same number. All other JSON types must remain unchanged.
    numeric = type(left) in (int, float) and type(right) in (int, float)
    return (type(left) is type(right) or numeric) and left == right


def _journal_stores(data: Any) -> tuple[WatchlistStore, AlertStore]:
    """Require complete canonical snapshots, never tolerant loader defaults."""
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "watchlists", "alerts"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["watchlists"], dict)
        or not isinstance(data["alerts"], dict)
    ):
        raise WatchlistError("Invalid watchlist rename journal structure")
    try:
        watchlists = watchlists_from_dict(data["watchlists"])
        alerts = alerts_from_dict(data["alerts"])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise WatchlistError("Invalid watchlist rename journal snapshots") from exc
    if not _same_json(
        watchlists_to_dict(watchlists), data["watchlists"]
    ) or not _same_json(alerts_to_dict(alerts), data["alerts"]):
        raise WatchlistError("Invalid watchlist rename journal snapshots")
    return watchlists, alerts


def _publish(data_dir: Path, watchlists: WatchlistStore, alerts: AlertStore) -> None:
    try:
        save_watchlists(watchlists_path(data_dir), watchlists)
        save_alerts(alerts_path(data_dir), alerts)
        (data_dir / WATCHLIST_RENAME_FILENAME).unlink()
    except OSError as exc:
        raise WatchlistRenamePendingError(
            f"Watchlist rename is saved in the journal; publication is pending: {exc}"
        ) from exc


def recover_watchlist_rename(data_dir: Path) -> None:
    """Replay a pending rename completely; retain malformed journals untouched."""
    journal = data_dir / WATCHLIST_RENAME_FILENAME
    try:
        raw = journal.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except UnicodeError as exc:
        raise WatchlistError("Cannot decode watchlist rename journal") from exc
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise WatchlistError("Cannot parse watchlist rename journal") from exc
    watchlists, alerts = _journal_stores(data)
    _publish(data_dir, watchlists, alerts)


def save_watchlist_rename(
    data_dir: Path, watchlists: WatchlistStore, alerts: AlertStore
) -> None:
    """Journal both desired snapshots before publishing either affected store."""
    try:
        recover_watchlist_rename(data_dir)
    except WatchlistRenamePendingError as exc:
        # This requested rename has not been journaled yet. The caller must
        # not accept its in-memory state on an earlier rename's pending error.
        raise OSError("Cannot start rename while earlier recovery is pending") from exc
    data = {
        "version": 1,
        "watchlists": watchlists_to_dict(watchlists),
        "alerts": alerts_to_dict(alerts),
    }
    _journal_stores(data)
    atomic_write_json(data_dir / WATCHLIST_RENAME_FILENAME, data)
    _publish(data_dir, watchlists, alerts)

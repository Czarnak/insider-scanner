"""Congress member list management and trade flagging.

Maintains a local JSON file of known Congress members so trades
by these individuals can be flagged in scan results.

Note: Family member disclosure data is not publicly machine-readable
and would require paid data services. This module documents that limitation.
"""

from __future__ import annotations

import json
from pathlib import Path

from insider_scanner.core.congress_identity import (
    MemberIndex,
    get_member_index,
    name_tokens,
)
from insider_scanner.core.models import InsiderTrade
from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.config import CONGRESS_FILE
from insider_scanner.utils.logging import get_logger

log = get_logger("senate")


def load_congress_members(path: Path | None = None) -> list[dict]:
    """Load the Congress member list from disk.

    Returns a list of dicts, each with at least ``"name"`` and optional
    ``"state"``, ``"chamber"`` (Senate/House), ``"party"``.
    """
    p = path or CONGRESS_FILE
    if not p.exists():
        log.debug("Congress file not found: %s", p)
        return []

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load congress file: %s", exc)
        return []


def save_congress_members(members: list[dict], path: Path | None = None) -> None:
    """Save the Congress member list to disk."""
    p = path or CONGRESS_FILE
    atomic_write_bytes(p, json.dumps(members, indent=2).encode("utf-8"))
    log.info("Saved %d congress members to %s", len(members), p)


def _normalize_name(name: str) -> str:
    """Normalize whole tokens without deleting substrings inside surnames."""
    return " ".join(name_tokens(name))


def flag_congress_trades(
    trades: list[InsiderTrade],
    members: list[dict] | None = None,
) -> list[InsiderTrade]:
    """Flag trades where the insider name matches a Congress member.

    Parameters
    ----------
    trades : list of InsiderTrade
        Trades to check.
    members : list of dict or None
        Congress member list. If None, loads from disk.

    Returns
    -------
    list of InsiderTrade
        Same list, mutated in place (is_congress and congress_member set).
    """
    index = get_member_index(CONGRESS_FILE) if members is None else MemberIndex(members)
    for trade in trades:
        member = index.resolve(trade.insider_name, allow_surname=False)
        if member is not None:
            trade.is_congress = True
            trade.congress_member = member.name

    flagged_count = sum(1 for t in trades if t.is_congress)
    if flagged_count:
        log.info("Flagged %d/%d trades as congress-related", flagged_count, len(trades))

    return trades


def init_default_congress_file(path: Path | None = None) -> None:
    """Create a missing member file from the authoritative packaged seed."""
    from insider_scanner.utils.config import initialize_congress_seed

    initialize_congress_seed(path or CONGRESS_FILE)

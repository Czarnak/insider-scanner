"""Merge raw trades using exact shared identities and explicit update rules."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import date
from pathlib import Path

import pandas as pd

from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.trade_identity import sec_identity, us_trade_identity
from insider_scanner.utils.config import SCAN_OUTPUTS_DIR, ensure_dirs
from insider_scanner.utils.logging import get_logger

log = get_logger("merger")


def merge_trade_pair(left: InsiderTrade, right: InsiderTrade) -> InsiderTrade:
    """Merge observations without mutation or JSON ranking.

    For the same SEC filing row, right is the authoritative incoming reparse,
    including corrected empty values and missing flags. Scraper duplicates
    coalesce each field independently with a stable typed maximum, so repeated
    enrichment is associative and independent of input order. Numeric values
    and their availability flags always travel together.
    """
    if left == right:
        return replace(left)
    if sec_identity(left) is not None and sec_identity(left) == sec_identity(right):
        merged = replace(right)
    else:
        updates = {}
        numeric_fields = ("shares", "price", "value", "shares_owned_after")
        numeric_flags = {f"{field}_missing" for field in numeric_fields}
        for field in fields(left):
            name = field.name
            if name in numeric_flags:
                continue
            if name in numeric_fields:
                flag = f"{name}_missing"
                winner = max(
                    (left, right),
                    key=lambda trade: (
                        not getattr(trade, flag),
                        abs(getattr(trade, name)),
                        getattr(trade, name),
                    ),
                )
                updates[name] = getattr(winner, name)
                updates[flag] = getattr(winner, flag)
            else:
                values = (getattr(left, name), getattr(right, name))
                updates[name] = (
                    max(value for value in values if value is not None)
                    if any(value is not None for value in values)
                    else None
                )
        merged = replace(left, **updates)
    edgar_urls = sorted(trade.edgar_url for trade in (left, right) if trade.edgar_url)
    congress_members = sorted(
        {
            trade.congress_member
            for trade in (left, right)
            if trade.is_congress and trade.congress_member
        }
    )
    return replace(
        merged,
        edgar_url=edgar_urls[-1] if edgar_urls else "",
        is_congress=left.is_congress or right.is_congress,
        congress_member=congress_members[-1] if congress_members else "",
    )


def merge_trades(
    *trade_lists: list[InsiderTrade],
) -> list[InsiderTrade]:
    """Merge and deduplicate trades from multiple sources.

    Parameters
    ----------
    *trade_lists : list of InsiderTrade
        One or more lists of trades (e.g. from secform4, openinsider).

    Returns
    -------
    list of InsiderTrade
        Merged and deduplicated, sorted by trade_date descending.
    """
    seen: dict[tuple, InsiderTrade] = {}

    for trades in trade_lists:
        for trade in trades:
            key = us_trade_identity(trade)
            existing = seen.get(key)

            if existing is None:
                seen[key] = replace(trade)
            else:
                seen[key] = merge_trade_pair(existing, trade)

    merged = list(seen.values())

    # Sort by trade date descending (None dates go last)
    merged.sort(
        key=lambda t: t.trade_date or date.min,
        reverse=True,
    )

    log.info(
        "Merged %d total trades → %d unique",
        sum(len(tl) for tl in trade_lists),
        len(merged),
    )
    return merged


def filter_trades(
    trades: list[InsiderTrade],
    *,
    ticker: str | None = None,
    trade_type: str | None = None,
    min_value: float | None = None,
    congress_only: bool = False,
    since: date | None = None,
    until: date | None = None,
) -> list[InsiderTrade]:
    """Filter trades by various criteria.

    Parameters
    ----------
    trades : list of InsiderTrade
        Input trades.
    ticker : str or None
        Filter by ticker symbol.
    trade_type : str or None
        Filter by trade type ("Buy", "Sell", etc.).
    min_value : float or None
        Minimum trade dollar value.
    congress_only : bool
        If True, only return congress-flagged trades.
    since : date or None
        Only return trades with filing_date on or after this date.
    until : date or None
        Only return trades with filing_date on or before this date.

    Returns
    -------
    list of InsiderTrade
    """
    result = trades

    if ticker:
        result = [t for t in result if t.ticker.upper() == ticker.upper()]
    if trade_type:
        result = [t for t in result if t.trade_type == trade_type]
    if min_value is not None:
        result = [
            t for t in result if not t.value_missing and abs(t.value) >= min_value
        ]
    if congress_only:
        result = [t for t in result if t.is_congress]
    if since:
        result = [t for t in result if t.filing_date and t.filing_date >= since]
    if until:
        result = [t for t in result if t.filing_date and t.filing_date <= until]

    return result


def trades_to_dataframe(trades: list[InsiderTrade]) -> pd.DataFrame:
    """Convert a list of InsiderTrade to a pandas DataFrame."""
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([t.to_dict() for t in trades])


def save_scan_results(
    trades: list[InsiderTrade],
    label: str = "scan",
    output_dir: Path | None = None,
) -> Path:
    """Save scan results as CSV and JSON.

    Returns the output directory.
    """
    ensure_dirs()
    out = output_dir or SCAN_OUTPUTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    # CSV
    df = trades_to_dataframe(trades)
    csv_path = out / f"{label}.csv"
    df.to_csv(csv_path, index=False)

    # JSON
    json_path = out / f"{label}.json"
    with open(json_path, "w") as f:
        json.dump([t.to_dict() for t in trades], f, indent=2)

    log.info("Saved %d trades to %s", len(trades), out)
    return out

"""European insider trade deduplication, filtering, and export.

Mirrors the role of ``merger.py`` for USA insider trades:
combines output from multiple country scrapers, deduplicates,
applies filters, and persists results.
"""

from __future__ import annotations

import json
from dataclasses import replace as _dc_replace
from datetime import date
from pathlib import Path

import pandas as pd

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.utils.config import SCAN_OUTPUTS_DIR, ensure_dirs
from insider_scanner.utils.logging import get_logger

log = get_logger("eu_merger")

# Ordered list of columns for display / export
DISPLAY_COLUMNS = [
    "isin",
    "issuer_name",
    "country",
    "regulatory_body",
    "insider_name",
    "position",
    "trade_date",
    "filing_date",
    "trade_type",
    "instrument_type",
    "volume",
    "price",
    "currency",
    "total_value",
    "source",
    "source_url",
]

# String fields where empty string means "missing" — filled from secondary source
_STR_FILL_FIELDS = (
    "issuer_name",
    "position",
    "instrument_type",
    "currency",
    "source_url",
)

# Optional fields where None means "missing" — filled from secondary source
_OPT_FILL_FIELDS = ("trade_date", "filing_date", "volume", "price", "total_value")


def _dedup_key(t: EuropeanInsiderTrade) -> tuple:
    """Composite key used to detect duplicate records across sources.

    Two records are considered duplicates when they refer to the same
    insider, security, trade date, and direction — regardless of which
    national regulator reported them.

    Note: ``volume`` is intentionally excluded from the key. Some sources
    (e.g. RNS HTML scraper) cannot always extract a volume figure for the
    same trade that BaFin or AMF report completely. Including volume would
    prevent deduplication whenever one source omits it.
    """
    return (
        (t.isin or "").upper(),
        (t.insider_name or "").lower().strip(),
        t.trade_date,
        t.trade_type,
    )


def _coalesce(
    primary: EuropeanInsiderTrade,
    secondary: EuropeanInsiderTrade,
) -> EuropeanInsiderTrade:
    """Return *primary* with any blank/None fields filled from *secondary*.

    This is the core of the coalescing merge strategy: rather than simply
    discarding a duplicate, we use it to fill in fields the primary record
    is missing.  The primary's non-empty fields are always kept as-is so
    that the highest-priority source always wins for fields it has data on.

    Example: RNS returns a trade with no ``filing_date`` or ``volume``.
    AMF returns the same trade (same ISIN / insider / date / direction)
    with both populated.  After coalescing, the merged record has the
    RNS data for its own fields plus AMF's ``filing_date`` and ``volume``.
    """
    updates = {}

    for f in _STR_FILL_FIELDS:
        primary_val = getattr(primary, f)
        secondary_val = getattr(secondary, f)
        if not primary_val and secondary_val:
            updates[f] = secondary_val

    for f in _OPT_FILL_FIELDS:
        primary_val = getattr(primary, f)
        secondary_val = getattr(secondary, f)
        if primary_val is None and secondary_val is not None:
            updates[f] = secondary_val

    return _dc_replace(primary, **updates) if updates else primary


def merge_eu_trades(
    *trade_lists: list[EuropeanInsiderTrade],
) -> list[EuropeanInsiderTrade]:
    """Merge trades from multiple country scrapers, deduplicating by key.

    Strategy: coalescing merge.
    - The first occurrence of a key establishes the primary record.
    - Subsequent duplicates are not discarded; instead their non-empty
      fields are used to fill any gaps in the primary record.
    - Pass higher-quality / more complete sources first so their data
      takes precedence for fields that both sources provide.

    Results are sorted by trade date descending (most recent first).
    """
    seen: dict[tuple, EuropeanInsiderTrade] = {}

    for trades in trade_lists:
        for t in trades:
            key = _dedup_key(t)
            if key not in seen:
                seen[key] = t
            else:
                seen[key] = _coalesce(seen[key], t)

    result = list(seen.values())
    result.sort(
        key=lambda t: t.trade_date or date.min,
        reverse=True,
    )
    return result


def filter_eu_trades(
    trades: list[EuropeanInsiderTrade],
    *,
    isin: str | None = None,
    country: str | None = None,
    trade_type: str | None = None,
    min_value: float | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[EuropeanInsiderTrade]:
    """Filter a list of European insider trades by multiple criteria.

    Parameters
    ----------
    trades:
        Input list to filter.
    isin:
        Keep only records matching this ISIN (case-insensitive).
    country:
        Keep only records from this country code (``UK``, ``DE``, ``FR``,
        ``NL``).  Pass ``None`` or ``"All"`` to disable.
    trade_type:
        Keep only ``"Buy"``, ``"Sell"``, or ``"Other"`` trades.  Pass
        ``None`` or ``"All"`` to disable.
    min_value:
        Keep only trades whose ``total_value`` is ≥ this threshold.
    since / until:
        Keep only trades whose ``trade_date`` falls within the range.
    """
    result = trades

    if isin:
        result = [t for t in result if t.isin.upper() == isin.upper()]

    if country and country.upper() not in ("ALL", ""):
        result = [t for t in result if t.country.upper() == country.upper()]

    if trade_type and trade_type not in ("All", ""):
        result = [t for t in result if t.trade_type == trade_type]

    if min_value is not None:
        result = [t for t in result if (t.total_value or 0.0) >= min_value]

    if since:
        result = [t for t in result if t.trade_date and t.trade_date >= since]

    if until:
        result = [t for t in result if t.trade_date and t.trade_date <= until]

    return result


def eu_trades_to_dataframe(
    trades: list[EuropeanInsiderTrade],
) -> pd.DataFrame:
    """Convert a list of EuropeanInsiderTrade to a pandas DataFrame.

    Returns a DataFrame with columns ordered per ``DISPLAY_COLUMNS``.
    Returns an empty DataFrame with correct columns when the list is empty.
    """
    if not trades:
        return pd.DataFrame(columns=DISPLAY_COLUMNS)

    df = pd.DataFrame([t.to_dict() for t in trades])
    # Keep only recognised columns in display order
    cols = [c for c in DISPLAY_COLUMNS if c in df.columns]
    return df[cols]


def save_eu_results(
    trades: list[EuropeanInsiderTrade],
    label: str = "eu_scan",
    output_dir: Path | None = None,
) -> Path:
    """Persist European scan results as CSV and JSON.

    Returns the output directory path.
    """
    ensure_dirs()
    out = output_dir or SCAN_OUTPUTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    df = eu_trades_to_dataframe(trades)
    df.to_csv(out / f"{label}.csv", index=False)

    with open(out / f"{label}.json", "w", encoding="utf-8") as f:
        json.dump([t.to_dict() for t in trades], f, indent=2, default=str)

    log.info("Saved %d EU trades to %s", len(trades), out)
    return out

"""Canonical keys and lossless domain/table mappings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Any

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.core.trade_identity import us_trade_identity

_US_FIELDS = tuple(InsiderTrade.__dataclass_fields__)
_CONGRESS_FIELDS = tuple(CongressTrade.__dataclass_fields__)
_EU_FIELDS = tuple(EuropeanInsiderTrade.__dataclass_fields__)
_EU_STRING_FILL_FIELDS = (
    "issuer_name",
    "position",
    "instrument_type",
    "currency",
    "source_url",
)
_EU_OPTIONAL_FILL_FIELDS = (
    "trade_date",
    "filing_date",
    "volume",
    "price",
    "total_value",
)


def _is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return value != 0
    return True


def _digest(parts: tuple[Any, ...]) -> str:
    payload = json.dumps(parts, default=str, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return value.strip().casefold()


def us_canonical_key(trade: InsiderTrade) -> str:
    """Hash the shared raw identity, retaining SEC/scraper separation."""
    return _digest(us_trade_identity(trade))


def congress_canonical_key(trade: CongressTrade) -> str:
    """Identify one filing transaction independently of descriptive URLs."""
    if trade.doc_id.strip() and trade.source_row_id.strip():
        return _digest(
            (
                "congress-row",
                _normalized(trade.source),
                trade.doc_id.strip(),
                trade.source_row_id.strip(),
            )
        )
    return _digest(
        (
            _normalized(trade.official_name),
            _normalized(trade.source),
            trade.doc_id.strip(),
            trade.filing_date,
            trade.trade_date,
            _normalized(trade.asset_description),
            trade.ticker.strip().upper(),
            trade.trade_type,
            _normalized(trade.owner),
            trade.amount_range.strip(),
        )
    )


def european_canonical_key(trade: EuropeanInsiderTrade) -> str:
    """Return a stable European transaction identity.

    Dated disclosures retain the established cross-source coalescing key.
    Undated disclosures include transaction and source evidence so separate
    transactions cannot collapse merely because their date is unavailable.
    """
    identity: tuple[Any, ...] = (
        trade.isin.strip().upper(),
        _normalized(trade.insider_name),
        trade.trade_date,
        trade.trade_type,
    )
    if trade.trade_date is None:
        identity += (
            trade.filing_date,
            _normalized(trade.instrument_type),
            trade.volume,
            trade.price,
            trade.currency.strip().upper(),
            trade.total_value,
            _normalized(trade.source),
            trade.source_url.strip(),
            trade.regulatory_body,
            trade.country,
        )
    return _digest(identity)


def us_to_values(trade: InsiderTrade, provenance: set[str] | None = None) -> dict:
    values = asdict(trade)
    sources = set(provenance or ())
    sources.add(trade.source)
    values.update(
        canonical_key=us_canonical_key(trade),
        ticker=trade.ticker.strip().upper(),
        source_provenance_json=json.dumps(sorted(sources)),
    )
    return values


def congress_to_values(trade: CongressTrade) -> dict:
    return {"canonical_key": congress_canonical_key(trade), **asdict(trade)}


def european_to_values(
    trade: EuropeanInsiderTrade,
    provenance: set[str] | None = None,
) -> dict:
    values = asdict(trade)
    sources = set(provenance or ())
    sources.add(trade.source)
    values.update(
        canonical_key=european_canonical_key(trade),
        isin=trade.isin.strip().upper(),
        source_provenance_json=json.dumps(sorted(sources)),
    )
    return values


def us_from_row(row: Any) -> InsiderTrade:
    return InsiderTrade(**{field: row[field] for field in _US_FIELDS if field in row})


def congress_from_row(row: Any) -> CongressTrade:
    return CongressTrade(**{field: row[field] for field in _CONGRESS_FIELDS})


def european_from_row(row: Any) -> EuropeanInsiderTrade:
    return EuropeanInsiderTrade(**{field: row[field] for field in _EU_FIELDS})


def us_provenance(row: Any) -> frozenset[str]:
    return _source_provenance(row)


def european_provenance(row: Any) -> frozenset[str]:
    return _source_provenance(row)


def _source_provenance(row: Any) -> frozenset[str]:
    try:
        values = json.loads(row["source_provenance_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        values = []
    return frozenset(str(value) for value in values) | {str(row["source"])}


def coalesce_congress_trades(
    left: CongressTrade,
    right: CongressTrade,
) -> CongressTrade:
    """Apply populated incoming corrections; empty fields retain stored data."""
    merged = replace(
        left,
        **{
            field: getattr(right, field)
            if _is_populated(getattr(right, field))
            else getattr(left, field)
            for field in _CONGRESS_FIELDS
        },
    )
    # A zero lower bound can be reported alongside a populated upper bound.
    # Select the complete range so a correction cannot create inverted bounds.
    amounts = right if right.amount_low or right.amount_high else left
    return replace(
        merged,
        amount_low=amounts.amount_low,
        amount_high=amounts.amount_high,
        filing_complete=right.filing_complete
        if right.report_title
        else left.filing_complete,
    )


def coalesce_european_trades(
    primary: EuropeanInsiderTrade,
    secondary: EuropeanInsiderTrade,
) -> EuropeanInsiderTrade:
    """Fill primary gaps from secondary while preserving source priority."""
    updates: dict[str, Any] = {}
    for field in _EU_STRING_FILL_FIELDS:
        if not getattr(primary, field) and getattr(secondary, field):
            updates[field] = getattr(secondary, field)
    for field in _EU_OPTIONAL_FILL_FIELDS:
        if getattr(primary, field) is None and getattr(secondary, field) is not None:
            updates[field] = getattr(secondary, field)
    return replace(primary, **updates) if updates else replace(primary)

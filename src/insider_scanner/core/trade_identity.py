"""Raw transaction identities shared by in-memory merging and persistence.

SEC rows retain filing/row identity and coexist with scraper observations.
Scraper observations match on exact quantity magnitude, price and trade kind;
there is no share bucketing or implicit fuzzy tolerance.
"""

from __future__ import annotations

from insider_scanner.core.models import InsiderTrade


def sec_identity(trade: InsiderTrade) -> tuple[str, str] | None:
    accession = (trade.accession_number or "").strip()
    row = (trade.sec_row_id or "").strip()
    return (accession, row) if accession and row else None


def us_trade_identity(trade: InsiderTrade) -> tuple:
    native = sec_identity(trade)
    if native:
        # Keep the established SEC digest unchanged.
        return ("sec", *native)
    identity: tuple = (
        "scraper-v2",
        trade.ticker.strip().upper(),
        trade.insider_name.strip().casefold(),
        trade.trade_date,
        trade.trade_type,
        abs(float(trade.shares)),
        float(trade.price),
        trade.shares_missing,
        trade.price_missing,
    )
    if trade.trade_date is None:
        # Without a transaction date, do not match unrelated source reports.
        identity += (
            trade.filing_date,
            trade.source.strip().casefold(),
            trade.edgar_url,
        )
    return identity

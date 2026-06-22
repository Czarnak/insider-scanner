"""Map ``OwnershipFiling`` + ``SecMasterIndexRow`` → ``tuple[InsiderTrade, ...]``.

Pure module — no database access, no network I/O.  Call
``ownership_filing_to_trades`` to convert a single parsed SEC filing into one
``InsiderTrade`` per transaction (non-derivative first, then derivative).

Field mapping rules
-------------------
* ``trade_type`` derives from the parser ``category`` field:
  purchase→Buy, sale→Sell, exercise→Exercise, everything else→Other.
* ``ticker`` = ``filing.issuer.trading_symbol`` when truthy, else ``""``.
* ``company`` = ``filing.issuer.name`` when truthy, else
  ``index_row.company_name`` (fall-through to index authoritative name).
* ``insider_title`` = ``officer_title`` when truthy, else a joined string of
  the active boolean-role flags (Director / Officer / 10% Owner / Other).
* ``value`` = ``float(shares * price_per_share)`` when BOTH numeric fields are
  present, else ``0.0``.
* ``sec_detail_json`` is serialized with ``sort_keys=True`` so repeated calls
  on identical input yield byte-identical output (idempotent re-ingest).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from insider_scanner.core import edgar
from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import (
    DerivativeTransaction,
    Footnote,
    NonDerivativeTransaction,
    OwnershipFiling,
)

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class SecTradeMappingError(Exception):
    """Raised when a transaction or filing cannot be safely mapped."""


# ---------------------------------------------------------------------------
# Footnote embedding caps (defensive; parser bounds text length but not count)
# ---------------------------------------------------------------------------

# Maximum characters kept per embedded footnote text in sec_detail_json.
_MAX_FOOTNOTE_CHARS: int = 4096
# Maximum number of footnote IDs / text entries embedded in sec_detail_json.
_MAX_EMBEDDED_FOOTNOTES: int = 64

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CATEGORY_TO_TRADE_TYPE: dict[str, str] = {
    "purchase": "Buy",
    "sale": "Sell",
    "exercise": "Exercise",
}


def _trade_type(category: str) -> str:
    return _CATEGORY_TO_TRADE_TYPE.get(category, "Other")  # type: ignore[return-value]


def _insider_title(
    officer_title: str | None,
    is_director: bool,
    is_officer: bool,
    is_ten_percent_owner: bool,
    is_other: bool,
) -> str:
    if officer_title:
        return officer_title
    roles: list[str] = []
    if is_director:
        roles.append("Director")
    if is_officer:
        roles.append("Officer")
    if is_ten_percent_owner:
        roles.append("10% Owner")
    if is_other:
        roles.append("Other")
    return ", ".join(roles)


def _footnote_index(footnotes: tuple[Footnote, ...]) -> dict[str, str]:
    """Build a lookup dict ``footnote_id → text`` from the filing footnotes.

    Text is truncated to ``_MAX_FOOTNOTE_CHARS`` to keep sec_detail_json
    bounded even when upstream XML contains unusually long footnotes.
    """
    return {fn.footnote_id: fn.text[:_MAX_FOOTNOTE_CHARS] for fn in footnotes}


def _decimal_or_null(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _date_or_null(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Validation (A4)
# ---------------------------------------------------------------------------


def _validate_non_derivative(
    txn: NonDerivativeTransaction,
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
) -> None:
    """Raise ``SecTradeMappingError`` for malformed non-derivative transaction."""
    if not txn.row_id or not txn.row_id.strip():
        raise SecTradeMappingError(
            f"Non-derivative transaction has empty row_id "
            f"(accession={filing.accession_number!r})"
        )
    _validate_common_dates(filing, index_row)


def _validate_derivative(
    txn: DerivativeTransaction,
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
) -> None:
    """Raise ``SecTradeMappingError`` for malformed derivative transaction."""
    if not txn.row_id or not txn.row_id.strip():
        raise SecTradeMappingError(
            f"Derivative transaction has empty row_id "
            f"(accession={filing.accession_number!r})"
        )
    if (
        txn.exercise_date is not None
        and txn.expiration_date is not None
        and txn.exercise_date > txn.expiration_date
    ):
        raise SecTradeMappingError(
            f"Derivative transaction row_id={txn.row_id!r} has exercise_date "
            f"({txn.exercise_date}) after expiration_date ({txn.expiration_date})"
        )
    _validate_common_dates(filing, index_row)


def _validate_common_dates(
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
) -> None:
    """Raise ``SecTradeMappingError`` when period_of_report is after filing_date."""
    if (
        filing.period_of_report is not None
        and filing.period_of_report > index_row.filing_date
    ):
        raise SecTradeMappingError(
            f"period_of_report ({filing.period_of_report}) is after "
            f"filing_date ({index_row.filing_date}) for "
            f"accession={filing.accession_number!r}"
        )


# ---------------------------------------------------------------------------
# sec_detail_json builders
# ---------------------------------------------------------------------------


def _amends_key(
    cik_issuer: str,
    cik_insider: str,
    period_of_report: date | None,
    security_title: str | None,
) -> str:
    period_str = period_of_report.isoformat() if period_of_report is not None else ""
    title_str = security_title or ""
    return f"{cik_issuer}|{cik_insider}|{period_str}|{title_str}"


def _build_non_derivative_detail(
    txn: NonDerivativeTransaction,
    filing: OwnershipFiling,
    fn_index: dict[str, str],
    cik_issuer: str,
    cik_insider: str,
) -> str:
    # Cap the number of embedded footnotes; determinism preserved by keeping
    # the original document order and applying a fixed slice limit.
    capped_ids = txn.footnote_ids[:_MAX_EMBEDDED_FOOTNOTES]
    detail: dict[str, object] = {
        "footnote_ids": list(capped_ids),
        "footnotes": {fid: fn_index.get(fid, "") for fid in capped_ids},
        "security_title": txn.security_title,
    }
    if filing.is_amendment:
        detail["amends"] = _amends_key(
            cik_issuer, cik_insider, filing.period_of_report, txn.security_title
        )
    return json.dumps(detail, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _build_derivative_detail(
    txn: DerivativeTransaction,
    filing: OwnershipFiling,
    fn_index: dict[str, str],
    cik_issuer: str,
    cik_insider: str,
) -> str:
    # Cap the number of embedded footnotes; determinism preserved by keeping
    # the original document order and applying a fixed slice limit.
    capped_ids = txn.footnote_ids[:_MAX_EMBEDDED_FOOTNOTES]
    detail: dict[str, object] = {
        "conversion_or_exercise_price": _decimal_or_null(
            txn.conversion_or_exercise_price
        ),
        "exercise_date": _date_or_null(txn.exercise_date),
        "expiration_date": _date_or_null(txn.expiration_date),
        "footnote_ids": list(capped_ids),
        "footnotes": {fid: fn_index.get(fid, "") for fid in capped_ids},
        "security_title": txn.security_title,
        "underlying_security_shares": _decimal_or_null(txn.underlying_security_shares),
        "underlying_security_title": txn.underlying_security_title,
    }
    if filing.is_amendment:
        detail["amends"] = _amends_key(
            cik_issuer, cik_insider, filing.period_of_report, txn.security_title
        )
    return json.dumps(detail, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Per-transaction trade builders
# ---------------------------------------------------------------------------


def _build_non_derivative_trade(
    txn: NonDerivativeTransaction,
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
    ticker: str,
    company: str,
    insider_name: str,
    insider_title: str,
    edgar_url: str,
    cik_issuer: str,
    cik_insider: str,
    fn_index: dict[str, str],
) -> InsiderTrade:
    shares_val = float(txn.shares) if txn.shares is not None else 0.0
    price_val = float(txn.price_per_share) if txn.price_per_share is not None else 0.0
    value_val = (
        float(txn.shares * txn.price_per_share)
        if txn.shares is not None and txn.price_per_share is not None
        else 0.0
    )
    owned_after = (
        float(txn.shares_owned_following)
        if txn.shares_owned_following is not None
        else 0.0
    )
    return InsiderTrade(
        ticker=ticker,
        company=company,
        insider_name=insider_name,
        insider_title=insider_title,
        trade_type=_trade_type(txn.category),  # type: ignore[arg-type]
        trade_date=txn.transaction_date,
        filing_date=index_row.filing_date,
        shares=shares_val,
        price=price_val,
        value=value_val,
        shares_owned_after=owned_after,
        source="sec_edgar",
        edgar_url=edgar_url,
        accession_number=filing.accession_number or "",
        sec_row_id=txn.row_id,
        form_type=filing.document_type,
        cik_issuer=cik_issuer,
        cik_insider=cik_insider,
        period_of_report=filing.period_of_report,
        is_amendment=filing.is_amendment,
        is_derivative=False,
        document_sha256=filing.document_sha256,
        transaction_code=txn.transaction_code or "",
        acquired_disposed=txn.acquired_disposed or "",
        direct_or_indirect=txn.direct_or_indirect or "",
        sec_detail_json=_build_non_derivative_detail(
            txn, filing, fn_index, cik_issuer, cik_insider
        ),
    )


def _build_derivative_trade(
    txn: DerivativeTransaction,
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
    ticker: str,
    company: str,
    insider_name: str,
    insider_title: str,
    edgar_url: str,
    cik_issuer: str,
    cik_insider: str,
    fn_index: dict[str, str],
) -> InsiderTrade:
    shares_val = float(txn.shares) if txn.shares is not None else 0.0
    price_val = float(txn.price_per_share) if txn.price_per_share is not None else 0.0
    value_val = (
        float(txn.shares * txn.price_per_share)
        if txn.shares is not None and txn.price_per_share is not None
        else 0.0
    )
    owned_after = (
        float(txn.shares_owned_following)
        if txn.shares_owned_following is not None
        else 0.0
    )
    return InsiderTrade(
        ticker=ticker,
        company=company,
        insider_name=insider_name,
        insider_title=insider_title,
        trade_type=_trade_type(txn.category),  # type: ignore[arg-type]
        trade_date=txn.transaction_date,
        filing_date=index_row.filing_date,
        shares=shares_val,
        price=price_val,
        value=value_val,
        shares_owned_after=owned_after,
        source="sec_edgar",
        edgar_url=edgar_url,
        accession_number=filing.accession_number or "",
        sec_row_id=txn.row_id,
        form_type=filing.document_type,
        cik_issuer=cik_issuer,
        cik_insider=cik_insider,
        period_of_report=filing.period_of_report,
        is_amendment=filing.is_amendment,
        is_derivative=True,
        document_sha256=filing.document_sha256,
        transaction_code=txn.transaction_code or "",
        acquired_disposed=txn.acquired_disposed or "",
        direct_or_indirect=txn.direct_or_indirect or "",
        sec_detail_json=_build_derivative_detail(
            txn, filing, fn_index, cik_issuer, cik_insider
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ownership_filing_to_trades(
    filing: OwnershipFiling,
    index_row: SecMasterIndexRow,
) -> tuple[InsiderTrade, ...]:
    """Convert one ``OwnershipFiling`` into zero or more ``InsiderTrade`` rows.

    Non-derivative transactions are emitted first (in their original order),
    followed by derivative transactions.  Raises ``SecTradeMappingError`` for
    malformed transactions before any ``InsiderTrade`` is constructed.

    Parameters
    ----------
    filing:
        Parsed, validated ownership filing produced by ``sec_ownership_parser``.
    index_row:
        The corresponding row from the SEC master index (provides authoritative
        filing date and archive URL path).

    Returns
    -------
    tuple[InsiderTrade, ...]
        One ``InsiderTrade`` per transaction; empty tuple when the filing has no
        transactions.
    """
    # Shared derived values (computed once per filing)
    ticker = filing.issuer.trading_symbol or ""
    company = filing.issuer.name or index_row.company_name
    insider_name = filing.reporting_owner.name or ""
    insider_title = _insider_title(
        filing.reporting_owner.officer_title,
        filing.reporting_owner.is_director,
        filing.reporting_owner.is_officer,
        filing.reporting_owner.is_ten_percent_owner,
        filing.reporting_owner.is_other,
    )
    edgar_url = edgar.build_filing_archive_url(index_row.archive_path)
    cik_issuer = filing.issuer.cik or ""
    cik_insider = filing.reporting_owner.cik or ""
    fn_index = _footnote_index(filing.footnotes)

    trades: list[InsiderTrade] = []

    for nd_txn in filing.non_derivative_transactions:
        _validate_non_derivative(nd_txn, filing, index_row)
        trades.append(
            _build_non_derivative_trade(
                nd_txn,
                filing,
                index_row,
                ticker,
                company,
                insider_name,
                insider_title,
                edgar_url,
                cik_issuer,
                cik_insider,
                fn_index,
            )
        )

    for d_txn in filing.derivative_transactions:
        _validate_derivative(d_txn, filing, index_row)
        trades.append(
            _build_derivative_trade(
                d_txn,
                filing,
                index_row,
                ticker,
                company,
                insider_name,
                insider_title,
                edgar_url,
                cik_issuer,
                cik_insider,
                fn_index,
            )
        )

    return tuple(trades)

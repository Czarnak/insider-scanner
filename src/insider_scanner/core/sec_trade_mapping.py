"""Map ``OwnershipFiling`` + ``SecMasterIndexRow`` → ``tuple[InsiderTrade, ...]``.

Pure module — no database access, no network I/O.  Call
``ownership_filing_to_trades`` to convert a single parsed SEC filing into one
``InsiderTrade`` per transaction (non-derivative first, then derivative).

Field mapping rules
-------------------
* Stock ``trade_type`` derives from ``category``: purchase→Buy, sale→Sell,
  exercise→Exercise. Derivatives remain neutral ``Other`` with their original
  category and amounts retained in detail, so an exercise's option leg is not
  counted as another stock acquisition.
* ``ticker`` = ``filing.issuer.trading_symbol`` when truthy, else ``""``.
* The index company name is a fallback only for the issuer's own index row.
* The primary owner's columns remain stable; all reporting owners and roles
  are retained in ``sec_detail_json`` without duplicating transactions.
* Value uses shares times price, or the separately reported transaction total.
  Missing numeric fields carry explicit flags; their legacy floats remain zero.
* ``sec_detail_json`` is serialized with ``sort_keys=True`` so repeated calls
  on identical input yield byte-identical output (idempotent re-ingest).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
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
    ReportingOwner,
)
from insider_scanner.utils.logging import get_logger

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
    other_text: str | None = None,
) -> str:
    roles: list[str] = [officer_title] if officer_title else []
    if is_director:
        roles.append("Director")
    if is_officer and not officer_title:
        roles.append("Officer")
    if is_ten_percent_owner:
        roles.append("10% Owner")
    if is_other:
        roles.append(other_text or "Other")
    return ", ".join(roles)


def _normalized_cik(cik: str | None) -> str:
    if not cik:
        return ""
    try:
        return edgar.normalize_cik(cik.strip())
    except (TypeError, ValueError) as exc:
        raise SecTradeMappingError("SEC CIK is invalid") from exc


def _owner_detail(owner: ReportingOwner) -> dict[str, object]:
    return {
        "cik": _normalized_cik(owner.cik),
        "name": owner.name,
        "officer_title": owner.officer_title,
        "is_director": owner.is_director,
        "is_officer": owner.is_officer,
        "is_ten_percent_owner": owner.is_ten_percent_owner,
        "is_other": owner.is_other,
        "other_text": owner.other_text,
    }


def _footnote_index(footnotes: tuple[Footnote, ...]) -> dict[str, str]:
    """Build a lookup dict ``footnote_id → text`` from the filing footnotes.

    Text is truncated to ``_MAX_FOOTNOTE_CHARS`` to keep sec_detail_json
    bounded even when upstream XML contains unusually long footnotes.
    """
    return {fn.footnote_id: fn.text[:_MAX_FOOTNOTE_CHARS] for fn in footnotes}


def _decimal_or_null(value: Decimal | None) -> str | None:
    if value is not None:
        _finite_float(value)
        return str(value)
    return None


def _date_or_null(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class _MappingContext:
    filing: OwnershipFiling
    index_row: SecMasterIndexRow
    ticker: str
    company: str
    insider_name: str
    insider_title: str
    edgar_url: str
    cik_issuer: str
    cik_insider: str
    footnotes: dict[str, str]
    warnings: tuple[str, ...]


_log = get_logger("sec_trade_mapping")


def _finite_float(value: Decimal | None) -> float:
    if value is None:
        return 0.0
    try:
        if not value.is_finite():
            raise ValueError("non-finite decimal")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("float overflow")
        return converted
    except (ArithmeticError, ValueError) as exc:
        raise SecTradeMappingError(
            "SEC numeric value is not finite or representable"
        ) from exc


def _amends_key(
    cik_issuer: str,
    cik_insider: str,
    period_of_report: date | None,
    security_title: str | None,
) -> str:
    """Legacy display hint; dated columns and transaction evidence own linkage."""
    period_str = period_of_report.isoformat() if period_of_report is not None else ""
    title_str = security_title or ""
    return f"{cik_issuer}|{cik_insider}|{period_str}|{title_str}"


def _build_detail(
    txn: NonDerivativeTransaction | DerivativeTransaction, ctx: _MappingContext
) -> str:
    price_ids = txn.price_footnote_ids[:_MAX_EMBEDDED_FOOTNOTES]
    capped_ids = tuple(dict.fromkeys((*price_ids, *txn.footnote_ids)))[
        :_MAX_EMBEDDED_FOOTNOTES
    ]
    detail: dict[str, object] = {
        "remarks": (ctx.filing.remarks or "")[:_MAX_FOOTNOTE_CHARS],
        "footnote_ids": list(capped_ids),
        "footnotes": {fid: ctx.footnotes.get(fid, "") for fid in capped_ids},
        "security_title": txn.security_title,
        "category": txn.category,
        "transaction_shares": _decimal_or_null(txn.shares),
        "transaction_price_per_share": _decimal_or_null(txn.price_per_share),
        "shares_owned_following": _decimal_or_null(txn.shares_owned_following),
        "transaction_total_value": _decimal_or_null(txn.transaction_total_value),
        "value_owned_following": _decimal_or_null(txn.value_owned_following),
        "price_footnote_ids": list(price_ids),
        "reporting_owners": [
            _owner_detail(owner)
            for owner in (ctx.filing.reporting_owners or (ctx.filing.reporting_owner,))
        ],
    }
    warnings = [*ctx.warnings, *txn.warnings]
    if isinstance(txn, DerivativeTransaction):
        detail.update(
            {
                "conversion_or_exercise_price": _decimal_or_null(
                    txn.conversion_or_exercise_price
                ),
                "exercise_date": _date_or_null(txn.exercise_date),
                "expiration_date": _date_or_null(txn.expiration_date),
                "underlying_security_shares": _decimal_or_null(
                    txn.underlying_security_shares
                ),
                "underlying_security_title": txn.underlying_security_title,
                "underlying_security_value": _decimal_or_null(
                    txn.underlying_security_value
                ),
            }
        )
        if (
            txn.exercise_date is not None
            and txn.expiration_date is not None
            and txn.exercise_date > txn.expiration_date
        ):
            warnings.append("exercise_date_after_expiration_date")
            _log.warning(
                "SEC data-quality warning reason=exercise_date_after_expiration_date"
            )
    if warnings:
        detail["warnings"] = warnings
    if ctx.filing.is_amendment:
        detail["amends"] = _amends_key(
            ctx.cik_issuer,
            ctx.cik_insider,
            ctx.filing.period_of_report,
            txn.security_title,
        )
    return json.dumps(detail, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _build_trade(
    txn: NonDerivativeTransaction | DerivativeTransaction, ctx: _MappingContext
) -> InsiderTrade:
    filing = ctx.filing
    value = (
        txn.shares * txn.price_per_share
        if txn.shares is not None and txn.price_per_share is not None
        else txn.transaction_total_value
    )
    return InsiderTrade(
        ticker=ctx.ticker,
        company=ctx.company,
        insider_name=ctx.insider_name,
        insider_title=ctx.insider_title,
        trade_type="Other"
        if isinstance(txn, DerivativeTransaction)
        else _trade_type(txn.category),  # type: ignore[arg-type]
        trade_date=txn.transaction_date,
        filing_date=ctx.index_row.filing_date,
        shares=_finite_float(txn.shares),
        price=_finite_float(txn.price_per_share),
        value=_finite_float(value),
        shares_owned_after=_finite_float(txn.shares_owned_following),
        shares_missing=txn.shares is None,
        price_missing=txn.price_per_share is None,
        value_missing=value is None,
        shares_owned_after_missing=txn.shares_owned_following is None,
        source="sec_edgar",
        edgar_url=ctx.edgar_url,
        accession_number=filing.accession_number or "",
        sec_row_id=txn.row_id,
        form_type=filing.document_type,
        cik_issuer=ctx.cik_issuer,
        cik_insider=ctx.cik_insider,
        period_of_report=filing.period_of_report,
        original_submission_date=filing.original_submission_date,
        is_amendment=filing.is_amendment,
        is_derivative=isinstance(txn, DerivativeTransaction),
        document_sha256=filing.document_sha256,
        transaction_code=txn.transaction_code or "",
        acquired_disposed=txn.acquired_disposed or "",
        direct_or_indirect=txn.direct_or_indirect or "",
        sec_detail_json=_build_detail(txn, ctx),
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
    unrepresentable numeric values. Date-quality anomalies retain rows with
    payload-free warnings in ``sec_detail_json``.

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
    warnings = list(filing.warnings)
    if (
        filing.period_of_report is not None
        and filing.period_of_report > index_row.filing_date
    ):
        warnings.append("period_of_report_after_filing_date")
        _log.warning(
            "SEC data-quality warning reason=period_of_report_after_filing_date"
        )
    cik_issuer = _normalized_cik(filing.issuer.cik)
    index_cik = _normalized_cik(index_row.cik)
    ctx = _MappingContext(
        filing=filing,
        index_row=index_row,
        ticker=filing.issuer.trading_symbol or "",
        company=filing.issuer.name
        or (index_row.company_name if cik_issuer and cik_issuer == index_cik else ""),
        insider_name=filing.reporting_owner.name or "",
        insider_title=_insider_title(
            filing.reporting_owner.officer_title,
            filing.reporting_owner.is_director,
            filing.reporting_owner.is_officer,
            filing.reporting_owner.is_ten_percent_owner,
            filing.reporting_owner.is_other,
            filing.reporting_owner.other_text,
        ),
        edgar_url=edgar.build_filing_archive_url(index_row.archive_path),
        cik_issuer=cik_issuer,
        cik_insider=_normalized_cik(filing.reporting_owner.cik),
        footnotes=_footnote_index(filing.footnotes),
        warnings=tuple(warnings),
    )
    transactions: tuple[NonDerivativeTransaction | DerivativeTransaction, ...] = (
        *filing.non_derivative_transactions,
        *filing.derivative_transactions,
    )
    try:
        return tuple(_build_trade(txn, ctx) for txn in transactions)
    except (ArithmeticError, ValueError) as exc:
        raise SecTradeMappingError("SEC numeric conversion failed") from exc

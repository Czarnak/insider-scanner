"""Parse Form 3/4/5 ownership XML into normalized, immutable in-memory records.

Accepts an ``OwnershipDocument`` (produced by ``sec_ownership_document``) and
returns an ``OwnershipFiling`` with typed dataclasses.  No persistence, no
network access.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from insider_scanner.core._sec_xml import (
    SecXmlSecurityError,
)
from insider_scanner.core.edgar import normalize_cik
from insider_scanner.core.sec_forms import OWNERSHIP_FORM_TYPES
from insider_scanner.core.sec_ownership_document import (
    OwnershipDocument,
    SecOwnershipDocumentError,
    _validated_ownership_xml,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)
from insider_scanner.utils.logging import get_logger

if TYPE_CHECKING:
    from lxml.etree import _Element


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _TransactionCommon:
    """Shared fields extracted from both derivative and non-derivative rows."""

    shares: Decimal | None
    price_per_share: Decimal | None
    acquired_disposed: str | None
    shares_owned_following: Decimal | None
    direct_or_indirect: str | None
    transaction_total_value: Decimal | None
    value_owned_following: Decimal | None
    price_footnote_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ParseContext:
    """Carries the active security policy and accession through field parsing."""

    policy: SecSecurityPolicy
    accession: str | None
    warnings: list[str] = field(default_factory=list)


# Collapses runs of whitespace (incl. newlines/tabs from flattened markup).
_WHITESPACE_RE = re.compile(r"\s+")
_XS_DATE_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))?\Z"
)
_GROUPED_NUMBER_RE = re.compile(r"[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?\Z")
_log = get_logger("sec_ownership_parser")

# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

# SEC Form 4, General Instruction 8 (Transaction Codes):
# https://www.sec.gov/files/form4.pdf
_CATEGORY_MAP: dict[str, str] = {
    "P": "purchase",
    "S": "sale",
    "A": "award",
    "C": "exercise",
    "M": "exercise",
    "O": "exercise",
    "X": "exercise",
    "F": "tax",
    "G": "gift",
    "D": "disposition_to_issuer",
    "E": "expiration",
    "H": "expiration",
    "I": "discretionary",
    "J": "other",
    "K": "equity_swap",
    "L": "small_acquisition",
    "U": "tender",
    "V": "voluntary_report",
    "W": "inheritance",
    "Z": "trust",
}

# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class SecOwnershipParseError(Exception):
    """Raised when an ownership document cannot be parsed safely."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Footnote:
    footnote_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Issuer:
    cik: str | None
    name: str | None
    trading_symbol: str | None


@dataclass(frozen=True, slots=True)
class ReportingOwner:
    cik: str | None
    name: str | None
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str | None
    other_text: str | None = None


@dataclass(frozen=True, slots=True)
class NonDerivativeTransaction:
    row_id: str
    security_title: str | None
    transaction_date: date | None
    transaction_code: str | None
    category: str
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    shares_owned_following: Decimal | None
    direct_or_indirect: str | None
    footnote_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    transaction_total_value: Decimal | None = None
    value_owned_following: Decimal | None = None
    price_footnote_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DerivativeTransaction:
    row_id: str
    security_title: str | None
    transaction_date: date | None
    transaction_code: str | None
    category: str
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    conversion_or_exercise_price: Decimal | None
    exercise_date: date | None
    expiration_date: date | None
    underlying_security_title: str | None
    underlying_security_shares: Decimal | None
    shares_owned_following: Decimal | None
    direct_or_indirect: str | None
    footnote_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    transaction_total_value: Decimal | None = None
    value_owned_following: Decimal | None = None
    price_footnote_ids: tuple[str, ...] = ()
    underlying_security_value: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OwnershipFiling:
    accession_number: str | None
    document_type: str
    is_amendment: bool
    period_of_report: date | None
    remarks: str | None
    document_sha256: str
    issuer: Issuer
    reporting_owner: ReportingOwner
    non_derivative_transactions: tuple[NonDerivativeTransaction, ...]
    derivative_transactions: tuple[DerivativeTransaction, ...]
    footnotes: tuple[Footnote, ...]
    warnings: tuple[str, ...] = ()
    reporting_owners: tuple[ReportingOwner, ...] = ()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def category_for_code(code: str | None) -> str:
    """Map a raw SEC transaction code to a normalized category string."""
    if code is None:
        return "other"
    return _CATEGORY_MAP.get(code, "other")


def parse_ownership_document(
    document: OwnershipDocument,
    *,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
) -> OwnershipFiling:
    """Convert an ``OwnershipDocument`` into a normalized ``OwnershipFiling``.

    Raises
    ------
    SecOwnershipParseError
        When the document or required identity/date/code fields are invalid.
    SecXmlSecurityError
        When the XML violates the security policy's resource or field limits.

    Notes
    -----
    Extracted documents reuse their private validated tree under the same policy.
    Manually constructed documents and changed policies are fully validated.
    """
    try:
        root, xml_bytes = _validated_ownership_xml(document, policy)
    except SecOwnershipDocumentError as exc:
        raise SecOwnershipParseError("Invalid ownership XML document") from exc
    sha256 = hashlib.sha256(xml_bytes).hexdigest()

    ctx = _ParseContext(policy=policy, accession=document.accession_number)

    # document_type: prefer XML element, fall back to document.document_type
    xml_doc_type = _text(root, "documentType", ctx)
    document_type = xml_doc_type or document.document_type
    if document_type not in OWNERSHIP_FORM_TYPES:
        raise SecOwnershipParseError("Missing or unsupported documentType")

    acc = document.accession_number
    base = acc if acc else f"sha256:{sha256}"

    period_of_report = _parse_date_elem(root, "periodOfReport", ctx, "periodOfReport")
    remarks = _remarks(root, ctx)

    issuer = _parse_issuer(root, ctx)
    reporting_owners = tuple(
        _parse_reporting_owner(el, ctx) for el in root.findall("reportingOwner")
    )
    reporting_owner = (
        reporting_owners[0]
        if reporting_owners
        else ReportingOwner(None, None, False, False, False, False, None)
    )
    if not issuer.cik or not reporting_owner.cik:
        raise SecOwnershipParseError(
            "Ownership XML requires issuer and reporting-owner CIKs"
        )
    footnotes = _parse_footnotes(root, ctx)

    non_derivative_transactions = _parse_non_derivative_table(root, base, ctx)
    derivative_transactions = _parse_derivative_table(root, base, ctx)

    return OwnershipFiling(
        accession_number=acc,
        document_type=document_type,
        is_amendment=document_type.endswith("/A"),
        period_of_report=period_of_report,
        remarks=remarks,
        document_sha256=sha256,
        issuer=issuer,
        reporting_owner=reporting_owner,
        non_derivative_transactions=non_derivative_transactions,
        derivative_transactions=derivative_transactions,
        footnotes=footnotes,
        warnings=tuple(ctx.warnings),
        reporting_owners=reporting_owners,
    )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_issuer(root: _Element, ctx: _ParseContext) -> Issuer:
    el = root.find("issuer")
    if el is None:
        return Issuer(cik=None, name=None, trading_symbol=None)
    symbol = _normalize_symbol(_text(el, "issuerTradingSymbol", ctx))
    return Issuer(
        cik=_normalized_cik(_text(el, "issuerCik", ctx), ctx, "issuerCik"),
        name=_text(el, "issuerName", ctx),
        trading_symbol=symbol,
    )


def _parse_reporting_owner(el: _Element, ctx: _ParseContext) -> ReportingOwner:
    id_el = el.find("reportingOwnerId")
    rel_el = el.find("reportingOwnerRelationship")

    cik = _normalized_cik(_text(id_el, "rptOwnerCik", ctx), ctx, "rptOwnerCik")
    name = _text(id_el, "rptOwnerName", ctx) if id_el is not None else None

    is_director = _bool_flag(rel_el, "isDirector")
    is_officer = _bool_flag(rel_el, "isOfficer")
    is_ten_pct = _bool_flag(rel_el, "isTenPercentOwner")
    is_other = _bool_flag(rel_el, "isOther")
    officer_title = _text(rel_el, "officerTitle", ctx) if rel_el is not None else None
    other_text = _text(rel_el, "otherText", ctx)

    return ReportingOwner(
        cik=cik,
        name=name,
        is_director=is_director,
        is_officer=is_officer,
        is_ten_percent_owner=is_ten_pct,
        is_other=is_other,
        officer_title=officer_title,
        other_text=other_text,
    )


def _normalized_cik(value: str | None, ctx: _ParseContext, field: str) -> str | None:
    if value is None:
        return None
    try:
        return normalize_cik(value)
    except ValueError:
        _optional_warning(ctx, field, "invalid_cik")
        return None


def _normalize_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE_RE.sub(" ", value).strip().upper()
    placeholders = {"NONE", "N/A", "NA", "-", "NOT APPLICABLE"}
    if normalized in placeholders:
        return None
    symbol = re.split(r"[,\s]+", normalized, maxsplit=1)[0]
    return None if symbol in placeholders else symbol


def _parse_footnotes(root: _Element, ctx: _ParseContext) -> tuple[Footnote, ...]:
    result: list[Footnote] = []
    fn_el = root.find("footnotes")
    if fn_el is None:
        return ()
    for fn in fn_el.findall("footnote"):
        fid = fn.get("id")
        if fid is None:
            continue
        text = _long_text(fn, ctx)
        result.append(Footnote(footnote_id=fid, text=text))
    return tuple(result)


def _parse_transaction_common(el: _Element, ctx: _ParseContext) -> _TransactionCommon:
    """Extract fields shared by both non-derivative and derivative rows.

    Returns None for each field when the element or its <value> child is absent.
    Invalid optional values become None with a payload-free warning.
    """
    amounts = el.find("transactionAmounts")
    if amounts is not None:
        shares = _value_decimal(
            el.find("transactionAmounts/transactionShares"),
            ctx,
            "transactionShares",
        )
        price_per_share = _value_decimal(
            el.find("transactionAmounts/transactionPricePerShare"),
            ctx,
            "transactionPricePerShare",
        )
        acquired_disposed = _value_text(
            el.find("transactionAmounts/transactionAcquiredDisposedCode"), ctx
        )
    else:
        shares = None
        price_per_share = None
        acquired_disposed = None

    post = el.find("postTransactionAmounts")
    shares_owned_following = (
        _value_decimal(
            el.find("postTransactionAmounts/sharesOwnedFollowingTransaction"),
            ctx,
            "sharesOwnedFollowingTransaction",
        )
        if post is not None
        else None
    )

    nature = el.find("ownershipNature")
    direct_or_indirect = (
        _value_text(el.find("ownershipNature/directOrIndirectOwnership"), ctx)
        if nature is not None
        else None
    )

    return _TransactionCommon(
        shares=shares,
        price_per_share=price_per_share,
        acquired_disposed=acquired_disposed,
        shares_owned_following=shares_owned_following,
        direct_or_indirect=direct_or_indirect,
        transaction_total_value=_value_decimal(
            el.find("transactionAmounts/transactionTotalValue"),
            ctx,
            "transactionTotalValue",
        ),
        value_owned_following=_value_decimal(
            el.find("postTransactionAmounts/valueOwnedFollowingTransaction"),
            ctx,
            "valueOwnedFollowingTransaction",
        ),
        price_footnote_ids=_collect_footnote_ids(
            el.find("transactionAmounts/transactionPricePerShare")
        ),
    )


def _parse_non_derivative_table(
    root: _Element, base: str, ctx: _ParseContext
) -> tuple[NonDerivativeTransaction, ...]:
    table = root.find("nonDerivativeTable")
    if table is None:
        return ()
    result: list[NonDerivativeTransaction] = []
    for idx, txn_el in enumerate(table.findall("nonDerivativeTransaction")):
        row_id = f"{base}:nonDerivative:{idx}"
        result.append(_parse_non_derivative_txn(txn_el, row_id, ctx))
    return tuple(result)


def _parse_derivative_table(
    root: _Element, base: str, ctx: _ParseContext
) -> tuple[DerivativeTransaction, ...]:
    table = root.find("derivativeTable")
    if table is None:
        return ()
    result: list[DerivativeTransaction] = []
    for idx, txn_el in enumerate(table.findall("derivativeTransaction")):
        row_id = f"{base}:derivative:{idx}"
        result.append(_parse_derivative_txn(txn_el, row_id, ctx))
    return tuple(result)


def _parse_non_derivative_txn(
    el: _Element, row_id: str, ctx: _ParseContext
) -> NonDerivativeTransaction:
    ctx = replace(ctx, warnings=[])
    security_title = _value_text(el, ctx, tag="securityTitle")
    transaction_date = _value_date(el, "transactionDate", ctx, "transactionDate")

    coding = el.find("transactionCoding")
    transaction_code = (
        _text(coding, "transactionCode", ctx) if coding is not None else None
    )

    if (
        transaction_date is None
        or transaction_code is None
        or not re.fullmatch(r"[A-Z]", transaction_code)
    ):
        raise SecOwnershipParseError(
            "Transaction requires a valid date and transaction code"
        )
    common = _parse_transaction_common(el, ctx)
    footnote_ids = _collect_footnote_ids(el)

    return NonDerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category_for_code(transaction_code),
        acquired_disposed=common.acquired_disposed,
        shares=common.shares,
        price_per_share=common.price_per_share,
        shares_owned_following=common.shares_owned_following,
        direct_or_indirect=common.direct_or_indirect,
        footnote_ids=footnote_ids,
        warnings=tuple(ctx.warnings),
        transaction_total_value=common.transaction_total_value,
        value_owned_following=common.value_owned_following,
        price_footnote_ids=common.price_footnote_ids,
    )


def _parse_derivative_txn(
    el: _Element, row_id: str, ctx: _ParseContext
) -> DerivativeTransaction:
    ctx = replace(ctx, warnings=[])
    security_title = _value_text(el, ctx, tag="securityTitle")
    transaction_date = _value_date(el, "transactionDate", ctx, "transactionDate")

    coding = el.find("transactionCoding")
    transaction_code = (
        _text(coding, "transactionCode", ctx) if coding is not None else None
    )

    if (
        transaction_date is None
        or transaction_code is None
        or not re.fullmatch(r"[A-Z]", transaction_code)
    ):
        raise SecOwnershipParseError(
            "Transaction requires a valid date and transaction code"
        )
    common = _parse_transaction_common(el, ctx)

    conv_el = el.find("conversionOrExercisePrice")
    conv_price = (
        _value_decimal(conv_el, ctx, "conversionOrExercisePrice")
        if conv_el is not None
        else None
    )

    exercise_date = _value_date(el, "exerciseDate", ctx, "exerciseDate")
    expiration_date = _value_date(el, "expirationDate", ctx, "expirationDate")

    underlying = el.find("underlyingSecurity")
    underlying_title = (
        _value_text(underlying.find("underlyingSecurityTitle"), ctx)
        if underlying is not None
        else None
    )
    underlying_shares = (
        _value_decimal(
            underlying.find("underlyingSecurityShares"),
            ctx,
            "underlyingSecurityShares",
        )
        if underlying is not None
        else None
    )
    underlying_value = _value_decimal(
        el.find("underlyingSecurity/underlyingSecurityValue"),
        ctx,
        "underlyingSecurityValue",
    )

    footnote_ids = _collect_footnote_ids(el)

    return DerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category_for_code(transaction_code),
        acquired_disposed=common.acquired_disposed,
        shares=common.shares,
        price_per_share=common.price_per_share,
        conversion_or_exercise_price=conv_price,
        exercise_date=exercise_date,
        expiration_date=expiration_date,
        underlying_security_title=underlying_title,
        underlying_security_shares=underlying_shares,
        shares_owned_following=common.shares_owned_following,
        direct_or_indirect=common.direct_or_indirect,
        footnote_ids=footnote_ids,
        warnings=tuple(ctx.warnings),
        transaction_total_value=common.transaction_total_value,
        value_owned_following=common.value_owned_following,
        price_footnote_ids=common.price_footnote_ids,
        underlying_security_value=underlying_value,
    )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _check_scalar(value: str, ctx: _ParseContext) -> None:
    """Reject a scalar field value longer than the policy's scalar limit."""
    if len(value) > ctx.policy.xml_max_scalar_chars:
        raise SecXmlSecurityError(accession=ctx.accession)


def _check_numeric(raw: str, ctx: _ParseContext) -> None:
    """Reject a numeric lexeme longer than the policy's numeric limit."""
    if len(raw) > ctx.policy.xml_max_numeric_chars:
        raise SecXmlSecurityError(accession=ctx.accession)


def _check_long_text(value: str, ctx: _ParseContext) -> None:
    """Reject a long-text field (remarks/footnote) over the policy limit."""
    if len(value) > ctx.policy.xml_max_long_text_chars:
        raise SecXmlSecurityError(accession=ctx.accession)


def _long_text(el: _Element, ctx: _ParseContext) -> str:
    text = _WHITESPACE_RE.sub(" ", "".join(str(text) for text in el.itertext())).strip()
    _check_long_text(text, ctx)
    return text


def _remarks(root: _Element, ctx: _ParseContext) -> str | None:
    el = root.find("remarks")
    return (_long_text(el, ctx) or None) if el is not None else None


def _text(parent: _Element | None, tag: str, ctx: _ParseContext) -> str | None:
    """Return the stripped, scalar-bounded text of the first child, or None."""
    if parent is None:
        return None
    el = parent.find(tag)
    if el is None or el.text is None:
        return None
    stripped = el.text.strip()
    if not stripped:
        return None
    _check_scalar(stripped, ctx)
    return stripped


def _value_text(
    el: _Element | None, ctx: _ParseContext, *, tag: str | None = None
) -> str | None:
    """Return the scalar-bounded text of a <value> child within *el* (or el[tag])."""
    if tag is not None:
        if el is None:
            return None
        el = el.find(tag)
    if el is None:
        return None
    val = el.find("value")
    if val is None or val.text is None:
        return None
    stripped = val.text.strip()
    if not stripped:
        return None
    _check_scalar(stripped, ctx)
    return stripped


def _value_decimal(
    el: _Element | None, ctx: _ParseContext, field: str
) -> Decimal | None:
    """Parse a Decimal from the <value> child of *el*.

    Returns None when the element or its <value> child is absent.
    Raises SecXmlSecurityError when the lexeme exceeds the numeric limit, and
    returns None with a warning for an invalid optional value.
    """
    if el is None:
        return None
    val = el.find("value")
    if val is None or val.text is None:
        return None
    raw = val.text.strip()
    if not raw:
        return None
    _check_numeric(raw, ctx)
    normalized = raw.removeprefix("$").strip()
    if _GROUPED_NUMBER_RE.fullmatch(normalized):
        normalized = normalized.replace(",", "")
    try:
        value = Decimal(normalized)
        if not value.is_finite() or not math.isfinite(float(value)):
            raise ValueError("non-finite numeric value")
        return value
    except (InvalidOperation, ValueError, OverflowError):
        _optional_warning(ctx, field, "invalid_decimal")
        return None


def _optional_warning(ctx: _ParseContext, field: str, reason: str) -> None:
    # Only schema field names and stable reason codes are retained or logged.
    ctx.warnings.append(f"{reason}:{field}")
    _log.warning("SEC optional field ignored field=%s reason=%s", field, reason)


def _date_value(
    raw: str, ctx: _ParseContext, field: str, *, required: bool = False
) -> date | None:
    _check_numeric(raw, ctx)
    try:
        if _XS_DATE_RE.fullmatch(raw) is None:
            raise ValueError("invalid xs:date")
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        if required:
            raise SecOwnershipParseError("Invalid transactionDate") from exc
        _optional_warning(ctx, field, "invalid_date")
        return None


def _value_date(
    parent: _Element, tag: str, ctx: _ParseContext, field: str
) -> date | None:
    el = parent.find(f"{tag}/value")
    raw = (el.text or "").strip() if el is not None else ""
    return (
        _date_value(raw, ctx, field, required=tag == "transactionDate") if raw else None
    )


def _parse_date_elem(
    root: _Element, tag: str, ctx: _ParseContext, field: str
) -> date | None:
    el = root.find(tag)
    raw = (el.text or "").strip() if el is not None else ""
    return _date_value(raw, ctx, field) if raw else None


def _bool_flag(parent: _Element | None, tag: str) -> bool:
    """Return True when parent[tag] text is '1' or 'true' (case-insensitive)."""
    if parent is None:
        return False
    el = parent.find(tag)
    if el is None or el.text is None:
        return False
    return el.text.strip().lower() in ("1", "true")


def _collect_footnote_ids(el: _Element | None) -> tuple[str, ...]:
    """Collect all <footnoteId id="..."/> values in document order, deduplicated."""
    seen: dict[str, None] = {}
    if el is None:
        return ()
    for fn_el in el.iter("footnoteId"):
        fid = fn_el.get("id")
        if fid and fid not in seen:
            seen[fid] = None
    return tuple(seen)

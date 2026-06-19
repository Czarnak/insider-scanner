"""Parse Form 3/4/5 ownership XML into normalized, immutable in-memory records.

Accepts an ``OwnershipDocument`` (produced by ``sec_ownership_document``) and
returns an ``OwnershipFiling`` with typed dataclasses.  No persistence, no
network access.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from lxml import etree

from insider_scanner.core.sec_ownership_document import OwnershipDocument

if TYPE_CHECKING:
    from lxml.etree import _Element  # noqa: PLC2701

# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

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
    "W": "gift",
    "Z": "gift",
}

# ---------------------------------------------------------------------------
# Hardened XML parser (same config as sec_ownership_document)
# ---------------------------------------------------------------------------

_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def category_for_code(code: str | None) -> str:
    """Map a raw SEC transaction code to a normalized category string."""
    if code is None:
        return "other"
    return _CATEGORY_MAP.get(code, "other")


def parse_ownership_document(document: OwnershipDocument) -> OwnershipFiling:
    """Convert an ``OwnershipDocument`` into a normalized ``OwnershipFiling``.

    Raises
    ------
    SecOwnershipParseError
        When required data is missing or a present value cannot be parsed.
    """
    xml_bytes = document.xml_text.encode("utf-8")
    sha256 = hashlib.sha256(xml_bytes).hexdigest()

    try:
        root = etree.fromstring(xml_bytes, _XML_PARSER)
    except etree.XMLSyntaxError as exc:
        acc = document.accession_number
        raise SecOwnershipParseError(
            f"XML parse failure (accession={acc})"
        ) from exc

    # document_type: prefer XML element, fall back to document.document_type
    xml_doc_type = _text(root, "documentType")
    document_type = xml_doc_type or document.document_type
    if not document_type:
        raise SecOwnershipParseError(
            f"documentType missing (accession={document.accession_number})"
        )

    acc = document.accession_number
    base = acc if acc else f"sha256:{sha256}"

    period_of_report = _parse_date_elem(
        root, "periodOfReport", document.accession_number, "periodOfReport"
    )
    remarks = _text(root, "remarks")

    issuer = _parse_issuer(root)
    reporting_owner = _parse_reporting_owner(root)
    footnotes = _parse_footnotes(root, document.accession_number)

    non_derivative_transactions = _parse_non_derivative_table(
        root, base, document.accession_number
    )
    derivative_transactions = _parse_derivative_table(
        root, base, document.accession_number
    )

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
    )


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_issuer(root: _Element) -> Issuer:
    el = root.find("issuer")
    if el is None:
        return Issuer(cik=None, name=None, trading_symbol=None)
    symbol = _text(el, "issuerTradingSymbol") or None
    return Issuer(
        cik=_text(el, "issuerCik"),
        name=_text(el, "issuerName"),
        trading_symbol=symbol,
    )


def _parse_reporting_owner(root: _Element) -> ReportingOwner:
    el = root.find("reportingOwner")
    if el is None:
        return ReportingOwner(
            cik=None, name=None,
            is_director=False, is_officer=False,
            is_ten_percent_owner=False, is_other=False,
            officer_title=None,
        )
    id_el = el.find("reportingOwnerId")
    rel_el = el.find("reportingOwnerRelationship")

    cik = _text(id_el, "rptOwnerCik") if id_el is not None else None
    name = _text(id_el, "rptOwnerName") if id_el is not None else None

    is_director = _bool_flag(rel_el, "isDirector")
    is_officer = _bool_flag(rel_el, "isOfficer")
    is_ten_pct = _bool_flag(rel_el, "isTenPercentOwner")
    is_other = _bool_flag(rel_el, "isOther")
    officer_title = _text(rel_el, "officerTitle") if rel_el is not None else None

    return ReportingOwner(
        cik=cik,
        name=name,
        is_director=is_director,
        is_officer=is_officer,
        is_ten_percent_owner=is_ten_pct,
        is_other=is_other,
        officer_title=officer_title,
    )


def _parse_footnotes(root: _Element, accession: str | None) -> tuple[Footnote, ...]:
    result: list[Footnote] = []
    fn_el = root.find("footnotes")
    if fn_el is None:
        return ()
    for fn in fn_el.findall("footnote"):
        fid = fn.get("id")
        if fid is None:
            continue
        text = "".join(str(t) for t in fn.itertext()).strip()
        result.append(Footnote(footnote_id=fid, text=text))
    return tuple(result)


def _parse_non_derivative_table(
    root: _Element, base: str, accession: str | None
) -> tuple[NonDerivativeTransaction, ...]:
    table = root.find("nonDerivativeTable")
    if table is None:
        return ()
    result: list[NonDerivativeTransaction] = []
    for idx, txn_el in enumerate(table.findall("nonDerivativeTransaction")):
        row_id = f"{base}:nonDerivative:{idx}"
        result.append(_parse_non_derivative_txn(txn_el, row_id, accession))
    return tuple(result)


def _parse_derivative_table(
    root: _Element, base: str, accession: str | None
) -> tuple[DerivativeTransaction, ...]:
    table = root.find("derivativeTable")
    if table is None:
        return ()
    result: list[DerivativeTransaction] = []
    for idx, txn_el in enumerate(table.findall("derivativeTransaction")):
        row_id = f"{base}:derivative:{idx}"
        result.append(_parse_derivative_txn(txn_el, row_id, accession))
    return tuple(result)


def _parse_non_derivative_txn(
    el: _Element, row_id: str, accession: str | None
) -> NonDerivativeTransaction:
    security_title = _value_text(el, "securityTitle")
    transaction_date = _value_date(el, "transactionDate", accession, "transactionDate")

    coding = el.find("transactionCoding")
    transaction_code = _text(coding, "transactionCode") if coding is not None else None

    amounts = el.find("transactionAmounts")
    shares = _value_decimal(el.find("transactionAmounts/transactionShares"), accession, "transactionShares") if amounts is not None else None
    price = _value_decimal(el.find("transactionAmounts/transactionPricePerShare"), accession, "transactionPricePerShare") if amounts is not None else None
    acquired_disposed = _value_text(el.find("transactionAmounts/transactionAcquiredDisposedCode")) if amounts is not None else None

    post = el.find("postTransactionAmounts")
    shares_following = _value_decimal(el.find("postTransactionAmounts/sharesOwnedFollowingTransaction"), accession, "sharesOwnedFollowingTransaction") if post is not None else None

    nature = el.find("ownershipNature")
    direct_or_indirect = _value_text(el.find("ownershipNature/directOrIndirectOwnership")) if nature is not None else None

    footnote_ids = _collect_footnote_ids(el)

    return NonDerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category_for_code(transaction_code),
        acquired_disposed=acquired_disposed,
        shares=shares,
        price_per_share=price,
        shares_owned_following=shares_following,
        direct_or_indirect=direct_or_indirect,
        footnote_ids=footnote_ids,
    )


def _parse_derivative_txn(
    el: _Element, row_id: str, accession: str | None
) -> DerivativeTransaction:
    security_title = _value_text(el, "securityTitle")
    transaction_date = _value_date(el, "transactionDate", accession, "transactionDate")

    coding = el.find("transactionCoding")
    transaction_code = _text(coding, "transactionCode") if coding is not None else None

    amounts = el.find("transactionAmounts")
    shares = _value_decimal(el.find("transactionAmounts/transactionShares"), accession, "transactionShares") if amounts is not None else None
    price = _value_decimal(el.find("transactionAmounts/transactionPricePerShare"), accession, "transactionPricePerShare") if amounts is not None else None
    acquired_disposed = _value_text(el.find("transactionAmounts/transactionAcquiredDisposedCode")) if amounts is not None else None

    conv_el = el.find("conversionOrExercisePrice")
    conv_price = _value_decimal(conv_el, accession, "conversionOrExercisePrice") if conv_el is not None else None

    exercise_date = _value_date(el, "exerciseDate", accession, "exerciseDate")
    expiration_date = _value_date(el, "expirationDate", accession, "expirationDate")

    underlying = el.find("underlyingSecurity")
    underlying_title = _value_text(underlying.find("underlyingSecurityTitle")) if underlying is not None else None
    underlying_shares = _value_decimal(underlying.find("underlyingSecurityShares") if underlying is not None else None, accession, "underlyingSecurityShares")

    post = el.find("postTransactionAmounts")
    shares_following = _value_decimal(el.find("postTransactionAmounts/sharesOwnedFollowingTransaction"), accession, "sharesOwnedFollowingTransaction") if post is not None else None

    nature = el.find("ownershipNature")
    direct_or_indirect = _value_text(el.find("ownershipNature/directOrIndirectOwnership")) if nature is not None else None

    footnote_ids = _collect_footnote_ids(el)

    return DerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category_for_code(transaction_code),
        acquired_disposed=acquired_disposed,
        shares=shares,
        price_per_share=price,
        conversion_or_exercise_price=conv_price,
        exercise_date=exercise_date,
        expiration_date=expiration_date,
        underlying_security_title=underlying_title,
        underlying_security_shares=underlying_shares,
        shares_owned_following=shares_following,
        direct_or_indirect=direct_or_indirect,
        footnote_ids=footnote_ids,
    )


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _text(parent: _Element | None, tag: str) -> str | None:
    """Return the stripped text of the first matching child, or None."""
    if parent is None:
        return None
    el = parent.find(tag)
    if el is None or el.text is None:
        return None
    stripped = el.text.strip()
    return stripped if stripped else None


def _value_text(el: _Element | None, tag: str | None = None) -> str | None:
    """Return the text of a <value> child within *el* (or within el[tag])."""
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
    return stripped if stripped else None


def _value_decimal(
    el: _Element | None, accession: str | None, field: str
) -> Decimal | None:
    """Parse a Decimal from the <value> child of *el*.

    Returns None when the element or its <value> child is absent.
    Raises SecOwnershipParseError when the value is present but unparseable.
    """
    if el is None:
        return None
    val = el.find("value")
    if val is None or val.text is None:
        return None
    raw = val.text.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise SecOwnershipParseError(
            f"Invalid decimal for field '{field}' (accession={accession})"
        ) from exc


def _value_date(
    parent: _Element, tag: str, accession: str | None, field: str
) -> date | None:
    """Parse a date from the <value> child of parent[tag].

    Returns None when the element or <value> is absent.
    Raises SecOwnershipParseError when the value is present but unparseable.
    """
    el = parent.find(tag)
    if el is None:
        return None
    val = el.find("value")
    if val is None or val.text is None:
        return None
    raw = val.text.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SecOwnershipParseError(
            f"Invalid date for field '{field}' (accession={accession})"
        ) from exc


def _parse_date_elem(
    root: _Element, tag: str, accession: str | None, field: str
) -> date | None:
    """Parse a bare text date element (not wrapped in <value>) from root."""
    el = root.find(tag)
    if el is None or el.text is None:
        return None
    raw = el.text.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SecOwnershipParseError(
            f"Invalid date for field '{field}' (accession={accession})"
        ) from exc


def _bool_flag(parent: _Element | None, tag: str) -> bool:
    """Return True when parent[tag] text is '1' or 'true' (case-insensitive)."""
    if parent is None:
        return False
    el = parent.find(tag)
    if el is None or el.text is None:
        return False
    return el.text.strip().lower() in ("1", "true")


def _collect_footnote_ids(el: _Element) -> tuple[str, ...]:
    """Collect all <footnoteId id="..."/> values in document order, deduplicated."""
    seen: dict[str, None] = {}
    for fn_el in el.iter("footnoteId"):
        fid = fn_el.get("id")
        if fid and fid not in seen:
            seen[fid] = None
    return tuple(seen)

"""Common data models for insider trades."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Literal, get_type_hints

from insider_scanner.core.trade_types import TransactionDirection, classify_direction
from insider_scanner.utils.parsing import parse_date

_FIELD_TYPES: dict[type, dict[str, Any]] = {}


def _field_types(cls: type[InsiderTrade | CongressTrade]) -> dict[str, Any]:
    if cls not in _FIELD_TYPES:
        _FIELD_TYPES[cls] = get_type_hints(cls)
    return _FIELD_TYPES[cls]


def _to_dict(trade: InsiderTrade | CongressTrade) -> dict:
    field_types = _field_types(type(trade))
    result = {}
    for field in fields(trade):
        value = getattr(trade, field.name)
        if field_types[field.name] == date | None:
            value = value.isoformat() if value else ""
        result[field.name] = value
    return result


def _from_dict(cls: type[InsiderTrade | CongressTrade], data: dict) -> dict:
    """Decode declared fields while leaving dataclass defaults authoritative."""
    field_types = _field_types(cls)
    result = {}
    for field in fields(cls):
        if field.name not in data:
            continue
        value = data[field.name]
        if field_types[field.name] == date | None:
            if not value:
                value = None
            elif not isinstance(value, date):
                parsed = parse_date(value)
                if parsed is None:
                    raise ValueError(f"Invalid date for {field.name}")
                value = parsed
        elif field_types[field.name] is float:
            value = float(value)
        result[field.name] = value
    return result


@dataclass
class InsiderTrade:
    """Unified insider trade record from any source."""

    # Identifiers
    ticker: str
    company: str = ""
    insider_name: str = ""
    insider_title: str = ""

    # Trade details
    trade_type: Literal["Buy", "Sell", "Exercise", "Other"] = "Other"
    trade_date: date | None = None
    filing_date: date | None = None
    shares: float = 0.0
    price: float = 0.0
    value: float = 0.0
    shares_owned_after: float = 0.0

    # Source tracking
    source: str = ""  # "secform4", "openinsider", "edgar"
    edgar_url: str = ""  # Link to SEC filing

    # Congress-specific
    is_congress: bool = False
    congress_member: str = ""

    # SEC EDGAR native fields
    accession_number: str = ""
    sec_row_id: str = ""
    form_type: str = ""
    cik_issuer: str = ""
    cik_insider: str = ""
    period_of_report: date | None = None
    is_amendment: bool = False
    is_derivative: bool = False
    document_sha256: str = ""
    transaction_code: str = ""
    acquired_disposed: str = ""
    direct_or_indirect: str = ""
    sec_detail_json: str = ""

    @property
    def direction(self) -> TransactionDirection:
        """Shared acquisition, disposal or neutral display direction."""
        return classify_direction(self.trade_type)

    def to_dict(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> InsiderTrade:
        return cls(**_from_dict(cls, {"ticker": "", **d}))


@dataclass
class CongressTrade:
    """Financial disclosure trade record for a Congress member.

    Unlike InsiderTrade, Congress disclosures report dollar *ranges*
    (e.g. "$1,001 - $15,000") rather than exact transaction values.
    """

    # Official identity
    official_name: str = ""
    chamber: Literal["House", "Senate", ""] = ""
    party: str = ""

    # Filing metadata
    filing_date: date | None = None
    doc_id: str = ""
    source_url: str = ""

    # Trade details
    trade_date: date | None = None
    asset_description: str = ""
    ticker: str = ""  # Parsed from asset_description when available
    trade_type: Literal["Purchase", "Sale", "Exchange", "Other"] = "Other"
    owner: str = ""  # Self, Spouse, Dependent Child, Joint

    # Amount (reported as a range)
    amount_range: str = ""  # Original string, e.g. "$1,001 - $15,000"
    amount_low: float = 0.0  # Lower bound parsed from range
    amount_high: float = 0.0  # Upper bound parsed from range

    # Context
    comment: str = ""
    source: str = ""  # "house", "senate"

    @property
    def direction(self) -> TransactionDirection:
        """Shared acquisition, disposal or neutral display direction."""
        return classify_direction(self.trade_type)

    def to_dict(self) -> dict:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CongressTrade:
        return cls(**_from_dict(cls, d))

    @staticmethod
    def parse_amount_range(text: str) -> tuple[float, float]:
        """Compatibility entry point for congressional disclosure amounts."""
        from insider_scanner.core.congress_common import parse_amount_range

        return parse_amount_range(text)

"""Common data models for insider trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


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

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "insider_name": self.insider_name,
            "insider_title": self.insider_title,
            "trade_type": self.trade_type,
            "trade_date": str(self.trade_date) if self.trade_date else "",
            "filing_date": str(self.filing_date) if self.filing_date else "",
            "shares": self.shares,
            "price": self.price,
            "value": self.value,
            "shares_owned_after": self.shares_owned_after,
            "source": self.source,
            "edgar_url": self.edgar_url,
            "is_congress": self.is_congress,
            "congress_member": self.congress_member,
            "accession_number": self.accession_number,
            "sec_row_id": self.sec_row_id,
            "form_type": self.form_type,
            "cik_issuer": self.cik_issuer,
            "cik_insider": self.cik_insider,
            "period_of_report": str(self.period_of_report) if self.period_of_report else "",
            "is_amendment": self.is_amendment,
            "is_derivative": self.is_derivative,
            "document_sha256": self.document_sha256,
            "transaction_code": self.transaction_code,
            "acquired_disposed": self.acquired_disposed,
            "direct_or_indirect": self.direct_or_indirect,
            "sec_detail_json": self.sec_detail_json,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InsiderTrade":
        td = d.get("trade_date", "")
        fd = d.get("filing_date", "")
        pr = d.get("period_of_report", "")
        return cls(
            ticker=d.get("ticker", ""),
            company=d.get("company", ""),
            insider_name=d.get("insider_name", ""),
            insider_title=d.get("insider_title", ""),
            trade_type=d.get("trade_type", "Other"),
            trade_date=date.fromisoformat(td) if td else None,
            filing_date=date.fromisoformat(fd) if fd else None,
            shares=float(d.get("shares", 0)),
            price=float(d.get("price", 0)),
            value=float(d.get("value", 0)),
            shares_owned_after=float(d.get("shares_owned_after", 0)),
            source=d.get("source", ""),
            edgar_url=d.get("edgar_url", ""),
            is_congress=d.get("is_congress", False),
            congress_member=d.get("congress_member", ""),
            accession_number=d.get("accession_number", ""),
            sec_row_id=d.get("sec_row_id", ""),
            form_type=d.get("form_type", ""),
            cik_issuer=d.get("cik_issuer", ""),
            cik_insider=d.get("cik_insider", ""),
            period_of_report=date.fromisoformat(pr) if pr else None,
            is_amendment=d.get("is_amendment", False),
            is_derivative=d.get("is_derivative", False),
            document_sha256=d.get("document_sha256", ""),
            transaction_code=d.get("transaction_code", ""),
            acquired_disposed=d.get("acquired_disposed", ""),
            direct_or_indirect=d.get("direct_or_indirect", ""),
            sec_detail_json=d.get("sec_detail_json", ""),
        )


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

    def to_dict(self) -> dict:
        return {
            "official_name": self.official_name,
            "chamber": self.chamber,
            "party": self.party,
            "filing_date": str(self.filing_date) if self.filing_date else "",
            "doc_id": self.doc_id,
            "source_url": self.source_url,
            "trade_date": str(self.trade_date) if self.trade_date else "",
            "asset_description": self.asset_description,
            "ticker": self.ticker,
            "trade_type": self.trade_type,
            "owner": self.owner,
            "amount_range": self.amount_range,
            "amount_low": self.amount_low,
            "amount_high": self.amount_high,
            "comment": self.comment,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CongressTrade":
        td = d.get("trade_date", "")
        fd = d.get("filing_date", "")
        return cls(
            official_name=d.get("official_name", ""),
            chamber=d.get("chamber", ""),
            party=d.get("party", ""),
            filing_date=date.fromisoformat(fd) if fd else None,
            doc_id=d.get("doc_id", ""),
            source_url=d.get("source_url", ""),
            trade_date=date.fromisoformat(td) if td else None,
            asset_description=d.get("asset_description", ""),
            ticker=d.get("ticker", ""),
            trade_type=d.get("trade_type", "Other"),
            owner=d.get("owner", ""),
            amount_range=d.get("amount_range", ""),
            amount_low=float(d.get("amount_low", 0)),
            amount_high=float(d.get("amount_high", 0)),
            comment=d.get("comment", ""),
            source=d.get("source", ""),
        )

    @staticmethod
    def parse_amount_range(text: str) -> tuple[float, float]:
        """Parse a dollar range string into (low, high) floats.

        Examples:
            "$1,001 - $15,000"  → (1001.0, 15000.0)
            "$50,001 - $100,000" → (50001.0, 100000.0)
            "$1,000,001 - $5,000,000" → (1000001.0, 5000000.0)
            "Over $50,000,000" → (50000000.0, 50000000.0)
            "" → (0.0, 0.0)
        """
        if not text or not text.strip():
            return 0.0, 0.0

        text = text.strip()

        # Handle "Over $X" pattern
        if text.lower().startswith("over"):
            num_str = text.split("$", 1)[-1].replace(",", "").strip()
            try:
                val = float(num_str)
                return val, val
            except ValueError:
                return 0.0, 0.0

        # Standard range: "$X - $Y"
        parts = text.split("-")
        if len(parts) != 2:
            return 0.0, 0.0

        try:
            low = float(parts[0].replace("$", "").replace(",", "").strip())
            high = float(parts[1].replace("$", "").replace(",", "").strip())
            return low, high
        except ValueError:
            return 0.0, 0.0

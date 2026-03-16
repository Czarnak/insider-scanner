"""European insider trade data model.

Covers disclosures from UK (FCA/RNS), Germany (BaFin),
France (AMF) and the Netherlands (AFM) under the EU/UK
Market Abuse Regulation (MAR) Article 19 framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

# ---------------------------------------------------------------------------
# Position normalisation
# ---------------------------------------------------------------------------
# Maps lowercase substrings found in raw position strings → standard English
# categories.  Evaluated in order; first match wins.
# Categories: Executive | Non-Executive | Board Member | Major Shareholder | Other

_POSITION_RULES: list[tuple[str, str]] = [
    # C-suite / executive management
    ("chief executive", "Executive"),
    ("ceo", "Executive"),
    ("cfo", "Executive"),
    ("coo", "Executive"),
    ("cto", "Executive"),
    ("chief financial", "Executive"),
    ("chief operating", "Executive"),
    ("chief technology", "Executive"),
    ("chief information", "Executive"),
    ("chief risk", "Executive"),
    ("managing director", "Executive"),
    ("executive director", "Executive"),
    ("executive chairman", "Executive"),
    ("executive chair", "Executive"),
    ("directeur général", "Executive"),
    ("directeur general", "Executive"),
    ("dirigeant", "Executive"),
    ("président directeur", "Executive"),
    ("president directeur", "Executive"),
    ("administrateur délégué", "Executive"),
    # German executive
    ("vorstandsvorsitzender", "Executive"),
    ("vorstandsmitglied", "Executive"),
    ("vorstand", "Executive"),
    ("geschäftsführer", "Executive"),
    ("geschäftsführerin", "Executive"),
    ("generaldirektor", "Executive"),
    # Dutch executive
    ("uitvoerend bestuurder", "Executive"),
    ("bestuurder", "Executive"),
    ("algemeen directeur", "Executive"),
    ("ceo", "Executive"),
    # Supervisory / non-executive
    ("non-executive", "Non-Executive"),
    ("non executive", "Non-Executive"),
    ("independent director", "Non-Executive"),
    ("supervisory board", "Non-Executive"),
    ("aufsichtsratsvorsitzender", "Non-Executive"),
    ("aufsichtsratsmitglied", "Non-Executive"),
    ("aufsichtsrat", "Non-Executive"),
    ("commissaris", "Non-Executive"),
    ("raad van commissarissen", "Non-Executive"),
    # French non-executive
    ("administrateur indépendant", "Non-Executive"),
    ("censeur", "Non-Executive"),
    # Generic board / director
    ("board member", "Board Member"),
    ("member of the board", "Board Member"),
    ("raad van bestuur", "Board Member"),
    ("conseil d'administration", "Board Member"),
    ("director", "Board Member"),
    ("administrateur", "Board Member"),
    ("conseil", "Board Member"),
    # Major shareholder (no board role)
    ("major shareholder", "Major Shareholder"),
    ("significant shareholder", "Major Shareholder"),
    ("actionnaire", "Major Shareholder"),
    ("aandeelhouder", "Major Shareholder"),
    ("aktionär", "Major Shareholder"),
    ("person closely associated", "Major Shareholder"),
    ("pca", "Major Shareholder"),
]


def normalize_position(raw: str) -> str:
    """Normalise a raw position/role string to a standard English category.

    Returns one of: ``Executive``, ``Non-Executive``, ``Board Member``,
    ``Major Shareholder``, or ``Other``.
    """
    if not raw:
        return "Other"
    lower = raw.lower().strip()
    for keyword, category in _POSITION_RULES:
        if len(keyword) <= 4 and keyword.isascii() and keyword.isalpha():
            if re.search(rf"\b{re.escape(keyword)}\b", lower):
                return category
        elif keyword in lower:
            return category
    return "Other"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class EuropeanInsiderTrade:
    """Unified insider trade record from a European regulatory disclosure."""

    # --- Identity ---
    isin: str = ""
    issuer_name: str = ""
    country: Literal["UK", "DE", "FR", "NL", ""] = ""
    regulatory_body: Literal["FCA", "BaFin", "AMF", "AFM", ""] = ""

    # --- Person ---
    insider_name: str = ""
    # Normalised English category; see normalize_position()
    position: str = ""

    # --- Trade ---
    trade_date: date | None = None
    filing_date: date | None = None
    trade_type: Literal["Buy", "Sell", "Other"] = "Other"
    instrument_type: str = ""  # Share, Option, Bond, Warrant, …

    volume: float | None = None  # Number of units traded
    price: float | None = None  # Price per unit as reported
    currency: str = ""  # ISO 4217 currency code
    # Computed as volume × price where possible; sourced directly when provided.
    total_value: float | None = None

    # --- Source ---
    source: str = ""  # "rns" | "bafin" | "amf" | "afm"
    source_url: str = ""

    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "isin": self.isin,
            "issuer_name": self.issuer_name,
            "country": self.country,
            "regulatory_body": self.regulatory_body,
            "insider_name": self.insider_name,
            "position": self.position,
            "trade_date": str(self.trade_date) if self.trade_date else "",
            "filing_date": str(self.filing_date) if self.filing_date else "",
            "trade_type": self.trade_type,
            "instrument_type": self.instrument_type,
            "volume": self.volume,
            "price": self.price,
            "currency": self.currency,
            "total_value": self.total_value,
            "source": self.source,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EuropeanInsiderTrade":
        td = d.get("trade_date", "")
        fd = d.get("filing_date", "")
        return cls(
            isin=d.get("isin", ""),
            issuer_name=d.get("issuer_name", ""),
            country=d.get("country", ""),
            regulatory_body=d.get("regulatory_body", ""),
            insider_name=d.get("insider_name", ""),
            position=d.get("position", ""),
            trade_date=date.fromisoformat(td) if td else None,
            filing_date=date.fromisoformat(fd) if fd else None,
            trade_type=d.get("trade_type", "Other"),
            instrument_type=d.get("instrument_type", ""),
            volume=float(d["volume"]) if d.get("volume") is not None else None,
            price=float(d["price"]) if d.get("price") is not None else None,
            currency=d.get("currency", ""),
            total_value=float(d["total_value"])
            if d.get("total_value") is not None
            else None,
            source=d.get("source", ""),
            source_url=d.get("source_url", ""),
        )

    @staticmethod
    def compute_total_value(
        volume: float | None,
        price: float | None,
    ) -> float | None:
        """Return volume × price if both are available, else None."""
        if volume is not None and price is not None:
            return volume * price
        return None

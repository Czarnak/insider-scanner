"""Shared normalization for House and Senate transaction disclosures."""

from __future__ import annotations

import re
from math import isfinite
from typing import Literal

CongressTransactionType = Literal["Purchase", "Sale", "Exchange", "Other"]

_TICKER_RE = re.compile(r"\(([A-Z]{1,5}(?:[.-][A-Z])?)\)")
_TX_TYPE_MAP: dict[str, CongressTransactionType] = {
    "p": "Purchase",
    "purchase": "Purchase",
    "s": "Sale",
    "sale": "Sale",
    "e": "Exchange",
    "exchange": "Exchange",
}
_OWNER_MAP = {
    "": "Self",
    "sp": "Spouse",
    "dc": "Dependent Child",
    "jt": "Joint",
}
_AMOUNT_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_OVER_AMOUNT_RE = re.compile(rf"(?:.*\s)?over\s+\$\s*({_AMOUNT_NUMBER})", re.IGNORECASE)
_AMOUNT_RANGE_RE = re.compile(
    rf"\$?\s*({_AMOUNT_NUMBER})\s*[-–—]\s*\$?\s*({_AMOUNT_NUMBER})"
)


def extract_ticker(asset_description: str) -> str:
    """Extract a parenthesized ticker, including dotted or hyphenated classes."""
    match = _TICKER_RE.search(asset_description)
    return match.group(1) if match else ""


def normalize_tx_type(raw: str) -> CongressTransactionType:
    """Normalize disclosure codes and labels to the Congress vocabulary."""
    normalized = re.sub(r"\s*\((?:partial|full)\)\s*$", "", raw.strip().casefold())
    return _TX_TYPE_MAP.get(normalized, "Other")


def normalize_owner(raw: str) -> str:
    """Normalize House owner codes, retaining its established uppercase fallback."""
    normalized = raw.strip()
    return _OWNER_MAP.get(normalized.casefold(), normalized.upper())


def parse_amount_range(text: str) -> tuple[float, float]:
    """Parse a disclosure range, including owner-qualified ``Over $X`` labels.

    Open-ended categories retain the established finite estimate: both bounds
    equal the reported threshold. The caller keeps the original disclosure text.
    """
    text = text.strip()
    over_match = _OVER_AMOUNT_RE.fullmatch(text)
    if over_match:
        threshold = float(over_match.group(1).replace(",", ""))
        return (threshold, threshold) if isfinite(threshold) else (0.0, 0.0)
    range_match = _AMOUNT_RANGE_RE.fullmatch(text)
    if range_match:
        low, high = range_match.groups()
        bounds = float(low.replace(",", "")), float(high.replace(",", ""))
        if all(isfinite(bound) for bound in bounds) and bounds[0] <= bounds[1]:
            return bounds
    return 0.0, 0.0

"""Shared text-parsing helpers for scraper modules.

Consolidates date / number / trade-type parsing that was previously
duplicated across the ``openinsider``, ``secform4`` and congressional
scrapers.
"""

from __future__ import annotations

import re
from datetime import date, datetime

__all__ = ["classify_trade", "parse_date", "parse_number", "parse_ptr_date"]


def parse_date(text: str) -> date | None:
    """Parse source dates and ISO filing timestamps without changing their day.

    US dates accept two- or four-digit years using ``strptime``'s conventional
    1969/2068 pivot. Blank values, placeholders and malformed input return None.
    """
    text = text.strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    # Validate the complete timestamp rather than accepting an arbitrary suffix
    # after a date prefix. Keep the calendar date reported by the source.
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_number(text: str) -> float:
    """Parse a numeric string.

    Strips ``$``, ``,`` and ``+``; treats parenthesised values as negative.
    Returns ``0.0`` for blank values or the ``-`` placeholder.
    """
    text = text.strip().replace(",", "").replace("$", "").replace("+", "")
    if not text or text == "-":
        return 0.0
    negative = False
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
        negative = True
    try:
        val = float(text)
        return -val if negative else val
    except ValueError:
        return 0.0


def classify_trade(text: str) -> str:
    """Map raw trade-type text to a canonical label.

    One of ``"Buy"``, ``"Sell"``, ``"Exercise"`` or ``"Other"``.
    """
    t = text.strip().lower()
    # OpenInsider's leading transaction code is authoritative: a disposition
    # labelled "D - Sale to issuer" must not become an open-market sale.
    coded = re.fullmatch(r"([a-z])(?:\s*-\s*.*)?", t)
    if coded:
        return {"p": "Buy", "s": "Sell", "m": "Exercise", "x": "Exercise"}.get(
            coded.group(1), "Other"
        )
    if "purchase" in t or "buy" in t or t == "p":
        return "Buy"
    if "sale" in t or "sell" in t or t == "s":
        return "Sell"
    if "exercise" in t or "option" in t or t == "m":
        return "Exercise"
    return "Other"


def parse_ptr_date(text: str) -> date | None:
    """Compatibility entry point for congressional PTR date parsing."""
    return parse_date(text)

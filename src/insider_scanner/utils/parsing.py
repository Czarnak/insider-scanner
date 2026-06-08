"""Shared text-parsing helpers for scraper modules.

Consolidates date / number / trade-type parsing that was previously
duplicated across the ``openinsider``, ``secform4`` and congressional
scrapers.
"""

from __future__ import annotations

from datetime import date, datetime

__all__ = ["classify_trade", "parse_date", "parse_number", "parse_ptr_date"]


def parse_date(text: str) -> date | None:
    """Parse a date from ISO, ``MM/DD/YYYY`` or ``MM-DD-YYYY`` text.

    Returns ``None`` for blank values or the ``-`` placeholder.
    """
    text = text.strip()
    if not text or text == "-":
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        parts = text.replace("/", "-").split("-")
        if len(parts) == 3:
            if len(parts[0]) == 4:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            return date(int(parts[2]), int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        pass
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
    if "purchase" in t or "buy" in t or t == "p":
        return "Buy"
    if "sale" in t or "sell" in t or t == "s":
        return "Sell"
    if "exercise" in t or "option" in t or t == "m":
        return "Exercise"
    return "Other"


def parse_ptr_date(text: str) -> date | None:
    """Parse a date from congressional PTR disclosures (House and Senate).

    Returns ``None`` for blank values or the ``--`` placeholder.
    """
    text = text.strip()
    if not text or text == "--":
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None

"""Shared transaction direction vocabulary for domain and display consumers."""

from enum import StrEnum


class TransactionDirection(StrEnum):
    """Broad direction without discarding the source transaction label."""

    BUY = "Buy"
    SELL = "Sell"
    OTHER = "Other"


BUY_TYPES: frozenset[str] = frozenset({"buy", "purchase", "exercise"})
SELL_TYPES: frozenset[str] = frozenset({"sell", "sale"})


def classify_direction(trade_type: str) -> TransactionDirection:
    """Classify canonical labels, preserving unknown transactions as neutral.

    Exercises retain the acquisition direction used by insider tables and
    charts. Gifts, exchanges and unknown labels do not imply a sale.
    """
    normalized = trade_type.strip().casefold()
    if normalized in BUY_TYPES:
        return TransactionDirection.BUY
    if normalized in SELL_TYPES:
        return TransactionDirection.SELL
    return TransactionDirection.OTHER

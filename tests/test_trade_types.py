"""Shared transaction directions preserve known labels and neutral trades."""

import pytest

from insider_scanner.core.trade_types import TransactionDirection, classify_direction


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Buy", TransactionDirection.BUY),
        (" purchase ", TransactionDirection.BUY),
        ("EXERCISE", TransactionDirection.BUY),
        ("Sell", TransactionDirection.SELL),
        (" sAlE ", TransactionDirection.SELL),
        ("Gift", TransactionDirection.OTHER),
        ("Exchange", TransactionDirection.OTHER),
        ("Other", TransactionDirection.OTHER),
        ("unknown", TransactionDirection.OTHER),
        ("sell gift", TransactionDirection.OTHER),
        ("repurchase gift", TransactionDirection.OTHER),
        ("", TransactionDirection.OTHER),
    ],
)
def test_classify_direction_uses_complete_normalized_labels(label, expected):
    assert classify_direction(label) is expected


def test_transaction_direction_preserves_existing_public_values():
    assert [direction.value for direction in TransactionDirection] == [
        "Buy",
        "Sell",
        "Other",
    ]

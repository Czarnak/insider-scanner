"""Shared congressional disclosure parsing contracts."""

import pytest

from insider_scanner.core.congress_common import (
    extract_ticker,
    normalize_owner,
    normalize_tx_type,
    parse_amount_range,
)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Berkshire Hathaway (BRK.B) [ST]", "BRK.B"),
        ("Brown-Forman Class B (BF-B)", "BF-B"),
        ("Vanguard (ETF) Growth Fund (VUG)", "ETF"),
        ("U.S. Treasury Bonds", ""),
    ],
)
def test_extract_ticker(description, expected):
    assert extract_ticker(description) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("P", "Purchase"),
        ("purchase", "Purchase"),
        (" S (partial) ", "Sale"),
        ("s(full)", "Sale"),
        ("Sale (Partial)", "Sale"),
        ("E", "Exchange"),
        ("exchange", "Exchange"),
        ("X", "Other"),
        ("unknown", "Other"),
    ],
)
def test_normalize_transaction_type(raw, expected):
    assert normalize_tx_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "Self"),
        (" self ", "Self"),
        ("SP", "Spouse"),
        ("spouse", "Spouse"),
        ("dc", "Dependent Child"),
        ("Dependent Child", "Dependent Child"),
        ("JT", "Joint"),
        ("Joint", "Joint"),
        ("Other owner", "Other owner"),
    ],
)
def test_normalize_owner(raw, expected):
    assert normalize_owner(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Spouse/DC Over $1,000,000", (1000000.0, 1000000.0)),
        ("Over $50,000,000", (50000000.0, 50000000.0)),
        ("spouse/DC over $1,000,000", (1000000.0, 1000000.0)),
        ("$1,001 - $15,000", (1001.0, 15000.0)),
        ("$1,001 – $15,000", (1001.0, 15000.0)),
        ("1001-15000", (1001.0, 15000.0)),
        ("$1,001.50 - $15,000.25", (1001.5, 15000.25)),
        ("", (0.0, 0.0)),
        ("--", (0.0, 0.0)),
        ("not disclosed", (0.0, 0.0)),
    ],
)
def test_parse_amount_range(raw, expected):
    assert parse_amount_range(raw) == expected

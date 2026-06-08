"""Tests for the shared scraper parsing helpers."""

from __future__ import annotations

from datetime import date

import pytest

from insider_scanner.utils.parsing import (
    classify_trade,
    parse_date,
    parse_number,
    parse_ptr_date,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024-03-05", date(2024, 3, 5)),  # ISO
        ("03/05/2024", date(2024, 3, 5)),  # MM/DD/YYYY
        ("03-05-2024", date(2024, 3, 5)),  # MM-DD-YYYY
        ("", None),
        ("-", None),
        ("not-a-date", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_parse_date_handles_us_slash_format_regression() -> None:
    # Regression: secform4's old _parse_date returned None for MM/DD/YYYY
    # because its format loop returned on the first iteration. The
    # consolidated helper must actually parse these dates.
    assert parse_date("01/15/2024") == date(2024, 1, 15)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.5", 1234.5),
        ("$2,000", 2000.0),
        ("+7", 7.0),
        ("(5)", -5.0),
        ("$(1,000)", -1000.0),
        ("", 0.0),
        ("-", 0.0),
        ("garbage", 0.0),
    ],
)
def test_parse_number(text: str, expected: float) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("P - Purchase", "Buy"),
        ("P", "Buy"),
        ("Sale", "Sell"),
        ("S", "Sell"),
        ("Option Exercise", "Exercise"),
        ("M", "Exercise"),
        ("Gift", "Other"),
        ("", "Other"),
    ],
)
def test_classify_trade(text: str, expected: str) -> None:
    assert classify_trade(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("01/15/2024", date(2024, 1, 15)),
        ("01/15/24", date(2024, 1, 15)),
        ("2024-01-15", date(2024, 1, 15)),
        ("", None),
        ("--", None),
        ("nonsense", None),
    ],
)
def test_parse_ptr_date(text: str, expected: date | None) -> None:
    assert parse_ptr_date(text) == expected

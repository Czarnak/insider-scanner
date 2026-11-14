"""Required fields stay strict; optional data-quality problems retain rows."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_document import extract_ownership_document
from insider_scanner.core.sec_ownership_parser import (
    SecOwnershipParseError,
    parse_ownership_document,
)

XML = (Path(__file__).parent / "fixtures/sec_form4_with_derivatives.xml").read_text(
    encoding="utf-8"
)


def _parse(text):
    return parse_ownership_document(extract_ownership_document(text))


@pytest.mark.parametrize("suffix", ["", "Z", "-05:00", "+14:00", "-14:00"])
def test_xs_date_timezone_preserves_disclosed_calendar_date(suffix):
    filing = _parse(XML.replace("2026-06-12", "2026-06-12" + suffix))
    assert filing.period_of_report == date(2026, 6, 12)
    assert filing.non_derivative_transactions[0].transaction_date == date(2026, 6, 12)
    assert filing.derivative_transactions[0].transaction_date == date(2026, 6, 12)


@pytest.mark.parametrize(
    "suffix", ["+14:01", "+15:00", "-05:60", "T12:00:00", "garbage"]
)
def test_invalid_transaction_date_suffix_remains_a_hard_failure(suffix):
    with pytest.raises(SecOwnershipParseError):
        _parse(XML.replace("2026-06-12", "2026-06-12" + suffix))


@pytest.mark.parametrize(
    "value", ["NaN", "Infinity", "-Infinity", "sNaN", "1E+99999", "PRIVATE-invalid"]
)
def test_invalid_optional_numeric_is_none_and_warned_without_payload(value, caplog):
    filing = _parse(XML.replace("<value>50000</value>", f"<value>{value}</value>"))
    assert (
        len(filing.non_derivative_transactions)
        == len(filing.derivative_transactions)
        == 1
    )
    nd = filing.non_derivative_transactions[0]
    derivative = filing.derivative_transactions[0]
    assert nd.shares is None
    assert derivative.underlying_security_shares is None
    assert nd.warnings == ("invalid_decimal:transactionShares",)
    assert "invalid_decimal:underlyingSecurityShares" in derivative.warnings
    assert value not in caplog.text
    assert value not in repr(nd.warnings)


def test_grouped_currency_number_is_unambiguous():
    filing = _parse(XML.replace("<value>50000</value>", "<value>$100,000.50</value>"))
    assert filing.non_derivative_transactions[0].shares == Decimal("100000.50")


def test_optional_invalid_dates_and_mixed_remarks_are_retained():
    text = (
        XML.replace("2036-06-12", "private-invalid-date")
        .replace(
            "</ownershipDocument>",
            "<remarks>Hello <b>bold</b>\n tail</remarks></ownershipDocument>",
        )
        .replace(
            "<periodOfReport>2026-06-12</periodOfReport>",
            "<periodOfReport>invalid</periodOfReport>",
        )
    )
    filing = _parse(text)
    assert filing.period_of_report is None
    assert filing.warnings == ("invalid_date:periodOfReport",)
    assert filing.derivative_transactions[0].expiration_date is None
    assert filing.derivative_transactions[0].warnings == (
        "invalid_date:expirationDate",
    )
    assert filing.remarks == "Hello bold tail"


@pytest.mark.parametrize(
    "tag", ["issuerCik", "rptOwnerCik", "transactionCode", "transactionDate"]
)
def test_missing_required_identity_date_or_code_is_rejected(tag):
    import re

    text = re.sub(rf"<{tag}>.*?</{tag}>", "", XML, flags=re.DOTALL)
    with pytest.raises(SecOwnershipParseError):
        _parse(text)


def test_foreign_namespace_identity_cannot_produce_blank_success():
    text = XML.replace("<issuer>", '<issuer xmlns="urn:foreign">')
    with pytest.raises(SecOwnershipParseError):
        _parse(text)


def test_optional_diagnostics_reach_trade_details_without_losing_valid_rows():
    from insider_scanner.core.sec_trade_mapping import ownership_filing_to_trades

    filing = _parse(
        XML.replace("2036-06-12", "private-invalid-date").replace(
            "<periodOfReport>2026-06-12</periodOfReport>",
            "<periodOfReport>private-invalid-period</periodOfReport>",
        )
    )
    row = SecMasterIndexRow(
        "0000320193",
        "Apple Inc.",
        "4",
        date(2026, 6, 15),
        "edgar/data/320193/0000320193-26-000001.txt",
    )
    trades = ownership_filing_to_trades(filing, row)
    assert len(trades) == 2
    assert json.loads(trades[0].sec_detail_json)["warnings"] == [
        "invalid_date:periodOfReport"
    ]
    derivative_detail = json.loads(trades[1].sec_detail_json)
    assert derivative_detail["expiration_date"] is None
    assert derivative_detail["warnings"] == [
        "invalid_date:periodOfReport",
        "invalid_date:expirationDate",
    ]
    assert "private-invalid" not in trades[1].sec_detail_json

"""SEC identity, co-owner and financial-value semantics independent of mapping."""

from decimal import Decimal

import pytest

from insider_scanner.core.sec_ownership_document import OwnershipDocument
from insider_scanner.core.sec_ownership_parser import (
    SecOwnershipParseError,
    category_for_code,
    parse_ownership_document,
)


def _parse(body: str, *, symbol: str = " abcd ", issuer_cik: str = "1"):
    return parse_ownership_document(
        OwnershipDocument(
            "0000000001-26-000001",
            "4",
            f"""<ownershipDocument><documentType>4</documentType>
            <issuer><issuerCik>{issuer_cik}</issuerCik><issuerName>Issuer</issuerName>
            <issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>
            <reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik>
            <rptOwnerName>Primary</rptOwnerName></reportingOwnerId>
            <reportingOwnerRelationship><isOfficer>1</isOfficer>
            <isDirector>1</isDirector><officerTitle>CEO</officerTitle>
            </reportingOwnerRelationship></reportingOwner>{body}
            </ownershipDocument>""",
        )
    )


def test_all_reporting_owners_keep_normalized_identity_and_roles() -> None:
    filing = _parse("""<reportingOwner><reportingOwnerId>
        <rptOwnerCik>3</rptOwnerCik><rptOwnerName>Co-owner</rptOwnerName>
        </reportingOwnerId><reportingOwnerRelationship><isOther>1</isOther>
        <otherText>Investment Manager</otherText><isTenPercentOwner>true</isTenPercentOwner>
        </reportingOwnerRelationship></reportingOwner>""")

    assert filing.issuer.cik == "0000000001"
    assert filing.reporting_owner is filing.reporting_owners[0]
    assert [owner.cik for owner in filing.reporting_owners] == [
        "0000000002",
        "0000000003",
    ]
    assert filing.reporting_owners[1].other_text == "Investment Manager"
    assert filing.reporting_owners[1].is_ten_percent_owner
    assert filing.reporting_owner.is_director and filing.reporting_owner.is_officer


@pytest.mark.parametrize("symbol", ["NONE", "n/a", " NA ", "-", "NOT APPLICABLE", ""])
def test_placeholder_symbols_are_not_shared_tickers(symbol: str) -> None:
    assert _parse("", symbol=symbol).issuer.trading_symbol is None


@pytest.mark.parametrize("symbol", [" abcd ", "ABCD, EFGH", "abcd efgh"])
def test_first_issuer_symbol_is_uppercase(symbol: str) -> None:
    assert _parse("", symbol=symbol).issuer.trading_symbol == "ABCD"


def test_invalid_primary_cik_is_rejected_without_exposing_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(SecOwnershipParseError):
        _parse("", issuer_cik="SECRET-INVALID")
    assert "SECRET-INVALID" not in caplog.text
    assert "invalid_cik" in caplog.text


def test_invalid_co_owner_cik_keeps_owner_and_safe_warning() -> None:
    filing = _parse("""<reportingOwner><reportingOwnerId>
        <rptOwnerCik>invalid</rptOwnerCik><rptOwnerName>Co-owner</rptOwnerName>
        </reportingOwnerId></reportingOwner>""")

    assert len(filing.reporting_owners) == 2
    assert filing.reporting_owners[1].name == "Co-owner"
    assert filing.reporting_owners[1].cik is None
    assert filing.warnings == ("invalid_cik:rptOwnerCik",)


@pytest.mark.parametrize(
    "code,category",
    [
        ("D", "disposition_to_issuer"),
        ("E", "expiration"),
        ("H", "expiration"),
        ("I", "discretionary"),
        ("J", "other"),
        ("K", "equity_swap"),
        ("L", "small_acquisition"),
        ("U", "tender"),
        ("V", "voluntary_report"),
        ("W", "inheritance"),
        ("Z", "trust"),
    ],
)
def test_sec_categories_preserve_transaction_meaning(code: str, category: str) -> None:
    assert category_for_code(code) == category


@pytest.mark.parametrize("derivative", [False, True])
def test_value_choices_and_price_footnotes_are_preserved(derivative: bool) -> None:
    table = "derivative" if derivative else "nonDerivative"
    underlying = (
        """<underlyingSecurity><underlyingSecurityTitle><value>Debt</value>
        </underlyingSecurityTitle><underlyingSecurityValue><value>120000</value>
        </underlyingSecurityValue></underlyingSecurity>"""
        if derivative
        else ""
    )
    filing = _parse(f"""<{table}Table><{table}Transaction>
        <transactionDate><value>2026-11-01</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts><transactionTotalValue><value>125000.25</value>
        <footnoteId id="TOTAL"/></transactionTotalValue><transactionPricePerShare>
        <footnoteId id="PRICE"/></transactionPricePerShare></transactionAmounts>
        <postTransactionAmounts><valueOwnedFollowingTransaction><value>250000.50</value>
        </valueOwnedFollowingTransaction></postTransactionAmounts>{underlying}
        </{table}Transaction></{table}Table>""")
    rows = (
        filing.derivative_transactions
        if derivative
        else filing.non_derivative_transactions
    )
    txn = rows[0]

    assert txn.shares is None
    assert txn.shares_owned_following is None
    assert txn.transaction_total_value == Decimal("125000.25")
    assert txn.value_owned_following == Decimal("250000.50")
    assert txn.price_footnote_ids == ("PRICE",)
    assert txn.footnote_ids == ("TOTAL", "PRICE")
    if derivative:
        assert txn.underlying_security_value == Decimal(120000)


def test_malformed_alternative_value_keeps_derivative_row_and_warning() -> None:
    filing = _parse("""<derivativeTable><derivativeTransaction>
        <transactionDate><value>2026-11-01</value></transactionDate>
        <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
        <transactionAmounts><transactionTotalValue><value>0</value>
        </transactionTotalValue></transactionAmounts>
        <underlyingSecurity><underlyingSecurityValue><value>INVALID-AMOUNT</value>
        </underlyingSecurityValue></underlyingSecurity>
        </derivativeTransaction></derivativeTable>""")

    txn = filing.derivative_transactions[0]
    assert txn.transaction_total_value == Decimal(0)
    assert txn.underlying_security_value is None
    assert txn.warnings == ("invalid_decimal:underlyingSecurityValue",)

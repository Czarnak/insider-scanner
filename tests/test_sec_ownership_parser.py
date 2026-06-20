"""Behavior tests for SEC Form 3/4/5 ownership transaction parser.

TDD: these tests are written FIRST. Run with pytest — all should FAIL until
sec_ownership_parser.py is implemented.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from insider_scanner.core.sec_ownership_document import OwnershipDocument

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_doc(
    filename: str,
    accession_number: str | None = "0000320193-26-000061",
    document_type: str = "4",
) -> OwnershipDocument:
    xml_text = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    return OwnershipDocument(
        accession_number=accession_number,
        document_type=document_type,
        xml_text=xml_text,
    )


# ---------------------------------------------------------------------------
# 1. Frozen + slotted dataclasses
# ---------------------------------------------------------------------------


def test_ownership_filing_is_frozen_and_slotted() -> None:
    from insider_scanner.core.sec_ownership_parser import OwnershipFiling, Issuer, ReportingOwner

    filing = OwnershipFiling(
        accession_number="0000320193-26-000061",
        document_type="4",
        is_amendment=False,
        period_of_report=None,
        remarks=None,
        document_sha256="abc",
        issuer=Issuer(cik=None, name=None, trading_symbol=None),
        reporting_owner=ReportingOwner(
            cik=None, name=None,
            is_director=False, is_officer=False,
            is_ten_percent_owner=False, is_other=False,
            officer_title=None,
        ),
        non_derivative_transactions=(),
        derivative_transactions=(),
        footnotes=(),
    )
    assert not hasattr(filing, "__dict__")
    with pytest.raises(FrozenInstanceError):
        filing.document_type = "5"  # type: ignore[misc]


def test_non_derivative_transaction_is_frozen_and_slotted() -> None:
    from insider_scanner.core.sec_ownership_parser import NonDerivativeTransaction

    txn = NonDerivativeTransaction(
        row_id="r",
        security_title=None,
        transaction_date=None,
        transaction_code=None,
        category="other",
        acquired_disposed=None,
        shares=None,
        price_per_share=None,
        shares_owned_following=None,
        direct_or_indirect=None,
        footnote_ids=(),
    )
    assert not hasattr(txn, "__dict__")
    with pytest.raises(FrozenInstanceError):
        txn.category = "purchase"  # type: ignore[misc]


def test_derivative_transaction_is_frozen_and_slotted() -> None:
    from insider_scanner.core.sec_ownership_parser import DerivativeTransaction

    txn = DerivativeTransaction(
        row_id="r",
        security_title=None,
        transaction_date=None,
        transaction_code=None,
        category="other",
        acquired_disposed=None,
        shares=None,
        price_per_share=None,
        conversion_or_exercise_price=None,
        exercise_date=None,
        expiration_date=None,
        underlying_security_title=None,
        underlying_security_shares=None,
        shares_owned_following=None,
        direct_or_indirect=None,
        footnote_ids=(),
    )
    assert not hasattr(txn, "__dict__")
    with pytest.raises(FrozenInstanceError):
        txn.category = "purchase"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Issuer from primary fixture
# ---------------------------------------------------------------------------


def test_issuer_parsed_from_primary() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml")
    filing = parse_ownership_document(doc)

    assert filing.issuer.cik == "0000320193"
    assert filing.issuer.name == "Apple Inc."
    assert filing.issuer.trading_symbol == "AAPL"


# ---------------------------------------------------------------------------
# 3. ReportingOwner from primary fixture
# ---------------------------------------------------------------------------


def test_reporting_owner_parsed_from_primary() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml")
    filing = parse_ownership_document(doc)

    owner = filing.reporting_owner
    assert owner.cik == "0001214156"
    assert owner.name == "COOK TIMOTHY D"
    assert owner.is_director is True
    assert owner.is_officer is True
    assert owner.is_ten_percent_owner is False
    assert owner.is_other is False
    assert owner.officer_title == "Chief Executive Officer"


# ---------------------------------------------------------------------------
# 4. Non-derivative transaction (primary) — every field
# ---------------------------------------------------------------------------


def test_non_derivative_transaction_primary_all_fields() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml")
    filing = parse_ownership_document(doc)

    assert len(filing.non_derivative_transactions) == 1
    txn = filing.non_derivative_transactions[0]

    assert txn.security_title == "Common Stock"
    assert txn.transaction_date == date(2026, 6, 12)
    assert txn.transaction_code == "S"
    assert txn.category == "sale"
    assert txn.acquired_disposed == "D"
    assert txn.shares == Decimal("100000")
    assert txn.price_per_share == Decimal("195.50")
    assert txn.shares_owned_following == Decimal("3200000")
    assert txn.direct_or_indirect == "D"
    assert txn.footnote_ids == ("F1",)


# ---------------------------------------------------------------------------
# 5. Footnotes from primary fixture
# ---------------------------------------------------------------------------


def test_footnotes_from_primary() -> None:
    from insider_scanner.core.sec_ownership_parser import Footnote, parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml")
    filing = parse_ownership_document(doc)

    assert filing.footnotes == (
        Footnote(
            "F1",
            "Shares sold pursuant to a Rule 10b5-1 trading plan adopted on March 1, 2026.",
        ),
    )


# ---------------------------------------------------------------------------
# 6. Derivatives fixture
# ---------------------------------------------------------------------------


def test_derivatives_fixture_non_derivative_m_code() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_with_derivatives.xml")
    filing = parse_ownership_document(doc)

    nd = filing.non_derivative_transactions[0]
    assert nd.transaction_code == "M"
    assert nd.category == "exercise"


def test_derivatives_fixture_derivative_transaction_all_fields() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_with_derivatives.xml")
    filing = parse_ownership_document(doc)

    assert len(filing.derivative_transactions) == 1
    d = filing.derivative_transactions[0]

    assert d.security_title == "Restricted Stock Unit"
    assert d.category == "award"
    assert d.conversion_or_exercise_price is None
    assert d.exercise_date is None
    assert d.expiration_date == date(2036, 6, 12)
    assert d.underlying_security_title == "Common Stock"
    assert d.underlying_security_shares == Decimal("50000")
    assert d.shares == Decimal("50000")
    assert d.shares_owned_following == Decimal("200000")
    assert d.direct_or_indirect == "D"
    assert d.footnote_ids == ("F1", "F2")


# ---------------------------------------------------------------------------
# 7. category_for_code — parametrized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected",
    [
        ("P", "purchase"),
        ("S", "sale"),
        ("A", "award"),
        ("C", "exercise"),
        ("M", "exercise"),
        ("O", "exercise"),
        ("X", "exercise"),
        ("F", "tax"),
        ("G", "gift"),
        ("W", "gift"),
        ("Z", "gift"),
        ("Q", "other"),
        (None, "other"),
    ],
)
def test_category_for_code(code: str | None, expected: str) -> None:
    from insider_scanner.core.sec_ownership_parser import category_for_code

    assert category_for_code(code) == expected


# ---------------------------------------------------------------------------
# 8. Amendment fixture
# ---------------------------------------------------------------------------


def test_amendment_fixture_metadata() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_amendment.xml", document_type="4/A")
    filing = parse_ownership_document(doc)

    assert filing.is_amendment is True
    assert filing.document_type == "4/A"
    assert filing.period_of_report == date(2026, 5, 20)
    assert filing.remarks is not None
    assert "Amendment to correct" in filing.remarks


# ---------------------------------------------------------------------------
# 9. Missing optional values → None (no exception)
# ---------------------------------------------------------------------------


_MINIMAL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-06-01</periodOfReport>
  <issuer>
    <issuerCik>0000000001</issuerCik>
    <issuerName>Minimal Corp</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0000000002</rptOwnerCik>
      <rptOwnerName>Test Owner</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <isOther>0</isOther>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle>
        <value>Common Stock</value>
      </securityTitle>
      <transactionDate>
        <value>2026-06-01</value>
      </transactionDate>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares>
          <value>500</value>
        </transactionShares>
        <transactionAcquiredDisposedCode>
          <value>A</value>
        </transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_missing_optional_values_are_none() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = OwnershipDocument(
        accession_number="0000000001-26-000001",
        document_type="4",
        xml_text=_MINIMAL_XML,
    )
    filing = parse_ownership_document(doc)

    txn = filing.non_derivative_transactions[0]
    assert txn.price_per_share is None
    assert txn.shares_owned_following is None
    assert txn.direct_or_indirect is None
    assert filing.issuer.trading_symbol is None
    assert filing.reporting_owner.officer_title is None


# ---------------------------------------------------------------------------
# 10. Malformed required data → SecOwnershipParseError
# ---------------------------------------------------------------------------


_MALFORMED_SHARES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000000001</issuerCik><issuerName>X</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000002</rptOwnerCik><rptOwnerName>Y</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-06-01</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>not-a-number</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

_MALFORMED_DATE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000000001</issuerCik><issuerName>X</issuerName></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>0000000002</rptOwnerCik><rptOwnerName>Y</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-13-99</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_malformed_shares_raises_parse_error() -> None:
    from insider_scanner.core.sec_ownership_parser import SecOwnershipParseError, parse_ownership_document

    doc = OwnershipDocument(
        accession_number="0000000001-26-000001",
        document_type="4",
        xml_text=_MALFORMED_SHARES_XML,
    )
    with pytest.raises(SecOwnershipParseError) as exc_info:
        parse_ownership_document(doc)

    # Raw XML body must not appear in the error message
    assert "not-a-number" not in str(exc_info.value)
    assert _MALFORMED_SHARES_XML not in str(exc_info.value)


def test_malformed_date_raises_parse_error() -> None:
    from insider_scanner.core.sec_ownership_parser import (
        SecOwnershipParseError,
        parse_ownership_document,
    )

    doc = OwnershipDocument(
        accession_number="0000000001-26-000001",
        document_type="4",
        xml_text=_MALFORMED_DATE_XML,
    )
    with pytest.raises(SecOwnershipParseError) as exc_info:
        parse_ownership_document(doc)

    assert _MALFORMED_DATE_XML not in str(exc_info.value)


def test_missing_document_type_raises() -> None:
    from insider_scanner.core.sec_ownership_parser import (
        SecOwnershipParseError,
        parse_ownership_document,
    )

    doc = OwnershipDocument(
        accession_number="x",
        document_type="",
        xml_text="<ownershipDocument/>",
    )
    with pytest.raises(SecOwnershipParseError):
        parse_ownership_document(doc)


# ---------------------------------------------------------------------------
# 11. Stable row identity
# ---------------------------------------------------------------------------


def test_row_id_with_accession_number() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml", accession_number="0000320193-26-000061")
    filing = parse_ownership_document(doc)

    assert filing.non_derivative_transactions[0].row_id == "0000320193-26-000061:nonDerivative:0"


def test_row_id_without_accession_number() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml_text = (FIXTURE_DIR / "sec_form4_primary.xml").read_text(encoding="utf-8")
    doc = OwnershipDocument(accession_number=None, document_type="4", xml_text=xml_text)
    filing = parse_ownership_document(doc)

    row_id = filing.non_derivative_transactions[0].row_id
    assert row_id.startswith("sha256:")
    assert row_id.endswith(":nonDerivative:0")


def test_derivative_row_id_suffix() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_with_derivatives.xml")
    filing = parse_ownership_document(doc)

    assert filing.derivative_transactions[0].row_id.endswith(":derivative:0")


# ---------------------------------------------------------------------------
# 12. Does not mutate source
# ---------------------------------------------------------------------------


def test_parse_same_document_twice_returns_equal_filings() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    doc = _load_doc("sec_form4_primary.xml")
    xml_before = doc.xml_text

    filing1 = parse_ownership_document(doc)
    filing2 = parse_ownership_document(doc)

    assert filing1 == filing2
    assert doc.xml_text == xml_before


# ---------------------------------------------------------------------------
# 13. document_sha256
# ---------------------------------------------------------------------------


def test_document_sha256_matches() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml_text = (FIXTURE_DIR / "sec_form4_primary.xml").read_text(encoding="utf-8")
    doc = OwnershipDocument(
        accession_number="0000320193-26-000061",
        document_type="4",
        xml_text=xml_text,
    )
    filing = parse_ownership_document(doc)

    expected = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
    assert filing.document_sha256 == expected


# ---------------------------------------------------------------------------
# Security policy: field-level limits, entity rejection, footnote flattening
# ---------------------------------------------------------------------------


def _doc(xml_text: str, accession: str = "0000000001-26-000001") -> OwnershipDocument:
    return OwnershipDocument(
        accession_number=accession, document_type="4", xml_text=xml_text
    )


def _small_policy(**overrides: object):
    from dataclasses import replace

    from insider_scanner.core.sec_security import DEFAULT_SEC_SECURITY_POLICY

    return replace(DEFAULT_SEC_SECURITY_POLICY, **overrides)  # type: ignore[arg-type]


def _non_derivative_with_security_title(value: str) -> str:
    return (
        "<ownershipDocument><documentType>4</documentType>"
        "<nonDerivativeTable><nonDerivativeTransaction>"
        f"<securityTitle><value>{value}</value></securityTitle>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )


def _non_derivative_with_shares(value: str) -> str:
    return (
        "<ownershipDocument><documentType>4</documentType>"
        "<nonDerivativeTable><nonDerivativeTransaction>"
        f"<transactionAmounts><transactionShares><value>{value}</value>"
        "</transactionShares></transactionAmounts>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )


def test_parser_rejects_dtd_declaration() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = (
        "<!DOCTYPE x>"
        "<ownershipDocument><documentType>4</documentType></ownershipDocument>"
    )
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(_doc(xml))


def test_parser_rejects_oversized_scalar_field() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = _non_derivative_with_security_title("X" * 50)
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(_doc(xml), policy=_small_policy(xml_max_scalar_chars=8))


def test_parser_rejects_oversized_numeric_lexeme() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = _non_derivative_with_shares("1" * 40)
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(
            _doc(xml), policy=_small_policy(xml_max_numeric_chars=8)
        )


def test_parser_rejects_oversized_remarks() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = (
        "<ownershipDocument><documentType>4</documentType>"
        f"<remarks>{'z' * 50}</remarks></ownershipDocument>"
    )
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(
            _doc(xml), policy=_small_policy(xml_max_long_text_chars=8)
        )


def test_parser_rejects_oversized_footnote() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = (
        "<ownershipDocument><documentType>4</documentType><footnotes>"
        f'<footnote id="F1">{"z" * 50}</footnote>'
        "</footnotes></ownershipDocument>"
    )
    with pytest.raises(SecXmlSecurityError):
        parse_ownership_document(
            _doc(xml), policy=_small_policy(xml_max_long_text_chars=8)
        )


def test_parser_flattens_and_normalizes_footnote_markup() -> None:
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    xml = (
        "<ownershipDocument><documentType>4</documentType><footnotes>"
        '<footnote id="F1">Shares   sold\n  pursuant to <i>a</i>\tplan.</footnote>'
        "</footnotes></ownershipDocument>"
    )
    filing = parse_ownership_document(_doc(xml))
    assert filing.footnotes[0].text == "Shares sold pursuant to a plan."


def test_security_rejection_message_has_no_field_value() -> None:
    from insider_scanner.core._sec_xml import SecXmlSecurityError
    from insider_scanner.core.sec_ownership_parser import parse_ownership_document

    secret = "S" * 50
    xml = _non_derivative_with_security_title(secret)
    with pytest.raises(SecXmlSecurityError) as exc_info:
        parse_ownership_document(_doc(xml), policy=_small_policy(xml_max_scalar_chars=8))
    assert secret not in str(exc_info.value)

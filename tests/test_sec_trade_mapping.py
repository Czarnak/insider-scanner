"""Tests for sec_trade_mapping.ownership_filing_to_trades (A4 + A5)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import (
    DerivativeTransaction,
    Footnote,
    Issuer,
    NonDerivativeTransaction,
    OwnershipFiling,
    ReportingOwner,
)
from insider_scanner.core.sec_trade_mapping import (
    SecTradeMappingError,
    _MAX_EMBEDDED_FOOTNOTES,
    _MAX_FOOTNOTE_CHARS,
    ownership_filing_to_trades,
)

# ---------------------------------------------------------------------------
# Fixtures / factory helpers
# ---------------------------------------------------------------------------

_FILING_DATE = date(2024, 3, 15)
_PERIOD = date(2024, 3, 10)

_INDEX_ROW = SecMasterIndexRow(
    cik="0001234567",
    company_name="Acme Corp",
    form_type="4",
    filing_date=_FILING_DATE,
    archive_path="edgar/data/1234567/0001234567-24-000001-index.htm",
)


def _issuer(
    *,
    cik: str | None = "0009876543",
    name: str | None = "Acme Corp",
    symbol: str | None = "ACME",
) -> Issuer:
    return Issuer(cik=cik, name=name, trading_symbol=symbol)


def _owner(
    *,
    cik: str | None = "0001111111",
    name: str | None = "Jane Doe",
    is_director: bool = False,
    is_officer: bool = True,
    is_ten_percent_owner: bool = False,
    is_other: bool = False,
    officer_title: str | None = "CEO",
) -> ReportingOwner:
    return ReportingOwner(
        cik=cik,
        name=name,
        is_director=is_director,
        is_officer=is_officer,
        is_ten_percent_owner=is_ten_percent_owner,
        is_other=is_other,
        officer_title=officer_title,
    )


def _non_deriv(
    *,
    row_id: str = "acc:nonDerivative:0",
    security_title: str | None = "Common Stock",
    transaction_date: date | None = date(2024, 3, 10),
    transaction_code: str | None = "P",
    category: str = "purchase",
    acquired_disposed: str | None = "A",
    shares: Decimal | None = Decimal("1000"),
    price_per_share: Decimal | None = Decimal("25.50"),
    shares_owned_following: Decimal | None = Decimal("5000"),
    direct_or_indirect: str | None = "D",
    footnote_ids: tuple[str, ...] = (),
) -> NonDerivativeTransaction:
    return NonDerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category,
        acquired_disposed=acquired_disposed,
        shares=shares,
        price_per_share=price_per_share,
        shares_owned_following=shares_owned_following,
        direct_or_indirect=direct_or_indirect,
        footnote_ids=footnote_ids,
    )


def _deriv(
    *,
    row_id: str = "acc:derivative:0",
    security_title: str | None = "Stock Option",
    transaction_date: date | None = date(2024, 3, 10),
    transaction_code: str | None = "M",
    category: str = "exercise",
    acquired_disposed: str | None = "A",
    shares: Decimal | None = Decimal("500"),
    price_per_share: Decimal | None = Decimal("10.00"),
    conversion_or_exercise_price: Decimal | None = Decimal("5.00"),
    exercise_date: date | None = date(2024, 3, 10),
    expiration_date: date | None = date(2030, 3, 10),
    underlying_security_title: str | None = "Common Stock",
    underlying_security_shares: Decimal | None = Decimal("500"),
    shares_owned_following: Decimal | None = Decimal("2000"),
    direct_or_indirect: str | None = "D",
    footnote_ids: tuple[str, ...] = (),
) -> DerivativeTransaction:
    return DerivativeTransaction(
        row_id=row_id,
        security_title=security_title,
        transaction_date=transaction_date,
        transaction_code=transaction_code,
        category=category,
        acquired_disposed=acquired_disposed,
        shares=shares,
        price_per_share=price_per_share,
        conversion_or_exercise_price=conversion_or_exercise_price,
        exercise_date=exercise_date,
        expiration_date=expiration_date,
        underlying_security_title=underlying_security_title,
        underlying_security_shares=underlying_security_shares,
        shares_owned_following=shares_owned_following,
        direct_or_indirect=direct_or_indirect,
        footnote_ids=footnote_ids,
    )


def _filing(
    *,
    non_derivs: tuple[NonDerivativeTransaction, ...] = (),
    derivs: tuple[DerivativeTransaction, ...] = (),
    footnotes: tuple[Footnote, ...] = (),
    is_amendment: bool = False,
    period_of_report: date | None = _PERIOD,
    accession_number: str | None = "0001234567-24-000001",
    issuer: Issuer | None = None,
    owner: ReportingOwner | None = None,
) -> OwnershipFiling:
    return OwnershipFiling(
        accession_number=accession_number,
        document_type="4",
        is_amendment=is_amendment,
        period_of_report=period_of_report,
        remarks=None,
        document_sha256="abc123",
        issuer=issuer if issuer is not None else _issuer(),
        reporting_owner=owner if owner is not None else _owner(),
        non_derivative_transactions=non_derivs,
        derivative_transactions=derivs,
        footnotes=footnotes,
    )


# ---------------------------------------------------------------------------
# Test: order — N non-derivatives then M derivatives
# ---------------------------------------------------------------------------

class TestTransactionOrdering:
    def test_non_deriv_first_then_deriv(self) -> None:
        nd0 = _non_deriv(row_id="nd:0")
        nd1 = _non_deriv(row_id="nd:1")
        d0 = _deriv(row_id="d:0")
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd0, nd1), derivs=(d0,)), _INDEX_ROW
        )
        assert len(trades) == 3
        assert trades[0].sec_row_id == "nd:0"
        assert trades[1].sec_row_id == "nd:1"
        assert trades[2].sec_row_id == "d:0"

    def test_is_derivative_flag(self) -> None:
        nd = _non_deriv()
        d = _deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), derivs=(d,)), _INDEX_ROW)
        assert trades[0].is_derivative is False
        assert trades[1].is_derivative is True

    def test_empty_filing_returns_empty_tuple(self) -> None:
        assert ownership_filing_to_trades(_filing(), _INDEX_ROW) == ()

    def test_only_derivatives(self) -> None:
        d = _deriv()
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        assert len(trades) == 1
        assert trades[0].is_derivative is True

    def test_only_non_derivatives(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert len(trades) == 1
        assert trades[0].is_derivative is False


# ---------------------------------------------------------------------------
# Test: category → trade_type mapping
# ---------------------------------------------------------------------------

class TestCategoryToTradeType:
    @pytest.mark.parametrize(
        "category, expected",
        [
            ("purchase", "Buy"),
            ("sale", "Sell"),
            ("exercise", "Exercise"),
            ("award", "Other"),
            ("gift", "Other"),
            ("tax", "Other"),
            ("other", "Other"),
            ("unknown_value", "Other"),
        ],
    )
    def test_non_derivative_category_map(self, category: str, expected: str) -> None:
        nd = _non_deriv(category=category, transaction_code=None)
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].trade_type == expected

    @pytest.mark.parametrize(
        "category, expected",
        [
            ("purchase", "Buy"),
            ("sale", "Sell"),
            ("exercise", "Exercise"),
            ("award", "Other"),
            ("other", "Other"),
        ],
    )
    def test_derivative_category_map(self, category: str, expected: str) -> None:
        d = _deriv(category=category, transaction_code=None)
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        assert trades[0].trade_type == expected


# ---------------------------------------------------------------------------
# Test: numeric fields
# ---------------------------------------------------------------------------

class TestNumericFields:
    def test_value_equals_shares_times_price(self) -> None:
        nd = _non_deriv(shares=Decimal("100"), price_per_share=Decimal("50"))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        t = trades[0]
        assert t.shares == pytest.approx(100.0)
        assert t.price == pytest.approx(50.0)
        assert t.value == pytest.approx(5000.0)

    def test_missing_price_gives_zero_value_and_price(self) -> None:
        nd = _non_deriv(shares=Decimal("100"), price_per_share=None)
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        t = trades[0]
        assert t.price == 0.0
        assert t.value == 0.0
        assert t.shares == pytest.approx(100.0)

    def test_missing_shares_gives_zero_value(self) -> None:
        nd = _non_deriv(shares=None, price_per_share=Decimal("50"))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        t = trades[0]
        assert t.shares == 0.0
        assert t.value == 0.0

    def test_shares_owned_after(self) -> None:
        nd = _non_deriv(shares_owned_following=Decimal("9999"))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].shares_owned_after == pytest.approx(9999.0)

    def test_shares_owned_after_missing_is_zero(self) -> None:
        nd = _non_deriv(shares_owned_following=None)
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].shares_owned_after == 0.0


# ---------------------------------------------------------------------------
# Test: ticker / company fallbacks
# ---------------------------------------------------------------------------

class TestTickerCompanyFallback:
    def test_ticker_uses_trading_symbol(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), issuer=_issuer(symbol="XYZ")), _INDEX_ROW
        )
        assert trades[0].ticker == "XYZ"

    def test_ticker_falls_back_to_empty_when_no_symbol(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), issuer=_issuer(symbol=None)), _INDEX_ROW
        )
        assert trades[0].ticker == ""

    def test_company_uses_issuer_name(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), issuer=_issuer(name="BigCo")), _INDEX_ROW
        )
        assert trades[0].company == "BigCo"

    def test_company_falls_back_to_index_row_name_when_issuer_name_none(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), issuer=_issuer(name=None)), _INDEX_ROW
        )
        assert trades[0].company == _INDEX_ROW.company_name


# ---------------------------------------------------------------------------
# Test: insider name / title
# ---------------------------------------------------------------------------

class TestInsiderTitle:
    def test_officer_title_used_when_present(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), owner=_owner(officer_title="CFO")), _INDEX_ROW
        )
        assert trades[0].insider_title == "CFO"

    def test_derived_title_from_flags_when_no_officer_title(self) -> None:
        nd = _non_deriv()
        owner = _owner(
            officer_title=None,
            is_director=True,
            is_officer=False,
            is_ten_percent_owner=True,
            is_other=False,
        )
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), owner=owner), _INDEX_ROW)
        assert trades[0].insider_title == "Director, 10% Owner"

    def test_empty_title_when_no_flags_and_no_officer_title(self) -> None:
        nd = _non_deriv()
        owner = _owner(
            officer_title=None,
            is_director=False,
            is_officer=False,
            is_ten_percent_owner=False,
            is_other=False,
        )
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), owner=owner), _INDEX_ROW)
        assert trades[0].insider_title == ""

    def test_insider_name_from_reporting_owner(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), owner=_owner(name="John Smith")), _INDEX_ROW
        )
        assert trades[0].insider_name == "John Smith"

    def test_insider_name_empty_when_none(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), owner=_owner(name=None)), _INDEX_ROW
        )
        assert trades[0].insider_name == ""


# ---------------------------------------------------------------------------
# Test: first-class SEC fields
# ---------------------------------------------------------------------------

class TestFirstClassSecFields:
    def test_source_is_sec_edgar(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].source == "sec_edgar"

    def test_edgar_url_built_from_index_row(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert "edgar/data" in trades[0].edgar_url
        assert trades[0].edgar_url.startswith("https://")

    def test_accession_number(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), accession_number="0001234567-24-000001"), _INDEX_ROW
        )
        assert trades[0].accession_number == "0001234567-24-000001"

    def test_accession_number_none_maps_to_empty_string(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), accession_number=None), _INDEX_ROW
        )
        assert trades[0].accession_number == ""

    def test_sec_row_id_from_txn(self) -> None:
        nd = _non_deriv(row_id="myrow:123")
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].sec_row_id == "myrow:123"

    def test_form_type_from_document_type(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].form_type == "4"

    def test_cik_issuer_and_insider(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].cik_issuer == "0009876543"
        assert trades[0].cik_insider == "0001111111"

    def test_period_of_report(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), period_of_report=_PERIOD), _INDEX_ROW)
        assert trades[0].period_of_report == _PERIOD

    def test_is_amendment_propagated(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), is_amendment=True), _INDEX_ROW)
        assert trades[0].is_amendment is True

    def test_document_sha256_propagated(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].document_sha256 == "abc123"

    def test_transaction_code(self) -> None:
        nd = _non_deriv(transaction_code="P")
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].transaction_code == "P"

    def test_transaction_code_none_maps_to_empty(self) -> None:
        nd = _non_deriv(transaction_code=None)
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].transaction_code == ""

    def test_acquired_disposed(self) -> None:
        nd = _non_deriv(acquired_disposed="D")
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].acquired_disposed == "D"

    def test_direct_or_indirect(self) -> None:
        nd = _non_deriv(direct_or_indirect="I")
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].direct_or_indirect == "I"

    def test_filing_date_from_index_row(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        assert trades[0].filing_date == _FILING_DATE


# ---------------------------------------------------------------------------
# Test: sec_detail_json for non-derivative
# ---------------------------------------------------------------------------

class TestSecDetailJsonNonDerivative:
    def test_security_title_in_detail(self) -> None:
        nd = _non_deriv(security_title="Common Stock")
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["security_title"] == "Common Stock"

    def test_footnote_ids_in_detail(self) -> None:
        fn = Footnote(footnote_id="F1", text="See note")
        nd = _non_deriv(footnote_ids=("F1",))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), footnotes=(fn,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["footnote_ids"] == ["F1"]
        assert detail["footnotes"]["F1"] == "See note"

    def test_footnote_missing_in_filing_gives_empty_string(self) -> None:
        nd = _non_deriv(footnote_ids=("F99",))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["footnotes"]["F99"] == ""

    def test_no_derivative_fields_in_non_deriv_detail(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert "conversion_or_exercise_price" not in detail
        assert "exercise_date" not in detail
        assert "expiration_date" not in detail
        assert "underlying_security_title" not in detail
        assert "underlying_security_shares" not in detail

    def test_amends_key_present_when_amendment(self) -> None:
        nd = _non_deriv(security_title="Common Stock")
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), is_amendment=True), _INDEX_ROW
        )
        detail = json.loads(trades[0].sec_detail_json)
        assert "amends" in detail
        assert "0009876543" in detail["amends"]
        assert "0001111111" in detail["amends"]
        assert _PERIOD.isoformat() in detail["amends"]
        assert "Common Stock" in detail["amends"]

    def test_amends_key_absent_when_not_amendment(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), is_amendment=False), _INDEX_ROW
        )
        detail = json.loads(trades[0].sec_detail_json)
        assert "amends" not in detail


# ---------------------------------------------------------------------------
# Test: sec_detail_json for derivative
# ---------------------------------------------------------------------------

class TestSecDetailJsonDerivative:
    def test_derivative_extra_fields_present(self) -> None:
        d = _deriv(
            security_title="Stock Option",
            conversion_or_exercise_price=Decimal("5.00"),
            exercise_date=date(2024, 3, 10),
            expiration_date=date(2030, 3, 10),
            underlying_security_title="Common Stock",
            underlying_security_shares=Decimal("500"),
        )
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["security_title"] == "Stock Option"
        assert detail["conversion_or_exercise_price"] == "5.00"
        assert detail["exercise_date"] == "2024-03-10"
        assert detail["expiration_date"] == "2030-03-10"
        assert detail["underlying_security_title"] == "Common Stock"
        assert detail["underlying_security_shares"] == "500"

    def test_none_derivative_fields_are_null(self) -> None:
        d = _deriv(
            conversion_or_exercise_price=None,
            exercise_date=None,
            expiration_date=None,
            underlying_security_title=None,
            underlying_security_shares=None,
        )
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["conversion_or_exercise_price"] is None
        assert detail["exercise_date"] is None
        assert detail["expiration_date"] is None
        assert detail["underlying_security_title"] is None
        assert detail["underlying_security_shares"] is None

    def test_derivative_footnote_resolution(self) -> None:
        fn = Footnote(footnote_id="F2", text="Derivative note")
        d = _deriv(footnote_ids=("F2",))
        trades = ownership_filing_to_trades(_filing(derivs=(d,), footnotes=(fn,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert detail["footnote_ids"] == ["F2"]
        assert detail["footnotes"]["F2"] == "Derivative note"

    def test_derivative_amends_key(self) -> None:
        d = _deriv(security_title="Stock Option")
        trades = ownership_filing_to_trades(
            _filing(derivs=(d,), is_amendment=True), _INDEX_ROW
        )
        detail = json.loads(trades[0].sec_detail_json)
        assert "amends" in detail
        expected = f"0009876543|0001111111|{_PERIOD.isoformat()}|Stock Option"
        assert detail["amends"] == expected


# ---------------------------------------------------------------------------
# Test: determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_identical_json_on_repeated_call(self) -> None:
        fn = Footnote(footnote_id="F1", text="Note text")
        nd = _non_deriv(footnote_ids=("F1",))
        d = _deriv(footnote_ids=("F1",))
        filing = _filing(non_derivs=(nd,), derivs=(d,), footnotes=(fn,))

        trades1 = ownership_filing_to_trades(filing, _INDEX_ROW)
        trades2 = ownership_filing_to_trades(filing, _INDEX_ROW)

        for t1, t2 in zip(trades1, trades2, strict=True):
            assert t1.sec_detail_json == t2.sec_detail_json, (
                f"sec_detail_json differed for row_id={t1.sec_row_id!r}"
            )

    def test_json_is_sorted_keys(self) -> None:
        """Keys in sec_detail_json must be ASCII-sorted (sort_keys=True)."""
        fn = Footnote(footnote_id="F1", text="x")
        d = _deriv(footnote_ids=("F1",))
        trades = ownership_filing_to_trades(_filing(derivs=(d,), footnotes=(fn,)), _INDEX_ROW)
        raw = trades[0].sec_detail_json
        parsed = json.loads(raw)
        keys = list(parsed.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Test: A4 validation — SecTradeMappingError raised
# ---------------------------------------------------------------------------

class TestA4Validation:
    def test_empty_row_id_non_deriv_raises(self) -> None:
        nd = _non_deriv(row_id="")
        with pytest.raises(SecTradeMappingError, match="empty row_id"):
            ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)

    def test_whitespace_row_id_non_deriv_raises(self) -> None:
        nd = _non_deriv(row_id="   ")
        with pytest.raises(SecTradeMappingError, match="empty row_id"):
            ownership_filing_to_trades(_filing(non_derivs=(nd,)), _INDEX_ROW)

    def test_empty_row_id_deriv_raises(self) -> None:
        d = _deriv(row_id="")
        with pytest.raises(SecTradeMappingError, match="empty row_id"):
            ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)

    def test_inverted_exercise_expiration_dates_raises(self) -> None:
        d = _deriv(
            exercise_date=date(2031, 1, 1),
            expiration_date=date(2030, 1, 1),
        )
        with pytest.raises(SecTradeMappingError, match="exercise_date"):
            ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)

    def test_same_exercise_expiration_date_does_not_raise(self) -> None:
        same = date(2025, 6, 1)
        d = _deriv(exercise_date=same, expiration_date=same)
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        assert len(trades) == 1

    def test_period_of_report_after_filing_date_raises(self) -> None:
        nd = _non_deriv()
        future_period = date(2025, 1, 1)  # after _FILING_DATE 2024-03-15
        with pytest.raises(SecTradeMappingError, match="period_of_report"):
            ownership_filing_to_trades(
                _filing(non_derivs=(nd,), period_of_report=future_period), _INDEX_ROW
            )

    def test_period_of_report_equal_to_filing_date_does_not_raise(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), period_of_report=_FILING_DATE), _INDEX_ROW
        )
        assert len(trades) == 1

    def test_missing_period_of_report_does_not_raise(self) -> None:
        nd = _non_deriv()
        trades = ownership_filing_to_trades(
            _filing(non_derivs=(nd,), period_of_report=None), _INDEX_ROW
        )
        assert len(trades) == 1

    def test_missing_exercise_date_does_not_raise(self) -> None:
        d = _deriv(exercise_date=None, expiration_date=date(2030, 1, 1))
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        assert len(trades) == 1

    def test_missing_expiration_date_does_not_raise(self) -> None:
        d = _deriv(exercise_date=date(2024, 1, 1), expiration_date=None)
        trades = ownership_filing_to_trades(_filing(derivs=(d,)), _INDEX_ROW)
        assert len(trades) == 1


# ---------------------------------------------------------------------------
# Test: footnote embedding caps (fix #5)
# ---------------------------------------------------------------------------

class TestFootnoteCaps:
    """Verify that the defensive footnote caps are deterministic."""

    def test_footnote_text_truncated_to_max_chars_non_deriv(self) -> None:
        long_text = "x" * (_MAX_FOOTNOTE_CHARS + 100)
        fn = Footnote(footnote_id="F1", text=long_text)
        nd = _non_deriv(footnote_ids=("F1",))
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), footnotes=(fn,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert len(detail["footnotes"]["F1"]) == _MAX_FOOTNOTE_CHARS

    def test_footnote_text_truncated_to_max_chars_deriv(self) -> None:
        long_text = "y" * (_MAX_FOOTNOTE_CHARS + 200)
        fn = Footnote(footnote_id="F2", text=long_text)
        d = _deriv(footnote_ids=("F2",))
        trades = ownership_filing_to_trades(_filing(derivs=(d,), footnotes=(fn,)), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert len(detail["footnotes"]["F2"]) == _MAX_FOOTNOTE_CHARS

    def test_excess_footnote_ids_capped_non_deriv(self) -> None:
        # Build _MAX_EMBEDDED_FOOTNOTES + 10 footnote IDs
        count = _MAX_EMBEDDED_FOOTNOTES + 10
        footnotes = tuple(Footnote(footnote_id=f"F{i}", text=f"note{i}") for i in range(count))
        fids = tuple(f"F{i}" for i in range(count))
        nd = _non_deriv(footnote_ids=fids)
        trades = ownership_filing_to_trades(_filing(non_derivs=(nd,), footnotes=footnotes), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert len(detail["footnote_ids"]) == _MAX_EMBEDDED_FOOTNOTES
        assert len(detail["footnotes"]) == _MAX_EMBEDDED_FOOTNOTES

    def test_excess_footnote_ids_capped_deriv(self) -> None:
        count = _MAX_EMBEDDED_FOOTNOTES + 5
        footnotes = tuple(Footnote(footnote_id=f"D{i}", text=f"dnote{i}") for i in range(count))
        fids = tuple(f"D{i}" for i in range(count))
        d = _deriv(footnote_ids=fids)
        trades = ownership_filing_to_trades(_filing(derivs=(d,), footnotes=footnotes), _INDEX_ROW)
        detail = json.loads(trades[0].sec_detail_json)
        assert len(detail["footnote_ids"]) == _MAX_EMBEDDED_FOOTNOTES
        assert len(detail["footnotes"]) == _MAX_EMBEDDED_FOOTNOTES

    def test_footnote_cap_is_deterministic(self) -> None:
        """Identical inputs produce byte-identical sec_detail_json on repeated calls."""
        count = _MAX_EMBEDDED_FOOTNOTES + 3
        footnotes = tuple(Footnote(footnote_id=f"F{i}", text="x" * (_MAX_FOOTNOTE_CHARS + 1)) for i in range(count))
        fids = tuple(f"F{i}" for i in range(count))
        nd = _non_deriv(footnote_ids=fids)
        filing = _filing(non_derivs=(nd,), footnotes=footnotes)

        trades1 = ownership_filing_to_trades(filing, _INDEX_ROW)
        trades2 = ownership_filing_to_trades(filing, _INDEX_ROW)
        assert trades1[0].sec_detail_json == trades2[0].sec_detail_json

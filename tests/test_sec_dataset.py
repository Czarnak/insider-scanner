"""Quarterly SEC TSV mapping, bounded joins and corrupt-filing isolation."""

from __future__ import annotations

import importlib
import importlib.util
import json
import struct
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from insider_scanner.core.sec_bulk import SecBulkError
from insider_scanner.core.sec_trade_mapping import ownership_filing_to_trades
from tests.dataset_fixtures import (
    ACCESSION,
    COLUMNS,
    SECOND_ACCESSION,
    make_dataset,
    owner,
    submission,
    transaction,
)


def _module():
    name = "insider_scanner.core.sec_dataset"
    assert importlib.util.find_spec(name) is not None, (
        "quarterly dataset reader is absent"
    )
    return importlib.import_module(name)


def _read(path, **kwargs):
    return list(_module().iter_dataset_filings(path, **kwargs))


def test_dataset_uses_native_surrogate_identity_without_invented_xml_hash(tmp_path):
    (item,) = _read(make_dataset(tmp_path / "quarter.zip"))
    assert item.error == ""
    assert item.accession == ACCESSION
    filing = item.filing
    assert filing.document_sha256 == ""
    assert filing.warnings == ("dataset_representation",)
    assert filing.non_derivative_transactions[0].row_id == (
        f"{ACCESSION}:dataset:nonDerivative:10042"
    )
    assert item.index_row.filing_date == date(2026, 6, 15)
    assert item.index_row.archive_path == f"edgar/data/1/{ACCESSION}.txt"
    (trade,) = ownership_filing_to_trades(filing, item.index_row)
    assert (trade.ticker, trade.insider_name, trade.value) == (
        "EXMP",
        "Example Person",
        25.0,
    )


def test_dataset_preserves_all_owners_amendments_and_derivative_fields(tmp_path):
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "SUBMISSION": [
                submission(DOCUMENT_TYPE="4/A", DATE_OF_ORIG_SUB="01-JUN-2026")
            ],
            "REPORTINGOWNER": [
                owner(
                    RPTOWNERCIK="999",
                    RPTOWNERNAME="Other Person",
                    RPTOWNER_RELATIONSHIP="TENPERCENTOWNER;OTHER",
                    RPTOWNER_TXT="Trustee",
                ),
                owner(),
            ],
            "DERIV_TRANS": [transaction(derivative=True)],
        },
    )
    (item,) = _read(path)
    assert not item.error
    filing = item.filing
    assert filing.is_amendment
    assert filing.original_submission_date == date(2026, 6, 1)
    assert {owner.cik for owner in filing.reporting_owners} == {
        "0000000025",
        "0000000999",
    }
    assert filing.reporting_owner.name == "Example Person"
    first, second = filing.reporting_owners
    assert first.is_officer and first.is_director
    assert (
        second.is_ten_percent_owner
        and second.is_other
        and second.other_text == "Trustee"
    )
    derivative = filing.derivative_transactions[0]
    assert derivative.row_id == f"{ACCESSION}:dataset:derivative:10042"
    assert derivative.conversion_or_exercise_price == Decimal("1.75")
    assert derivative.exercise_date == date(2026, 1, 1)
    assert derivative.expiration_date == date(2027, 1, 1)
    assert derivative.underlying_security_shares == Decimal("12.50")
    trades = ownership_filing_to_trades(filing, item.index_row)
    assert [trade.trade_type for trade in trades] == ["Buy", "Other"]
    assert len(json.loads(trades[0].sec_detail_json)["reporting_owners"]) == 2


def test_missing_amounts_and_explicit_zero_remain_distinct(tmp_path):
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "NONDERIV_TRANS": [
                transaction(
                    TRANS_SHARES="", TRANS_PRICEPERSHARE="0", SHRS_OWND_FOLWNG_TRANS=""
                )
            ],
            "DERIV_TRANS": [
                transaction(
                    derivative=True,
                    TRANS_SHARES="",
                    TRANS_PRICEPERSHARE="",
                    TRANS_TOTAL_VALUE="75.25",
                )
            ],
        },
    )
    (item,) = _read(path)
    nonderivative, derivative = ownership_filing_to_trades(item.filing, item.index_row)
    assert nonderivative.shares_missing and nonderivative.value_missing
    assert nonderivative.shares_owned_after_missing
    assert not nonderivative.price_missing and nonderivative.price == 0
    assert derivative.shares_missing and derivative.price_missing
    assert not derivative.value_missing and derivative.value == 75.25


def test_transaction_footnote_references_join_by_accession_and_id(tmp_path):
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "NONDERIV_TRANS": [
                transaction(
                    SECURITY_TITLE_FN="F1,F2",
                    TRANS_PRICEPERSHARE_FN="F2",
                    NATURE_OF_OWNERSHIP_FN="F1",
                )
            ],
            "FOOTNOTES": [
                {
                    "ACCESSION_NUMBER": ACCESSION,
                    "FOOTNOTE_ID": "F1",
                    "FOOTNOTE_TXT": "Held by a trust.",
                },
                {
                    "ACCESSION_NUMBER": ACCESSION,
                    "FOOTNOTE_ID": "F2",
                    "FOOTNOTE_TXT": "Weighted average price.",
                },
            ],
        },
    )
    (item,) = _read(path)
    transaction_row = item.filing.non_derivative_transactions[0]
    assert transaction_row.footnote_ids == ("F1", "F2")
    assert transaction_row.price_footnote_ids == ("F2",)
    (trade,) = ownership_filing_to_trades(item.filing, item.index_row)
    detail = json.loads(trade.sec_detail_json)
    assert detail["footnotes"] == {
        "F1": "Held by a trust.",
        "F2": "Weighted average price.",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("TRANS_SHARES", "NaN"),
        ("TRANS_PRICEPERSHARE", "Infinity"),
        ("TRANS_DATE", "31-FEB-2026"),
        ("NONDERIV_TRANS_SK", "../1"),
    ],
)
def test_bad_transaction_isolates_whole_filing_and_keeps_next_filing(
    tmp_path, field, value
):
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "SUBMISSION": [submission(), submission(SECOND_ACCESSION)],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [
                transaction(**{field: value}),
                transaction(SECOND_ACCESSION),
            ],
        },
    )
    failed, successful = _read(path)
    assert failed.accession == ACCESSION and failed.error and failed.filing is None
    assert successful.accession == SECOND_ACCESSION and not successful.error
    assert successful.filing is not None
    assert value not in failed.error


def test_inclusive_filing_date_bounds_are_applied_before_transaction_mapping(tmp_path):
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "SUBMISSION": [
                submission(FILING_DATE="14-JUN-2026"),
                submission(SECOND_ACCESSION),
            ],
            "REPORTINGOWNER": [owner(), owner(SECOND_ACCESSION)],
            "NONDERIV_TRANS": [
                transaction(TRANS_SHARES="NaN"),
                transaction(SECOND_ACCESSION),
            ],
        },
    )
    result = _read(path, since=date(2026, 6, 15), until=date(2026, 6, 15))
    assert [item.accession for item in result] == [SECOND_ACCESSION]
    assert not result[0].error


@pytest.mark.parametrize("cik", ["0000000001", "0000000025"])
def test_cik_filter_matches_issuer_or_any_reporting_owner(tmp_path, cik):
    path = make_dataset(tmp_path / "quarter.zip")
    assert len(_read(path, ciks=frozenset({cik}))) == 1
    assert _read(path, ciks=frozenset({"0000000999"})) == []


@pytest.mark.parametrize(
    "table",
    ["SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS", "DERIV_TRANS", "FOOTNOTES"],
)
def test_missing_required_table_cannot_be_marked_complete(tmp_path, table):
    with pytest.raises(SecBulkError, match="table"):
        _read(make_dataset(tmp_path / "quarter.zip", tables={table: None}))


def test_missing_required_column_rejects_archive_before_yield(tmp_path):
    columns = tuple(
        name for name in COLUMNS["NONDERIV_TRANS"] if name != "TRANS_SHARES"
    )
    with pytest.raises(SecBulkError, match="column"):
        _read(
            make_dataset(tmp_path / "quarter.zip", columns={"NONDERIV_TRANS": columns})
        )


def test_orphan_transaction_cannot_silently_disappear(tmp_path):
    with pytest.raises(SecBulkError, match="orphan"):
        _read(
            make_dataset(
                tmp_path / "quarter.zip",
                tables={"NONDERIV_TRANS": [transaction(SECOND_ACCESSION)]},
            )
        )


def test_malformed_orphan_transaction_cannot_disappear_into_error_only_staging(
    tmp_path,
):
    malformed = transaction(SECOND_ACCESSION)
    malformed["NONDERIV_TRANS_SK"] = ""
    with pytest.raises(SecBulkError, match="orphan"):
        _read(
            make_dataset(
                tmp_path / "quarter.zip",
                tables={"NONDERIV_TRANS": [malformed]},
            )
        )


def test_missing_required_issuer_name_fails_filing(tmp_path):
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip", tables={"SUBMISSION": [submission(ISSUERNAME="")]}
        )
    )
    assert item.error and item.filing is None


def test_missing_required_period_of_report_fails_filing(tmp_path):
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip",
            tables={"SUBMISSION": [submission(PERIOD_OF_REPORT="")]},
        )
    )
    assert item.error and item.filing is None


def test_bound_transaction_count_marks_filing_retryable(tmp_path):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, xml_max_elements=1)
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip",
            tables={
                "NONDERIV_TRANS": [transaction(), transaction(NONDERIV_TRANS_SK="777")]
            },
        ),
        policy=policy,
    )
    assert item.error and item.filing is None


def test_duplicate_transaction_identity_fails_filing(tmp_path):
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip",
            tables={"NONDERIV_TRANS": [transaction(), transaction()]},
        )
    )
    assert item.error and item.filing is None


def test_missing_reporting_owner_fails_filing(tmp_path):
    (item,) = _read(
        make_dataset(tmp_path / "quarter.zip", tables={"REPORTINGOWNER": []})
    )
    assert item.error and item.filing is None


def test_unresolved_footnote_fails_filing_instead_of_claiming_complete(tmp_path):
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip",
            tables={"NONDERIV_TRANS": [transaction(SECURITY_TITLE_FN="F9")]},
        )
    )
    assert item.error and item.filing is None


def test_unsafe_archive_members_are_rejected_even_when_ignored(tmp_path):
    with pytest.raises(SecBulkError):
        _read(
            make_dataset(
                tmp_path / "quarter.zip", extra_members={"../metadata.json": "{}"}
            )
        )


def test_duplicate_casefolded_table_name_is_rejected(tmp_path):
    path = make_dataset(
        tmp_path / "quarter.zip", extra_members={"SUBMISSION.tsv": "ACCESSION_NUMBER\n"}
    )
    with pytest.raises(SecBulkError, match="duplicate"):
        _read(path)


def test_zip_size_limit_is_enforced_before_reading(tmp_path):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, zip_max_member_bytes=8)
    with pytest.raises(SecBulkError, match="zip_max_member_bytes"):
        _read(make_dataset(tmp_path / "quarter.zip"), policy=policy)


def test_scalar_limits_fail_only_attributable_filing(tmp_path):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, xml_max_scalar_chars=40)
    (item,) = _read(
        make_dataset(
            tmp_path / "quarter.zip",
            tables={"NONDERIV_TRANS": [transaction(SECURITY_TITLE="x" * 41)]},
        ),
        policy=policy,
    )
    assert item.error and item.filing is None


def test_requested_owner_with_bad_fields_is_still_a_retryable_filing(tmp_path):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, xml_max_scalar_chars=40)
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={"REPORTINGOWNER": [owner(RPTOWNERNAME="x" * 41)]},
    )
    (item,) = _read(path, ciks=frozenset({"0000000025"}), policy=policy)
    assert item.error and item.filing is None


def test_encrypted_archive_member_raises_readable_dataset_error(tmp_path):
    path = make_dataset(tmp_path / "quarter.zip")
    data = bytearray(path.read_bytes())
    position = data.index(b"PK\x01\x02")
    flags = struct.unpack_from("<H", data, position + 8)[0]
    struct.pack_into("<H", data, position + 8, flags | 1)
    path.write_bytes(data)
    with pytest.raises(SecBulkError, match="encrypt"):
        _read(path)


def test_cancel_during_staging_leaves_no_files_or_yielded_filings(tmp_path):
    path = make_dataset(tmp_path / "quarter.zip")
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 4

    assert _read(path, cancelled=cancelled) == []
    path.rename(tmp_path / "closed.zip")
    assert sorted(item.name for item in tmp_path.iterdir()) == ["closed.zip"]


def test_progress_is_reported_while_staging_and_empty_holdings_add_no_trades(tmp_path):
    progress = []
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={"NONDERIV_TRANS": []},
        extra_members={
            "nonderiv_holding.tsv": "ACCESSION_NUMBER\tNONDERIV_HOLDING_SK\n"
        },
    )
    (item,) = _read(path, on_progress=progress.append)
    assert not item.error and item.filing.non_derivative_transactions == ()
    assert progress and progress[-1] == 2
    assert sorted(tmp_path.iterdir()) == [path]


def test_invalid_date_range_rejected_before_archive_access(tmp_path):
    with pytest.raises(ValueError, match="since"):
        _read(tmp_path / "missing.zip", since=date(2026, 7, 1), until=date(2026, 6, 1))


@pytest.mark.parametrize("remarks", ["x" * 1000, "line with some text\n" * 60])
def test_record_size_is_bounded_before_csv_allocates_fields(tmp_path, remarks):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, xml_max_text_bytes=512)
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={"SUBMISSION": [submission(REMARKS=remarks)]},
    )
    with pytest.raises(SecBulkError, match="record"):
        _read(path, policy=policy)


def test_literal_quotes_unicode_and_quoted_multiline_footnotes_roundtrip(tmp_path):
    narrative = 'A "quoted" security\nwith Łódź text.'
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "FOOTNOTES": [
                {
                    "ACCESSION_NUMBER": ACCESSION,
                    "FOOTNOTE_ID": "F1",
                    "FOOTNOTE_TXT": narrative,
                }
            ],
            "NONDERIV_TRANS": [transaction(SECURITY_TITLE_FN="F1")],
        },
    )
    (item,) = _read(path)
    assert not item.error and item.filing.footnotes[0].text == narrative


def test_per_accession_aggregate_bytes_are_bounded_before_materializing(tmp_path):
    module = _module()
    policy = replace(module.DATASET_SECURITY_POLICY, xml_max_bytes=2000)
    path = make_dataset(
        tmp_path / "quarter.zip",
        tables={
            "FOOTNOTES": [
                {
                    "ACCESSION_NUMBER": ACCESSION,
                    "FOOTNOTE_ID": f"F{index}",
                    "FOOTNOTE_TXT": "x" * 600,
                }
                for index in range(5)
            ]
        },
    )
    (item,) = _read(path, policy=policy)
    assert item.error and item.filing is None


def test_corrupt_archive_read_error_has_stable_payload_free_message(tmp_path):
    path = tmp_path / "broken.zip"
    path.write_bytes(b"not a quarterly archive")
    with pytest.raises(SecBulkError):
        _read(path)

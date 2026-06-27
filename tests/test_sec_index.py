"""Tests for the pure SEC daily master-index parser."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.core.sec_index import (
    OWNERSHIP_FORMS,
    SecMasterIndexRow,
    parse_master_index,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sec_master_20260615_excerpt.idx"


def test_ownership_forms_are_exact_and_immutable() -> None:
    assert OWNERSHIP_FORMS == frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})
    assert isinstance(OWNERSHIP_FORMS, frozenset)


def test_master_index_row_is_frozen_and_slotted() -> None:
    row = SecMasterIndexRow(
        cik="0000320193",
        company_name="APPLE INC",
        form_type="4",
        filing_date=date(2026, 6, 15),
        archive_path="edgar/data/320193/0000320193-26-000061.txt",
    )

    assert row.__slots__ == (
        "cik",
        "company_name",
        "form_type",
        "filing_date",
        "archive_path",
    )
    with pytest.raises(FrozenInstanceError):
        row.company_name = "MUTATED"  # type: ignore[misc]


def test_parse_master_index_filters_validates_deduplicates_and_preserves_order() -> (
    None
):
    text = FIXTURE_PATH.read_text(encoding="utf-8")

    result = parse_master_index(text)

    assert isinstance(result, tuple)
    assert result == (
        SecMasterIndexRow(
            cik="0000320193",
            company_name="APPLE INC",
            form_type="3",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/320193/0000320193-26-000061.txt",
        ),
        SecMasterIndexRow(
            cik="0001652044",
            company_name="ALPHABET INC.",
            form_type="3/A",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/1652044/0001652044-26-000072.txt",
        ),
        SecMasterIndexRow(
            cik="0000789019",
            company_name="MICROSOFT CORP",
            form_type="4",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/789019/0001062993-26-009876.txt",
        ),
        SecMasterIndexRow(
            cik="0001018724",
            company_name="AMAZON COM INC",
            form_type="4/A",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/1018724/0001018724-26-000088.txt",
        ),
        SecMasterIndexRow(
            cik="0001326801",
            company_name="META PLATFORMS INC",
            form_type="5",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/1326801/0001326801-26-000044.txt",
        ),
        SecMasterIndexRow(
            cik="0001067983",
            company_name="BERKSHIRE HATHAWAY INC",
            form_type="5/A",
            filing_date=date(2026, 6, 15),
            archive_path="edgar/data/1067983/0001193125-26-141414.txt",
        ),
    )


@pytest.mark.parametrize("invalid_text", [None, b"", 42, object()])
def test_parse_master_index_rejects_non_string_input(invalid_text: object) -> None:
    with pytest.raises(TypeError, match="text must be a string"):
        parse_master_index(invalid_text)  # type: ignore[arg-type]


def test_parse_master_index_returns_empty_tuple_when_no_ownership_rows() -> None:
    text = "CIK|Company Name|Form Type|Date Filed|Filename\n1|Issuer|8-K|bad|bad"

    assert parse_master_index(text) == ()


def test_malformed_ownership_rows_are_skipped_without_raw_content_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "DO-NOT-LOG-THIS-RAW-ROW"
    text = f"{marker}|Issuer|4|2026-06-15|edgar/data/1/filing.txt"

    assert parse_master_index(text) == ()
    assert marker not in caplog.text


def test_ownership_row_with_blank_company_name_is_skipped() -> None:
    text = "320193|   |4|2026-06-15|edgar/data/320193/filing.txt"

    assert parse_master_index(text) == ()

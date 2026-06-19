"""Behavior tests for the SEC bulk submissions ZIP metadata parser."""

from __future__ import annotations

import inspect
import json
import types
import zipfile
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.core.sec_bulk import (
    BulkFilingMetadata,
    OWNERSHIP_FORMS,
    SecBulkError,
    iter_ownership_filings,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_ZIP = FIXTURE_DIR / "sec_submissions_bulk_small.zip"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    """Build a minimal ZIP at tmp_path / 'test.zip' with the given members."""
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return zip_path


def _cik_json_main(
    cik_digits: str,
    accessions: list[str],
    dates: list[str],
    forms: list[str],
    primaries: list[str],
) -> bytes:
    """Build a main CIK JSON file (filings.recent wrapper)."""
    data = {
        "cik": cik_digits.lstrip("0"),
        "filings": {
            "recent": {
                "accessionNumber": accessions,
                "filingDate": dates,
                "form": forms,
                "primaryDocument": primaries,
            },
            "files": [],
        },
    }
    return json.dumps(data).encode()


def _cik_json_cont(
    accessions: list[str],
    dates: list[str],
    forms: list[str],
    primaries: list[str],
) -> bytes:
    """Build a continuation JSON file (top-level parallel arrays)."""
    data = {
        "accessionNumber": accessions,
        "filingDate": dates,
        "form": forms,
        "primaryDocument": primaries,
    }
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# 1. Dataclass contract
# ---------------------------------------------------------------------------


def test_bulk_filing_metadata_is_frozen_and_slotted() -> None:
    record = BulkFilingMetadata(
        cik="0000320193",
        form_type="4",
        filing_date=date(2026, 6, 13),
        accession_number="0000320193-26-000061",
        primary_document="xslF345X05/form4.xml",
    )
    # frozen
    with pytest.raises((FrozenInstanceError, AttributeError)):
        record.form_type = "8-K"  # type: ignore[misc]
    # slotted
    assert not hasattr(record, "__dict__")
    assert hasattr(record, "__slots__")


# ---------------------------------------------------------------------------
# 2. Fixture: correct record count and form filter
# ---------------------------------------------------------------------------


def test_fixture_yields_exactly_four_ownership_records() -> None:
    results = list(iter_ownership_filings(FIXTURE_ZIP))
    assert len(results) == 4
    for r in results:
        assert r.form_type in OWNERSHIP_FORMS
        assert r.form_type not in {"8-K", "10-K"}


# ---------------------------------------------------------------------------
# 3. Each of the 4 expected records is present with all fields exact
# ---------------------------------------------------------------------------


def _by_accession(results: list[BulkFilingMetadata]) -> dict[str, BulkFilingMetadata]:
    return {r.accession_number: r for r in results}


def test_fixture_record_form4_apple_main() -> None:
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP)))
    r = by_acc["0000320193-26-000061"]
    assert r.cik == "0000320193"
    assert r.form_type == "4"
    assert r.filing_date == date(2026, 6, 13)
    assert r.primary_document == "xslF345X05/form4.xml"


def test_fixture_record_form4a_apple_main() -> None:
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP)))
    r = by_acc["0000320193-26-000035"]
    assert r.cik == "0000320193"
    assert r.form_type == "4/A"
    assert r.filing_date == date(2026, 3, 1)
    assert r.primary_document == "xslF345X05/form4a.xml"


def test_fixture_record_form4_tesla_main() -> None:
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP)))
    r = by_acc["0001318605-26-000012"]
    assert r.cik == "0001318605"
    assert r.form_type == "4"
    assert r.filing_date == date(2026, 5, 20)
    assert r.primary_document == "xslF345X05/form4.xml"


# ---------------------------------------------------------------------------
# 4. Continuation file: Form 3 record is present, CIK taken from filename
# ---------------------------------------------------------------------------


def test_fixture_record_form3_apple_continuation() -> None:
    """Continuation file (CIK0000320193-submissions-001.json) is parsed; CIK
    comes from the filename, not from the JSON body."""
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP)))
    r = by_acc["0000320193-24-000010"]
    assert r.cik == "0000320193"
    assert r.form_type == "3"
    assert r.filing_date == date(2024, 5, 1)
    assert r.primary_document == "xslF345X05/form3.xml"


# ---------------------------------------------------------------------------
# 5. Laziness: iter_ownership_filings returns a generator
# ---------------------------------------------------------------------------


def test_iter_ownership_filings_is_lazy_generator() -> None:
    result = iter_ownership_filings(FIXTURE_ZIP)
    assert isinstance(result, types.GeneratorType) or inspect.isgenerator(result)
    # Confirm it actually produces a record (doesn't require full consume)
    first = next(result)
    assert isinstance(first, BulkFilingMetadata)


# ---------------------------------------------------------------------------
# 6. All CIK values are 10-digit zero-padded
# ---------------------------------------------------------------------------


def test_all_cik_values_are_ten_digits() -> None:
    for r in iter_ownership_filings(FIXTURE_ZIP):
        assert len(r.cik) == 10, f"CIK {r.cik!r} is not 10 digits"
        assert r.cik.isdigit(), f"CIK {r.cik!r} is not all digits"


# ---------------------------------------------------------------------------
# 7. Malformed JSON → SecBulkError; raw body not in message
# ---------------------------------------------------------------------------


def test_malformed_json_raises_sec_bulk_error(tmp_path: Path) -> None:
    bad_body = b"{not json"
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": bad_body})

    with pytest.raises(SecBulkError) as exc_info:
        list(iter_ownership_filings(zip_path))

    exc_msg = str(exc_info.value)
    assert bad_body.decode() not in exc_msg


# ---------------------------------------------------------------------------
# 8. Type / format guard
# ---------------------------------------------------------------------------


def test_non_path_argument_raises_type_error() -> None:
    with pytest.raises(TypeError):
        # deliberately pass a string instead of Path
        list(iter_ownership_filings("not_a_path"))  # type: ignore[arg-type]


def test_non_zip_file_raises_sec_bulk_error(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "fake.zip"
    not_a_zip.write_bytes(b"this is not a zip file at all")

    with pytest.raises(SecBulkError):
        list(iter_ownership_filings(not_a_zip))


# ---------------------------------------------------------------------------
# 9a. Missing primaryDocument array → primary_document is None
# ---------------------------------------------------------------------------


def test_missing_primary_document_array_yields_none(tmp_path: Path) -> None:
    data = {
        "cik": "1",
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-26-000001"],
                "filingDate": ["2026-01-01"],
                "form": ["4"],
                # primaryDocument is intentionally absent
            },
            "files": [],
        },
    }
    zip_path = _make_zip(
        tmp_path, {"CIK0000000001.json": json.dumps(data).encode()}
    )
    results = list(iter_ownership_filings(zip_path))
    assert len(results) == 1
    assert results[0].primary_document is None


# ---------------------------------------------------------------------------
# 9b. Unparseable filingDate → filing_date is None, row still yielded
# ---------------------------------------------------------------------------


def test_unparseable_filing_date_yields_none_and_row_survives(
    tmp_path: Path,
) -> None:
    data = {
        "cik": "2",
        "filings": {
            "recent": {
                "accessionNumber": ["0000000002-26-000001"],
                "filingDate": ["2026-13-40"],  # invalid date
                "form": ["4"],
                "primaryDocument": ["form4.xml"],
            },
            "files": [],
        },
    }
    zip_path = _make_zip(
        tmp_path, {"CIK0000000002.json": json.dumps(data).encode()}
    )
    results = list(iter_ownership_filings(zip_path))
    assert len(results) == 1
    assert results[0].filing_date is None
    assert results[0].accession_number == "0000000002-26-000001"

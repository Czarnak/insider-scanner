"""Behavior tests for the SEC bulk submissions ZIP metadata parser."""

from __future__ import annotations

import inspect
import json
import stat
import types
import zipfile
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.core.sec_bulk import (
    BulkFilingMetadata,
    OWNERSHIP_FORMS,
    SecBulkError,
    SecBulkSecurityError,
    iter_ownership_filings,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityReason,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_ZIP = FIXTURE_DIR / "sec_submissions_bulk_small.zip"


def _small_policy(**overrides: object):
    return replace(DEFAULT_SEC_SECURITY_POLICY, **overrides)  # type: ignore[arg-type]


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
    results = list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
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
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)))
    r = by_acc["0000320193-26-000061"]
    assert r.cik == "0000320193"
    assert r.form_type == "4"
    assert r.filing_date == date(2026, 6, 13)
    assert r.primary_document == "xslF345X05/form4.xml"


def test_fixture_record_form4a_apple_main() -> None:
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)))
    r = by_acc["0000320193-26-000035"]
    assert r.cik == "0000320193"
    assert r.form_type == "4/A"
    assert r.filing_date == date(2026, 3, 1)
    assert r.primary_document == "xslF345X05/form4a.xml"


def test_fixture_record_form4_tesla_main() -> None:
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)))
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
    by_acc = _by_accession(list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)))
    r = by_acc["0000320193-24-000010"]
    assert r.cik == "0000320193"
    assert r.form_type == "3"
    assert r.filing_date == date(2024, 5, 1)
    assert r.primary_document == "xslF345X05/form3.xml"


# ---------------------------------------------------------------------------
# 5. Laziness: iter_ownership_filings returns a generator
# ---------------------------------------------------------------------------


def test_iter_ownership_filings_is_lazy_generator() -> None:
    result = iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)
    assert isinstance(result, types.GeneratorType) or inspect.isgenerator(result)
    # Confirm it actually produces a record (doesn't require full consume)
    first = next(result)
    assert isinstance(first, BulkFilingMetadata)


# ---------------------------------------------------------------------------
# 6. All CIK values are 10-digit zero-padded
# ---------------------------------------------------------------------------


def test_all_cik_values_are_ten_digits() -> None:
    for r in iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR):
        assert len(r.cik) == 10, f"CIK {r.cik!r} is not 10 digits"
        assert r.cik.isdigit(), f"CIK {r.cik!r} is not all digits"


# ---------------------------------------------------------------------------
# 7. Malformed JSON → SecBulkError; raw body not in message
# ---------------------------------------------------------------------------


def test_malformed_json_raises_sec_bulk_error(tmp_path: Path) -> None:
    bad_body = b"{not json"
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": bad_body})

    with pytest.raises(SecBulkError) as exc_info:
        list(iter_ownership_filings(zip_path, cache_root=tmp_path))

    exc_msg = str(exc_info.value)
    assert bad_body.decode() not in exc_msg


# ---------------------------------------------------------------------------
# 8. Type / format guard
# ---------------------------------------------------------------------------


def test_non_path_argument_raises_type_error(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        # deliberately pass a string instead of Path
        list(iter_ownership_filings("not_a_path", cache_root=tmp_path))  # type: ignore[arg-type]


def test_non_zip_file_raises_sec_bulk_error(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "fake.zip"
    not_a_zip.write_bytes(b"this is not a zip file at all")

    with pytest.raises(SecBulkError):
        list(iter_ownership_filings(not_a_zip, cache_root=tmp_path))


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
    results = list(iter_ownership_filings(zip_path, cache_root=tmp_path))
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
    results = list(iter_ownership_filings(zip_path, cache_root=tmp_path))
    assert len(results) == 1
    assert results[0].filing_date is None
    assert results[0].accession_number == "0000000002-26-000001"


# ---------------------------------------------------------------------------
# 10. Generator early-close releases the ZIP handle cleanly (Fix 1)
# ---------------------------------------------------------------------------


def test_early_generator_close_raises_no_exception_and_stops() -> None:
    """Abandon iteration early, call gen.close(), and verify clean finalization.

    Confirms the ZIP handle is released without error and that the generator
    is properly exhausted after close().
    """
    gen = iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR)
    assert isinstance(gen, types.GeneratorType)
    # Consume exactly one record to advance into the with-zf block
    first = next(gen)
    assert isinstance(first, BulkFilingMetadata)
    # Abandon early — close() must not raise
    gen.close()
    # Generator must now be exhausted
    with pytest.raises(StopIteration):
        next(gen)


# ---------------------------------------------------------------------------
# 11. Non-list accessionNumber is silently skipped (Fix 3)
# ---------------------------------------------------------------------------


def test_non_list_accession_number_is_silently_skipped(tmp_path: Path) -> None:
    """A member whose accessionNumber is a string (not a list) yields no rows.

    Exercises the isinstance guard in _filter_ownership_rows.
    """
    data = {
        "cik": "3",
        "filings": {
            "recent": {
                "accessionNumber": "not-a-list",
                "filingDate": ["2026-01-01"],
                "form": ["4"],
                "primaryDocument": ["form4.xml"],
            },
            "files": [],
        },
    }
    zip_path = _make_zip(
        tmp_path, {"CIK0000000003.json": json.dumps(data).encode()}
    )
    results = list(iter_ownership_filings(zip_path, cache_root=tmp_path))
    assert results == []


# ---------------------------------------------------------------------------
# 12. Cache-root containment
# ---------------------------------------------------------------------------


def test_zip_outside_cache_root_is_rejected(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outside = _make_zip(tmp_path, {"CIK0000000001.json": b"{}"})

    with pytest.raises(SecBulkSecurityError) as exc_info:
        list(iter_ownership_filings(outside, cache_root=cache_root))
    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    cache_root = tmp_path
    real = _make_zip(tmp_path, {"CIK0000000001.json": b"{}"})
    link = tmp_path / "link.zip"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")

    with pytest.raises(SecBulkSecurityError) as exc_info:
        list(iter_ownership_filings(link, cache_root=cache_root))
    assert exc_info.value.reason is SecSecurityReason.CACHE_PATH


# ---------------------------------------------------------------------------
# 13. Member preflight: counts, sizes, ratios, totals
# ---------------------------------------------------------------------------


def test_too_many_entries_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": b"{}",
            "CIK0000000002.json": b"{}",
            "CIK0000000003.json": b"{}",
        },
    )
    with pytest.raises(SecBulkSecurityError) as exc_info:
        list(
            iter_ownership_filings(
                zip_path, cache_root=tmp_path, policy=_small_policy(zip_max_entries=2)
            )
        )
    assert exc_info.value.reason is SecSecurityReason.ZIP


def test_oversized_member_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": b"X" * 50})
    with pytest.raises(SecBulkSecurityError):
        list(
            iter_ownership_filings(
                zip_path,
                cache_root=tmp_path,
                policy=_small_policy(zip_max_member_bytes=10),
            )
        )


def test_compression_ratio_bomb_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": b"A" * 100_000})
    with pytest.raises(SecBulkSecurityError):
        list(
            iter_ownership_filings(
                zip_path,
                cache_root=tmp_path,
                policy=_small_policy(zip_max_compression_ratio=2.0),
            )
        )


def test_total_bytes_exceeded_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": b"X" * 50,
            "CIK0000000002.json": b"Y" * 50,
        },
    )
    with pytest.raises(SecBulkSecurityError):
        list(
            iter_ownership_filings(
                zip_path,
                cache_root=tmp_path,
                policy=_small_policy(zip_max_total_bytes=80),
            )
        )


# ---------------------------------------------------------------------------
# 14. Unsafe member names and entry types
# ---------------------------------------------------------------------------


def test_overlong_member_name_is_rejected(tmp_path: Path) -> None:
    long_name = "CIK" + "0" * 600 + ".json"
    zip_path = _make_zip(tmp_path, {long_name: b"{}"})
    with pytest.raises(SecBulkSecurityError):
        list(
            iter_ownership_filings(
                zip_path,
                cache_root=tmp_path,
                policy=_small_policy(zip_max_member_name_chars=16),
            )
        )


def test_directory_entry_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path, {"somedir/": b""})
    with pytest.raises(SecBulkSecurityError):
        list(iter_ownership_filings(zip_path, cache_root=tmp_path))


def test_symlink_entry_is_rejected(tmp_path: Path) -> None:
    zip_path = tmp_path / "sym.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("link.json")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(SecBulkSecurityError):
        list(iter_ownership_filings(zip_path, cache_root=tmp_path))


def test_path_traversal_entry_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path, {"../evil.json": b"{}"})
    with pytest.raises(SecBulkSecurityError):
        list(iter_ownership_filings(zip_path, cache_root=tmp_path))


def test_absolute_member_name_is_rejected(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path, {"/abs.json": b"{}"})
    with pytest.raises(SecBulkSecurityError):
        list(iter_ownership_filings(zip_path, cache_root=tmp_path))


# ---------------------------------------------------------------------------
# 15. Payload never leaks; explicit policy still parses a valid archive
# ---------------------------------------------------------------------------


def test_payload_not_in_security_error_message(tmp_path: Path) -> None:
    secret = "SECRET" * 100
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": secret.encode()})
    with pytest.raises(SecBulkSecurityError) as exc_info:
        list(
            iter_ownership_filings(
                zip_path,
                cache_root=tmp_path,
                policy=_small_policy(zip_max_member_bytes=8),
            )
        )
    assert secret not in str(exc_info.value)


def test_valid_archive_parses_with_explicit_default_policy(tmp_path: Path) -> None:
    zip_path = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": _cik_json_main(
                "0000000001",
                ["0000000001-26-000001"],
                ["2026-01-01"],
                ["4"],
                ["form4.xml"],
            ),
        },
    )
    results = list(
        iter_ownership_filings(
            zip_path, cache_root=tmp_path, policy=DEFAULT_SEC_SECURITY_POLICY
        )
    )
    assert len(results) == 1
    assert results[0].accession_number == "0000000001-26-000001"


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_duplicate_member_name_reads_each_preflighted_entry(tmp_path: Path) -> None:
    """Each entry is read by its own ZipInfo, not by a name that aliases the last.

    The first entry has no filings (0 rows); the second has one ownership row.
    Reading by name would resolve both iterations to the last entry (2 rows).
    """
    zip_path = tmp_path / "dup.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CIK0000000001.json", b"{}")
        zf.writestr(
            "CIK0000000001.json",
            _cik_json_main(
                "0000000001",
                ["0000000001-26-000001"],
                ["2026-01-01"],
                ["4"],
                ["form4.xml"],
            ),
        )

    results = list(iter_ownership_filings(zip_path, cache_root=tmp_path))
    assert len(results) == 1

"""Behavior tests for the SEC bulk submissions ZIP metadata parser."""

from __future__ import annotations

import inspect
import json
import logging
import stat
import types
import zipfile
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.core.sec_bulk import (
    OWNERSHIP_FORMS,
    BulkFilingMetadata,
    SecBulkError,
    SecBulkSecurityError,
    bulk_archive_fingerprint,
    bulk_metadata_to_index_row,
    iter_ownership_filings,
)
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityReason,
)
from tests.sec_stubs import FIXTURE_ZIP

# Sentinel string present in the malformed-metadata fixture — must never leak.
TASK6_METADATA_SENTINEL = "TASK6_METADATA_SENTINEL"

FIXTURE_DIR = Path(__file__).parent / "fixtures"


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


def test_fixture_yields_exactly_three_transaction_records() -> None:
    results = list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
    assert len(results) == 3
    for r in results:
        assert r.form_type in OWNERSHIP_FORMS
        assert r.form_type not in {"3", "3/A", "8-K", "10-K"}


# ---------------------------------------------------------------------------
# 3. Each expected transaction record is present with all fields exact
# ---------------------------------------------------------------------------


def _by_accession(results: list[BulkFilingMetadata]) -> dict[str, BulkFilingMetadata]:
    return {r.accession_number: r for r in results}


def test_fixture_record_form4_apple_main() -> None:
    by_acc = _by_accession(
        list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
    )
    r = by_acc["0000320193-26-000061"]
    assert r.cik == "0000320193"
    assert r.form_type == "4"
    assert r.filing_date == date(2026, 6, 13)
    assert r.primary_document == "xslF345X05/form4.xml"


def test_fixture_record_form4a_apple_main() -> None:
    by_acc = _by_accession(
        list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
    )
    r = by_acc["0000320193-26-000035"]
    assert r.cik == "0000320193"
    assert r.form_type == "4/A"
    assert r.filing_date == date(2026, 3, 1)
    assert r.primary_document == "xslF345X05/form4a.xml"


def test_fixture_record_form4_tesla_main() -> None:
    by_acc = _by_accession(
        list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
    )
    r = by_acc["0001318605-26-000012"]
    assert r.cik == "0001318605"
    assert r.form_type == "4"
    assert r.filing_date == date(2026, 5, 20)
    assert r.primary_document == "xslF345X05/form4.xml"


# ---------------------------------------------------------------------------
# 4. Continuation files exclude initial holdings reports
# ---------------------------------------------------------------------------


def test_fixture_form3_apple_continuation_is_excluded() -> None:
    by_acc = _by_accession(
        list(iter_ownership_filings(FIXTURE_ZIP, cache_root=FIXTURE_DIR))
    )
    assert "0000320193-24-000010" not in by_acc


def test_continuation_skips_initial_holdings_and_retains_form5(tmp_path: Path) -> None:
    archive = _make_zip(
        tmp_path,
        {
            "CIK0000320193-submissions-001.json": _cik_json_cont(
                [
                    "0000320193-24-000010",
                    "0000320193-24-000011",
                    "0000320193-24-000012",
                ],
                ["2024-05-01"] * 3,
                ["3", "3/A", "5"],
                ["form3.xml", "form3a.xml", "form5.xml"],
            )
        },
    )

    results = list(iter_ownership_filings(archive, cache_root=tmp_path))

    assert results == [
        BulkFilingMetadata(
            cik="0000320193",
            form_type="5",
            filing_date=date(2024, 5, 1),
            accession_number="0000320193-24-000012",
            primary_document="form5.xml",
            member_index=0,
        )
    ]


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
# 7. Malformed JSON is isolated; raw body is never reported
# ---------------------------------------------------------------------------


def test_malformed_json_is_reported_without_payload(tmp_path: Path) -> None:
    bad_body = b"{not json"
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": bad_body})
    errors = []
    completed = []
    assert (
        list(
            iter_ownership_filings(
                zip_path,
                on_member_error=lambda index, reason: errors.append((index, reason)),
                on_member_complete=completed.append,
            )
        )
        == []
    )
    assert errors == [(0, "SecBulkError")]
    assert completed == [0]
    assert bad_body.decode() not in str(errors)


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
    zip_path = _make_zip(tmp_path, {"CIK0000000001.json": json.dumps(data).encode()})
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
    zip_path = _make_zip(tmp_path, {"CIK0000000002.json": json.dumps(data).encode()})
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
    zip_path = _make_zip(tmp_path, {"CIK0000000003.json": json.dumps(data).encode()})
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


# ---------------------------------------------------------------------------
# 16. Whole-archive ZIP preflight proof: unsafe archives fail BEFORE any read
# ---------------------------------------------------------------------------

# Minimal valid recognised-member content (low file_size, poor compressibility).
_VALID_MEMBER_NAME = "CIK0000320193.json"
_VALID_MEMBER_BYTES = b'{"filings":{"recent":{},"files":[]}}'


def _make_zip_with_unsafe(
    tmp_path: Path,
    unsafe_info: zipfile.ZipInfo,
    unsafe_bytes: bytes,
) -> Path:
    """Build a ZIP: safe recognised member first, then one unsafe member."""
    zip_path = tmp_path / "preflight_test.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_VALID_MEMBER_NAME, _VALID_MEMBER_BYTES)
        zf.writestr(unsafe_info, unsafe_bytes)
    return zip_path


def _symlink_info(name: str = "link.json") -> zipfile.ZipInfo:
    """Return a ZipInfo whose unix mode marks it as a symlink."""
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _deflated_info(name: str) -> zipfile.ZipInfo:
    """Return a ZipInfo with ZIP_DEFLATED compression (so compress_size reflects ratio)."""
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


# Policies sized so that only the unsafe member trips the check.
# _VALID_MEMBER_BYTES is 36 bytes stored uncompressed (compress_size == file_size,
# ratio == 1.0) because writestr with a string name uses the ZipFile default
# compression, but small data has no appreciable ratio.
_POLICY_OVERSIZED = replace(
    DEFAULT_SEC_SECURITY_POLICY,
    zip_max_member_bytes=50,  # valid (36 B) passes; unsafe (100 B) fails
)
_POLICY_RATIO_BOMB = replace(
    DEFAULT_SEC_SECURITY_POLICY,
    zip_max_compression_ratio=50.0,  # valid ratio 1.0; b"A"*50_000 deflated ~758
)

_UNSAFE_CASES = [
    pytest.param(
        "traversal",
        zipfile.ZipInfo("../evil.json"),
        b"evil content",
        DEFAULT_SEC_SECURITY_POLICY,
        id="traversal_name",
    ),
    pytest.param(
        "symlink",
        _symlink_info(),
        b"/etc/passwd",
        DEFAULT_SEC_SECURITY_POLICY,
        id="symlink_entry",
    ),
    pytest.param(
        "oversized",
        zipfile.ZipInfo("CIK0000000099.json"),
        b"X" * 100,
        _POLICY_OVERSIZED,
        id="oversized_member",
    ),
    pytest.param(
        "ratio_bomb",
        _deflated_info("CIK0000000099.json"),  # compress_type=DEFLATED so ratio is real
        b"A" * 50_000,
        _POLICY_RATIO_BOMB,
        id="compression_ratio_bomb",
    ),
]


@pytest.mark.parametrize("_label,unsafe_info,unsafe_bytes,policy", _UNSAFE_CASES)
def test_unsafe_archives_fail_preflight_before_any_member_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _label: str,
    unsafe_info: zipfile.ZipInfo,
    unsafe_bytes: bytes,
    policy: object,
) -> None:
    """Preflight rejects unsafe archives without reading any member content.

    Instruments ``zipfile.ZipFile.open`` with a counting wrapper.  For every
    unsafe case the generator must raise ``SecBulkSecurityError`` before
    ``open`` is called even once (open-count == 0).
    """
    zip_path = _make_zip_with_unsafe(tmp_path, unsafe_info, unsafe_bytes)

    open_count: list[int] = [0]
    _original_open: Callable[..., object] = zipfile.ZipFile.open  # type: ignore[assignment]

    def _counting_open(
        self: zipfile.ZipFile, *args: object, **kwargs: object
    ) -> object:
        open_count[0] += 1
        return _original_open(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", _counting_open)

    # Safe recognised member is written FIRST and the unsafe member SECOND: the
    # open-count==0 assertion only holds because preflight scans the entire central
    # directory before opening any member. This guards against a regression to
    # scan-as-you-read.
    with pytest.raises(SecBulkSecurityError):
        list(iter_ownership_filings(zip_path, cache_root=tmp_path, policy=policy))  # type: ignore[arg-type]

    assert open_count[0] == 0, (
        f"[{_label}] preflight must not call zf.open; got {open_count[0]} call(s)"
    )


# ---------------------------------------------------------------------------
# 17. Malformed fixture: member error reported; sentinel never leaks
# ---------------------------------------------------------------------------


def test_malformed_metadata_error_does_not_leak_payload_or_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed metadata reports a stable member error without its payload."""
    malformed_bytes = (
        FIXTURE_DIR / "sec_submissions_malformed_metadata.json"
    ).read_bytes()

    zip_path = tmp_path / "malformed_meta.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_VALID_MEMBER_NAME, malformed_bytes)

    errors = []
    with caplog.at_level(logging.DEBUG):
        assert (
            list(
                iter_ownership_filings(
                    zip_path,
                    on_member_error=lambda index, reason: errors.append(
                        (index, reason)
                    ),
                )
            )
            == []
        )
    assert errors == [(0, "SecBulkError")]
    assert TASK6_METADATA_SENTINEL not in str(errors)
    assert TASK6_METADATA_SENTINEL not in caplog.text, (
        "Sentinel found in log output — raw payload leaked"
    )


# ---------------------------------------------------------------------------
# 18. bulk_metadata_to_index_row converter
# ---------------------------------------------------------------------------


def _meta(
    cik="0000320193",
    form="4",
    d=date(2026, 6, 13),
    acc="0000320193-26-000061",
    primary="form4.xml",
):
    return BulkFilingMetadata(
        cik=cik,
        form_type=form,
        filing_date=d,
        accession_number=acc,
        primary_document=primary,
    )


def test_synthesizes_archive_path_from_cik_and_accession():
    row = bulk_metadata_to_index_row(_meta())
    assert isinstance(row, SecMasterIndexRow)
    assert row.cik == "0000320193"
    assert row.form_type == "4"
    assert row.filing_date == date(2026, 6, 13)
    assert row.archive_path == "edgar/data/320193/0000320193-26-000061.txt"


def test_missing_filing_date_is_rejected():
    with pytest.raises(SecBulkError):
        bulk_metadata_to_index_row(_meta(d=None))


def test_malformed_accession_is_rejected():
    with pytest.raises(SecBulkError):
        bulk_metadata_to_index_row(_meta(acc="../etc/passwd"))
    with pytest.raises(SecBulkError):
        bulk_metadata_to_index_row(_meta(acc="not-an-accession"))


def test_cik_filter_limits_yielded_records():
    results = list(
        iter_ownership_filings(
            FIXTURE_ZIP, cache_root=FIXTURE_DIR, ciks=frozenset({"0001318605"})
        )
    )
    assert [r.cik for r in results] == ["0001318605"]


def _one_filing(cik: int) -> bytes:
    return _cik_json_main(
        str(cik).zfill(10),
        [f"{cik:010d}-26-000001"],
        ["2026-01-01"],
        ["4"],
        ["form4.xml"],
    )


def test_resume_opens_only_retry_members_and_unfinished_suffix(tmp_path, monkeypatch):
    archive = _make_zip(
        tmp_path,
        {f"CIK{cik:010d}.json": _one_filing(cik) for cik in range(1, 5)},
    )
    original = zipfile.ZipFile.open
    opened = []
    completed = []

    def track(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", track)
    rows = list(
        iter_ownership_filings(
            archive,
            start_member=3,
            retry_members=frozenset({1}),
            on_member_complete=completed.append,
        )
    )
    assert [row.member_index for row in rows] == [1, 3]
    assert opened == ["CIK0000000002.json", "CIK0000000004.json"]
    assert completed == [1, 3]


def test_valid_bad_valid_members_continue_and_report_positions(tmp_path):
    archive = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": _one_filing(1),
            "CIK0000000002.json": b"{not json",
            "CIK0000000003.json": _one_filing(3),
        },
    )
    errors = []
    completed = []
    rows = list(
        iter_ownership_filings(
            archive,
            on_member_error=lambda index, reason: errors.append((index, reason)),
            on_member_complete=completed.append,
        )
    )
    assert [row.member_index for row in rows] == [0, 2]
    assert errors == [(1, "SecBulkError")]
    assert completed == [0, 1, 2]


@pytest.mark.parametrize(
    "key", ["accessionNumber", "form", "filingDate", "primaryDocument"]
)
def test_invalid_parallel_array_type_isolated_per_member(tmp_path, key):
    bad = json.loads(_one_filing(1))
    bad["filings"]["recent"][key] = {"0": "untrusted"}
    archive = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": json.dumps(bad).encode(),
            "CIK0000000002.json": _one_filing(2),
        },
    )
    errors = []
    rows = list(
        iter_ownership_filings(
            archive,
            on_member_error=lambda index, reason: errors.append((index, reason)),
        )
    )
    assert [row.member_index for row in rows] == [1]
    assert errors == [(0, "SecBulkError")]


@pytest.mark.parametrize("error", [zipfile.BadZipFile, EOFError, OSError])
def test_corrupt_member_read_does_not_abandon_later_members(
    tmp_path, monkeypatch, error
):
    archive = _make_zip(
        tmp_path,
        {
            "CIK0000000001.json": _one_filing(1),
            "CIK0000000002.json": _one_filing(2),
        },
    )
    original = zipfile.ZipFile.open
    errors = []

    def corrupt(self, member, *args, **kwargs):
        if member.filename == "CIK0000000001.json":
            raise error("SECRET_MEMBER_CONTENT")
        return original(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt)
    rows = list(
        iter_ownership_filings(
            archive,
            on_member_error=lambda index, reason: errors.append((index, reason)),
        )
    )
    assert [row.member_index for row in rows] == [1]
    assert errors == [(0, error.__name__)]
    assert "SECRET_MEMBER_CONTENT" not in str(errors)


def test_completed_callback_excludes_partially_consumed_member(tmp_path):
    archive = _make_zip(tmp_path, {"CIK0000000001.json": _one_filing(1)})
    completed = []
    iterator = iter_ownership_filings(archive, on_member_complete=completed.append)
    assert next(iterator).member_index == 0
    iterator.close()
    assert completed == []


def test_empty_filtered_and_unrecognized_members_complete_without_open(
    tmp_path, monkeypatch
):
    archive = _make_zip(
        tmp_path,
        {
            "ignore.json": b"{}",
            "CIK0000000001.json": _one_filing(1),
            "CIK0000000002.json": b"{}",
        },
    )
    original = zipfile.ZipFile.open
    opened = []
    completed = []

    def track(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", track)
    assert (
        list(
            iter_ownership_filings(
                archive,
                ciks=frozenset({"0000000002"}),
                on_member_complete=completed.append,
            )
        )
        == []
    )
    assert opened == ["CIK0000000002.json"]
    assert completed == [0, 1, 2]


def test_fingerprint_ignores_path_but_detects_content_and_order(tmp_path, monkeypatch):
    members = {
        "CIK0000000001.json": _one_filing(1),
        "CIK0000000002.json": _one_filing(2),
    }
    archive = _make_zip(tmp_path, members)
    original = bulk_archive_fingerprint(archive)
    renamed = archive.rename(tmp_path / "renamed.zip")
    assert bulk_archive_fingerprint(renamed) == original
    changed = _make_zip(tmp_path, dict(reversed(list(members.items()))))
    assert bulk_archive_fingerprint(changed) != original
    changed = _make_zip(tmp_path, {**members, "CIK0000000002.json": _one_filing(3)})
    assert bulk_archive_fingerprint(changed) != original

    def no_content(*args, **kwargs):
        pytest.fail("Fingerprint must not decompress a member")

    monkeypatch.setattr(zipfile.ZipFile, "open", no_content)
    assert bulk_archive_fingerprint(renamed) == original


@pytest.mark.parametrize(
    "limit,allowed,observed",
    [
        ("zip_max_entries", 1, 2),
        ("zip_max_total_bytes", 3, 4),
        ("zip_max_member_bytes", 1, 2),
        ("zip_max_member_name_chars", 1, 18),
    ],
)
def test_limit_diagnostics_name_bound_without_member_content(
    tmp_path, limit, allowed, observed
):
    archive = _make_zip(
        tmp_path, {"CIK0000000001.json": b"{}", "CIK0000000002.json": b"{}"}
    )
    with pytest.raises(SecBulkSecurityError) as caught:
        bulk_archive_fingerprint(archive, policy=_small_policy(**{limit: allowed}))
    message = str(caught.value)
    assert limit in message
    assert f"observed={observed}" in message
    assert f"allowed={allowed}" in message
    assert "CIK0000000001" not in message


def test_direct_local_archive_must_be_regular_file(tmp_path):
    with pytest.raises(SecBulkSecurityError) as caught:
        list(iter_ownership_filings(tmp_path))
    assert caught.value.reason is SecSecurityReason.CACHE_PATH

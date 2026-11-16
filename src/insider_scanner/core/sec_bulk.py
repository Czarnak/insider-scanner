"""SEC submissions bulk ZIP metadata parser.

Streams ownership-form filing metadata (Forms 3/4/5 and amendments) out of a
submissions bulk archive ZIP without extracting the whole archive to disk.

Member naming conventions in the archive:
- Main file:         ``CIK{10-digit}.json``  → filings under ``filings.recent``
- Continuation file: ``CIK{10-digit}-submissions-{NNN}.json``  → top-level arrays
- Any other member is silently ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
import zlib
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from insider_scanner.core import edgar
from insider_scanner.core._sec_paths import resolves_within
from insider_scanner.core.sec_forms import TRANSACTION_FORM_TYPES as OWNERSHIP_FORMS
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
    SecSecurityReason,
)
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_bulk")

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# SEC submissions metadata archive for bounded legacy ingestion and XML repairs.
SEC_BULK_SUBMISSIONS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
)
SEC_BULK_INSTRUCTIONS = (
    "For history from 2006 Q1, use quarterly SEC datasets:\n"
    "   insider-scanner-cli sec-backfill-dataset --since 2006-01-01\n"
    "For 2003–2005 or targeted XML repairs, use the SEC 'submissions' archive "
    "(multi-GB, refreshed nightly).\n"
    "1. Download it once:\n"
    f"   {SEC_BULK_SUBMISSIONS_URL}\n"
    "2. Run backfill against the local file:\n"
    "   insider-scanner-cli sec-backfill --zip PATH\\TO\\submissions.zip --confirm-full-backfill\n"
    "Use --since/--until to bound filing dates, and --cik for targeted repairs.\n"
    "Add --force to a scoped repair to replace an already-ingested dataset filing.\n"
    "Backfill is resumable: re-run the same command after an interruption to continue."
)


# ---------------------------------------------------------------------------
# Filename patterns
# ---------------------------------------------------------------------------

_RE_MAIN = re.compile(r"^CIK([0-9]{10})\.json$")
_RE_CONT = re.compile(r"^CIK([0-9]{10})-submissions-([0-9]+)\.json$")
_ACCESSION_RE = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BulkFilingMetadata:
    """Immutable ownership-filing metadata record extracted from a bulk archive."""

    cik: str  # 10-digit zero-padded
    form_type: str
    filing_date: date | None
    accession_number: str
    primary_document: str | None
    member_index: int | None = None


class SecBulkError(Exception):
    """Raised when a submissions bulk archive member cannot be parsed."""


class SecBulkSecurityError(SecBulkError):
    """Raised when a bulk archive violates the immutable security policy.

    Subclasses :class:`SecBulkError` so existing ``except SecBulkError`` handlers
    still catch it. Messages carry stable reason codes and numeric limit
    diagnostics, never member names or payload.
    """

    def __init__(
        self,
        reason: SecSecurityReason = SecSecurityReason.ZIP,
        *,
        limit: str | None = None,
        observed: float | None = None,
        allowed: float | None = None,
    ) -> None:
        self.reason = reason
        detail = f": {limit} observed={observed} allowed={allowed}" if limit else ""
        super().__init__(f"SEC bulk archive rejected ({reason.value}){detail}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def bulk_metadata_to_index_row(meta: BulkFilingMetadata) -> SecMasterIndexRow:
    """Convert bulk metadata into a SecMasterIndexRow the hardened pipeline accepts.

    Raises SecBulkError when filing_date is absent or the accession is malformed
    (defense in depth — accession is used to build a download path).
    """
    if not isinstance(meta, BulkFilingMetadata):
        raise TypeError("meta must be a BulkFilingMetadata")
    if meta.filing_date is None:
        raise SecBulkError("bulk metadata has no filing_date")
    if not _ACCESSION_RE.fullmatch(meta.accession_number):
        raise SecBulkError("bulk metadata has a malformed accession number")
    cik_no_zeros = str(int(meta.cik))  # meta.cik is 10-digit zero-padded
    archive_path = f"edgar/data/{cik_no_zeros}/{meta.accession_number}.txt"
    edgar.build_filing_archive_url(archive_path)  # raises on any unsafe path
    return SecMasterIndexRow(
        cik=meta.cik,
        company_name="",  # bulk metadata has no issuer name; mapper falls back to parsed filing
        form_type=meta.form_type,
        filing_date=meta.filing_date,
        archive_path=archive_path,
    )


def bulk_archive_fingerprint(
    zip_path: Path,
    *,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    on_members: Callable[[int], None] | None = None,
) -> str:
    """Identify central-directory content and order without reading member bodies.

    Renaming an archive preserves its identity. Replaced or reordered members
    change it, so a saved member cursor cannot silently skip a new archive.
    The same archive-level preflight used by iteration runs before hashing.
    """
    _validate_zip_location(zip_path, None)
    with _open_archive(zip_path) as zf:
        members = zf.infolist()
        _preflight_members(members, policy)
        if on_members is not None:
            on_members(len(members))
        digest = hashlib.sha256()
        for info in members:
            descriptor = json.dumps(
                [
                    info.filename,
                    info.CRC,
                    info.file_size,
                    info.compress_size,
                    info.header_offset,
                    info.compress_type,
                    info.flag_bits,
                    info.external_attr,
                ],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            digest.update(descriptor)
            digest.update(b"\n")
        return digest.hexdigest()


def iter_ownership_filings(
    zip_path: Path,
    *,
    cache_root: Path | None = None,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    ciks: frozenset[str] | None = None,
    start_member: int = 0,
    retry_members: frozenset[int] = frozenset(),
    on_member_error: Callable[[int, str], None] | None = None,
    on_member_complete: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Generator[BulkFilingMetadata, None, None]:
    """Stream ownership metadata from a preflighted local archive.

    ``start_member`` is a central-directory position; members before it are
    never opened unless explicitly listed in ``retry_members``. Positions are
    valid only for the archive fingerprint they were saved with. The caller
    must retain failed positions separately when advancing its durable cursor.

    Malformed member JSON, array shapes and corrupt member reads are reported
    by index and stable error class, then iteration continues. Security-policy
    violations remain fatal. Completion callbacks run only after a selected
    member is fully consumed, including empty, ignored or malformed members;
    closing the generator partway through a member does not complete it.
    Cancellation is checked between members, including discovery that yields
    no ownership filings. The callback should report state without side effects.

    Explicit local paths are accepted without an artificial containment root.
    A supplied ``cache_root`` retains the older trusted-root contract. Symlink
    archives and non-regular files are always rejected. Every member is
    preflighted before content reads, and decompression is bounded.

    The ZIP remains open until exhaustion or generator.close(); callers that
    stop early must close the generator promptly.
    """
    _validate_zip_location(zip_path, cache_root)
    if type(start_member) is not int or start_member < 0:
        raise ValueError("start_member must be a nonnegative integer")
    if any(type(index) is not int or index < 0 for index in retry_members):
        raise ValueError("retry_members must contain nonnegative integers")

    with _open_archive(zip_path) as zf:
        members = zf.infolist()
        _preflight_members(members, policy)
        for member_index, info in enumerate(members):
            if cancelled is not None and cancelled():
                return
            if member_index < start_member and member_index not in retry_members:
                continue
            try:
                yield from _iter_member(zf, info, policy, ciks, member_index)
            except SecBulkSecurityError:
                raise
            except (
                SecBulkError,
                zipfile.BadZipFile,
                EOFError,
                OSError,
                RuntimeError,
                NotImplementedError,
                zlib.error,
            ) as error:
                reason = type(error).__name__
                _log.warning(
                    "SEC bulk member skipped index=%s reason=%s", member_index, reason
                )
                if on_member_error is not None:
                    on_member_error(member_index, reason)
            if on_member_complete is not None:
                on_member_complete(member_index)


def _open_archive(zip_path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as error:
        raise SecBulkError("Unable to open SEC bulk ZIP archive") from error


def _iter_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    policy: SecSecurityPolicy,
    ciks: frozenset[str] | None,
    member_index: int,
) -> Iterator[BulkFilingMetadata]:
    main = _RE_MAIN.match(info.filename)
    continuation = _RE_CONT.match(info.filename)
    match = main or continuation
    if match is None:
        return
    cik = edgar.normalize_cik(match.group(1))
    if ciks is not None and cik not in ciks:
        return
    data = _read_member_json(zf, info, policy)
    rows = _extract_recent_rows(data) if main else _coerce_to_arrays_dict(data)
    _validate_arrays(rows)
    yield from _filter_ownership_rows(cik, rows, member_index=member_index)


# ---------------------------------------------------------------------------
# Security: containment, preflight, bounded reads
# ---------------------------------------------------------------------------


def _validate_zip_location(zip_path: Path, cache_root: Path | None) -> None:
    """Validate an explicit local file, optionally constrained to a real root."""
    if not isinstance(zip_path, Path):
        raise TypeError("zip_path must be a pathlib.Path")
    if cache_root is not None and not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path or None")
    try:
        if zip_path.is_symlink():
            raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
        if zip_path.exists() and not zip_path.is_file():
            raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
        if cache_root is not None:
            if cache_root.is_symlink():
                raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
            if cache_root.exists() and not cache_root.is_dir():
                raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
            if not resolves_within(zip_path, cache_root):
                raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
    except OSError as error:
        raise SecBulkError("Unable to inspect SEC bulk archive location") from error


def _preflight_members(
    members: list[zipfile.ZipInfo], policy: SecSecurityPolicy
) -> None:
    """Validate every member from the central directory before any content read."""
    _check_limit("zip_max_entries", len(members), policy.zip_max_entries)
    total = 0
    for info in members:
        _preflight_member(info, policy)
        total += info.file_size
        _check_limit("zip_max_total_bytes", total, policy.zip_max_total_bytes)


def _preflight_member(info: zipfile.ZipInfo, policy: SecSecurityPolicy) -> None:
    """Reject one member on unsafe name, entry type, size, or ratio."""
    name = info.filename
    _check_limit(
        "zip_max_member_name_chars", len(name), policy.zip_max_member_name_chars
    )
    if info.is_dir() or name.endswith("/"):
        raise SecBulkSecurityError()
    if _is_symlink_entry(info):
        raise SecBulkSecurityError()
    if _is_unsafe_member_name(name):
        raise SecBulkSecurityError()
    _check_limit("zip_max_member_bytes", info.file_size, policy.zip_max_member_bytes)
    ratio = (
        info.file_size / info.compress_size
        if info.compress_size > 0
        else (float("inf") if info.file_size > 0 else 0)
    )
    _check_limit("zip_max_compression_ratio", ratio, policy.zip_max_compression_ratio)


def _check_limit(name: str, observed: float, allowed: float) -> None:
    if observed > allowed:
        raise SecBulkSecurityError(limit=name, observed=observed, allowed=allowed)


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Return True when the entry's stored unix mode marks it a symlink.

    Windows-created entries leave ``external_attr`` unix bits zero, so this
    reads as not-a-symlink — safe, since such tools cannot store a symlink entry.
    """
    return stat.S_ISLNK(info.external_attr >> 16)


def _is_unsafe_member_name(name: str) -> bool:
    """Reject absolute, drive-qualified, backslashed, or traversing names."""
    if not name:
        return True
    if name.startswith("/") or "\\" in name:
        return True
    if len(name) >= 2 and name[1] == ":":  # drive letter, e.g. C:/...
        return True
    pure = PurePosixPath(name)
    if pure.is_absolute():
        return True
    return any(part == ".." for part in pure.parts)


def _read_member_json(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, policy: SecSecurityPolicy
) -> object:
    """Read one recognized member with a bounded stream and parse its JSON.

    Reads at most ``zip_max_member_bytes`` decompressed bytes, so a forged
    central-directory size cannot expand into an unbounded read.
    """
    limit = policy.zip_max_member_bytes
    # Open by ZipInfo (not by name) so we read exactly the preflighted entry;
    # name lookup would resolve a duplicate name to a different central-dir row.
    with zf.open(info) as fp:
        raw = fp.read(limit + 1)
    _check_limit("zip_max_member_bytes", len(raw), limit)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise SecBulkError("Malformed JSON in bulk archive member") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_recent_rows(data: object) -> dict[str, list[object]]:
    """Return the parallel-array dict from a main file's ``filings.recent``."""
    if not isinstance(data, dict):
        return {}
    filings = data.get("filings")
    if not isinstance(filings, dict):
        return {}
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return {}
    return recent


def _coerce_to_arrays_dict(data: object) -> dict[str, list[object]]:
    """Return the parallel-array dict from a continuation file's top level."""
    if not isinstance(data, dict):
        return {}
    return data


def _validate_arrays(rows: dict[str, list[object]]) -> None:
    for key in ("accessionNumber", "form", "filingDate", "primaryDocument"):
        if key in rows and not isinstance(rows[key], list):
            raise SecBulkError("Malformed parallel arrays in bulk archive member")


def _filter_ownership_rows(
    cik: str, rows: dict[str, list[object]], *, member_index: int | None = None
) -> Iterator[BulkFilingMetadata]:
    """Iterate over parallel arrays and yield ownership-form records."""
    accessions: list[object] = rows.get("accessionNumber") or []
    if not isinstance(accessions, list):
        return
    if not accessions:
        return

    forms: list[object] = rows.get("form") or []
    filing_dates: list[object] = rows.get("filingDate") or []
    primaries: list[object] = rows.get("primaryDocument") or []

    for idx, raw_acc in enumerate(accessions):
        if not isinstance(raw_acc, str) or not raw_acc:
            continue

        form_type = forms[idx] if idx < len(forms) else None
        if not isinstance(form_type, str):
            continue
        if form_type not in OWNERSHIP_FORMS:
            continue

        raw_date = filing_dates[idx] if idx < len(filing_dates) else None
        filing_date = _parse_date_lenient(raw_date)

        raw_primary = primaries[idx] if idx < len(primaries) else None
        primary_document = (
            raw_primary if isinstance(raw_primary, str) and raw_primary else None
        )

        yield BulkFilingMetadata(
            cik=cik,
            form_type=form_type,
            filing_date=filing_date,
            accession_number=raw_acc,
            primary_document=primary_document,
            member_index=member_index,
        )


def _parse_date_lenient(raw: object) -> date | None:
    """Parse an ISO date string; return None on any failure (including bad values)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None

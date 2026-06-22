"""SEC submissions bulk ZIP metadata parser.

Streams ownership-form filing metadata (Forms 3/4/5 and amendments) out of a
submissions bulk archive ZIP without extracting the whole archive to disk.

Member naming conventions in the archive:
- Main file:         ``CIK{10-digit}.json``  → filings under ``filings.recent``
- Continuation file: ``CIK{10-digit}-submissions-{NNN}.json``  → top-level arrays
- Any other member is silently ignored.
"""

from __future__ import annotations

import json
import re
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from insider_scanner.core import edgar
from insider_scanner.core._sec_paths import resolves_within
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
    SecSecurityReason,
)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

OWNERSHIP_FORMS: frozenset[str] = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

# ---------------------------------------------------------------------------
# Filename patterns
# ---------------------------------------------------------------------------

_RE_MAIN = re.compile(r"^CIK(\d{10})\.json$")
_RE_CONT = re.compile(r"^CIK(\d{10})-submissions-(\d+)\.json$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


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


class SecBulkError(Exception):
    """Raised when a submissions bulk archive member cannot be parsed."""


class SecBulkSecurityError(SecBulkError):
    """Raised when a bulk archive violates the immutable security policy.

    Subclasses :class:`SecBulkError` so existing ``except SecBulkError`` handlers
    still catch it.  The message carries only a stable reason code — never a
    member name or payload.
    """

    def __init__(self, reason: SecSecurityReason = SecSecurityReason.ZIP) -> None:
        self.reason = reason
        super().__init__(f"SEC bulk archive rejected ({reason.value})")


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
    if not _ACCESSION_RE.match(meta.accession_number):
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


def iter_ownership_filings(
    zip_path: Path,
    *,
    cache_root: Path,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    ciks: frozenset[str] | None = None,
) -> Iterator[BulkFilingMetadata]:
    """Yield ownership-form filing metadata streamed from a submissions bulk ZIP.

    Parameters
    ----------
    zip_path:
        Path to the submissions bulk archive.  Must be a :class:`pathlib.Path`
        that resolves inside *cache_root* and is not a symlink.
    cache_root:
        Injected trusted root the archive must live under.  Must be a
        :class:`pathlib.Path`.
    policy:
        Immutable security policy bounding member counts, sizes, totals,
        compression ratios, and names.  Defaults to the secure default.
    ciks:
        Optional frozenset of normalized 10-digit CIKs to include; ``None``
        means all CIKs are yielded.  Filtering occurs before the JSON read for
        efficiency on large multi-GB archives.

    Yields
    ------
    BulkFilingMetadata
        One record per ownership-form row found across all recognized members.

    Raises
    ------
    TypeError
        If *zip_path* or *cache_root* is not a :class:`pathlib.Path`.
    SecBulkSecurityError
        If the archive is outside *cache_root*, is a symlink, or any member
        fails preflight (unsafe name, directory/symlink entry, excessive count,
        size, total, or compression ratio).
    SecBulkError
        If *zip_path* is not a valid ZIP file, or if a recognized member
        contains malformed JSON.

    Notes
    -----
    Every member is preflighted from the central directory before any member
    content is read.  Recognized members are then read with a bounded stream;
    ZIP extraction APIs are never used.

    **ZIP resource lifetime**: the underlying :class:`zipfile.ZipFile` handle
    stays open for the lifetime of the generator — it is released when the
    generator is exhausted, garbage-collected, or explicitly closed.  Callers
    who abandon iteration early should call ``.close()`` on the generator
    object to release the file handle promptly::

        gen = iter_ownership_filings(path, cache_root=root)
        first = next(gen)
        gen.close()   # releases the ZIP handle immediately
    """
    if not isinstance(zip_path, Path):
        raise TypeError(
            f"zip_path must be a pathlib.Path, got {type(zip_path).__name__!r}"
        )
    if not isinstance(cache_root, Path):
        raise TypeError(
            f"cache_root must be a pathlib.Path, got {type(cache_root).__name__!r}"
        )

    _validate_zip_location(zip_path, cache_root)

    try:
        zf = zipfile.ZipFile(zip_path)  # noqa: SIM115 — we close it below
    except zipfile.BadZipFile as exc:
        raise SecBulkError(f"Not a valid ZIP archive: {zip_path.name}") from exc

    with zf:
        members = zf.infolist()
        _preflight_members(members, policy)

        for info in members:
            name = info.filename
            m_main = _RE_MAIN.match(name)
            m_cont = _RE_CONT.match(name)
            if m_main:
                cik_str = m_main.group(1)
            elif m_cont:
                cik_str = m_cont.group(1)
            else:
                continue  # stray member — preflighted, never read

            cik = edgar.normalize_cik(cik_str)
            if ciks is not None and cik not in ciks:
                continue
            data = _read_member_json(zf, info, policy)

            if m_main:
                rows = _extract_recent_rows(data)
            else:
                rows = _coerce_to_arrays_dict(data)

            yield from _filter_ownership_rows(cik, rows)


# ---------------------------------------------------------------------------
# Security: containment, preflight, bounded reads
# ---------------------------------------------------------------------------


def _validate_zip_location(zip_path: Path, cache_root: Path) -> None:
    """Reject archives outside the trusted cache root or reached via symlinks."""
    if cache_root.is_symlink():
        raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
    if cache_root.exists() and not cache_root.is_dir():
        raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
    if not resolves_within(zip_path, cache_root):
        raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)
    if zip_path.is_symlink():
        raise SecBulkSecurityError(SecSecurityReason.CACHE_PATH)


def _preflight_members(
    members: list[zipfile.ZipInfo], policy: SecSecurityPolicy
) -> None:
    """Validate every member from the central directory before any content read."""
    if len(members) > policy.zip_max_entries:
        raise SecBulkSecurityError()
    total = 0
    for info in members:
        _preflight_member(info, policy)
        total += info.file_size
        if total > policy.zip_max_total_bytes:
            raise SecBulkSecurityError()


def _preflight_member(info: zipfile.ZipInfo, policy: SecSecurityPolicy) -> None:
    """Reject one member on unsafe name, entry type, size, or ratio."""
    name = info.filename
    if len(name) > policy.zip_max_member_name_chars:
        raise SecBulkSecurityError()
    if info.is_dir() or name.endswith("/"):
        raise SecBulkSecurityError()
    if _is_symlink_entry(info):
        raise SecBulkSecurityError()
    if _is_unsafe_member_name(name):
        raise SecBulkSecurityError()
    if info.file_size > policy.zip_max_member_bytes:
        raise SecBulkSecurityError()
    if _exceeds_compression_ratio(info, policy):
        raise SecBulkSecurityError()


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


def _exceeds_compression_ratio(
    info: zipfile.ZipInfo, policy: SecSecurityPolicy
) -> bool:
    if info.compress_size <= 0:
        # Non-empty output from zero compressed bytes is a decompression bomb.
        return info.file_size > 0
    return info.file_size / info.compress_size > policy.zip_max_compression_ratio


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
    if len(raw) > limit:
        raise SecBulkSecurityError()
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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


def _filter_ownership_rows(
    cik: str, rows: dict[str, list[object]]
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
        )


def _parse_date_lenient(raw: object) -> date | None:
    """Parse an ISO date string; return None on any failure (including bad values)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None

"""SEC submissions bulk ZIP metadata parser.

Streams ownership-form filing metadata (Forms 3/4/5 and amendments) out of a
submissions bulk archive ZIP without extracting the whole archive to disk.

Member naming conventions in the archive:
- Main file:         ``CIK{10-digit}.json``  → filings under ``filings.recent``
- Continuation file: ``CIK{10-digit}-submissions-{NNN}.json``  → top-level arrays
- Any other member is silently ignored.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from insider_scanner.core import edgar

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

OWNERSHIP_FORMS: frozenset[str] = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

# ---------------------------------------------------------------------------
# Filename patterns
# ---------------------------------------------------------------------------

_RE_MAIN = re.compile(r"^CIK(\d{10})\.json$")
_RE_CONT = re.compile(r"^CIK(\d{10})-submissions-(\d+)\.json$")


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def iter_ownership_filings(zip_path: Path) -> Iterator[BulkFilingMetadata]:
    """Yield ownership-form filing metadata streamed from a submissions bulk ZIP.

    Parameters
    ----------
    zip_path:
        Path to the submissions bulk archive.  Must be a :class:`pathlib.Path`.

    Yields
    ------
    BulkFilingMetadata
        One record per ownership-form row found across all recognized members.

    Raises
    ------
    TypeError
        If *zip_path* is not a :class:`pathlib.Path`.
    SecBulkError
        If *zip_path* is not a valid ZIP file, or if a recognized member
        contains malformed JSON.
    """
    if not isinstance(zip_path, Path):
        raise TypeError(
            f"zip_path must be a pathlib.Path, got {type(zip_path).__name__!r}"
        )

    try:
        zf = zipfile.ZipFile(zip_path)  # noqa: SIM115 — we close it below
    except zipfile.BadZipFile as exc:
        raise SecBulkError(f"Not a valid ZIP archive: {zip_path.name}") from exc

    with zf:
        for info in zf.infolist():
            name = info.filename
            m_main = _RE_MAIN.match(name)
            m_cont = _RE_CONT.match(name)
            if m_main:
                cik_str = m_main.group(1)
            elif m_cont:
                cik_str = m_cont.group(1)
            else:
                continue  # stray member — ignore

            cik = edgar.normalize_cik(cik_str)

            with zf.open(name) as fp:
                try:
                    data = json.load(io.TextIOWrapper(fp, encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise SecBulkError(
                        f"Malformed JSON in member {name!r}"
                    ) from exc

            if m_main:
                rows = _extract_recent_rows(data)
            else:
                rows = _extract_top_level_rows(data)

            yield from _filter_ownership_rows(cik, rows)


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


def _extract_top_level_rows(data: object) -> dict[str, list[object]]:
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

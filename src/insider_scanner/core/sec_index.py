"""Pure parser for SEC EDGAR daily master-index files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from insider_scanner.core import edgar
from insider_scanner.core.sec_forms import TRANSACTION_FORM_TYPES as OWNERSHIP_FORMS

_ACCESSION_FILENAME = re.compile(r"([0-9]{10}-[0-9]{2}-[0-9]{6})\.txt", re.IGNORECASE)


def filing_accession(archive_path: str) -> str:
    """Return the accession shared by an issuer's and reporting owners' paths."""
    match = _ACCESSION_FILENAME.fullmatch(archive_path.rsplit("/", 1)[-1])
    if match is None:
        raise ValueError("filing archive path has no valid accession")
    return match.group(1)


@dataclass(frozen=True, slots=True)
class SecMasterIndexRow:
    """One validated ownership filing from a SEC daily master index."""

    cik: str
    company_name: str
    form_type: str
    filing_date: date
    archive_path: str


def parse_master_index(text: str) -> tuple[SecMasterIndexRow, ...]:
    """Parse validated ownership filings while preserving index order."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    rows: list[SecMasterIndexRow] = []
    seen_accessions: set[str] = set()

    for line in text.splitlines():
        fields = line.split("|")
        if len(fields) != 5:
            continue

        cik, company_name, form_type, filing_date_text, archive_path = (
            field.strip() for field in fields
        )
        if form_type not in OWNERSHIP_FORMS:
            continue
        if not company_name:
            continue

        try:
            normalized_cik = edgar.normalize_cik(cik)
            filing_date = date.fromisoformat(filing_date_text)
            edgar.build_filing_archive_url(archive_path)
            accession = filing_accession(archive_path)
        except (TypeError, ValueError):
            continue

        if accession in seen_accessions:
            continue

        seen_accessions.add(accession)
        rows.append(
            SecMasterIndexRow(
                cik=normalized_cik,
                company_name=company_name,
                form_type=form_type,
                filing_date=filing_date,
                archive_path=archive_path,
            )
        )

    return tuple(rows)

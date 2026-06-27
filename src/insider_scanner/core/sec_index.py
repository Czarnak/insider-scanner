"""Pure parser for SEC EDGAR daily master-index files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from insider_scanner.core import edgar


OWNERSHIP_FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})


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
    seen_archive_paths: set[str] = set()

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
        except (TypeError, ValueError):
            continue

        if archive_path in seen_archive_paths:
            continue

        seen_archive_paths.add(archive_path)
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

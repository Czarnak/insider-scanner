"""Reproducible generator for tests/fixtures/sec_submissions_bulk_small.zip.

Running this script regenerates the committed fixture archive from deterministic
hard-coded bytes so that the fixture can always be reconstructed identically,
regardless of local environment.

Members
-------
CIK0000320193.json
    Apple Inc. main submission file.  Contains ``filings.recent`` with three
    rows: one Form 4, one 8-K (non-ownership, filtered out), and one Form 4/A.
    Also carries a ``files`` entry that references the continuation archive.

CIK0000320193-submissions-001.json
    Apple Inc. continuation file.  Top-level parallel arrays.  Contains two
    rows: one Form 3 (ownership) and one 10-K (non-ownership, filtered out).
    CIK is inferred from the filename, not from the JSON body.

CIK0001318605.json
    Tesla, Inc. main submission file.  Contains ``filings.recent`` with a
    single Form 4 row.

metadata.txt
    Stray non-JSON member.  Must be silently ignored by the parser.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Member contents — exact bytes that the test suite asserts against
# ---------------------------------------------------------------------------

_APPLE_MAIN = (
    '{"cik": "320193", "name": "Apple Inc.", "tickers": ["AAPL"], '
    '"filings": {"recent": {'
    '"accessionNumber": ["0000320193-26-000061", "0000320193-26-000040", "0000320193-26-000035"], '
    '"filingDate": ["2026-06-13", "2026-04-10", "2026-03-01"], '
    '"form": ["4", "8-K", "4/A"], '
    '"primaryDocument": ["xslF345X05/form4.xml", "a8-k.htm", "xslF345X05/form4a.xml"]'
    "}, "
    '"files": [{"name": "CIK0000320193-submissions-001.json", "filingCount": 2, '
    '"filingFrom": "2024-02-15", "filingTo": "2024-05-01"}]}}'
)

_APPLE_CONT = (
    '{"accessionNumber": ["0000320193-24-000010", "0000320193-24-000005"], '
    '"filingDate": ["2024-05-01", "2024-02-15"], '
    '"form": ["3", "10-K"], '
    '"primaryDocument": ["xslF345X05/form3.xml", "aapl-20231231.htm"]}'
)

_TESLA_MAIN = (
    '{"cik": "1318605", "name": "Tesla, Inc.", "tickers": ["TSLA"], '
    '"filings": {"recent": {'
    '"accessionNumber": ["0001318605-26-000012"], '
    '"filingDate": ["2026-05-20"], '
    '"form": ["4"], '
    '"primaryDocument": ["xslF345X05/form4.xml"]'
    "}, "
    '"files": []}}'
)

_METADATA = "not a submissions file"

_MEMBERS: dict[str, str] = {
    "CIK0000320193.json": _APPLE_MAIN,
    "CIK0000320193-submissions-001.json": _APPLE_CONT,
    "CIK0001318605.json": _TESLA_MAIN,
    "metadata.txt": _METADATA,
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "sec_submissions_bulk_small.zip"


def main() -> None:
    """Write the fixture ZIP with deterministic, hard-coded member contents."""
    with zipfile.ZipFile(_FIXTURE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in _MEMBERS.items():
            zf.writestr(name, content)
    print(f"Written: {_FIXTURE_PATH}")
    for name in _MEMBERS:
        print(f"  + {name}")


if __name__ == "__main__":
    main()

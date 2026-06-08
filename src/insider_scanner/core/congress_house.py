"""House of Representatives financial disclosure index and PDF parsing.

Data comes from disclosures-clerk.house.gov:
- ZIP index: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{year}FD.zip
  Contains {year}FD.xml (structured index of all filings)
- PTR PDFs: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf

Pipeline:
  1. ensure_house_index(year) — download ZIP if missing, extract XML
  2. parse_house_index(year) — parse XML into filing metadata dicts
  3. fetch_ptr_pdf(doc_id, year) — download individual PTR PDF
  4. parse_ptr_pdf(pdf_bytes) — extract transactions from electronic PDFs
  5. scrape_house_trades(...) — full pipeline → list[CongressTrade]
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from insider_scanner.core.models import CongressTrade
from insider_scanner.utils.config import HOUSE_DISCLOSURES_DIR
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.parsing import parse_ptr_date as _parse_date_flexible

log = get_logger("congress_house")

BASE_URL = "https://disclosures-clerk.house.gov"
INDEX_ZIP_URL = BASE_URL + "/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF_URL = BASE_URL + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# Filing types in the House index
FILING_TYPE_PTR = "P"  # Periodic Transaction Report (actual trades)

# Transaction type mapping from PTR PDFs
_TX_TYPE_MAP = {
    "P": "Purchase",
    "p": "Purchase",
    "purchase": "Purchase",
    "S": "Sale",
    "s": "Sale",
    "sale": "Sale",
    "sale (full)": "Sale",
    "sale (partial)": "Sale",
    "E": "Exchange",
    "e": "Exchange",
    "exchange": "Exchange",
}

# Owner code mapping from PTR PDFs
_OWNER_MAP = {
    "SP": "Spouse",
    "DC": "Dependent Child",
    "JT": "Joint",
    "": "Self",
}

# Regex to extract ticker from asset descriptions like "Apple Inc (AAPL) [ST]"
_TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")

# Amount range patterns used in House disclosures
_AMOUNT_RANGES = [
    "$1,001 - $15,000",
    "$15,001 - $50,000",
    "$50,001 - $100,000",
    "$100,001 - $250,000",
    "$250,001 - $500,000",
    "$500,001 - $1,000,000",
    "$1,000,001 - $5,000,000",
    "$5,000,001 - $25,000,000",
    "$25,000,001 - $50,000,000",
    "Over $50,000,000",
]


# -----------------------------------------------------------------------
# Index management — ZIP download + extraction
# -----------------------------------------------------------------------


def _index_xml_path(year: int) -> Path:
    """Path where the extracted XML index lives."""
    return HOUSE_DISCLOSURES_DIR / f"{year}FD.xml"


def _index_txt_path(year: int) -> Path:
    """Path where the extracted TXT index lives."""
    return HOUSE_DISCLOSURES_DIR / f"{year}FD.txt"


def _pdf_cache_path(doc_id: str, year: int) -> Path:
    """Path where a cached PTR PDF lives."""
    pdf_dir = HOUSE_DISCLOSURES_DIR / str(year) / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    return pdf_dir / f"{doc_id}.pdf"


def ensure_house_index(year: int, *, force: bool = False) -> Path:
    """Ensure the House financial disclosure index for *year* is available.

    Downloads the ZIP from disclosures-clerk.house.gov and extracts the
    XML + TXT index files into ``data/house_disclosures/``.

    Parameters
    ----------
    year : int
        Filing year (e.g. 2026).
    force : bool
        If True, re-download even if already present.

    Returns
    -------
    Path
        Path to the extracted XML index file.

    Raises
    ------
    requests.HTTPError
        If the download fails (e.g. 404 for a year with no data yet).
    """
    xml_path = _index_xml_path(year)

    if xml_path.exists() and not force:
        log.debug("Index already exists: %s", xml_path)
        return xml_path

    url = INDEX_ZIP_URL.format(year=year)
    log.info("Downloading House index for %d from %s", year, url)

    HOUSE_DISCLOSURES_DIR.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            # Extract only the FD files (XML + TXT)
            if name.endswith("FD.xml") or name.endswith("FD.txt"):
                zf.extract(name, HOUSE_DISCLOSURES_DIR)
                log.info("Extracted %s", name)

    if not xml_path.exists():
        raise FileNotFoundError(f"ZIP did not contain expected {year}FD.xml")

    return xml_path


def refresh_all_indexes(*, years: list[int] | None = None) -> dict[int, Path]:
    """Re-download indexes for all specified years (force=True).

    Parameters
    ----------
    years : list of int or None
        Years to refresh. If None, refreshes 2008 through current year.

    Returns
    -------
    dict mapping year → XML path for successfully downloaded indexes.
    """
    if years is None:
        current_year = date.today().year
        years = list(range(2008, current_year + 1))

    results = {}
    for year in years:
        try:
            path = ensure_house_index(year, force=True)
            results[year] = path
        except Exception as exc:
            log.warning("Failed to refresh index for %d: %s", year, exc)

    return results


def refresh_current_year() -> Path | None:
    """Re-download only the current year's index."""
    year = date.today().year
    try:
        return ensure_house_index(year, force=True)
    except Exception as exc:
        log.warning("Failed to refresh current year index: %s", exc)
        return None


# -----------------------------------------------------------------------
# XML index parsing
# -----------------------------------------------------------------------


def parse_house_index(year: int) -> list[dict]:
    """Parse the House financial disclosure XML index for a given year.

    Returns a list of dicts, each with keys: prefix, last, first, suffix,
    filing_type, state_dst, year, filing_date, doc_id.
    """
    xml_path = _index_xml_path(year)
    if not xml_path.exists():
        log.warning("Index file not found: %s", xml_path)
        return []

    # Handle BOM and Windows line endings
    raw = xml_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    root = ET.fromstring(raw)
    filings = []

    for member in root.findall("Member"):
        prefix = (member.findtext("Prefix") or "").strip()
        last = (member.findtext("Last") or "").strip()
        first = (member.findtext("First") or "").strip()
        suffix = (member.findtext("Suffix") or "").strip()
        filing_type = (member.findtext("FilingType") or "").strip()
        state_dst = (member.findtext("StateDst") or "").strip()
        year_str = (member.findtext("Year") or "").strip()
        filing_date_str = (member.findtext("FilingDate") or "").strip()
        doc_id = (member.findtext("DocID") or "").strip()

        # Parse filing date (format: "1/15/2026")
        filing_date = None
        if filing_date_str:
            try:
                filing_date = datetime.strptime(filing_date_str, "%m/%d/%Y").date()
            except ValueError:
                log.debug("Unparseable filing date: %s", filing_date_str)

        filings.append(
            {
                "prefix": prefix,
                "last": last,
                "first": first,
                "suffix": suffix,
                "filing_type": filing_type,
                "state_dst": state_dst,
                "year": int(year_str) if year_str.isdigit() else year,
                "filing_date": filing_date,
                "doc_id": doc_id,
            }
        )

    log.info("Parsed %d filings from %d index", len(filings), year)
    return filings


def search_filings(
    year: int,
    *,
    name: str | None = None,
    filing_type: str = FILING_TYPE_PTR,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Search the House index for matching filings.

    Parameters
    ----------
    year : int
        The index year to search.
    name : str or None
        Official's name to filter by (matches "Last First" or "First Last").
        If None, returns all matching filings.
    filing_type : str
        Filing type code (default "P" for PTR).
    date_from, date_to : date or None
        Optional filing date range.

    Returns
    -------
    list of dicts matching the criteria.
    """
    filings = parse_house_index(year)
    results = []

    name_lower = name.lower().strip() if name else None

    for f in filings:
        # Filter by filing type
        if filing_type and f["filing_type"] != filing_type:
            continue

        # Filter by name
        if name_lower:
            last_first = f"{f['last']} {f['first']}".lower().strip()
            first_last = f"{f['first']} {f['last']}".lower().strip()
            official = (
                f"{f['prefix']} {f['first']} {f['last']} {f['suffix']}".lower().strip()
            )

            if not (
                name_lower in last_first
                or name_lower in first_last
                or name_lower in official
                or last_first in name_lower
                or first_last in name_lower
            ):
                continue

        # Filter by date range
        fd = f.get("filing_date")
        if fd:
            if date_from and fd < date_from:
                continue
            if date_to and fd > date_to:
                continue

        results.append(f)

    return results


# -----------------------------------------------------------------------
# PDF download
# -----------------------------------------------------------------------


def fetch_ptr_pdf(doc_id: str, year: int, *, force: bool = False) -> bytes:
    """Download a PTR PDF and return its raw bytes.

    Caches locally under data/house_disclosures/{year}/pdfs/{doc_id}.pdf.
    Returns cached version if present (unless force=True).

    Raises
    ------
    requests.HTTPError
        On download failure.
    """
    cache_path = _pdf_cache_path(doc_id, year)

    if cache_path.exists() and not force:
        log.debug("PDF cache hit: %s", cache_path)
        return cache_path.read_bytes()

    url = PTR_PDF_URL.format(year=year, doc_id=doc_id)
    log.info("Downloading PTR PDF: %s", url)

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    cache_path.write_bytes(resp.content)
    return resp.content


# -----------------------------------------------------------------------
# PDF parsing (pdfplumber — electronic filings only)
# -----------------------------------------------------------------------


def _extract_ticker(asset_description: str) -> str:
    """Try to extract a ticker symbol from an asset description.

    Looks for patterns like "(AAPL)", "(MSFT)", etc.
    """
    match = _TICKER_RE.search(asset_description)
    return match.group(1) if match else ""


def _normalize_tx_type(raw: str) -> str:
    """Normalize transaction type string to Purchase/Sale/Exchange/Other."""
    raw_stripped = raw.strip()
    return _TX_TYPE_MAP.get(
        raw_stripped, _TX_TYPE_MAP.get(raw_stripped.lower(), "Other")
    )


def _normalize_owner(raw: str) -> str:
    """Normalize owner code to Self/Spouse/Dependent Child/Joint."""
    raw_stripped = raw.strip().upper()
    return _OWNER_MAP.get(raw_stripped, raw_stripped or "Self")


def _is_scanned_pdf(pdf) -> bool:
    """Detect if a PDF is scanned (image-based) rather than electronic.

    Scanned PDFs have very little extractable text and no tables.
    """
    text = ""
    for page in pdf.pages[:2]:  # Check first 2 pages
        text += page.extract_text() or ""

    # If we can't extract meaningful text, it's probably scanned
    return len(text.strip()) < 50


def parse_ptr_pdf(pdf_bytes: bytes) -> list[dict]:
    """Extract transaction rows from an electronically-filed House PTR PDF.

    Returns a list of dicts with keys: owner, asset, tx_type, tx_date,
    notification_date, amount, cap_gains_over_200.

    Skips scanned PDFs (returns empty list).

    Requires pdfplumber.
    """
    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber not installed — pip install pdfplumber")
        return []

    transactions = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if _is_scanned_pdf(pdf):
                log.debug("Scanned PDF detected, skipping")
                return []

            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue

                    # Detect header row
                    header_idx = _find_header_row(table)
                    if header_idx is None:
                        continue

                    headers = [
                        (cell or "").strip().lower() for cell in table[header_idx]
                    ]
                    col_map = _map_columns(headers)

                    if not col_map.get("asset"):
                        continue  # Not a transaction table

                    # Parse data rows
                    for row in table[header_idx + 1 :]:
                        if not row or all(not (cell or "").strip() for cell in row):
                            continue

                        tx = _parse_table_row(row, col_map)
                        if tx and tx.get("asset"):
                            transactions.append(tx)

    except Exception as exc:
        log.warning("PDF parse error: %s", exc)
        return []

    return transactions


def _find_header_row(table: list[list]) -> int | None:
    """Find the header row index in a table by looking for key column names."""
    for i, row in enumerate(table):
        cells = [(cell or "").strip().lower() for cell in row]
        joined = " ".join(cells)
        if "asset" in joined and ("transaction" in joined or "amount" in joined):
            return i
    return None


def _map_columns(headers: list[str]) -> dict[str, int]:
    """Map column names to indices based on header keywords."""
    col_map: dict[str, int] = {}

    for i, h in enumerate(headers):
        h_lower = h.lower()

        if "owner" in h_lower:
            col_map["owner"] = i
        elif "asset" in h_lower and "asset" not in col_map:
            col_map["asset"] = i
        elif "type" in h_lower and "transaction" not in col_map:
            # "Transaction Type" or "Type"
            col_map["transaction"] = i
        elif "notification" in h_lower or "notified" in h_lower:
            col_map["notification_date"] = i
        elif (
            "date" in h_lower
            and "notification" not in h_lower
            and "date" not in col_map
        ):
            col_map["date"] = i
        elif "amount" in h_lower:
            col_map["amount"] = i
        elif "cap" in h_lower and "gain" in h_lower:
            col_map["cap_gains"] = i

    return col_map


def _parse_table_row(row: list, col_map: dict[str, int]) -> dict | None:
    """Parse a single table row into a transaction dict."""

    def _cell(key: str) -> str:
        idx = col_map.get(key)
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    asset = _cell("asset")
    if not asset:
        return None

    return {
        "owner": _cell("owner"),
        "asset": asset,
        "tx_type": _cell("transaction"),
        "tx_date": _cell("date"),
        "notification_date": _cell("notification_date"),
        "amount": _cell("amount"),
        "cap_gains_over_200": _cell("cap_gains"),
    }


# -----------------------------------------------------------------------
# Full pipeline: index → PDFs → CongressTrade records
# -----------------------------------------------------------------------


def scrape_house_trades(
    *,
    official_name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    progress_callback: callable | None = None,
) -> list[CongressTrade]:
    """Scrape House PTR filings and return parsed CongressTrade records.

    Parameters
    ----------
    official_name : str or None
        Name to filter by. If None, scans all PTR filings.
    date_from, date_to : date or None
        Filing date range. Determines which year indexes to download.
    progress_callback : callable or None
        Called with (current, total, message) for progress reporting.

    Returns
    -------
    list of CongressTrade
    """
    # Determine which years to scan
    years = _determine_years(date_from, date_to)

    all_trades: list[CongressTrade] = []
    all_filings: list[dict] = []

    # Step 1: Ensure indexes and collect matching filings
    for year in years:
        try:
            ensure_house_index(year)
        except Exception as exc:
            log.warning("Could not get index for %d: %s", year, exc)
            continue

        filings = search_filings(
            year,
            name=official_name,
            filing_type=FILING_TYPE_PTR,
            date_from=date_from,
            date_to=date_to,
        )
        all_filings.extend(filings)

    if not all_filings:
        log.info("No matching House PTR filings found")
        return []

    total = len(all_filings)
    log.info("Found %d matching PTR filings, fetching PDFs...", total)

    # Step 2: Fetch and parse each PDF
    for i, filing in enumerate(all_filings):
        doc_id = filing["doc_id"]
        year = filing["year"]

        if progress_callback:
            official = f"{filing['first']} {filing['last']}"
            progress_callback(i, total, f"Processing {official} ({doc_id})")

        try:
            pdf_bytes = fetch_ptr_pdf(doc_id, year)
            raw_transactions = parse_ptr_pdf(pdf_bytes)
        except Exception as exc:
            log.warning("Failed to process PDF %s/%s: %s", year, doc_id, exc)
            continue

        # Convert raw transactions to CongressTrade records
        official = f"{filing.get('prefix', '')} {filing['first']} {filing['last']} {filing.get('suffix', '')}".strip()
        source_url = PTR_PDF_URL.format(year=year, doc_id=doc_id)

        for tx in raw_transactions:
            asset_desc = tx.get("asset", "")
            ticker = _extract_ticker(asset_desc)
            amount_range = tx.get("amount", "")
            amount_low, amount_high = CongressTrade.parse_amount_range(amount_range)

            trade = CongressTrade(
                official_name=official,
                chamber="House",
                filing_date=filing.get("filing_date"),
                doc_id=doc_id,
                source_url=source_url,
                trade_date=_parse_date_flexible(tx.get("tx_date", "")),
                asset_description=asset_desc,
                ticker=ticker,
                trade_type=_normalize_tx_type(tx.get("tx_type", "")),
                owner=_normalize_owner(tx.get("owner", "")),
                amount_range=amount_range,
                amount_low=amount_low,
                amount_high=amount_high,
                comment=tx.get("cap_gains_over_200", ""),
                source="house",
            )
            all_trades.append(trade)

    if progress_callback:
        progress_callback(total, total, "Done")

    log.info(
        "Scraped %d trades from %d House PTR filings",
        len(all_trades),
        total,
    )
    return all_trades


def _determine_years(date_from: date | None, date_to: date | None) -> list[int]:
    """Determine which year indexes need to be fetched for the date range."""
    current_year = date.today().year

    if date_from and date_to:
        return list(range(date_from.year, date_to.year + 1))
    elif date_from:
        return list(range(date_from.year, current_year + 1))
    elif date_to:
        return list(range(date_to.year, date_to.year + 1))
    else:
        # Default: current year only
        return [current_year]

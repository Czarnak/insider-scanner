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
import zipfile
from collections.abc import Callable
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from insider_scanner.core.congress_common import extract_ticker as _extract_ticker
from insider_scanner.core.congress_common import normalize_owner as _normalize_owner
from insider_scanner.core.congress_common import normalize_tx_type as _normalize_tx_type
from insider_scanner.core.fetch_outcome import (
    FetchIssue,
    FetchResult,
    FetchStatus,
    as_fetch_result,
)
from insider_scanner.core.models import CongressTrade
from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.config import HOUSE_DISCLOSURES_DIR
from insider_scanner.utils.http import HttpSession
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.parsing import parse_ptr_date as _parse_date_flexible

log = get_logger("congress_house")

BASE_URL = "https://disclosures-clerk.house.gov"
INDEX_ZIP_URL = BASE_URL + "/public_disc/financial-pdfs/{year}FD.zip"
PTR_PDF_URL = BASE_URL + "/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# Filing types in the House index
FILING_TYPE_PTR = "P"  # Periodic Transaction Report (actual trades)

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
    return pdf_dir / f"{doc_id}.pdf"


def _house_session() -> HttpSession:
    return HttpSession(
        min_interval_seconds=0.25,
        user_agent="InsiderScanner/2.0 (House financial disclosures research)",
    )


def _download_bytes(url: str, session: HttpSession | None) -> bytes:
    with _house_session() if session is None else nullcontext(session) as http:
        response = http.get(url, timeout=30)
        try:
            return response.content
        finally:
            response.close()


def _validate_index(content: bytes) -> None:
    root = ET.fromstring(content)
    if root.tag != "FinancialDisclosure":
        raise ValueError("House index is not a financial disclosure document")


def _validate_pdf(content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise ValueError("House response is not a PDF")
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if not pdf.pages:
                raise ValueError("House PDF contains no pages")
    except Exception as exc:
        raise ValueError("House PDF could not be read") from exc


def ensure_house_index(
    year: int, *, force: bool = False, session: HttpSession | None = None
) -> Path:
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
        try:
            _validate_index(xml_path.read_bytes())
        except (OSError, ValueError, ET.ParseError) as exc:
            log.warning(
                "Invalid cached House index %s: %s; downloading again", xml_path, exc
            )
        else:
            log.debug("Index already exists: %s", xml_path)
            return xml_path

    url = INDEX_ZIP_URL.format(year=year)
    log.info("Downloading House index for %d from %s", year, url)

    HOUSE_DISCLOSURES_DIR.mkdir(parents=True, exist_ok=True)

    content = _download_bytes(url, session)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        xml_name = f"{year}FD.xml"
        txt_name = f"{year}FD.txt"
        if xml_name not in zf.namelist():
            raise FileNotFoundError(f"ZIP did not contain expected {xml_name}")
        xml_content = zf.read(xml_name)
        _validate_index(xml_content)
        # Publish only validated, explicitly named members. Existing valid files
        # survive download, validation and interrupted-write failures.
        atomic_write_bytes(xml_path, xml_content)
        if txt_name in zf.namelist():
            atomic_write_bytes(_index_txt_path(year), zf.read(txt_name))

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
        except Exception as exc:  # noqa: BLE001 - isolate a failed year from the refresh batch
            log.warning("Failed to refresh index for %d: %s", year, exc)

    return results


def refresh_current_year() -> Path | None:
    """Re-download only the current year's index."""
    year = date.today().year
    try:
        return ensure_house_index(year, force=True)
    except Exception as exc:  # noqa: BLE001 - an optional refresh must retain the existing index
        log.warning("Failed to refresh current year index: %s", exc)
        return None


# -----------------------------------------------------------------------
# XML index parsing
# -----------------------------------------------------------------------


def parse_house_index(year: int) -> FetchResult[dict]:
    """Parse the House financial disclosure XML index for a given year.

    Returns a list of dicts, each with keys: prefix, last, first, suffix,
    filing_type, state_dst, year, filing_date, doc_id.
    """
    xml_path = _index_xml_path(year)
    if not xml_path.exists():
        log.warning("Index file not found: %s", xml_path)
        return FetchResult(
            issues=[FetchIssue("index_missing", source="house", identifier=str(year))]
        )

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
    return FetchResult(filings)


def search_filings(
    year: int,
    *,
    name: str | None = None,
    filing_type: str = FILING_TYPE_PTR,
    date_from: date | None = None,
    date_to: date | None = None,
) -> FetchResult[dict]:
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
    filings = as_fetch_result(parse_house_index(year))
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

    return FetchResult(
        results,
        status=None if filings.complete else filings.status,
        issues=filings.issues,
    )


# -----------------------------------------------------------------------
# PDF download
# -----------------------------------------------------------------------


def _read_cached_pdf(cache_path: Path) -> bytes | None:
    """Return validated cached bytes, preserving their provenance for repairs."""
    try:
        content = cache_path.read_bytes()
        _validate_pdf(content)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning(
            "Invalid cached House PDF %s: %s; downloading again", cache_path, exc
        )
        return None
    log.debug("PDF cache hit: %s", cache_path)
    return content


def fetch_ptr_pdf(
    doc_id: str, year: int, *, force: bool = False, session: HttpSession | None = None
) -> bytes:
    """Download a PTR PDF and return its raw bytes.

    Caches locally under data/house_disclosures/{year}/pdfs/{doc_id}.pdf.
    Returns cached version if present (unless force=True).

    Raises
    ------
    requests.HTTPError
        On download failure.
    """
    cache_path = _pdf_cache_path(doc_id, year)

    if not force:
        content = _read_cached_pdf(cache_path)
        if content is not None:
            return content

    url = PTR_PDF_URL.format(year=year, doc_id=doc_id)
    log.info("Downloading PTR PDF: %s", url)

    content = _download_bytes(url, session)
    _validate_pdf(content)
    try:
        atomic_write_bytes(cache_path, content)
    except OSError as exc:
        log.warning(
            "House PDF cache write failed doc_id=%s reason=%s",
            doc_id,
            type(exc).__name__,
        )
    return content


# -----------------------------------------------------------------------
# PDF parsing (pdfplumber — electronic filings only)
# -----------------------------------------------------------------------


def _is_scanned_pdf(pdf) -> bool:
    """Detect if a PDF is scanned (image-based) rather than electronic.

    Scanned PDFs have very little extractable text and no tables.
    """
    text = ""
    for page in pdf.pages[:2]:  # Check first 2 pages
        text += page.extract_text() or ""

    # If we can't extract meaningful text, it's probably scanned
    return len(text.strip()) < 50


def parse_ptr_pdf(
    pdf_bytes: bytes, *, strict: bool = False, doc_id: str = ""
) -> FetchResult[dict]:
    """Extract transaction rows from an electronically-filed House PTR PDF.

    Returns a list of dicts with keys: owner, asset, tx_type, tx_date,
    notification_date, amount, cap_gains_over_200.

    Scanned PDFs are explicitly unsupported. A failed page retains rows from
    the other pages and reports an incomplete result, including in strict mode.

    Requires pdfplumber.
    """
    try:
        import pdfplumber
    except ImportError:
        log.error("pdfplumber not installed — pip install pdfplumber")
        return FetchResult(
            status=FetchStatus.TRANSIENT_FAILURE,
            issues=[
                FetchIssue("pdf_parser_unavailable", source="house", identifier=doc_id)
            ],
        )

    transactions = []
    issues: list[FetchIssue] = []
    recognized_table = False

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            try:
                scanned = _is_scanned_pdf(pdf)
            except Exception:  # noqa: BLE001 - damaged text must not discard intact page tables
                # A damaged text layer does not prevent extracting intact
                # tables from other pages. Each page below owns its outcome.
                scanned = False
            if scanned:
                log.info("Scanned House PDF unsupported doc_id=%s", doc_id)
                return FetchResult(
                    status=FetchStatus.UNSUPPORTED,
                    issues=[
                        FetchIssue(
                            "scanned_pdf",
                            source="house",
                            identifier=doc_id,
                            retryable=False,
                        )
                    ],
                )

            for page_number, page in enumerate(pdf.pages, start=1):
                try:
                    tables = page.extract_tables()
                    for table in tables:
                        if not table:
                            continue

                        header_idx = _find_header_row(table)
                        if header_idx is None:
                            continue
                        headers = [
                            (cell or "").strip().lower() for cell in table[header_idx]
                        ]
                        col_map = _map_columns(headers)
                        if not {"asset", "transaction", "date", "amount"}.issubset(
                            col_map
                        ):
                            raise ValueError(
                                "House transaction table headers are incomplete"
                            )
                        recognized_table = True
                        for row in table[header_idx + 1 :]:
                            if not row or all(not (cell or "").strip() for cell in row):
                                continue
                            tx = _parse_table_row(row, col_map)
                            if tx and tx.get("asset"):
                                transactions.append(tx)
                except Exception as exc:  # noqa: BLE001 - keep rows from intact PDF pages
                    log.warning(
                        "House PDF page failed doc_id=%s page=%d reason=%s",
                        doc_id,
                        page_number,
                        type(exc).__name__,
                    )
                    issues.append(
                        FetchIssue(
                            "pdf_page_parse_failed",
                            source="house",
                            identifier=f"{doc_id}:page:{page_number}",
                        )
                    )

    except Exception as exc:
        if strict:
            raise ValueError("House PDF transaction extraction failed") from exc
        log.warning(
            "House PDF parse failed doc_id=%s reason=%s", doc_id, type(exc).__name__
        )
        issues.append(FetchIssue("pdf_parse_failed", source="house", identifier=doc_id))

    if not recognized_table and not issues:
        issues.append(
            FetchIssue("transaction_table_missing", source="house", identifier=doc_id)
        )
    return FetchResult(
        transactions,
        issues=issues,
    )


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
    progress_callback: Callable[[int, int, str], None] | None = None,
    session: HttpSession | None = None,
) -> FetchResult[CongressTrade]:
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
    with _house_session() if session is None else nullcontext(session) as http:
        return _scrape_house_trades(
            official_name=official_name,
            date_from=date_from,
            date_to=date_to,
            progress_callback=progress_callback,
            session=http,
        )


def _scrape_house_trades(
    *,
    official_name: str | None,
    date_from: date | None,
    date_to: date | None,
    progress_callback: Callable[[int, int, str], None] | None,
    session: HttpSession,
) -> FetchResult[CongressTrade]:
    years = _determine_years(date_from, date_to)

    all_trades: list[CongressTrade] = []
    all_filings: list[dict] = []
    issues: list[FetchIssue] = []

    # Step 1: Ensure indexes and collect matching filings
    for year in years:
        try:
            ensure_house_index(year, session=session)
            filings = search_filings(
                year,
                name=official_name,
                filing_type=FILING_TYPE_PTR,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:  # noqa: BLE001 - isolate a failed year from the scan
            log.warning(
                "House index failed year=%d reason=%s", year, type(exc).__name__
            )
            issues.append(
                FetchIssue("index_failed", source="house", identifier=str(year))
            )
            continue
        all_filings.extend(filings)
        if isinstance(filings, FetchResult):
            issues.extend(filings.issues)

    if not all_filings:
        log.info("No matching House PTR filings found")
        return FetchResult(
            status=FetchStatus.TRANSIENT_FAILURE if issues else None,
            issues=issues,
        )

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
            cached_pdf = _read_cached_pdf(_pdf_cache_path(doc_id, year))
            pdf_bytes = (
                cached_pdf
                if cached_pdf is not None
                else fetch_ptr_pdf(doc_id, year, force=True, session=session)
            )
            try:
                raw_transactions = parse_ptr_pdf(pdf_bytes, strict=True, doc_id=doc_id)
            except ValueError:
                if cached_pdf is None:
                    raise
                raw_transactions = FetchResult(
                    issues=[
                        FetchIssue(
                            "pdf_parse_failed", source="house", identifier=doc_id
                        )
                    ]
                )
            if (
                cached_pdf is not None
                and isinstance(raw_transactions, FetchResult)
                and any(issue.retryable for issue in raw_transactions.issues)
            ):
                log.warning(
                    "Could not parse cached House PDF %s/%s; downloading once",
                    year,
                    doc_id,
                )
                cached_transactions = raw_transactions
                try:
                    pdf_bytes = fetch_ptr_pdf(doc_id, year, force=True, session=session)
                    raw_transactions = parse_ptr_pdf(
                        pdf_bytes, strict=True, doc_id=doc_id
                    )
                except Exception as exc:  # noqa: BLE001 - a failed repair must retain cached partial rows
                    log.warning(
                        "House PDF repair failed year=%s doc_id=%s reason=%s",
                        year,
                        doc_id,
                        type(exc).__name__,
                    )
                    raw_transactions = FetchResult(
                        cached_transactions,
                        issues=[
                            *cached_transactions.issues,
                            FetchIssue(
                                "pdf_repair_failed", source="house", identifier=doc_id
                            ),
                        ],
                    )
        except Exception as exc:  # noqa: BLE001 - isolate a failed filing from the scan
            log.warning(
                "House PDF failed year=%s doc_id=%s reason=%s",
                year,
                doc_id,
                type(exc).__name__,
            )
            issues.append(FetchIssue("pdf_failed", source="house", identifier=doc_id))
            continue
        if isinstance(raw_transactions, FetchResult):
            issues.extend(raw_transactions.issues)

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
    return FetchResult(
        all_trades,
        status=(
            FetchStatus.UNSUPPORTED
            if issues
            and not all_trades
            and all(not issue.retryable for issue in issues)
            else None
        ),
        issues=issues,
    )


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

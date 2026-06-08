"""Senate financial disclosure scraper via efdsearch.senate.gov.

The Senate's Electronic Financial Disclosure (EFD) system requires a
specific session flow:
  1. GET  /search/           → landing page, extract CSRF token
  2. POST /search/home/      → accept prohibition agreement
  3. POST /search/report/data/ → search for filings (JSON API)
  4. GET  /search/view/ptr/{uuid}/ → individual PTR page (HTML table)

Pipeline:
  create_efd_session() → search_senate_filings() → parse_ptr_page() →
  scrape_senate_trades() → list[CongressTrade]
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from insider_scanner.core.models import CongressTrade
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.parsing import parse_ptr_date as _parse_date

log = get_logger("congress_senate")

BASE_URL = "https://efdsearch.senate.gov"
SEARCH_LANDING = BASE_URL + "/search/"
SEARCH_HOME = BASE_URL + "/search/home/"
REPORT_DATA = BASE_URL + "/search/report/data/"

# Report type codes for the EFD search API
REPORT_TYPE_PTR = 11  # Periodic Transaction Report

# Filter type codes
FILTER_TYPE_SENATOR = 1

# Rate limit: be polite to the Senate servers
_MIN_REQUEST_INTERVAL = 1.0  # seconds between requests
_last_request_time: float = 0.0

# Regex for extracting PTR UUID links from search results
_PTR_LINK_RE = re.compile(r"/search/view/ptr/([a-f0-9-]+)/")
_PAPER_LINK_RE = re.compile(r"/search/view/paper/")

# Regex to extract ticker from asset descriptions
_TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)")

# Transaction type normalization
_TX_TYPE_MAP = {
    "purchase": "Purchase",
    "sale": "Sale",
    "sale (full)": "Sale",
    "sale (partial)": "Sale",
    "exchange": "Exchange",
}


def _rate_limit() -> None:
    """Enforce rate limiting between requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


# -----------------------------------------------------------------------
# Session management
# -----------------------------------------------------------------------


class EFDSession:
    """Manages an authenticated session with efdsearch.senate.gov.

    Handles CSRF token extraction, prohibition agreement acceptance,
    and maintains session cookies for subsequent API calls.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "InsiderScanner/0.1 (research tool)",
                "Origin": BASE_URL,
            }
        )
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    def authenticate(self) -> None:
        """Complete the EFD authentication flow.

        1. GET landing page → extract CSRF middleware token
        2. POST agreement acceptance → establish session

        Raises
        ------
        ConnectionError
            If authentication fails.
        """
        _rate_limit()

        # Step 1: GET the search landing page
        log.info("Fetching EFD landing page for CSRF token...")
        resp = self.session.get(SEARCH_LANDING, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if not csrf_input:
            raise ConnectionError("Could not find CSRF token on EFD landing page")
        csrf_token = csrf_input["value"]

        _rate_limit()

        # Step 2: POST the prohibition agreement
        log.info("Accepting EFD prohibition agreement...")
        self.session.headers.update(
            {
                "Referer": SEARCH_HOME,
            }
        )
        resp = self.session.post(
            SEARCH_HOME,
            data={
                "prohibition_agreement": "1",
                "csrfmiddlewaretoken": csrf_token,
            },
            timeout=15,
        )
        resp.raise_for_status()

        # Update headers with the CSRF token from cookies
        cookies = self.session.cookies.get_dict()
        csrf_cookie = cookies.get("csrftoken", "")
        if csrf_cookie:
            self.session.headers.update(
                {
                    "X-CSRFToken": csrf_cookie,
                    "Referer": SEARCH_LANDING,
                }
            )

        self._authenticated = True
        log.info("EFD session authenticated successfully")

    def search(
        self,
        *,
        first_name: str = "",
        last_name: str = "",
        report_types: list[int] | None = None,
        filter_types: list[int] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        start: int = 0,
        length: int = 100,
    ) -> dict:
        """Search the EFD report data API.

        Parameters
        ----------
        first_name, last_name : str
            Senator name filters.
        report_types : list of int
            Report type codes (default: [11] = PTR).
        filter_types : list of int
            Filer type codes (default: [1] = Senator).
        date_from, date_to : date or None
            Filing date range.
        start, length : int
            Pagination parameters.

        Returns
        -------
        dict
            JSON response with keys: result, recordsTotal,
            recordsFiltered, data (list of lists).

        Raises
        ------
        ConnectionError
            If not authenticated.
        requests.HTTPError
            On request failure.
        """
        if not self._authenticated:
            raise ConnectionError(
                "EFD session not authenticated. Call authenticate() first."
            )

        if report_types is None:
            report_types = [REPORT_TYPE_PTR]
        if filter_types is None:
            filter_types = [FILTER_TYPE_SENATOR]

        form_data: dict = {
            "start": str(start),
            "length": str(length),
            "report_types": str(report_types),
            "filter_types": str(filter_types),
        }

        if first_name:
            form_data["first_name"] = first_name
        if last_name:
            form_data["last_name"] = last_name
        if date_from:
            form_data["submitted_start_date"] = (
                date_from.strftime("%m/%d/%Y") + " 00:00:00"
            )
        if date_to:
            form_data["submitted_end_date"] = date_to.strftime("%m/%d/%Y") + " 00:00:00"

        _rate_limit()
        log.info(
            "Searching EFD: last=%s first=%s types=%s",
            last_name,
            first_name,
            report_types,
        )

        resp = self.session.post(REPORT_DATA, data=form_data, timeout=30)
        resp.raise_for_status()

        return resp.json()

    def fetch_page(self, url: str) -> str:
        """Fetch a page within the EFD session.

        Returns the HTML text of the page.
        """
        if not url.startswith("http"):
            url = BASE_URL + url

        _rate_limit()
        log.debug("Fetching EFD page: %s", url)
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text


def create_efd_session() -> EFDSession:
    """Create and authenticate an EFD session.

    Returns
    -------
    EFDSession
        An authenticated session ready for searching.
    """
    session = EFDSession()
    session.authenticate()
    return session


# -----------------------------------------------------------------------
# Search result parsing
# -----------------------------------------------------------------------


def parse_search_results(data: dict) -> list[dict]:
    """Parse the JSON response from the EFD search API.

    Each result row is: [first_name, last_name, filer_type, report_html, date]

    Returns a list of dicts with: first_name, last_name, filer_type,
    report_title, report_url, report_uuid, filing_date, is_paper.
    """
    results = []

    for row in data.get("data", []):
        if len(row) < 5:
            continue

        first_name = row[0].strip()
        last_name = row[1].strip()
        filer_type = row[2].strip()
        report_html = row[3]
        date_str = row[4].strip()

        # Parse the report link from HTML
        soup = BeautifulSoup(report_html, "html.parser")
        link = soup.find("a")
        if not link:
            continue

        href = link.get("href", "")
        title = link.get_text(strip=True)

        # Check if it's a paper filing (scanned PDF — skip these)
        is_paper = bool(_PAPER_LINK_RE.search(href))

        # Extract UUID from PTR link
        uuid_match = _PTR_LINK_RE.search(href)
        report_uuid = uuid_match.group(1) if uuid_match else ""

        # Parse filing date
        filing_date = None
        if date_str:
            try:
                filing_date = datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError:
                log.debug("Unparseable date: %s", date_str)

        results.append(
            {
                "first_name": first_name,
                "last_name": last_name,
                "filer_type": filer_type,
                "report_title": title,
                "report_url": href,
                "report_uuid": report_uuid,
                "filing_date": filing_date,
                "is_paper": is_paper,
            }
        )

    return results


# -----------------------------------------------------------------------
# PTR page parsing
# -----------------------------------------------------------------------


def parse_ptr_page(html: str) -> list[dict]:
    """Extract transactions from a Senate PTR HTML page.

    The page contains a table with columns:
    #, Transaction Date, Owner, Ticker, Asset Name, Asset Type,
    Type, Amount, Comment

    Returns a list of dicts with these fields normalized.
    """
    soup = BeautifulSoup(html, "html.parser")
    transactions = []

    # Find the transaction table — it's typically inside a div with
    # class "table-responsive" or a <table> with specific headers
    table = _find_transaction_table(soup)
    if not table:
        log.debug("No transaction table found on PTR page")
        return []

    # Parse header row
    headers = []
    thead = table.find("thead")
    if thead:
        for th in thead.find_all("th"):
            headers.append(th.get_text(strip=True).lower())
    else:
        # Try first row as header
        first_row = table.find("tr")
        if first_row:
            for cell in first_row.find_all(["th", "td"]):
                headers.append(cell.get_text(strip=True).lower())

    col_map = _map_senate_columns(headers)

    # Parse data rows
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue

        tx = _parse_senate_row(cells, col_map)
        if tx:
            transactions.append(tx)

    return transactions


def _find_transaction_table(soup: BeautifulSoup):
    """Find the transaction table on a Senate PTR page."""
    # Look for tables that have transaction-related headers
    for table in soup.find_all("table"):
        text = table.get_text(separator=" ").lower()
        if "asset" in text and (
            "transaction" in text or "amount" in text or "type" in text
        ):
            return table

    # Fallback: look in table-responsive div
    responsive = soup.find("div", class_="table-responsive")
    if responsive:
        return responsive.find("table")

    return None


def _map_senate_columns(headers: list[str]) -> dict[str, int]:
    """Map Senate PTR column names to indices."""
    col_map: dict[str, int] = {}

    for i, h in enumerate(headers):
        h = h.strip()
        if h == "#" or h == "id":
            col_map["id"] = i
        elif "transaction" in h and "date" in h:
            col_map["tx_date"] = i
        elif h == "owner":
            col_map["owner"] = i
        elif h == "ticker":
            col_map["ticker"] = i
        elif "asset" in h and "name" in h:
            col_map["asset_name"] = i
        elif "asset" in h and "type" in h:
            col_map["asset_type"] = i
        elif h == "type":
            col_map["type"] = i
        elif h == "amount":
            col_map["amount"] = i
        elif h == "comment":
            col_map["comment"] = i

    return col_map


def _parse_senate_row(cells: list, col_map: dict[str, int]) -> dict | None:
    """Parse a single transaction row from a Senate PTR table."""

    def _cell(key: str) -> str:
        idx = col_map.get(key)
        if idx is None or idx >= len(cells):
            return ""
        return cells[idx].get_text(strip=True)

    asset_name = _cell("asset_name")
    if not asset_name:
        return None

    return {
        "tx_date": _cell("tx_date"),
        "owner": _cell("owner"),
        "ticker": _cell("ticker"),
        "asset_name": asset_name,
        "asset_type": _cell("asset_type"),
        "tx_type": _cell("type"),
        "amount": _cell("amount"),
        "comment": _cell("comment"),
    }


# -----------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------


def _split_name(full_name: str) -> tuple[str, str]:
    """Split an official name into (first_name, last_name) for search.

    Handles formats like:
    - "Pelosi Nancy" → ("Nancy", "Pelosi")
    - "Nancy Pelosi" → ("Nancy", "Pelosi")
    - "John D. Booker" → ("John D.", "Booker")
    - "Booker, Cory" → ("Cory", "Booker")
    """
    name = full_name.strip()

    # Handle "Last, First" format
    if "," in name:
        parts = name.split(",", 1)
        return parts[1].strip(), parts[0].strip()

    parts = name.split()
    if len(parts) < 2:
        return "", name

    # Heuristic: if the first word is all-caps or starts with a title,
    # assume "Last First" format
    # Otherwise assume "First Last" format (more common in our data)
    # Since our congress_members.json uses "Last First", we try both
    return parts[0], " ".join(parts[1:])


def _normalize_tx_type(raw: str) -> str:
    """Normalize transaction type string."""
    return _TX_TYPE_MAP.get(raw.strip().lower(), "Other")


def _extract_ticker(asset_description: str) -> str:
    """Extract ticker from asset description."""
    match = _TICKER_RE.search(asset_description)
    return match.group(1) if match else ""


# -----------------------------------------------------------------------
# Full pipeline
# -----------------------------------------------------------------------


def search_senate_filings(
    session: EFDSession,
    *,
    first_name: str = "",
    last_name: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Search for Senate PTR filings and return parsed results.

    Handles pagination automatically (up to 1000 results).
    Filters out paper filings (scanned PDFs).
    """
    all_results: list[dict] = []
    start = 0
    page_size = 100

    while True:
        data = session.search(
            first_name=first_name,
            last_name=last_name,
            report_types=[REPORT_TYPE_PTR],
            date_from=date_from,
            date_to=date_to,
            start=start,
            length=page_size,
        )

        if data.get("result") != "ok":
            log.warning("EFD search returned non-ok: %s", data.get("result"))
            break

        results = parse_search_results(data)
        if not results:
            break

        all_results.extend(results)

        total = data.get("recordsFiltered", 0)
        start += page_size

        if start >= total or start >= 1000:
            break

    # Filter out paper filings
    electronic = [r for r in all_results if not r["is_paper"]]
    log.info(
        "Found %d Senate PTR filings (%d electronic, %d paper)",
        len(all_results),
        len(electronic),
        len(all_results) - len(electronic),
    )
    return electronic


def scrape_senate_trades(
    *,
    official_name: str | None = None,
    first_name: str = "",
    last_name: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    session: EFDSession | None = None,
    progress_callback: callable | None = None,
) -> list[CongressTrade]:
    """Scrape Senate PTR filings and return CongressTrade records.

    Parameters
    ----------
    official_name : str or None
        Full name to split into first/last. If provided, overrides
        first_name and last_name parameters.
    first_name, last_name : str
        Direct name parameters for the search.
    date_from, date_to : date or None
        Filing date range.
    session : EFDSession or None
        Reuse an existing session, or create a new one.
    progress_callback : callable or None
        Called with (current, total, message) for progress reporting.

    Returns
    -------
    list of CongressTrade
    """
    # Split name if needed
    if official_name:
        first_name, last_name = _split_name(official_name)

    # Create session if not provided
    if session is None:
        session = create_efd_session()

    # Search for filings
    filings = search_senate_filings(
        session,
        first_name=first_name,
        last_name=last_name,
        date_from=date_from,
        date_to=date_to,
    )

    if not filings:
        log.info("No Senate PTR filings found")
        return []

    total = len(filings)
    log.info("Processing %d Senate PTR filings...", total)

    all_trades: list[CongressTrade] = []

    for i, filing in enumerate(filings):
        official = f"{filing['first_name']} {filing['last_name']}".strip()
        ptr_url = filing["report_url"]

        if progress_callback:
            progress_callback(i, total, f"Processing {official} PTR")

        if not filing["report_uuid"]:
            log.debug("Skipping filing without UUID: %s", filing["report_title"])
            continue

        try:
            html = session.fetch_page(ptr_url)
            raw_transactions = parse_ptr_page(html)
        except Exception as exc:
            log.warning("Failed to fetch/parse PTR %s: %s", ptr_url, exc)
            continue

        source_url = BASE_URL + ptr_url if not ptr_url.startswith("http") else ptr_url

        for tx in raw_transactions:
            ticker = tx.get("ticker", "")
            if not ticker or ticker == "--":
                ticker = _extract_ticker(tx.get("asset_name", ""))

            amount_range = tx.get("amount", "")
            amount_low, amount_high = CongressTrade.parse_amount_range(amount_range)

            trade = CongressTrade(
                official_name=official,
                chamber="Senate",
                filing_date=filing.get("filing_date"),
                doc_id=filing["report_uuid"],
                source_url=source_url,
                trade_date=_parse_date(tx.get("tx_date", "")),
                asset_description=tx.get("asset_name", ""),
                ticker=ticker,
                trade_type=_normalize_tx_type(tx.get("tx_type", "")),
                owner=tx.get("owner", "Self") or "Self",
                amount_range=amount_range,
                amount_low=amount_low,
                amount_high=amount_high,
                comment=tx.get("comment", ""),
                source="senate",
            )
            all_trades.append(trade)

    if progress_callback:
        progress_callback(total, total, "Done")

    log.info(
        "Scraped %d trades from %d Senate PTR filings",
        len(all_trades),
        total,
    )
    return all_trades

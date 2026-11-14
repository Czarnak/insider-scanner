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
from collections.abc import Callable
from datetime import date, datetime
from typing import Self
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from insider_scanner.core.congress_common import extract_ticker as _extract_ticker
from insider_scanner.core.congress_common import normalize_tx_type as _normalize_tx_type
from insider_scanner.core.congress_identity import (
    MemberIdentity,
    identity_for,
    name_matches,
)
from insider_scanner.core.fetch_outcome import (
    FetchIssue,
    FetchResult,
    FetchStatus,
    as_fetch_result,
)
from insider_scanner.core.models import CongressTrade
from insider_scanner.core.sec_fair_access import RateLimiter
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.parsing import parse_ptr_date as _parse_date

log = get_logger("congress_senate")

BASE_URL = "https://efdsearch.senate.gov"
SEARCH_LANDING = BASE_URL + "/search/"
SEARCH_HOME = BASE_URL + "/search/home/"
REPORT_DATA = BASE_URL + "/search/report/data/"


def _create_browser_session():
    """Create an HTTP session with a browser-compatible transport fingerprint."""
    return curl_requests.Session(
        impersonate="chrome",
        headers={"Origin": BASE_URL},
    )


# Report type codes for the EFD search API
REPORT_TYPE_PTR = 11  # Periodic Transaction Report

# Filter type codes
FILTER_TYPE_SENATOR = 1

# Rate limit: be polite to the Senate servers
_SENATE_RATE_LIMITER = RateLimiter(min_interval_seconds=1.0)

# Regex for extracting PTR UUID links from search results
_PTR_LINK_RE = re.compile(r"/search/view/ptr/([a-f0-9-]+)/")
_PAPER_LINK_RE = re.compile(r"/search/view/paper/")


# -----------------------------------------------------------------------
# Session management
# -----------------------------------------------------------------------


class EFDSession:
    """Manages an authenticated session with efdsearch.senate.gov.

    Handles CSRF token extraction, prohibition agreement acceptance,
    and maintains session cookies for subsequent API calls.
    """

    def __init__(self, *, rate_limiter: RateLimiter | None = None):
        self.rate_limiter = (
            rate_limiter if rate_limiter is not None else _SENATE_RATE_LIMITER
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "InsiderScanner/0.1 (research tool)",
                "Origin": BASE_URL,
            }
        )
        self._authenticated = False

    def close(self) -> None:
        """Close this session's transport and discard authentication state."""
        self._authenticated = False
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

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
        self._authenticated = False
        self.rate_limiter.acquire()

        # Step 1: GET the search landing page
        log.info("Fetching EFD landing page for CSRF token...")
        resp = self.session.get(SEARCH_LANDING, timeout=15)
        if resp.status_code == 403:
            log.warning(
                "EFD rejected the standard HTTP transport; retrying with "
                "browser impersonation"
            )
            resp.close()
            self.session.close()
            self.session = _create_browser_session()
            self.rate_limiter.acquire()
            resp = self.session.get(SEARCH_LANDING, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        if not csrf_input:
            raise ConnectionError("Could not find CSRF token on EFD landing page")
        csrf_token = csrf_input["value"]

        self.rate_limiter.acquire()

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

    def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: int,
        data: dict | None = None,
    ) -> requests.Response:
        """Retry a request once after a detected return to the agreement."""
        for attempt in range(2):
            self.rate_limiter.acquire()
            resp = self.session.request(method, url, data=data, timeout=timeout)
            try:
                resp.raise_for_status()
                path = urlsplit(resp.url).path.rstrip("/")
                agreement = path in {"/search", "/search/home"}
                if not agreement and "prohibition_agreement" in resp.text:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    agreement = (
                        soup.find("input", {"name": "prohibition_agreement"})
                        is not None
                    )
                if not agreement:
                    return resp
            except Exception:
                resp.close()
                raise
            resp.close()
            self._authenticated = False
            if attempt:
                break
            log.info("EFD returned the agreement; re-authenticating once")
            self.authenticate()
        raise ConnectionError("EFD returned the agreement after re-authentication")

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
            # The service contract includes the entire final filing date.
            form_data["submitted_end_date"] = date_to.strftime("%m/%d/%Y") + " 23:59:59"

        log.info(
            "Searching EFD: last=%s first=%s types=%s",
            last_name,
            first_name,
            report_types,
        )

        resp = self._request("POST", REPORT_DATA, data=form_data, timeout=30)
        try:
            return resp.json()
        finally:
            resp.close()

    def fetch_page(self, url: str) -> str:
        """Fetch a page within the EFD session.

        Returns the HTML text of the page.
        """
        if not url.startswith("http"):
            url = BASE_URL + url

        log.debug("Fetching EFD page: %s", url)
        resp = self._request("GET", url, timeout=15)
        try:
            return resp.text
        finally:
            resp.close()


def create_efd_session() -> EFDSession:
    """Create and authenticate an EFD session.

    Returns
    -------
    EFDSession
        An authenticated session ready for searching.
    """
    session = EFDSession()
    try:
        session.authenticate()
    except BaseException:
        session.close()
        raise
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
        if not isinstance(href, str):
            continue
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


def parse_ptr_page(html: str) -> FetchResult[dict]:
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
        if "no transactions to display" in soup.get_text(" ", strip=True).lower():
            return FetchResult()
        log.warning("No transaction table found on Senate PTR page")
        return FetchResult(issues=[FetchIssue("ptr_layout", source="senate")])

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
    if not {"tx_date", "asset_name", "type", "amount"}.issubset(col_map):
        return FetchResult(issues=[FetchIssue("ptr_headers", source="senate")])

    # Parse data rows
    issues: list[FetchIssue] = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue

        tx = _parse_senate_row(cells, col_map)
        if tx:
            transactions.append(tx)
        elif any(cell.get_text(strip=True) for cell in cells):
            issues.append(FetchIssue("ptr_row", source="senate"))

    return FetchResult(transactions, issues=issues)


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
    """Resolve structured seed fields, with a conservative full-name fallback."""
    member = identity_for(full_name)
    return member.first_name, member.last_name


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
) -> FetchResult[dict]:
    """Search for Senate PTR filings and return parsed results.

    Handles pagination automatically (up to 1000 results).
    Filters out paper filings (scanned PDFs).
    """
    all_results: list[dict] = []
    issues: list[FetchIssue] = []
    start = 0
    page_size = 100

    while True:
        try:
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
                raise ValueError("search returned non-ok response")
            rows = data["data"]
            total = int(data["recordsFiltered"])
            if not isinstance(rows, list) or total < 0:
                raise ValueError("invalid search result metadata")
            results = parse_search_results(data)
        except Exception as exc:  # noqa: BLE001 - retain previously fetched pages
            log.warning(
                "Senate search failed offset=%d reason=%s", start, type(exc).__name__
            )
            issues.append(
                FetchIssue("search_failed", source="senate", identifier=str(start))
            )
            break

        if len(results) != len(rows):
            issues.append(
                FetchIssue("search_rows", source="senate", identifier=str(start))
            )
        if not rows:
            if start < total:
                issues.append(
                    FetchIssue(
                        "search_incomplete", source="senate", identifier=str(start)
                    )
                )
            break

        all_results.extend(results)
        start += len(rows)
        if start >= total:
            break
        if start >= 1000:
            issues.append(
                FetchIssue("result_limit", source="senate", identifier="1000")
            )
            break

    # Filter out paper filings
    electronic = [r for r in all_results if not r["is_paper"]]
    issues.extend(
        FetchIssue(
            "paper_filing", source="senate", identifier=r["report_url"], retryable=False
        )
        for r in all_results
        if r["is_paper"]
    )
    log.info(
        "Found %d Senate PTR filings (%d electronic, %d paper)",
        len(all_results),
        len(electronic),
        len(all_results) - len(electronic),
    )
    status = (
        FetchStatus.UNSUPPORTED
        if issues and not electronic and all(not issue.retryable for issue in issues)
        else None
    )
    return FetchResult(electronic, issues=issues, status=status)


def scrape_senate_trades(
    *,
    official_name: str | None = None,
    member: MemberIdentity | dict | None = None,
    first_name: str = "",
    last_name: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    session: EFDSession | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> FetchResult[CongressTrade]:
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
    # Prefer structured fields over any display-name parsing. The remote first
    # name search is strict, so filter the returned surname filings locally.
    requested_member = member if member is not None else official_name
    query = identity_for(requested_member) if requested_member is not None else None
    if query is not None:
        first_name, last_name = query.first_name, query.last_name

    # The caller retains ownership of an explicitly supplied session.
    owns_session = session is None
    if session is None:
        try:
            session = create_efd_session()
        except Exception as exc:  # noqa: BLE001 - expose authentication failures
            log.warning("Senate authentication failed reason=%s", type(exc).__name__)
            return FetchResult(
                issues=[FetchIssue("authentication_failed", source="senate")]
            )

    try:
        # Search for filings
        filings = as_fetch_result(
            search_senate_filings(
                session,
                first_name="" if query is not None else first_name,
                last_name=last_name,
                date_from=date_from,
                date_to=date_to,
            )
        )
        issues = list(filings.issues)

        if not filings:
            log.info("No Senate PTR filings found")
            status: FetchStatus | None = (
                FetchStatus.UNSUPPORTED
                if issues and all(not issue.retryable for issue in issues)
                else filings.status
            )
            return FetchResult(status=status, issues=issues)

        total = len(filings)
        log.info("Processing %d Senate PTR filings...", total)

        all_trades: list[CongressTrade] = []

        for i, filing in enumerate(filings):
            if query is not None and not name_matches(query, filing):
                continue
            official = f"{filing['first_name']} {filing['last_name']}".strip()
            ptr_url = filing["report_url"]

            if progress_callback:
                progress_callback(i, total, f"Processing {official} PTR")

            if not filing["report_uuid"]:
                log.debug("Skipping filing without UUID: %s", filing["report_title"])
                issues.append(
                    FetchIssue("missing_uuid", source="senate", identifier=ptr_url)
                )
                continue

            try:
                html = session.fetch_page(ptr_url)
                raw_transactions = as_fetch_result(parse_ptr_page(html))
            except Exception as exc:  # noqa: BLE001 - isolate a failed filing from the scan
                log.warning(
                    "Senate PTR failed id=%s reason=%s",
                    filing["report_uuid"],
                    type(exc).__name__,
                )
                issues.append(
                    FetchIssue(
                        "ptr_failed", source="senate", identifier=filing["report_uuid"]
                    )
                )
                continue
            issues.extend(
                FetchIssue(
                    issue.reason,
                    source="senate",
                    identifier=filing["report_uuid"],
                    retryable=issue.retryable,
                )
                for issue in raw_transactions.issues
            )

            source_url = (
                BASE_URL + ptr_url if not ptr_url.startswith("http") else ptr_url
            )

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
        status = (
            FetchStatus.UNSUPPORTED
            if issues
            and not all_trades
            and all(not issue.retryable for issue in issues)
            else None
        )
        return FetchResult(all_trades, issues=issues, status=status)
    finally:
        if owns_session:
            session.close()

"""SEC EDGAR CIK resolver and Form 4 filing lookup.

Compliance: Uses proper User-Agent header and rate-limits to 10 req/s
as required by https://www.sec.gov/os/accessing-edgar-data.
"""

from __future__ import annotations

from datetime import date
import json

from insider_scanner.core.models import InsiderTrade
from insider_scanner.utils.config import EDGAR_CACHE_DIR
from insider_scanner.utils.http import fetch_url
from insider_scanner.utils.logging import get_logger

log = get_logger("edgar")

EDGAR_COMPANY_SEARCH = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt={start}&enddt={end}&forms=4"
EDGAR_CIK_LOOKUP = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=4"
EDGAR_FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index?q={query}&forms=4&dateRange=custom&startdt={start}&enddt={end}"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_FILING_BASE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=40"
EDGAR_FILING_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count={count}"
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives"
SEC_DAILY_INDEX_BASE_URL = f"{SEC_ARCHIVES_BASE_URL}/edgar/daily-index"


def normalize_cik(cik: str | int) -> str:
    """Return a SEC CIK as a zero-padded 10-digit string."""
    if isinstance(cik, bool) or not isinstance(cik, (str, int)):
        raise TypeError("cik must be a string or integer")

    cik_text = str(cik)
    if (
        not cik_text
        or not cik_text.isascii()
        or not cik_text.isdigit()
        or len(cik_text) > 10
    ):
        raise ValueError("cik must contain 1 to 10 ASCII digits")

    return cik_text.zfill(10)


def quarter_for_date(day: date) -> str:
    """Return the SEC quarter label for a calendar date."""
    if not isinstance(day, date):
        raise TypeError("day must be a datetime.date")

    return f"QTR{((day.month - 1) // 3) + 1}"


def build_daily_master_index_url(day: date) -> str:
    """Build the SEC daily master-index URL for a calendar date."""
    if not isinstance(day, date):
        raise TypeError("day must be a datetime.date")

    quarter = quarter_for_date(day)
    return f"{SEC_DAILY_INDEX_BASE_URL}/{day.year}/{quarter}/master.{day:%Y%m%d}.idx"


def build_filing_archive_url(index_path: str) -> str:
    """Build a SEC Archives URL from a validated master-index path."""
    if not isinstance(index_path, str):
        raise TypeError("index_path must be a string")
    if not index_path.startswith("edgar/data/"):
        raise ValueError("index_path must be relative and begin with edgar/data/")
    if "\\" in index_path or "?" in index_path or "#" in index_path:
        raise ValueError("index_path contains unsafe URL characters")

    segments = index_path.split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise ValueError("index_path contains an empty or traversal segment")
    if any(
        len(segment) >= 2 and segment[0].isalpha() and segment[1] == ":"
        for segment in segments
    ):
        raise ValueError("index_path contains a drive-letter fragment")
    return f"{SEC_ARCHIVES_BASE_URL}/{index_path}"


def resolve_cik_from_json(ticker: str, use_cache: bool = True) -> str | None:
    """Resolve a ticker to CIK using SEC's company_tickers.json.

    This is the preferred method — fast, reliable, and doesn't require
    HTML parsing. The JSON file maps every publicly traded company to its
    CIK and ticker symbol.

    Parameters
    ----------
    ticker : str
        Stock ticker (e.g. "AAPL").
    use_cache : bool
        Whether to use the file cache.

    Returns
    -------
    str or None
        Raw CIK string (not zero-padded) or None if not found.
    """
    cache_dir = EDGAR_CACHE_DIR if use_cache else None

    try:
        text = fetch_url(
            COMPANY_TICKERS_URL,
            cache_dir=cache_dir,
            cache_ttl=86400,  # 24h — this file changes rarely
            use_sec_agent=True,
        )
        data = json.loads(text)
    except Exception as exc:
        log.warning("company_tickers.json fetch failed: %s", exc)
        return None

    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = entry.get("cik_str", "")
            if cik:
                return str(cik)
    return None


def resolve_cik(ticker: str, use_cache: bool = True) -> str | None:
    """Resolve a ticker symbol to a SEC CIK number.

    Uses the SEC company_tickers.json as the primary source (fast, reliable),
    falling back to HTML scraping of the EDGAR browse page if needed.

    Parameters
    ----------
    ticker : str
        Stock ticker (e.g. "AAPL").
    use_cache : bool
        Whether to use the file cache.

    Returns
    -------
    str or None
        CIK number (zero-padded to 10 digits) or None if not found.
    """
    # Primary: JSON lookup
    raw_cik = resolve_cik_from_json(ticker, use_cache=use_cache)
    if raw_cik:
        return raw_cik.zfill(10)

    # Fallback: HTML scraping
    log.info("JSON lookup missed for %s, trying HTML fallback", ticker)
    return _resolve_cik_html(ticker, use_cache=use_cache)


def _resolve_cik_html(ticker: str, use_cache: bool = True) -> str | None:
    """Resolve CIK by scraping the EDGAR company browse page (fallback)."""
    url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker.upper()}"
        f"&type=4&dateb=&owner=include&count=1&search_text=&action=getcompany"
    )
    cache_dir = EDGAR_CACHE_DIR if use_cache else None

    try:
        html = fetch_url(url, cache_dir=cache_dir, cache_ttl=86400, use_sec_agent=True)
    except Exception as exc:
        log.warning("CIK HTML lookup failed for %s: %s", ticker, exc)
        return None

    return parse_cik_from_html(html)


def parse_cik_from_html(html: str) -> str | None:
    """Extract CIK from EDGAR company search result page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # Look for CIK in the page (appears in links like /cgi-bin/browse-edgar?action=getcompany&CIK=0000320193)
    for a in soup.find_all("a"):
        href = a.get("href")
        if not isinstance(href, str):
            continue
        if "CIK=" in href:
            parts = href.split("CIK=")
            if len(parts) > 1:
                cik = parts[1].split("&")[0].strip()
                if cik.isdigit():
                    return cik.zfill(10)

    # Alternative: check page text for "CIK" followed by digits
    import re

    match = re.search(r"CIK[=:\s]*(\d{4,10})", html)
    if match:
        return match.group(1).zfill(10)

    return None


def get_filing_url(cik: str, count: int = 40) -> str:
    """Return the EDGAR filing listing URL for a given CIK."""
    return EDGAR_FILING_URL.format(cik=cik, count=count)


def fetch_filings_page(cik: str, count: int = 40, use_cache: bool = True) -> str:
    """Fetch the EDGAR Form 4 filings listing page for a CIK.

    Parameters
    ----------
    cik : str
        SEC CIK number.
    count : int
        Number of filings to retrieve.
    use_cache : bool
        Whether to use the file cache.

    Returns
    -------
    str
        HTML of the filings listing page.
    """
    url = get_filing_url(cik, count)
    cache_dir = EDGAR_CACHE_DIR if use_cache else None

    return fetch_url(url, cache_dir=cache_dir, cache_ttl=3600, use_sec_agent=True)


def fetch_company_info(cik: str, use_cache: bool = True) -> dict:
    """Fetch company submission info from EDGAR.

    Returns a dict with keys like 'name', 'tickers', 'filings'.
    """
    padded = cik.zfill(10)
    url = EDGAR_SUBMISSIONS.format(cik=padded)
    cache_dir = EDGAR_CACHE_DIR if use_cache else None

    try:
        text = fetch_url(url, cache_dir=cache_dir, cache_ttl=86400, use_sec_agent=True)
        return json.loads(text)
    except Exception as exc:
        log.warning("Company info fetch failed for CIK %s: %s", cik, exc)
        return {}


def build_edgar_url_for_trade(trade: InsiderTrade) -> str:
    """Generate an EDGAR search URL for a given trade (for verification)."""
    name = trade.insider_name.replace(" ", "+")
    ticker = trade.ticker
    d = trade.filing_date or trade.trade_date
    if d:
        start = d.replace(day=1).isoformat()
        end = d.isoformat()
    else:
        start = "2020-01-01"
        end = "2030-12-31"

    return (
        f"https://efts.sec.gov/LATEST/search-index?"
        f"q=%22{name}%22+%22{ticker}%22&forms=4"
        f"&dateRange=custom&startdt={start}&enddt={end}"
    )

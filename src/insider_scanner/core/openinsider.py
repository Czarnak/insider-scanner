"""Scrape insider trades from openinsider.com."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, FetchStatus
from insider_scanner.core.models import InsiderTrade
from insider_scanner.core.sec_client import SecClientError
from insider_scanner.utils.config import SCRAPER_CACHE_DIR
from insider_scanner.utils.http import fetch_url
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.parsing import (
    classify_trade as _classify_trade,
)
from insider_scanner.utils.parsing import (
    parse_date as _parse_date,
)
from insider_scanner.utils.parsing import (
    parse_number as _parse_number,
)

log = get_logger("openinsider")

BASE_URL = "http://openinsider.com"


def _screener_url(
    *,
    ticker: str = "",
    count: int,
    start_date: date | None,
    end_date: date | None,
) -> str:
    """Select custom filing dates for both ticker and market-wide requests."""
    bounded = start_date is not None or end_date is not None
    filing_range = ""
    if bounded:
        start = start_date.strftime("%m/%d/%Y") if start_date else "01/01/2000"
        end = end_date.strftime("%m/%d/%Y") if end_date else "12/31/2030"
        filing_range = f"{start} - {end}"
    params = {
        "s": ticker.upper(),
        "o": "",
        "pl": "",
        "ph": "",
        "st": "",
        "lt": "",
        "lh": "",
        "fd": "-1" if bounded else "0",
        "fdr": filing_range,
        "td": "0",
        "tdr": "",
        "feession": "",
        "xp": "1",
        "vl": "",
        "vh": "",
        "ocl": "",
        "och": "",
        "session": "",
        "ession": "",
        "cnt": str(count),
    }
    return f"{BASE_URL}/screener?{urlencode(params)}"


def scrape_ticker(
    ticker: str,
    use_cache: bool = True,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FetchResult[InsiderTrade]:
    """Scrape insider trades for a specific ticker from openinsider.com.

    Parameters
    ----------
    ticker : str
        Stock ticker symbol.
    use_cache : bool
        Whether to use the file cache.
    start_date : date or None
        Only include filings on or after this date.
    end_date : date or None
        Only include filings on or before this date.

    Returns
    -------
    list of InsiderTrade
    """
    url = _screener_url(
        ticker=ticker,
        count=100,
        start_date=start_date,
        end_date=end_date,
    )
    cache_dir = SCRAPER_CACHE_DIR if use_cache else None

    try:
        html = fetch_url(url, cache_dir=cache_dir, cache_ttl=3600)
    except (requests.RequestException, SecClientError, OSError, UnicodeError) as exc:
        log.warning(
            "OpenInsider fetch failed ticker=%s reason=%s", ticker, type(exc).__name__
        )
        return _failed("fetch_failed", ticker)

    return parse_openinsider_html(html, ticker, result_limit=100)


def scrape_latest(
    count: int = 100,
    use_cache: bool = True,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FetchResult[InsiderTrade]:
    """Scrape the latest insider trades across all tickers.

    Parameters
    ----------
    count : int
        Number of recent trades to fetch.
    use_cache : bool
        Whether to use the file cache.
    start_date : date or None
        Only include filings on or after this date.
    end_date : date or None
        Only include filings on or before this date.

    Returns
    -------
    list of InsiderTrade
    """
    if count < 1:
        raise ValueError("count must be positive")
    url = _screener_url(
        count=count,
        start_date=start_date,
        end_date=end_date,
    )
    cache_dir = SCRAPER_CACHE_DIR if use_cache else None

    try:
        html = fetch_url(url, cache_dir=cache_dir, cache_ttl=1800)
    except (requests.RequestException, SecClientError, OSError, UnicodeError) as exc:
        log.warning("OpenInsider latest fetch failed reason=%s", type(exc).__name__)
        return _failed("fetch_failed")

    # An unbounded latest request promises only the requested recent slice.
    # A bounded request, however, cannot prove its interval complete at the cap.
    result_limit = count if start_date or end_date else None
    return parse_openinsider_html(html, result_limit=result_limit)


def parse_openinsider_html(
    html: str, ticker: str = "", *, result_limit: int | None = None
) -> FetchResult[InsiderTrade]:
    """Parse insider trades from openinsider.com HTML.

    Parameters
    ----------
    html : str
        Raw HTML response body.
    ticker : str
        If empty, ticker is extracted from each row.

    Returns
    -------
    list of InsiderTrade
    """
    soup = BeautifulSoup(html, "lxml")
    trades: list[InsiderTrade] = []

    issues: list[FetchIssue] = []
    # A block/redesign page is not evidence of an empty trading history.
    table = soup.find("table", class_="tinytable")
    if table is None:
        return _failed("missing_trade_table", ticker)

    rows = table.find_all("tr")
    if not rows:
        return _failed("missing_trade_headers", ticker)

    # Parse header
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True).lower() for c in header_cells]

    col_map = {}
    for i, h in enumerate(headers):
        if h in ("x",):
            continue
        if "filing" in h and "date" in h:
            col_map.setdefault("filing_date", i)
        elif "trade" in h and "date" in h:
            col_map.setdefault("trade_date", i)
        elif "ticker" in h:
            col_map.setdefault("ticker", i)
        elif "company" in h:
            col_map.setdefault("company", i)
        elif "insider" in h and "name" in h:
            col_map.setdefault("name", i)
        elif "title" in h:
            col_map.setdefault("title", i)
        elif "trade type" in h or "type" in h:
            col_map.setdefault("type", i)
        elif "qty" in h or "shares" in h and "owned" not in h:
            col_map.setdefault("shares", i)
        elif "price" in h:
            col_map.setdefault("price", i)
        elif "value" in h:
            col_map.setdefault("value", i)
        elif "owned" in h:
            col_map.setdefault("owned_after", i)

    required = {"filing_date", "trade_date", "name", "shares", "type"}
    if not ticker:
        required.add("ticker")
    if not required.issubset(col_map):
        return _failed("missing_trade_headers", ticker)

    data_rows = 0
    # Parse data rows
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        if len(cells) == 1 and any(
            text in cells[0].get_text(" ", strip=True).lower()
            for text in ("no data", "no results", "no matching", "no transactions")
        ):
            continue
        data_rows += 1
        if len(cells) <= max(col_map[key] for key in required):
            issues.append(FetchIssue("malformed_trade_row", "openinsider", ticker))
            continue

        def cell_text(key: str, cells=cells) -> str:
            idx = col_map.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx].get_text(strip=True)
            return ""

        row_ticker = cell_text("ticker") or ticker.upper()

        trade = InsiderTrade(
            ticker=row_ticker.upper(),
            company=cell_text("company"),
            insider_name=cell_text("name"),
            insider_title=cell_text("title"),
            trade_type=_classify_trade(cell_text("type")),
            trade_date=_parse_date(cell_text("trade_date")),
            filing_date=_parse_date(cell_text("filing_date")),
            # OpenInsider signs sales negative; the model stores magnitudes,
            # while trade_type carries the transaction's direction.
            shares=abs(_parse_number(cell_text("shares"))),
            price=_parse_number(cell_text("price")),
            value=abs(_parse_number(cell_text("value"))),
            shares_owned_after=_parse_number(cell_text("owned_after")),
            source="openinsider",
        )

        if (
            trade.insider_name
            and trade.trade_date
            and trade.filing_date
            and trade.ticker
        ):
            trades.append(trade)
        else:
            issues.append(FetchIssue("malformed_trade_row", "openinsider", ticker))

    if result_limit is not None and data_rows >= result_limit:
        issues.append(FetchIssue("result_limit_reached", "openinsider", ticker))
    log.info("openinsider: parsed %d trades for %s", len(trades), ticker or "latest")
    return FetchResult(trades, issues=issues)


def _failed(reason: str, ticker: str = "") -> FetchResult[InsiderTrade]:
    log.warning(
        "OpenInsider incomplete ticker=%s reason=%s", ticker or "latest", reason
    )
    return FetchResult(
        status=FetchStatus.TRANSIENT_FAILURE,
        issues=(FetchIssue(reason, "openinsider", ticker),),
    )

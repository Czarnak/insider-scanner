"""Scrape insider trades from secform4.com."""

from __future__ import annotations

from datetime import date

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

log = get_logger("secform4")

BASE_URL = "https://www.secform4.com/insider-trading"


def scrape_ticker(
    ticker: str,
    use_cache: bool = True,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FetchResult[InsiderTrade]:
    """Scrape insider trades for a specific ticker from secform4.com.

    Resolves the ticker to a CIK number via SEC's company_tickers.json,
    then fetches the secform4.com page at BASE_URL/{cik}.htm.

    Parameters
    ----------
    ticker : str
        Stock ticker symbol (e.g. "AAPL").
    use_cache : bool
        Whether to use the file cache.
    start_date : date or None
        Only include trades with filing_date on or after this date.
    end_date : date or None
        Only include trades with filing_date on or before this date.

    Returns
    -------
    list of InsiderTrade
    """
    from insider_scanner.core.edgar import resolve_cik_from_json

    # Resolve ticker → CIK (raw, not zero-padded)
    try:
        raw_cik = resolve_cik_from_json(ticker, use_cache=use_cache)
    except (requests.RequestException, SecClientError, OSError, UnicodeError) as exc:
        log.warning(
            "SECForm4 CIK resolution failed ticker=%s reason=%s",
            ticker,
            type(exc).__name__,
        )
        return _failed("cik_resolution_failed", ticker)
    if not raw_cik:
        return _failed("cik_unresolved", ticker)

    url = f"{BASE_URL}/{raw_cik}.htm"
    cache_dir = SCRAPER_CACHE_DIR if use_cache else None

    try:
        html = fetch_url(url, cache_dir=cache_dir, cache_ttl=3600)
    except (requests.RequestException, SecClientError, OSError, UnicodeError) as exc:
        log.warning(
            "SECForm4 fetch failed ticker=%s reason=%s", ticker, type(exc).__name__
        )
        return _failed("fetch_failed", ticker)

    result = parse_secform4_html(html, ticker)
    trades = list(result)

    # Post-filter by filing date (secform4 doesn't support date params in URL)
    if start_date:
        trades = [t for t in trades if t.filing_date and t.filing_date >= start_date]
    if end_date:
        trades = [t for t in trades if t.filing_date and t.filing_date <= end_date]

    return FetchResult(
        trades,
        status=result.status if not result.complete else None,
        issues=result.issues,
    )


def parse_secform4_html(html: str, ticker: str = "") -> FetchResult[InsiderTrade]:
    """Parse insider trades from secform4.com HTML.

    secform4.com uses compound table cells where multiple data fields are
    packed into a single ``<td>`` separated by ``<br>`` tags and nested
    elements.  This parser extracts sub-fields using the actual DOM
    structure rather than plain ``get_text()``.

    Parameters
    ----------
    html : str
        Raw HTML from secform4.com.
    ticker : str
        Ticker to assign to trades.

    Returns
    -------
    list of InsiderTrade
    """
    soup = BeautifulSoup(html, "lxml")
    trades: list[InsiderTrade] = []
    issues: list[FetchIssue] = []

    # Only the known table is evidence of a successful source response.
    data_table = soup.find("table", id="filing_table")
    if data_table is None:
        return _failed("missing_trade_table", ticker)

    # Collect data rows (skip <thead>)
    tbody = data_table.find("tbody")
    rows = tbody.find_all("tr") if tbody else data_table.find_all("tr")[1:]

    # Build column index from the header row
    header_row = data_table.find("thead")
    if header_row:
        header_cells = header_row.find_all(["th", "td"])
    else:
        first_row = data_table.find("tr")
        header_cells = first_row.find_all(["th", "td"]) if first_row else []

    headers = [c.get_text(separator=" ", strip=True).lower() for c in header_cells]
    col = {}
    for i, h in enumerate(headers):
        if "transaction" in h:
            col.setdefault("transaction", i)
        elif "reported" in h:
            col.setdefault("reported", i)
        elif "company" in h:
            col.setdefault("company", i)
        elif "symbol" in h:
            col.setdefault("symbol", i)
        elif "insider" in h or "relationship" in h:
            col.setdefault("insider", i)
        elif "traded" in h:
            col.setdefault("shares", i)
        elif "price" in h:
            col.setdefault("price", i)
        elif "amount" in h or "total" in h:
            col.setdefault("value", i)
        elif "owned" in h:
            col.setdefault("owned", i)
        elif "filing" in h:
            col.setdefault("filing", i)

    required = {"transaction", "reported", "insider", "shares"}
    if not ticker:
        required.add("symbol")
    if not required.issubset(col):
        return _failed("missing_trade_headers", ticker)

    for row in rows:
        cells = row.find_all("td")
        if not cells:
            continue
        if len(cells) == 1 and any(
            text in cells[0].get_text(" ", strip=True).lower()
            for text in ("no data", "no results", "no matching", "no transactions")
        ):
            continue
        if len(cells) <= max(col[key] for key in required):
            issues.append(FetchIssue("malformed_trade_row", "secform4", ticker))
            continue

        def _cell(key: str, cells=cells):
            """Return the <td> element for *key*, or None."""
            idx = col.get(key)
            if idx is not None and idx < len(cells):
                return cells[idx]
            return None

        # --- Transaction cell: date + trade type split by <br> ---
        trade_date_val = None
        trade_type_val = "Other"
        tx_cell = _cell("transaction")
        if tx_cell:
            parts = _br_split(tx_cell)
            if parts:
                trade_date_val = _parse_date(parts[0])
            if len(parts) > 1:
                trade_type_val = _classify_trade(parts[1])
            # CSS class hint: S=Sale, P=Purchase, M=Exercise
            css = " ".join(tx_cell.get("class", []))
            if trade_type_val == "Other" and css:
                if "S" in css:
                    trade_type_val = "Sell"
                elif "P" in css:
                    trade_type_val = "Buy"
                elif "M" in css:
                    trade_type_val = "Exercise"

        # --- Reported cell: filing date (ignore time) ---
        filing_date_val = None
        rpt_cell = _cell("reported")
        if rpt_cell:
            parts = _br_split(rpt_cell)
            if parts:
                filing_date_val = _parse_date(parts[0])

        # --- Company ---
        company_val = ""
        comp_cell = _cell("company")
        if comp_cell:
            company_val = comp_cell.get_text(strip=True)

        # --- Symbol (may override ticker) ---
        sym_cell = _cell("symbol")
        row_ticker = ticker.upper()
        if sym_cell:
            sym_text = sym_cell.get_text(strip=True)
            if sym_text:
                row_ticker = sym_text.upper()

        # --- Insider cell: <a> = name, <span class="pos"> = title ---
        insider_name = ""
        insider_title = ""
        ins_cell = _cell("insider")
        if ins_cell:
            a_tag = ins_cell.find("a")
            insider_name = a_tag.get_text(strip=True) if a_tag else ""
            pos_span = ins_cell.find("span", class_="pos")
            insider_title = pos_span.get_text(strip=True) if pos_span else ""
            # Fallback: if no <a>, use br-split
            if not insider_name:
                parts = _br_split(ins_cell)
                insider_name = parts[0] if parts else ""
                insider_title = parts[1] if len(parts) > 1 else insider_title

        # --- Numeric columns ---
        shares_val = (
            _parse_number(_cell("shares").get_text(strip=True))
            if _cell("shares")
            else 0.0
        )
        price_val = (
            _parse_number(_cell("price").get_text(strip=True))
            if _cell("price")
            else 0.0
        )
        value_val = (
            _parse_number(_cell("value").get_text(strip=True))
            if _cell("value")
            else 0.0
        )

        # --- Shares owned: first text node, ignore <span class="ownership"> ---
        owned_val = 0.0
        own_cell = _cell("owned")
        if own_cell:
            parts = _br_split(own_cell)
            if parts:
                owned_val = _parse_number(parts[0])

        # --- Filing link ---
        edgar_url = ""
        filing_cell = _cell("filing")
        if filing_cell:
            a_tag = filing_cell.find("a", href=True)
            if a_tag:
                href = a_tag["href"]
                if href.startswith("/"):
                    edgar_url = f"https://www.secform4.com{href}"
                else:
                    edgar_url = href

        trade = InsiderTrade(
            ticker=row_ticker,
            company=company_val,
            insider_name=insider_name,
            insider_title=insider_title,
            trade_type=trade_type_val,
            trade_date=trade_date_val,
            filing_date=filing_date_val,
            shares=shares_val,
            price=price_val,
            value=value_val,
            shares_owned_after=owned_val,
            source="secform4",
            edgar_url=edgar_url,
        )

        if (
            trade.insider_name
            and trade.trade_date
            and trade.filing_date
            and trade.ticker
        ):
            trades.append(trade)
        else:
            issues.append(FetchIssue("malformed_trade_row", "secform4", ticker))

    log.info("secform4: parsed %d trades for %s", len(trades), ticker)
    return FetchResult(trades, issues=issues)


def _failed(reason: str, ticker: str) -> FetchResult[InsiderTrade]:
    log.warning("SECForm4 incomplete ticker=%s reason=%s", ticker, reason)
    return FetchResult(
        status=FetchStatus.TRANSIENT_FAILURE,
        issues=(FetchIssue(reason, "secform4", ticker),),
    )


def _br_split(td) -> list[str]:
    """Split a <td> element on <br> tags and return stripped text parts.

    Handles nested elements (spans, links) by collecting text nodes
    between <br> separators.
    """
    from bs4 import NavigableString, Tag

    parts: list[str] = []
    current: list[str] = []

    for child in td.children:
        if isinstance(child, Tag) and child.name == "br":
            text = "".join(current).strip()
            if text:
                parts.append(text)
            current = []
        elif isinstance(child, Tag):
            current.append(child.get_text(strip=True))
        elif isinstance(child, NavigableString):
            current.append(str(child).strip())

    # Flush remaining
    text = "".join(current).strip()
    if text:
        parts.append(text)

    return parts

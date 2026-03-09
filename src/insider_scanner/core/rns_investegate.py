"""
UK insider trade scraper — Investegate (RNS/GNW announcements).

Data source: https://www.investegate.co.uk
Format:      MAR Article 19 standard notification form
Filter:      ISIN keyword search → /announcement/.../director-pdmr-shareholding/...

Table structure (confirmed via diagnostic):
  Row format: [section_id, label, value]  (3 cells per data row)
  1a  Name                          → insider_name
  2a  Position/status               → position (raw, normalised via eu_models)
  2b  Initial Notification/...      → notification_type
  3a  Name                          → issuer_name
  3b  LEI                           → lei
  4a  Description / ISIN            → instrument_type + isin_in_text
  4b  Nature of the transaction     → trade_type_raw
  4c  Price(s) and volume(s)        → price + volume (needs regex)
  4d  Aggregated information        → aggregated_volume + aggregated_price
  4e  Date of the transaction       → trade_date
  4f  Place of the transaction      → place
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position

logger = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://www.investegate.co.uk/Index.aspx"
    "?keywords={isin}&searchtype=announcements&category=POS"
)
_BASE_URL = "https://www.investegate.co.uk"
_REQUEST_DELAY = 1.5  # seconds between requests (polite scraping)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-GB,en;q=0.9",
}


# ── Internal data container before mapping to EuropeanInsiderTrade ──────────

@dataclass
class _RawRns:
    insider_name: str = ""
    position_raw: str = ""
    notification_type: str = ""
    issuer_name: str = ""
    lei: str = ""
    instrument_description: str = ""
    instrument_isin: str = ""
    trade_type_raw: str = ""
    price_raw: str = ""
    volume_raw: str = ""
    aggregated_raw: str = ""
    trade_date_raw: str = ""
    place: str = ""
    source_url: str = ""
    filing_date_raw: str = ""  # from page metadata if available


# ── Section-label → field mapping ──────────────────────────────────────────

# Maps (section_id, label_keyword) → _RawRns field name
# section_id is the value in the first cell (e.g. '1', 'a)', '2', 'b)')
# The table iterates top-to-bottom so we track the current section.
_LABEL_MAP: dict[str, str] = {
    "name": "_name_field",          # context-dependent: could be insider OR issuer
    "position": "position_raw",
    "status": "position_raw",
    "initial notification": "notification_type",
    "amendment": "notification_type",
    "lei": "lei",
    "description of the financial": "instrument_description",
    "identification code": "instrument_description",
    "nature of the transaction": "trade_type_raw",
    "price": "price_raw",
    "volume": "price_raw",          # same cell as price
    "aggregated information": "aggregated_raw",
    "aggregated volume": "aggregated_raw",
    "date of the transaction": "trade_date_raw",
    "place of the transaction": "place",
}


def _parse_announcement(html: str, url: str) -> Optional[_RawRns]:
    """
    Parse a single MAR Article 19 announcement page into a _RawRns object.

    The page has a single <table> with rows in the form:
        [section_num | label | value]
    We iterate row by row, tracking which major section (1/2/3/4) we are in
    so that the 'Name' label resolves to the correct field.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        logger.debug("No tables found at %s", url)
        return None

    raw = _RawRns(source_url=url)
    current_section = 0   # 1=person, 2=reason, 3=issuer, 4=transaction

    # Use the first table (the MAR form)
    table = tables[0]

    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]

        if len(cells) < 2:
            continue

        # Identify the section number (cell[0] like '1', '2', '3', '4')
        if len(cells) >= 1 and cells[0].strip() in ("1", "2", "3", "4"):
            try:
                current_section = int(cells[0].strip())
            except ValueError:
                pass
            continue

        # 3-cell rows: [sub_section_id, label, value]
        if len(cells) >= 3:
            label = cells[1].lower().strip()
            value = cells[2].strip()
        elif len(cells) == 2:
            label = cells[0].lower().strip()
            value = cells[1].strip()
        else:
            continue

        if not value:
            continue

        # Context-sensitive 'Name' field
        if "name" in label and len(label) < 10:
            if current_section == 1:
                raw.insider_name = value
            elif current_section == 3:
                raw.issuer_name = value
            continue

        # Map other labels
        for keyword, field in _LABEL_MAP.items():
            if keyword in label:
                if field == "_name_field":
                    continue  # handled above
                if field == "price_raw":
                    # Cell 4c contains "Price(s)  Volume(s)  €0.065  12345"
                    # Store full raw text for later parsing
                    raw.price_raw = value
                elif field == "instrument_description":
                    raw.instrument_description = value
                    # Try to extract ISIN from text like "ICG UnitISIN : IE00BLP58571"
                    m = re.search(r"ISIN\s*[:\s]+([A-Z]{2}[A-Z0-9]{10})", value)
                    if m:
                        raw.instrument_isin = m.group(1)
                else:
                    setattr(raw, field, value)
                break

    # Extract filing date from page metadata if present
    pub_date = soup.find("meta", {"name": "dateCreated"})
    if pub_date and pub_date.get("content"):
        raw.filing_date_raw = pub_date["content"][:10]  # YYYY-MM-DD

    return raw if raw.insider_name or raw.issuer_name else None


def _parse_price_volume(price_raw: str) -> tuple[Optional[float], Optional[float], str]:
    """
    Parse BaFin-style price/volume cell.
    Example: "Price(s)  Volume(s) €0.065  12 345"
    Returns: (price, volume, currency)
    """
    currency = ""
    price: Optional[float] = None
    volume: Optional[float] = None

    # Currency symbol
    currency_match = re.search(r"[€£$¥]", price_raw)
    if currency_match:
        symbol_map = {"€": "EUR", "£": "GBP", "$": "USD", "¥": "JPY"}
        currency = symbol_map.get(currency_match.group(), "")

    # Find all numeric sequences (possibly with spaces as thousands separators)
    numbers = re.findall(r"[\d][\d\s]*(?:[,\.]\d+)?", price_raw.replace("\xa0", " "))
    cleaned = []
    for n in numbers:
        n_clean = n.strip().replace(" ", "").replace(",", ".")
        try:
            cleaned.append(float(n_clean))
        except ValueError:
            pass

    if len(cleaned) >= 1:
        price = cleaned[0]
    if len(cleaned) >= 2:
        volume = cleaned[1]

    return price, volume, currency


def _parse_trade_date(date_raw: str) -> Optional[date]:
    """Parse '6 March 2026' or '2026-03-06' into a date object."""
    for fmt in ("%d %B %Y", "%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_raw.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _determine_trade_type(nature_raw: str) -> str:
    """Map free-text nature of transaction to Buy/Sell/Other."""
    lower = nature_raw.lower()
    if any(kw in lower for kw in ["acqui", "purchas", "buy", "bought", "award", "vest", "exercise"]):
        return "Buy"
    if any(kw in lower for kw in ["dispos", "sale", "sold", "sell"]):
        return "Sell"
    return "Other"


def _to_eu_trade(raw: _RawRns, original_isin: str) -> Optional[EuropeanInsiderTrade]:
    """Convert _RawRns to EuropeanInsiderTrade dataclass."""
    if not raw.insider_name:
        return None

    price, volume, currency = _parse_price_volume(raw.price_raw)
    trade_date = _parse_trade_date(raw.trade_date_raw)
    filing_date: Optional[date] = None
    if raw.filing_date_raw:
        try:
            filing_date = datetime.strptime(raw.filing_date_raw, "%Y-%m-%d").date()
        except ValueError:
            pass

    # The ISIN in the announcement body may differ from the search ISIN
    # (e.g. search on company ISIN but trade is in a share option scheme)
    # We keep the body ISIN but fall back to the search ISIN.
    isin = raw.instrument_isin or original_isin

    # Instrument type: extract from description before the ISIN
    instr_type = re.sub(r"\s*ISIN\s*[:\s]+[A-Z]{2}[A-Z0-9]{10}.*", "", raw.instrument_description).strip()
    if not instr_type:
        instr_type = "Equity"

    total_value: Optional[float] = None
    if price is not None and volume is not None:
        total_value = round(price * volume, 2)

    return EuropeanInsiderTrade(
        isin=isin,
        issuer_name=raw.issuer_name,
        country="UK",
        regulatory_body="FCA",
        insider_name=raw.insider_name,
        position=normalize_position(raw.position_raw),
        trade_date=trade_date,
        filing_date=filing_date,
        trade_type=_determine_trade_type(raw.trade_type_raw),
        instrument_type=instr_type,
        volume=volume,
        price=price,
        currency=currency or "GBP",
        total_value=total_value,
        source="rns",
        source_url=raw.source_url,
    )


# ── Public interface ─────────────────────────────────────────────────────────

def fetch_uk_trades(
    isin: str,
    since: Optional[date] = None,
    until: Optional[date] = None,
    max_announcements: int = 50,
    session: Optional[requests.Session] = None,
) -> list[EuropeanInsiderTrade]:
    """
    Fetch MAR Article 19 insider trade notifications for a given ISIN from Investegate.

    Args:
        isin:              UK ISIN (GB prefix typical but not enforced).
        since:             Only return trades on or after this date.
        until:             Only return trades on or before this date.
        max_announcements: Cap on announcement pages to fetch (avoids runaway scraping).
        session:           Optional requests.Session for connection pooling.

    Returns:
        List of EuropeanInsiderTrade records.
    """
    sess = session or requests.Session()
    sess.headers.update(_HEADERS)

    # ── Step 1: get the list of announcements for this ISIN ──
    search_url = _SEARCH_URL.format(isin=isin)
    logger.info("Fetching Investegate results for ISIN %s", isin)

    try:
        resp = sess.get(search_url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Investegate search failed for %s: %s", isin, exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Filter to announcement links that are Director/PDMR Shareholding notifications
    ann_links = [
        a.get("href", "")
        for a in soup.select("a[href*='/announcement/']")
        if "director-pdmr-shareholding" in a.get("href", "")
    ]

    if not ann_links:
        logger.info("No Director/PDMR Shareholding announcements found for ISIN %s", isin)
        return []

    logger.info("Found %d Director/PDMR Shareholding links for %s", len(ann_links), isin)

    # ── Step 2: fetch and parse each announcement detail page ──
    trades: list[EuropeanInsiderTrade] = []

    for href in ann_links[:max_announcements]:
        detail_url = href if href.startswith("http") else _BASE_URL + href

        try:
            time.sleep(_REQUEST_DELAY)
            detail_resp = sess.get(detail_url, timeout=15)
            detail_resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Failed to fetch %s: %s", detail_url, exc)
            continue

        raw = _parse_announcement(detail_resp.text, detail_url)
        if raw is None:
            logger.debug("Could not parse announcement at %s", detail_url)
            continue

        trade = _to_eu_trade(raw, isin)
        if trade is None:
            continue

        # Apply date filters
        if trade.trade_date:
            if since and trade.trade_date < since:
                continue
            if until and trade.trade_date > until:
                continue

        trades.append(trade)

    logger.info("Parsed %d trades for ISIN %s", len(trades), isin)
    return trades
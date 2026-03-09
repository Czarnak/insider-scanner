"""
German insider trade scraper — BaFin Directors' Dealings portal.

Data source: https://portal.mvp.bafin.de/database/DealingsInfo/
Format:      HTML table (server-side rendered Java/Struts app)

KEY FIX (found via diagnostic): form field is `emittentIsin`, not `isin`.
Submit button field is `emittentButton=Suche Emittent`.

Result table columns (0-indexed):
  0  Issuer name (with link to detail: ergebnisListe.do?cmd=loadMeldepflichtigeAction
                                                        &emittentBafinId=...&meldungId=...)
  1  BaFin issuer ID
  2  ISIN
  3  Insider name (Meldepflichtiger)
  4  Position (German: Vorstand / Aufsichtsrat / in enger Beziehung / ...)
  5  Instrument type (German: Aktie / Schuldtitel / ...)
  6  Transaction type (German: Kauf / Verkauf / Sonstiges)
  7  Transaction date (DD.MM.YYYY)
  8  Exchange/venue
  9  Filing datetime (DD.MM.YYYY HH:MM:SS)

Price and volume are NOT in the list view.
They are available on the detail page (ergebnisListe.do) or via CSV export.
CSV export URL pattern (discovered in HTML):
  sucheForm.do?meldepflichtigerName=&zeitraum=0&d-4000784-e=1
             &emittentButton=Suche+Emittent&emittentName=
             &zeitraumVon=&emittentIsin={ISIN}&6578706f7274=1&zeitraumBis=
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position

logger = logging.getLogger(__name__)

_PORTAL_BASE = "https://portal.mvp.bafin.de"
_START_URL   = f"{_PORTAL_BASE}/database/DealingsInfo/start.do"
_SEARCH_URL  = f"{_PORTAL_BASE}/database/DealingsInfo/sucheForm.do"
_DETAIL_BASE = f"{_PORTAL_BASE}/database/DealingsInfo/"
_REQUEST_DELAY = 2.0  # seconds between requests

_HEADERS = {
    "User-Agent": "Mozilla/5.0 InsiderScanner/0.3",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-GB,en;q=0.9",
}

# ── German → normalised mappings ────────────────────────────────────────────

_TRADE_TYPE_MAP: dict[str, str] = {
    "kauf": "Buy",
    "verkauf": "Sell",
    "sonstiges": "Other",
    "other": "Other",
}

_INSTRUMENT_MAP: dict[str, str] = {
    "aktie": "Share",
    "schuldtitel": "Debt Instrument",
    "derivat": "Derivative",
    "option": "Option",
    "zertifikat": "Certificate",
}

# BaFin position strings for normalize_position() input
# normalize_position() (in eu_models) handles Vorstand→Executive etc.


def _parse_trade_date(raw: str) -> Optional[date]:
    """Parse DD.MM.YYYY or YYYY-MM-DD."""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _parse_filing_date(raw: str) -> Optional[date]:
    """Parse 'DD.MM.YYYY HH:MM:SS' filing timestamp."""
    try:
        return datetime.strptime(raw.strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        pass
    return None


def _map_trade_type(raw_de: str) -> str:
    return _TRADE_TYPE_MAP.get(raw_de.lower().strip(), "Other")


def _map_instrument(raw_de: str) -> str:
    return _INSTRUMENT_MAP.get(raw_de.lower().strip(), raw_de.strip())


# ── List page parser ─────────────────────────────────────────────────────────

def _parse_result_table(html: str) -> list[dict]:
    """
    Parse the BaFin result HTML table into a list of raw row dicts.
    Returns empty list if no results found.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if not tables:
        return []

    # The result table is the first <table> with tbody data rows
    rows = []
    for table in tables:
        tbody = table.find("tbody")
        if tbody:
            trs = tbody.find_all("tr")
            if trs:
                for tr in trs:
                    cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                    # Extract the meldungId from the link in col 0
                    link = tr.find("a", href=True)
                    meldung_id = ""
                    bafin_id = ""
                    detail_url = ""
                    if link:
                        href = link.get("href", "")
                        import re
                        m_id = re.search(r"meldungId=(\d+)", href)
                        b_id = re.search(r"emittentBafinId=(\d+)", href)
                        meldung_id = m_id.group(1) if m_id else ""
                        bafin_id = b_id.group(1) if b_id else ""
                        detail_url = _DETAIL_BASE + href.lstrip("/database/DealingsInfo/")

                    if len(cells) >= 9:
                        rows.append({
                            "issuer_name":    cells[0],
                            "bafin_id":       cells[1] if len(cells) > 1 else bafin_id,
                            "isin":           cells[2] if len(cells) > 2 else "",
                            "insider_name":   cells[3] if len(cells) > 3 else "",
                            "position_de":    cells[4] if len(cells) > 4 else "",
                            "instrument_de":  cells[5] if len(cells) > 5 else "",
                            "trade_type_de":  cells[6] if len(cells) > 6 else "",
                            "trade_date_raw": cells[7] if len(cells) > 7 else "",
                            "place":          cells[8] if len(cells) > 8 else "",
                            "filing_raw":     cells[9] if len(cells) > 9 else "",
                            "meldung_id":     meldung_id,
                            "bafin_id":       bafin_id,
                            "detail_url":     detail_url,
                        })
                break  # found the result table

    return rows


def _row_to_trade(row: dict) -> Optional[EuropeanInsiderTrade]:
    """Convert a parsed result row to EuropeanInsiderTrade."""
    trade_date = _parse_trade_date(row["trade_date_raw"])
    filing_date = _parse_filing_date(row["filing_raw"])

    return EuropeanInsiderTrade(
        isin=row["isin"],
        issuer_name=row["issuer_name"],
        country="DE",
        regulatory_body="BaFin",
        insider_name=row["insider_name"],
        position=normalize_position(row["position_de"]),
        trade_date=trade_date,
        filing_date=filing_date,
        trade_type=_map_trade_type(row["trade_type_de"]),
        instrument_type=_map_instrument(row["instrument_de"]),
        volume=None,    # not in list view; fetch detail page if needed
        price=None,     # not in list view; fetch detail page if needed
        currency="EUR",
        total_value=None,
        source="bafin",
        source_url=row.get("detail_url", _SEARCH_URL),
    )


# ── CSV export parser (richer data if available) ─────────────────────────────

def _fetch_csv_export(
    isin: str,
    session: requests.Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict]:
    """
    Download the CSV export from BaFin and parse it.
    CSV export URL discovered in diagnostic:
      sucheForm.do?meldepflichtigerName=&zeitraum=0&d-4000784-e=1
                 &emittentButton=Suche+Emittent&emittentName=
                 &zeitraumVon=&emittentIsin={ISIN}&6578706f7274=1&zeitraumBis=

    The hex token 6578706f7274 = "export" in ASCII — BaFin's export trigger param.
    d-4000784-e=1 → CSV (d-4000784-e=3 → XML)
    """
    von = date_from.strftime("%d.%m.%Y") if date_from else ""
    bis = date_to.strftime("%d.%m.%Y") if date_to else ""

    params = {
        "meldepflichtigerName": "",
        "zeitraum": "0",
        "d-4000784-e": "1",
        "emittentButton": "Suche Emittent",
        "emittentName": "",
        "zeitraumVon": von,
        "emittentIsin": isin,
        "6578706f7274": "1",
        "zeitraumBis": bis,
    }

    try:
        resp = session.get(_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("BaFin CSV export failed for %s: %s", isin, exc)
        return []

    content_type = resp.headers.get("Content-Type", "")
    if "csv" not in content_type.lower() and "text/plain" not in content_type.lower():
        logger.debug("BaFin CSV export returned non-CSV content (%s), falling back", content_type)
        return []

    # Parse CSV — BaFin CSVs use semicolons and latin-1 or utf-8-sig
    try:
        text = resp.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        return [row for row in reader]
    except Exception as exc:
        logger.warning("BaFin CSV parse error: %s", exc)
        return []


# ── Public interface ─────────────────────────────────────────────────────────

def fetch_de_trades(
    isin: str,
    since: Optional[date] = None,
    until: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> list[EuropeanInsiderTrade]:
    """
    Fetch BaFin Directors' Dealings for a given ISIN.

    Strategy:
      1. Initialise session (GET start.do to get JSESSIONID cookie).
      2. POST to sucheForm.do with correct field names.
      3. Parse HTML result table (confirmed working via de.html diagnostic).
      4. Optionally attempt CSV export for richer data (price/volume).

    Args:
        isin:    German ISIN (typically DE prefix).
        since:   Filter — only trades on or after this date.
        until:   Filter — only trades on or before this date.
        session: Optional requests.Session.

    Returns:
        List of EuropeanInsiderTrade records.
    """
    sess = session or requests.Session()
    sess.headers.update(_HEADERS)

    # ── Step 1: initialise session cookie ──
    try:
        sess.get(_START_URL, timeout=15)
    except requests.RequestException as exc:
        logger.warning("BaFin session init failed: %s", exc)

    time.sleep(_REQUEST_DELAY)

    # ── Step 2: POST search with correct field names ──
    # KEY FIX: field is 'emittentIsin', button is 'emittentButton'
    # Using zeitraum=0 (all time) — date filtering applied client-side
    # because the date range fields (zeitraumVon/zeitraumBis) need DD.MM.YYYY format
    # and validation errors occur easily with wrong values.
    form_data = {
        "emittentIsin":       isin,
        "emittentName":       "",
        "emittentButton":     "Suche Emittent",
        "meldepflichtigerName": "",
        "zeitraum":           "0",   # 0 = total period
        "zeitraumVon":        "",
        "zeitraumBis":        "",
        "locale":             "en_GB",
    }

    logger.info("Fetching BaFin data for ISIN %s", isin)

    try:
        resp = sess.post(_SEARCH_URL, data=form_data, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("BaFin search POST failed for %s: %s", isin, exc)
        return []

    rows = _parse_result_table(resp.text)
    logger.info("BaFin HTML table: %d rows found for %s", len(rows), isin)

    if not rows:
        # Try CSV export as fallback (sometimes table renders differently)
        logger.debug("No HTML table rows — trying CSV export")
        csv_rows = _fetch_csv_export(isin, sess, since, until)
        if csv_rows:
            logger.info("BaFin CSV export: %d rows for %s", len(csv_rows), isin)
            # CSV columns may vary — map best effort
            rows = _map_csv_rows(csv_rows, isin)

    trades: list[EuropeanInsiderTrade] = []
    for row in rows:
        trade = _row_to_trade(row)
        if trade is None:
            continue
        if trade.trade_date:
            if since and trade.trade_date < since:
                continue
            if until and trade.trade_date > until:
                continue
        trades.append(trade)

    logger.info("Returning %d BaFin trades for %s", len(trades), isin)
    return trades


def _map_csv_rows(csv_rows: list[dict], isin: str) -> list[dict]:
    """
    Map BaFin CSV column names to our internal dict format.
    BaFin CSV columns (English locale) vary slightly between exports,
    so we try multiple name variants.
    """
    def get(row: dict, *keys: str, default: str = "") -> str:
        for k in keys:
            if k in row:
                return row[k].strip()
            # Case-insensitive fallback
            for rk in row:
                if rk.strip().lower() == k.lower():
                    return row[rk].strip()
        return default

    mapped = []
    for row in csv_rows:
        mapped.append({
            "issuer_name":    get(row, "Issuer", "Emittent", "issuer"),
            "bafin_id":       get(row, "BaFin-ID", "ID"),
            "isin":           get(row, "ISIN", "isin") or isin,
            "insider_name":   get(row, "Person subject to notification", "Meldepflichtiger", "name"),
            "position_de":    get(row, "Position/function", "Funktion", "position"),
            "instrument_de":  get(row, "Instrument", "Art des Instruments"),
            "trade_type_de":  get(row, "Nature of transaction", "Art des Geschäfts", "type"),
            "trade_date_raw": get(row, "Date of transaction", "Transaktionsdatum", "date"),
            "place":          get(row, "Trading venue", "Handelsplatz", "venue"),
            "filing_raw":     get(row, "Publication date", "Meldedatum"),
            "meldung_id":     get(row, "Notification ID", "Meldungs-ID"),
            "bafin_id":       get(row, "BaFin-ID"),
            "detail_url":     "",
        })
    return mapped
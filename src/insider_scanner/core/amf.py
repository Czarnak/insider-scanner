"""
French insider trade scraper — AMF BDIF (Autorité des Marchés Financiers).

Data source: https://bdif.amf-france.org

Confirmed API (discovered via HAR analysis, no auth required):

  Search:
    GET /back/api/v1/informations
        ?rechercheTexte={ISIN}
        &typesInformation=DD
        &From=0
        &Size=50
    → JSON with .result[] each containing:
        .numero          "2026DD1099088"
        .datePublication "2026-03-06T18:08:10.927+01:00"
        .dateInformation "2026-03-06T00:00:00"
        .documents[0].path  "2026/2026DD1099088/{SHA512}.pdf"
        .societes[0].jeton  "RS00002627"  (company token, informational)
        .societes[0].raisonSociale  "CEGEDIM"

  PDF download:
    GET /back/api/v1/documents/{path}
    → application/pdf

PDF field mapping (French → English):
  NOM / FONCTION DE LA PERSONNE CONCERNEE → insider_name, position_raw
  NOTIFICATION INITIALE / MODIFICATION    → notification_type
  NOM (issuer section)                    → issuer_name
  LEI                                     → lei
  DATE DE LA TRANSACTION                  → trade_date
  LIEU DE LA TRANSACTION                  → place
  NATURE DE LA TRANSACTION                → trade_type_raw  (Acquisition / Cession)
  DESCRIPTION DE L'INSTRUMENT FINANCIER   → instrument_type
  CODE D'IDENTIFICATION (ISIN)            → isin_in_doc
  PRIX UNITAIRE                           → price
  VOLUME                                  → volume
  DATE DE RECEPTION                       → filing_date
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import date, datetime
from typing import Optional

import pdfplumber
import requests

from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position

logger = logging.getLogger(__name__)

_BDIF = "https://bdif.amf-france.org"
_SEARCH_URL = f"{_BDIF}/back/api/v1/informations"
_PDF_BASE_URL = f"{_BDIF}/back/api/v1/documents"

_PAGE_SIZE = 50
_REQUEST_DELAY = 1.0  # seconds between PDF downloads

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "accept-language": "en",
    "Referer": f"{_BDIF}/en?typesInformation=DD",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


# ── Search ────────────────────────────────────────────────────────────────────


def _search_dd_documents(
    isin: str,
    since: Optional[date],
    until: Optional[date],
    session: requests.Session,
) -> list[dict]:
    """
    Call /back/api/v1/informations to get all DD filings for an ISIN.

    Returns a list of raw result dicts from the API, each guaranteed to have
    at least one entry in .documents[] with a valid .path.

    Pagination: the API uses From/Size. We stop early if publication dates
    fall before `since`, since results are newest-first.
    """
    results: list[dict] = []
    from_offset = 0

    while True:
        params = {
            "rechercheTexte": isin,
            "typesInformation": "DD",
            "From": from_offset,
            "Size": _PAGE_SIZE,
        }
        try:
            r = session.get(_SEARCH_URL, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.error(
                "BDIF search failed for ISIN %s (offset %d): %s", isin, from_offset, exc
            )
            break

        data = r.json()
        page = data.get("result", [])
        if not page:
            break

        for item in page:
            if not item.get("documents"):
                continue

            pub_date = _parse_iso_date(item.get("datePublication", ""))

            # Results are newest-first; stop paginating once we go before `since`
            if since and pub_date and pub_date < since:
                return results

            if until and pub_date and pub_date > until:
                continue

            results.append(item)

        if len(page) < _PAGE_SIZE:
            break

        from_offset += _PAGE_SIZE

    return results


def _parse_iso_date(raw: str) -> Optional[date]:
    """Parse ISO datetime string to date, tolerating timezone suffixes."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


# ── PDF download ──────────────────────────────────────────────────────────────


def _download_pdf(path: str, session: requests.Session) -> Optional[bytes]:
    """
    Download a PDF given its path from the API (e.g. "2026/2026DD1099088/{hash}.pdf").
    Returns raw bytes or None on failure.
    """
    url = f"{_PDF_BASE_URL}/{path}"
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200 and "pdf" in r.headers.get("Content-Type", "").lower():
            return r.content
        logger.warning("PDF download failed for path %s (HTTP %s)", path, r.status_code)
        return None
    except requests.RequestException as exc:
        logger.error("PDF download error for path %s: %s", path, exc)
        return None


# ── PDF parsing ───────────────────────────────────────────────────────────────

_FIELD_PATTERNS: list[tuple[str, str]] = [
    (r"NOM\s*/\s*FONCTION[^:]*:\s*(.+)", "insider_and_position"),
    (r"NOTIFICATION INITIALE\s*/\s*MODIFICATION\s*:\s*(.+)", "notification_type"),
    (r"NOM\s*:\s*(.+)", "issuer_name"),
    (r"LEI\s*:\s*([A-Z0-9]{18,20})", "lei"),
    (r"DATE DE LA TRANSACTION\s*:\s*(.+)", "trade_date_raw"),
    (r"LIEU DE LA TRANSACTION\s*:\s*(.+)", "place"),
    (r"NATURE DE LA TRANSACTION\s*:\s*(.+)", "trade_type_raw"),
    (r"DESCRIPTION DE L.INSTRUMENT[^:]*:\s*(.+)", "instrument_type"),
    (r"CODE D.IDENTIFICATION[^:]*:\s*([A-Z]{2}[A-Z0-9]{10})", "isin_in_doc"),
    (r"PRIX UNITAIRE\s*:\s*([0-9\s.,]+(?:Euro|EUR|€|GBP|USD)?)", "price_raw"),
    (r"VOLUME\s*:\s*([0-9\s.,]+)", "volume_raw"),
    (r"DATE DE RECEPTION[^:]*:\s*(.+)", "filing_date_raw"),
]

_FR_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

_TRADE_TYPE_MAP = {
    "acquisition": "Buy",
    "achat": "Buy",
    "cession": "Sell",
    "vente": "Sell",
    "exercice": "Buy",
    "attribution": "Buy",
}


def _parse_pdf_text(text: str) -> dict:
    """Extract structured fields from AMF DD PDF text."""
    fields: dict[str, str] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = "\n".join(lines)

    for pattern, key in _FIELD_PATTERNS:
        if key in fields:
            continue
        m = re.search(pattern, full_text, re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()

    # Split "Jean DUPONT, Directeur Général" → name + position
    raw_iap = fields.get("insider_and_position", "")
    if raw_iap:
        parts = [p.strip() for p in raw_iap.rsplit(",", 1)]
        fields["insider_name"] = parts[0]
        fields["position_raw"] = parts[1] if len(parts) == 2 else ""

    # Aggregated section fallback for price/volume
    if "price_raw" not in fields:
        m = re.search(
            r"PRIX\s*:\s*([0-9\s.,]+(?:Euro|EUR|€|GBP|USD)?)", full_text, re.I
        )
        if m:
            fields["price_raw"] = m.group(1).strip()
    if "volume_raw" not in fields:
        m = re.search(r"VOLUME\s*:\s*([0-9\s.,]+)", full_text, re.I)
        if m:
            fields["volume_raw"] = m.group(1).strip()

    return fields


def _parse_french_date(raw: str) -> Optional[date]:
    raw = raw.strip().lower()
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    m = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m:
        day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = _FR_MONTHS.get(month_str)
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


def _parse_price(raw: str) -> tuple[Optional[float], str]:
    currency = "EUR"
    for sym, code in [
        ("euro", "EUR"),
        ("eur", "EUR"),
        ("€", "EUR"),
        ("gbp", "GBP"),
        ("£", "GBP"),
        ("usd", "USD"),
        ("$", "USD"),
    ]:
        if sym in raw.lower():
            currency = code
            break
    num_str = re.sub(r"[^\d.,]", "", raw.replace("\xa0", "").replace(" ", ""))
    num_str = num_str.replace(",", ".")
    try:
        return float(num_str), currency
    except ValueError:
        return None, currency


def _map_trade_type(raw: str) -> str:
    lower = raw.lower()
    for keyword, mapped in _TRADE_TYPE_MAP.items():
        if keyword in lower:
            return mapped
    return "Other"


def _item_to_trade(
    item: dict,
    pdf_bytes: bytes,
    original_isin: str,
) -> Optional[EuropeanInsiderTrade]:
    """Convert a search result item + PDF bytes into an EuropeanInsiderTrade."""
    doc_id = item.get("numero", "")
    detail_url = f"{_BDIF}/en/details/{doc_id}"

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        logger.error("PDF parse error for %s: %s", doc_id, exc)
        return None

    fields = _parse_pdf_text(text)

    if not fields.get("insider_name"):
        logger.debug("No insider name in PDF %s, skipping", doc_id)
        return None

    price, currency = _parse_price(fields.get("price_raw", ""))

    volume: Optional[float] = None
    vol_raw = fields.get("volume_raw", "")
    if vol_raw:
        vol_clean = re.sub(r"[^\d.,]", "", vol_raw.replace("\xa0", "").replace(" ", ""))
        try:
            volume = float(vol_clean.replace(",", "."))
        except ValueError:
            pass

    trade_date = _parse_french_date(fields.get("trade_date_raw", ""))

    # datePublication = filing date per AMF schema
    filing_date = _parse_iso_date(
        item.get("datePublication", "")
    ) or _parse_french_date(fields.get("filing_date_raw", ""))

    total_value: Optional[float] = None
    if price is not None and volume is not None:
        total_value = round(price * volume, 2)

    issuer_name = fields.get("issuer_name", "")
    if not issuer_name and item.get("societes"):
        issuer_name = item["societes"][0].get("raisonSociale", "")

    return EuropeanInsiderTrade(
        isin=fields.get("isin_in_doc", original_isin),
        issuer_name=issuer_name,
        country="FR",
        regulatory_body="AMF",
        insider_name=fields["insider_name"],
        position=normalize_position(fields.get("position_raw", "")),
        trade_date=trade_date,
        filing_date=filing_date,
        trade_type=_map_trade_type(fields.get("trade_type_raw", "")),
        instrument_type=fields.get("instrument_type", "Share"),
        volume=volume,
        price=price,
        currency=currency,
        total_value=total_value,
        source="amf_bdif",
        source_url=detail_url,
    )


# ── Public interface ──────────────────────────────────────────────────────────


def fetch_fr_trades(
    isin: str,
    since: Optional[date] = None,
    until: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> list[EuropeanInsiderTrade]:
    """
    Fetch AMF insider trade disclosures for a French ISIN.

    Strategy:
      1. GET /back/api/v1/informations?rechercheTexte={ISIN}&typesInformation=DD
         → paginated list of director disclosure filings, each with PDF path
      2. For each filing: GET /back/api/v1/documents/{path} → PDF bytes
      3. Parse PDF and return structured EuropeanInsiderTrade records

    No API key, jeton, or authentication required.

    Args:
        isin:    French ISIN (FR prefix).
        since:   Only return trades published on or after this date.
        until:   Only return trades published on or before this date.
        session: Optional requests.Session (created fresh if omitted).

    Returns:
        List of EuropeanInsiderTrade records, newest first.
    """
    isin = isin.upper().strip()
    sess = session or requests.Session()
    sess.headers.update(_HEADERS)

    logger.info("Fetching BDIF DD filings for ISIN %s", isin)
    items = _search_dd_documents(isin, since, until, sess)

    if not items:
        logger.info("No DD filings found for ISIN %s", isin)
        return []

    logger.info("Found %d DD filing(s) for %s", len(items), isin)

    trades: list[EuropeanInsiderTrade] = []
    for item in items:
        path = item["documents"][0]["path"]
        doc_id = item.get("numero", path)

        time.sleep(_REQUEST_DELAY)
        pdf_bytes = _download_pdf(path, sess)

        if pdf_bytes is None:
            logger.warning("Skipping %s — PDF download failed", doc_id)
            continue

        trade = _item_to_trade(item, pdf_bytes, isin)
        if trade is None:
            continue

        # Secondary date filter on actual trade date extracted from PDF
        if trade.trade_date:
            if since and trade.trade_date < since:
                continue
            if until and trade.trade_date > until:
                continue

        trades.append(trade)
        logger.debug(
            "Parsed: %s | %s | %s @ %s",
            trade.insider_name,
            trade.trade_type,
            trade.volume,
            trade.trade_date,
        )

    logger.info("Returning %d AMF trades for ISIN %s", len(trades), isin)
    return trades


# ── Latest (global, no ISIN filter) ─────────────────────────────────────────


def fetch_fr_latest(
    n: int = 50,
    since: Optional[date] = None,
    until: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> list[EuropeanInsiderTrade]:
    """Fetch the N most recent AMF Director Disclosure filings without ISIN filter.

    Uses GET /back/api/v1/informations?typesInformation=DD&From=0&Size=N.
    Confirmed from HAR analysis: the unfiltered call returns results ordered
    newest-first (most recently published first).

    Args:
        n:       Maximum number of trades to return.
        since:   Drop trades whose filing/publication date is before this date.
        until:   Drop trades whose filing/publication date is after this date.
        session: Optional requests.Session.
    """
    sess = session or requests.Session()
    sess.headers.update(_HEADERS)

    logger.info("Fetching %d latest AMF DD filings", n)

    params = {
        "typesInformation": "DD",
        "From": 0,
        "Size": n,
    }

    try:
        r = sess.get(_SEARCH_URL, params=params, timeout=15)
        r.raise_for_status()
    except requests.RequestException as exc:
        logger.error("AMF latest search failed: %s", exc)
        return []

    items = r.json().get("result", [])
    logger.info("AMF latest: %d items returned", len(items))

    trades: list[EuropeanInsiderTrade] = []
    for item in items:
        if not item.get("documents"):
            continue

        path = item["documents"][0]["path"]
        # ISIN is not in the API item metadata — it's extracted from the PDF
        # by _item_to_trade via the "CODE D'IDENTIFICATION" field.
        # Passing "" here is correct: _item_to_trade falls back to it only
        # if the PDF parser finds no ISIN, in which case "" is the honest value.
        isin = ""

        time.sleep(_REQUEST_DELAY)
        pdf_bytes = _download_pdf(path, sess)
        if pdf_bytes is None:
            logger.warning("Skipping %s — PDF download failed", item.get("numero"))
            continue

        trade = _item_to_trade(item, pdf_bytes, isin)
        if trade is None:
            continue

        if trade.filing_date:
            if since and trade.filing_date < since:
                continue
            if until and trade.filing_date > until:
                continue

        trades.append(trade)
        if len(trades) >= n:
            break

    logger.info("Returning %d latest FR trades", len(trades))
    return trades

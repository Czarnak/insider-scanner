"""Dutch insider trade scraper — AFM Directors' Dealings register.

Fetches insider trading disclosures from the AFM (Autoriteit Financiële
Markten) Directors' Dealings register under MAR Article 19.

The AFM provides a public search interface at:
https://www.afm.nl/en/professionals/registers/directors-dealings

The underlying API endpoint accepts GET requests with ISIN and date
parameters and returns JSON.

Note: AFM API endpoint paths may change.  Verify with browser devtools
against the AFM website if requests fail.  The AFM also periodically
publishes a downloadable register which can be used as a fallback.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position
from insider_scanner.utils.logging import get_logger

log = get_logger("afm")

_API_URL = "https://www.afm.nl/api/DealersDealings/DealerDealings/SearchDealing"
_DEFAULT_LOOKBACK_DAYS = 90
_DEFAULT_PAGE_SIZE = 100
_HEADERS = {
    "User-Agent": "InsiderScanner/0.1 (research)",
    "Accept": "application/json",
    "Referer": "https://www.afm.nl/",
}


def _parse_nl_date(text: str) -> date | None:
    """Parse date strings from AFM API responses."""
    if not text:
        return None
    text = str(text).strip()
    if "T" in text:
        text = text.split("T")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalise_trade_type(text: str) -> str:
    """Map AFM transaction type strings to Buy / Sell / Other."""
    if not text:
        return "Other"
    lower = text.lower()
    if any(w in lower for w in ("koop", "aankoop", "buy", "purchase", "acquisition")):
        return "Buy"
    if any(w in lower for w in ("verkoop", "sell", "disposal", "sale")):
        return "Sell"
    return "Other"


def _parse_record(record: dict, isin: str) -> EuropeanInsiderTrade | None:
    """Convert a single AFM API result record to an EuropeanInsiderTrade."""
    record_isin = (record.get("isin") or record.get("ISIN") or isin).strip()

    raw_position = (
        record.get("function") or record.get("position") or record.get("functie") or ""
    )

    # AFM may use different field name conventions
    insider_name = (
        record.get("personName")
        or record.get("name")
        or record.get("naam")
        or record.get("managerName")
        or ""
    ).strip()

    issuer_name = (
        record.get("issuerName")
        or record.get("emittent")
        or record.get("uitgevende")
        or record.get("companyName")
        or ""
    ).strip()

    volume_raw = record.get("volume") or record.get("aantal") or record.get("quantity")
    price_raw = record.get("price") or record.get("prijs") or record.get("unitPrice")
    total_raw = (
        record.get("totalValue") or record.get("totaalBedrag") or record.get("amount")
    )

    try:
        volume = (
            float(str(volume_raw).replace(",", ".")) if volume_raw is not None else None
        )
    except (TypeError, ValueError):
        volume = None

    try:
        price = (
            float(str(price_raw).replace(",", ".")) if price_raw is not None else None
        )
    except (TypeError, ValueError):
        price = None

    try:
        total_value = (
            float(str(total_raw).replace(",", ".")) if total_raw is not None else None
        )
    except (TypeError, ValueError):
        total_value = None

    if total_value is None:
        total_value = EuropeanInsiderTrade.compute_total_value(volume, price)

    currency = (record.get("currency") or record.get("valuta") or "EUR").strip()

    trade_type_raw = (
        record.get("transactionType")
        or record.get("typeTransactie")
        or record.get("nature")
        or ""
    )

    instrument_type = (
        record.get("instrumentType")
        or record.get("typeInstrument")
        or record.get("financialInstrument")
        or "Share"
    ).strip()

    source_url = (
        record.get("url") or record.get("sourceUrl") or record.get("link") or ""
    ).strip()

    return EuropeanInsiderTrade(
        isin=record_isin,
        issuer_name=issuer_name,
        country="NL",
        regulatory_body="AFM",
        insider_name=insider_name,
        position=normalize_position(raw_position),
        trade_date=_parse_nl_date(
            record.get("transactionDate") or record.get("datumTransactie") or ""
        ),
        filing_date=_parse_nl_date(
            record.get("publicationDate") or record.get("datumPublicatie") or ""
        ),
        trade_type=_normalise_trade_type(trade_type_raw),
        instrument_type=instrument_type,
        volume=volume,
        price=price,
        currency=currency,
        total_value=total_value,
        source="afm",
        source_url=source_url,
    )


def scrape_afm_trades(
    isin: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[EuropeanInsiderTrade]:
    """Fetch Directors' Dealings for an ISIN from the AFM register.

    Parameters
    ----------
    isin:
        12-character ISIN (e.g. ``NL0000009165`` for Heineken).
    date_from / date_to:
        Date range.  Defaults to last ``_DEFAULT_LOOKBACK_DAYS`` days.
    page_size:
        Results per page.

    Returns
    -------
    list of EuropeanInsiderTrade
    """
    today = date.today()
    effective_from = date_from or (today - timedelta(days=_DEFAULT_LOOKBACK_DAYS))
    effective_to = date_to or today

    session = requests.Session()
    session.headers.update(_HEADERS)

    all_records: list[dict] = []
    page = 1

    log.info(
        "Querying AFM for ISIN %s (%s → %s)",
        isin,
        effective_from,
        effective_to,
    )

    while True:
        params = {
            "isin": isin,
            "dateFrom": effective_from.strftime("%Y-%m-%d"),
            "dateTo": effective_to.strftime("%Y-%m-%d"),
            "pageNumber": page,
            "pageSize": page_size,
        }

        try:
            resp = session.get(_API_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("AFM API request failed for %s (page %d): %s", isin, page, exc)
            break

        # AFM API may return a list directly or a paginated wrapper
        if isinstance(data, list):
            records = data
            total_pages = 1
        else:
            records = (
                data.get("results")
                or data.get("items")
                or data.get("data")
                or data.get("content")
                or []
            )
            total = data.get("totalCount") or data.get("total") or 0
            total_pages = (total + page_size - 1) // page_size if total else 1

        all_records.extend(records)
        page += 1

        if not records or page > total_pages:
            break

    log.info("Retrieved %d raw records from AFM for %s", len(all_records), isin)

    trades: list[EuropeanInsiderTrade] = []
    for record in all_records:
        try:
            trade = _parse_record(record, isin)
            if trade:
                trades.append(trade)
        except Exception as exc:
            log.debug("Failed to parse AFM record: %s — %s", record, exc)

    log.info("Extracted %d trades for ISIN %s from AFM", len(trades), isin)
    return trades

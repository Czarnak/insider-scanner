"""Shared European scan orchestration used by GUI and CLI."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_merger import merge_eu_trades
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.utils.logging import get_logger

log = get_logger("eu_scan")


def _verify_isin(
    trades: list[EuropeanInsiderTrade],
    query_isin: str,
) -> list[EuropeanInsiderTrade]:
    """Filter out false-match records and assign the query ISIN to blank ones.

    Some scrapers (notably RNS/Investegate) perform full-text search rather
    than exact ISIN lookup.  Searching for ``FR0000131104`` can return UK
    announcements that merely mention that ISIN in passing — those records
    end up tagged with the wrong company name and a mismatched ISIN.

    Rules applied per record:
    - If the record's ISIN matches the query ISIN → keep as-is.
    - If the record has no ISIN (parser couldn't extract it) → drop.
      We cannot verify the match, so it's safer to discard.
    - If the record's ISIN does NOT match the query ISIN → drop.
      These are false positives from a full-text search engine.
    """
    result: list[EuropeanInsiderTrade] = []
    for t in trades:
        if not t.isin:
            # Parser could not extract any ISIN from the document.
            # Drop rather than assume — assigning the query ISIN would be wrong
            # if this is a false-positive from a full-text search engine.
            log.debug(
                "Dropping unverifiable record: no ISIN in document (queried %s, issuer=%s)",
                query_isin,
                t.issuer_name,
            )
        elif t.isin.upper() == query_isin.upper():
            result.append(t)
        else:
            log.debug(
                "Dropping false match: queried %s but record has ISIN %s (%s)",
                query_isin,
                t.isin,
                t.issuer_name,
            )
    return result


def scrape_eu_trades_for_isin(
    isin: str,
    country: str,
    date_from: date | None,
    date_to: date | None,
) -> list[EuropeanInsiderTrade]:
    """Dispatch scraping for one ISIN across the selected European sources."""
    trade_batches: list[list[EuropeanInsiderTrade]] = []

    run_uk = country in ("All", "UK")
    run_de = country in ("All", "DE")
    run_fr = country in ("All", "FR")
    run_nl = country in ("All", "NL")

    if run_uk:
        try:
            from insider_scanner.core.rns_investegate import fetch_uk_trades

            batch = fetch_uk_trades(isin, since=date_from, until=date_to)
            trade_batches.append(_verify_isin(batch, isin))
        except Exception as exc:
            log.warning("RNS scrape failed for %s: %s", isin, exc)

    if run_de:
        try:
            from insider_scanner.core.bafin import fetch_de_trades

            batch = fetch_de_trades(isin, since=date_from, until=date_to)
            trade_batches.append(_verify_isin(batch, isin))
        except Exception as exc:
            log.warning("BaFin scrape failed for %s: %s", isin, exc)

    if run_fr:
        try:
            from insider_scanner.core.amf import fetch_fr_trades

            batch = fetch_fr_trades(isin, since=date_from, until=date_to)
            trade_batches.append(_verify_isin(batch, isin))
        except Exception as exc:
            log.warning("AMF scrape failed for %s: %s", isin, exc)

    if run_nl:
        try:
            from insider_scanner.core.afm import scrape_afm_trades

            batch = scrape_afm_trades(isin, date_from=date_from, date_to=date_to)
            trade_batches.append(_verify_isin(batch, isin))
        except Exception as exc:
            log.warning("AFM scrape failed for %s: %s", isin, exc)

    return merge_eu_trades(*trade_batches) if trade_batches else []


def scrape_eu_latest(
    n: int,
    country: str,
    date_from: date | None,
    date_to: date | None,
) -> list[EuropeanInsiderTrade]:
    """Fetch the N most recent insider trades from each selected EU source.

    Unlike ``scrape_eu_trades_for_isin``, this function does not filter by
    ISIN. Each scraper fetches its own N most recent disclosures globally.
    Results are merged, deduplicated, and sorted newest-first.

    ``n`` is passed to each individual scraper — so with 3 active sources
    (UK + DE + FR) and n=50 you may receive up to ~150 trades before
    deduplication.

    Args:
        n:          Trades to request from each source.
        country:    "All" | "UK" | "DE" | "FR" | "NL"
        date_from:  Optional lower bound on trade/filing date.
        date_to:    Optional upper bound on trade/filing date.
    """
    trade_batches: list[list[EuropeanInsiderTrade]] = []

    run_uk = country in ("All", "UK")
    run_de = country in ("All", "DE")
    run_fr = country in ("All", "FR")
    # NL (AFM) has no global latest endpoint — stub returns []

    if run_uk:
        try:
            from insider_scanner.core.rns_investegate import fetch_uk_latest

            trade_batches.append(fetch_uk_latest(n=n, since=date_from, until=date_to))
        except Exception as exc:
            log.warning("RNS latest failed: %s", exc)

    if run_de:
        try:
            from insider_scanner.core.bafin import fetch_de_latest

            trade_batches.append(fetch_de_latest(n=n, since=date_from, until=date_to))
        except Exception as exc:
            log.warning("BaFin latest failed: %s", exc)

    if run_fr:
        try:
            from insider_scanner.core.amf import fetch_fr_latest

            trade_batches.append(fetch_fr_latest(n=n, since=date_from, until=date_to))
        except Exception as exc:
            log.warning("AMF latest failed: %s", exc)

    return merge_eu_trades(*trade_batches) if trade_batches else []

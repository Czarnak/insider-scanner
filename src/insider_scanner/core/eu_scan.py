"""Shared European scan orchestration used by GUI and CLI."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_merger import merge_eu_trades
from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.utils.logging import get_logger

log = get_logger("eu_scan")


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

            trade_batches.append(
                fetch_uk_trades(isin, since=date_from, until=date_to)
            )
        except Exception as exc:
            log.warning("RNS scrape failed for %s: %s", isin, exc)

    if run_de:
        try:
            from insider_scanner.core.bafin import fetch_de_trades

            trade_batches.append(
                fetch_de_trades(isin, since=date_from, until=date_to)
            )
        except Exception as exc:
            log.warning("BaFin scrape failed for %s: %s", isin, exc)

    if run_fr:
        try:
            from insider_scanner.core.amf import fetch_fr_trades

            trade_batches.append(
                fetch_fr_trades(isin, since=date_from, until=date_to)
            )
        except Exception as exc:
            log.warning("AMF scrape failed for %s: %s", isin, exc)

    if run_nl:
        try:
            from insider_scanner.core.afm import scrape_afm_trades

            trade_batches.append(
                scrape_afm_trades(isin, date_from=date_from, date_to=date_to)
            )
        except Exception as exc:
            log.warning("AFM scrape failed for %s: %s", isin, exc)

    return merge_eu_trades(*trade_batches) if trade_batches else []

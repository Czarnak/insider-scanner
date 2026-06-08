"""Default adapters around the existing scraper functions."""

from __future__ import annotations

from datetime import date

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.services.common import (
    BoundedSourceAdapter,
    LatestSourceAdapter,
)

EUROPEAN_BOUNDED_COVERAGE_SOURCES = frozenset({"rns", "bafin", "afm"})


def default_us_adapters() -> dict[str, BoundedSourceAdapter[InsiderTrade]]:
    from insider_scanner.core import openinsider, secform4

    return {
        "secform4": lambda ticker, start, end, use_cache: secform4.scrape_ticker(
            ticker or "",
            use_cache=use_cache,
            start_date=start,
            end_date=end,
        ),
        "openinsider": lambda ticker, start, end, use_cache: openinsider.scrape_ticker(
            ticker or "",
            use_cache=use_cache,
            start_date=start,
            end_date=end,
        ),
    }


def default_us_latest_adapters() -> dict[str, LatestSourceAdapter[InsiderTrade]]:
    from insider_scanner.core.openinsider import scrape_latest

    return {
        "openinsider": lambda count, use_cache, start, end: scrape_latest(
            count=count,
            use_cache=use_cache,
            start_date=start,
            end_date=end,
        )
    }


def default_congress_adapters() -> dict[str, BoundedSourceAdapter[CongressTrade]]:
    from insider_scanner.core.congress_house import scrape_house_trades
    from insider_scanner.core.congress_senate import scrape_senate_trades

    return {
        "house": lambda official, start, end, _use_cache: scrape_house_trades(
            official_name=official,
            date_from=start,
            date_to=end,
        ),
        "senate": lambda official, start, end, _use_cache: scrape_senate_trades(
            official_name=official,
            date_from=start,
            date_to=end,
        ),
    }


def default_european_adapters() -> dict[
    str, BoundedSourceAdapter[EuropeanInsiderTrade]
]:
    from insider_scanner.core.afm import scrape_afm_trades
    from insider_scanner.core.amf import fetch_fr_trades
    from insider_scanner.core.bafin import fetch_de_trades
    from insider_scanner.core.rns_investegate import fetch_uk_trades

    return {
        "rns": lambda isin, start, end, _use_cache: fetch_uk_trades(
            isin or "", since=start, until=end
        ),
        "bafin": lambda isin, start, end, _use_cache: fetch_de_trades(
            isin or "", since=start, until=end
        ),
        "amf": lambda isin, start, end, _use_cache: fetch_fr_trades(
            isin or "", since=start, until=end
        ),
        "afm": lambda isin, start, end, _use_cache: scrape_afm_trades(
            isin or "", date_from=start, date_to=end
        ),
    }


def default_european_latest_adapters() -> dict[
    str, LatestSourceAdapter[EuropeanInsiderTrade]
]:
    from insider_scanner.core.amf import fetch_fr_latest
    from insider_scanner.core.bafin import fetch_de_latest
    from insider_scanner.core.rns_investegate import fetch_uk_latest

    def adapt(function):
        def fetch(
            count: int,
            _use_cache: bool,
            start_date: date | None,
            end_date: date | None,
        ) -> list[EuropeanInsiderTrade]:
            return function(n=count, since=start_date, until=end_date)

        return fetch

    return {
        "rns": adapt(fetch_uk_latest),
        "bafin": adapt(fetch_de_latest),
        "amf": adapt(fetch_fr_latest),
    }

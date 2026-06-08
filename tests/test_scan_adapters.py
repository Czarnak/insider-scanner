"""Default scan adapters delegate to current scraper APIs without network."""

from __future__ import annotations

from datetime import date

from insider_scanner.services.adapters import (
    default_congress_adapters,
    default_european_adapters,
    default_european_latest_adapters,
    default_us_adapters,
    default_us_latest_adapters,
)


def test_default_us_adapters_forward_identifier_dates_and_cache(monkeypatch):
    calls = []

    def scrape(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return []

    monkeypatch.setattr("insider_scanner.core.secform4.scrape_ticker", scrape)
    monkeypatch.setattr("insider_scanner.core.openinsider.scrape_ticker", scrape)
    adapters = default_us_adapters()
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)

    adapters["secform4"]("AAPL", start, end, False)
    adapters["openinsider"]("AAPL", start, end, True)

    assert calls == [
        (
            "AAPL",
            {"use_cache": False, "start_date": start, "end_date": end},
        ),
        (
            "AAPL",
            {"use_cache": True, "start_date": start, "end_date": end},
        ),
    ]


def test_default_us_latest_adapter_forwards_overlap_request(monkeypatch):
    calls = []

    def scrape(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr("insider_scanner.core.openinsider.scrape_latest", scrape)
    adapter = default_us_latest_adapters()["openinsider"]
    start = date(2026, 2, 1)
    end = date(2026, 2, 2)

    adapter(200, False, start, end)

    assert calls == [
        {
            "count": 200,
            "use_cache": False,
            "start_date": start,
            "end_date": end,
        }
    ]


def test_default_congress_adapters_forward_official_and_range(monkeypatch):
    calls = []

    def house(**kwargs):
        calls.append(("house", kwargs))
        return []

    def senate(**kwargs):
        calls.append(("senate", kwargs))
        return []

    monkeypatch.setattr(
        "insider_scanner.core.congress_house.scrape_house_trades", house
    )
    monkeypatch.setattr(
        "insider_scanner.core.congress_senate.scrape_senate_trades", senate
    )
    adapters = default_congress_adapters()
    start = date(2026, 3, 1)
    end = date(2026, 3, 2)

    adapters["house"]("Jane Doe", start, end, True)
    adapters["senate"](None, start, end, False)

    assert calls == [
        (
            "house",
            {
                "official_name": "Jane Doe",
                "date_from": start,
                "date_to": end,
            },
        ),
        (
            "senate",
            {"official_name": None, "date_from": start, "date_to": end},
        ),
    ]


def test_default_european_adapters_cover_all_country_sources(monkeypatch):
    calls = []

    def record(source):
        def fetch(isin, **kwargs):
            calls.append((source, isin, kwargs))
            return []

        return fetch

    monkeypatch.setattr(
        "insider_scanner.core.rns_investegate.fetch_uk_trades",
        record("rns"),
    )
    monkeypatch.setattr(
        "insider_scanner.core.bafin.fetch_de_trades",
        record("bafin"),
    )
    monkeypatch.setattr(
        "insider_scanner.core.amf.fetch_fr_trades",
        record("amf"),
    )
    monkeypatch.setattr(
        "insider_scanner.core.afm.scrape_afm_trades",
        record("afm"),
    )
    adapters = default_european_adapters()
    start = date(2026, 4, 1)
    end = date(2026, 4, 2)

    for source, adapter in adapters.items():
        adapter("GB0002875804", start, end, True)

    assert set(adapters) == {"rns", "bafin", "amf", "afm"}
    assert {call[0] for call in calls} == set(adapters)


def test_default_european_latest_excludes_afm(monkeypatch):
    calls = []

    def record(source):
        def fetch(**kwargs):
            calls.append((source, kwargs))
            return []

        return fetch

    monkeypatch.setattr(
        "insider_scanner.core.rns_investegate.fetch_uk_latest",
        record("rns"),
    )
    monkeypatch.setattr(
        "insider_scanner.core.bafin.fetch_de_latest",
        record("bafin"),
    )
    monkeypatch.setattr(
        "insider_scanner.core.amf.fetch_fr_latest",
        record("amf"),
    )
    adapters = default_european_latest_adapters()

    for source, adapter in adapters.items():
        assert adapter(50, True, None, None) == []

    assert set(adapters) == {"rns", "bafin", "amf"}
    assert {call[0] for call in calls} == {"rns", "bafin", "amf"}


def test_bounded_french_adapter_uses_full_trade_fetch_not_latest(monkeypatch):
    calls = []

    def bounded(isin, **kwargs):
        calls.append(("bounded", isin, kwargs))
        return []

    def latest(**kwargs):
        calls.append(("latest", kwargs))
        return []

    monkeypatch.setattr("insider_scanner.core.amf.fetch_fr_trades", bounded)
    monkeypatch.setattr("insider_scanner.core.amf.fetch_fr_latest", latest)
    adapter = default_european_adapters()["amf"]
    start = date(2026, 5, 1)
    end = date(2026, 5, 31)

    adapter(None, start, end, True)

    assert calls == [
        ("bounded", "", {"since": start, "until": end}),
    ]

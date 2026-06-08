"""Cross-domain scan failure and cancellation semantics."""

from __future__ import annotations

from datetime import date

import pytest

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services import (
    CongressScanService,
    EuropeanScanService,
    ScanError,
    open_persistence,
)


def test_congress_source_error_preserves_uncovered_interval(tmp_path):
    context = open_persistence(tmp_path / "congress-error.sqlite3")

    def fail(_official, _start, _end, _use_cache):
        raise ConnectionError("offline")

    service = CongressScanService(context, adapters={"house": fail})
    try:
        with pytest.raises(ScanError, match="house.*2026-01-01") as exc:
            service.scan(
                "Jane Doe",
                sources=("house",),
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            )

        assert isinstance(exc.value.__cause__, ConnectionError)
        assert context.coverage.get("congress", "Jane Doe", "house") == ()
    finally:
        context.close()


def test_eu_cancellation_after_fetch_discards_current_interval(tmp_path):
    context = open_persistence(tmp_path / "eu-cancel.sqlite3")
    cancelled = False

    def fetch(isin, start, _end, _use_cache):
        nonlocal cancelled
        cancelled = True
        return [
            EuropeanInsiderTrade(
                isin=isin or "",
                issuer_name="Example PLC",
                country="UK",
                regulatory_body="FCA",
                insider_name="Jane Doe",
                position="Director",
                trade_date=start,
                filing_date=start,
                trade_type="Buy",
                source="rns",
            )
        ]

    service = EuropeanScanService(context, adapters={"rns": fetch})
    try:
        result = service.scan(
            "GB0002875804",
            country="UK",
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 2),
            cancelled=lambda: cancelled,
        )

        assert result == []
        assert context.coverage.get("eu", "GB0002875804", "rns") == ()
        assert context.european_trades.query("GB0002875804") == []
    finally:
        context.close()


def test_eu_drops_unverifiable_blank_and_false_match_isins(tmp_path):
    context = open_persistence(tmp_path / "eu-verification.sqlite3")

    def fetch(_isin, start, _end, _use_cache):
        base = EuropeanInsiderTrade(
            issuer_name="Example PLC",
            country="UK",
            regulatory_body="FCA",
            insider_name="Jane Doe",
            position="Director",
            trade_date=start,
            filing_date=start,
            trade_type="Buy",
            source="rns",
        )
        return [
            base,
            EuropeanInsiderTrade(**{**base.to_dict(), "isin": "FR0000131104"}),
        ]

    service = EuropeanScanService(context, adapters={"rns": fetch})
    try:
        result = service.scan(
            "GB0002875804",
            country="UK",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 1),
        )

        assert result == []
        assert context.coverage.get("eu", "GB0002875804", "rns") == (
            DateInterval(date(2026, 3, 1), date(2026, 3, 1)),
        )
    finally:
        context.close()


def test_congress_empty_success_is_covered_without_rows(tmp_path):
    context = open_persistence(tmp_path / "congress-empty.sqlite3")
    calls = 0

    def fetch(_official, _start, _end, _use_cache):
        nonlocal calls
        calls += 1
        return []

    service = CongressScanService(context, adapters={"senate": fetch})
    try:
        for _ in range(2):
            assert (
                service.scan(
                    "*",
                    sources=("senate",),
                    start_date=date(2026, 4, 1),
                    end_date=date(2026, 4, 2),
                )
                == []
            )

        assert calls == 1
    finally:
        context.close()

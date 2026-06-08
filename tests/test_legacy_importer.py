from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.services.context import open_persistence
from insider_scanner.services.importer import import_legacy_path

DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES = 50 * 1024 * 1024


def _us_record() -> dict:
    return {
        "ticker": "AAPL",
        "company": "Apple",
        "insider_name": "Jane Doe",
        "trade_type": "Buy",
        "trade_date": "2025-01-02",
        "filing_date": "2025-01-03",
        "shares": 10,
        "price": 100,
        "value": 1000,
        "source": "openinsider",
    }


def _congress_record() -> dict:
    return {
        "official_name": "Doe Jane",
        "chamber": "House",
        "filing_date": "2025-01-04",
        "doc_id": "20012345",
        "trade_date": "2025-01-02",
        "asset_description": "Apple Inc (AAPL)",
        "ticker": "AAPL",
        "trade_type": "Purchase",
        "amount_range": "$1,001 - $15,000",
        "amount_low": 1001,
        "amount_high": 15000,
        "source": "house",
    }


def _eu_record() -> dict:
    return {
        "isin": "GB0002875804",
        "issuer_name": "Example PLC",
        "country": "UK",
        "regulatory_body": "FCA",
        "insider_name": "Jane Doe",
        "trade_date": "2025-01-02",
        "filing_date": "2025-01-03",
        "trade_type": "Buy",
        "source": "rns",
    }


def test_imports_supported_json_shapes_and_is_idempotent(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "us.json").write_text(json.dumps([_us_record()]), encoding="utf-8")
    (root / "congress.json").write_text(
        json.dumps([_congress_record()]), encoding="utf-8"
    )
    nested = root / "nested"
    nested.mkdir()
    (nested / "eu.json").write_text(json.dumps([_eu_record()]), encoding="utf-8")
    source_bytes = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")
    }

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        refreshed_at = datetime(2025, 1, 5, 12, tzinfo=UTC)
        persistence.refresh.set("us", "*", "openinsider", "latest:200", refreshed_at)
        first = import_legacy_path(root, persistence)
        second = import_legacy_path(root, persistence)

        assert first.imported == 3
        assert first.inserted == 3
        assert first.updated == 0
        assert first.skipped == 0
        assert first.errors == 0
        assert second.imported == 0
        assert second.inserted == 0
        assert second.updated == 0
        assert second.skipped == 3
        assert len(persistence.us_trades.query("AAPL")) == 1
        assert len(persistence.congress_trades.query("Doe Jane")) == 1
        assert len(persistence.european_trades.query("GB0002875804")) == 1
        interval = DateInterval(date(2025, 1, 1), date(2025, 1, 31))
        assert persistence.coverage.gaps("us", "AAPL", "openinsider", interval) == (
            interval,
        )
        assert (
            persistence.refresh.get("us", "*", "openinsider", "latest:200")
            == refreshed_at
        )
        assert {
            path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")
        } == source_bytes
    finally:
        persistence.close()


def test_continues_across_invalid_files_and_records(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "mixed.json").write_text(
        json.dumps([_us_record(), "not-an-object", {"unknown": True}]),
        encoding="utf-8",
    )
    (root / "not-list.json").write_text(json.dumps({"ticker": "AAPL"}))
    (root / "broken.json").write_text("{")

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(root, persistence)
    finally:
        persistence.close()

    assert report.imported == 1
    assert report.skipped == 0
    assert report.errors == 4
    assert len(report.files) == 3
    categories = {
        message.split(":")[-1].strip()
        for item in report.files
        for message in item.messages
    }
    assert categories <= {
        "invalid_record",
        "invalid_document",
        "malformed_json",
    }


def test_import_rejects_incomplete_collision_prone_and_invalid_typed_records(tmp_path):
    records = [
        {**_us_record(), "ticker": " "},
        {**_us_record(), "insider_name": ""},
        {**_us_record(), "source": ""},
        {**_us_record(), "trade_date": "", "filing_date": ""},
        {**_us_record(), "shares": "not-a-number"},
        {**_congress_record(), "doc_id": "", "source_url": ""},
        {**_congress_record(), "official_name": 123},
        {**_congress_record(), "source": ""},
        {**_congress_record(), "trade_date": "", "filing_date": ""},
        {**_eu_record(), "isin": "GB123"},
        {**_eu_record(), "insider_name": ""},
        {**_eu_record(), "source": ""},
        {**_eu_record(), "trade_date": "", "source_url": ""},
        {**_eu_record(), "price": {"secret": "raw-detail"}},
    ]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(path, persistence)
        assert persistence.us_trades.query("AAPL") == []
        assert persistence.congress_trades.query() == []
        assert persistence.european_trades.query("GB0002875804") == []
    finally:
        persistence.close()

    assert report.inserted == 0
    assert report.updated == 0
    assert report.skipped == 0
    assert report.errors == len(records)
    assert all(
        message.endswith("invalid_record") for message in report.files[0].messages
    )
    assert "raw-detail" not in " ".join(report.files[0].messages)


def test_import_reports_updated_when_duplicate_enriches_existing_record(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([_us_record()]), encoding="utf-8")
    enriched = {**_us_record(), "insider_title": "CFO"}

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        first = import_legacy_path(path, persistence)
        path.write_text(json.dumps([enriched]), encoding="utf-8")
        second = import_legacy_path(path, persistence)
    finally:
        persistence.close()

    assert first.inserted == 1
    assert second.updated == 1
    assert second.inserted == 0
    assert second.skipped == 0


def test_import_normalizes_supported_sources_tickers_and_domain_enums(tmp_path):
    records = [
        {**_us_record(), "ticker": "brk.b", "source": "EDGAR"},
        {
            **_congress_record(),
            "ticker": "msft",
            "source": "SENATE",
            "chamber": "senate",
        },
        {
            **_eu_record(),
            "isin": "fr0000131104",
            "country": "fr",
            "regulatory_body": "amf",
            "source": "amf_bdif",
        },
    ]
    path = tmp_path / "normalized.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(path, persistence)
        us_trade = persistence.us_trades.query("BRK.B")[0]
        congress_trade = persistence.congress_trades.query("Doe Jane")[0]
        european_trade = persistence.european_trades.query("FR0000131104")[0]
    finally:
        persistence.close()

    assert report.errors == 0
    assert us_trade.ticker == "BRK.B"
    assert us_trade.source == "edgar"
    assert congress_trade.ticker == "MSFT"
    assert congress_trade.source == "senate"
    assert congress_trade.chamber == "Senate"
    assert european_trade.country == "FR"
    assert european_trade.regulatory_body == "AMF"
    assert european_trade.source == "amf"


def test_import_rejects_unreachable_sources_tickers_and_mismatched_eu_enums(tmp_path):
    records = [
        {**_us_record(), "ticker": "BAD TICKER"},
        {**_us_record(), "source": "unknown"},
        {**_congress_record(), "ticker": "../../etc"},
        {**_congress_record(), "source": "committee"},
        {**_congress_record(), "source": "house", "chamber": "Senate"},
        {**_eu_record(), "country": "US"},
        {**_eu_record(), "source": "unknown"},
        {**_eu_record(), "country": "FR", "source": "rns"},
        {**_eu_record(), "regulatory_body": "BaFin"},
    ]
    path = tmp_path / "invalid-enums.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(path, persistence)
    finally:
        persistence.close()

    assert report.imported == 0
    assert report.errors == len(records)


def test_directory_import_does_not_follow_symlink_outside_root(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.json").write_text(json.dumps([_us_record()]))
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(root, persistence)
        assert report.imported == 0
        assert persistence.us_trades.query("AAPL") == []
    finally:
        persistence.close()


def test_rejects_missing_non_json_and_non_file_inputs(tmp_path):
    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        for path in (
            tmp_path / "missing",
            tmp_path / "legacy.csv",
        ):
            if path.suffix:
                path.write_text("ticker")
            try:
                import_legacy_path(path, persistence)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{path} should be rejected")
    finally:
        persistence.close()


def test_rejects_oversized_file_before_reading(tmp_path):
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES + 1)

    persistence = open_persistence(tmp_path / "db.sqlite3")
    try:
        report = import_legacy_path(path, persistence)
    finally:
        persistence.close()

    assert report.imported == 0
    assert report.errors == 1
    assert report.files[0].messages == ("file: file_too_large",)


def test_persistence_failure_log_is_detailed_but_sanitized(tmp_path, caplog):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([_us_record()]), encoding="utf-8")

    class FailingRepository:
        def upsert(self, trades):
            raise RuntimeError("SELECT * FROM trades WHERE secret='raw-record'")

    persistence = SimpleNamespace(
        us_trades=FailingRepository(),
        congress_trades=FailingRepository(),
        european_trades=FailingRepository(),
    )

    report = import_legacy_path(path, persistence)

    assert report.errors == 1
    assert "Legacy record persistence failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "SELECT *" not in caplog.text
    assert "raw-record" not in caplog.text

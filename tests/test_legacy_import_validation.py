"""Boundary validation for boolean flags and Windows JSON exports."""

from __future__ import annotations

import json

import pytest

from insider_scanner.services.importer import import_legacy_path

_US_RECORD = {
    "ticker": "AAPL",
    "insider_name": "Jane Doe",
    "trade_date": "2025-01-02",
    "source": "openinsider",
}
_CONGRESS_RECORD = {
    "official_name": "Doe Jane",
    "chamber": "House",
    "doc_id": "20012345",
    "trade_date": "2025-01-02",
    "source": "house",
}
_EU_RECORD = {
    "isin": "GB0002875804",
    "country": "UK",
    "regulatory_body": "FCA",
    "insider_name": "Jane Doe",
    "trade_date": "2025-01-02",
    "source": "rns",
}
_BOOLEAN_CASES = (
    (
        "us",
        _US_RECORD,
        "AAPL",
        (
            "is_congress",
            "is_amendment",
            "is_derivative",
            "shares_missing",
            "price_missing",
            "value_missing",
            "shares_owned_after_missing",
        ),
    ),
    (
        "congress",
        _CONGRESS_RECORD,
        "Doe Jane",
        ("is_amendment", "filing_complete"),
    ),
)


@pytest.mark.parametrize("domain,record,identifier,flags", _BOOLEAN_CASES)
@pytest.mark.parametrize("value", ("false", "true", 0, 1, None, [], {}))
def test_import_rejects_non_boolean_flags_before_persistence(
    tmp_path, persistence, domain, record, identifier, flags, value
):
    path = tmp_path / f"{domain}.json"
    invalid = [{**record, flag: value} for flag in flags]
    path.write_text(json.dumps([*invalid, record]), encoding="utf-8")

    report = import_legacy_path(path, persistence)

    assert report.inserted == 1
    assert report.errors == len(flags)
    assert report.files[0].messages == tuple(
        f"record {index}: invalid_record" for index in range(len(flags))
    )
    assert len(getattr(persistence, f"{domain}_trades").query(identifier)) == 1


@pytest.mark.parametrize("domain,record,identifier,flags", _BOOLEAN_CASES)
@pytest.mark.parametrize("value", (True, False))
def test_import_preserves_all_valid_boolean_flags(
    tmp_path, persistence, domain, record, identifier, flags, value
):
    path = tmp_path / f"{domain}.json"
    path.write_text(
        json.dumps([{**record, **dict.fromkeys(flags, value)}]), encoding="utf-8"
    )

    report = import_legacy_path(path, persistence)

    assert report.inserted == 1
    assert report.errors == 0
    trade = getattr(persistence, f"{domain}_trades").query(identifier)[0]
    assert all(getattr(trade, flag) is value for flag in flags)


@pytest.mark.parametrize(
    "domain,record,identifier",
    (
        ("us", _US_RECORD, "AAPL"),
        ("congress", _CONGRESS_RECORD, "Doe Jane"),
        ("european", _EU_RECORD, "GB0002875804"),
    ),
)
def test_import_accepts_utf8_bom_without_changing_export(
    tmp_path, persistence, domain, record, identifier
):
    path = tmp_path / f"{domain}.json"
    path.write_text(json.dumps([record]), encoding="utf-8-sig")
    original = path.read_bytes()

    report = import_legacy_path(path, persistence)

    assert report.inserted == 1
    assert report.errors == 0
    assert len(getattr(persistence, f"{domain}_trades").query(identifier)) == 1
    assert path.read_bytes() == original

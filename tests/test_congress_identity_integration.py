"""Congress search identity stays consistent across sources, storage and views."""

import json
from datetime import date

import pytest

from insider_scanner.core.congress_identity import identity_for
from insider_scanner.gui.congress_tab import filter_congress_trades
from insider_scanner.services.congress import CongressScanService
from tests.factories import congress_trade


@pytest.fixture
def member_file(monkeypatch, tmp_path):
    from insider_scanner.utils import config

    path = tmp_path / "members.json"
    path.write_text(
        json.dumps(
            [
                {
                    "bioguide_id": "B001319",
                    "first_name": "Katie",
                    "last_name": "Britt",
                    "official_name": "Katie Boyd Britt",
                    "name": "Britt Katie",
                    "chamber": "Senate",
                    "sector": ["Finance"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONGRESS_FILE", path)
    return path


def test_alias_scans_reuse_coverage_and_return_source_names(persistence, member_file):
    calls = []

    def adapter(official, start, end, cache):
        calls.append(official)
        return [
            congress_trade(
                official_name="Hon. Katie Britt",
                filing_date=start,
                source="senate",
                chamber="Senate",
            )
        ]

    service = CongressScanService(
        persistence, adapters={"senate": adapter}, today=lambda: date(2026, 11, 14)
    )
    for query in ("Britt", "BRITT KATIE", "Katie Boyd Britt", identity_for("Britt")):
        result = service.scan(
            query,
            sources=("senate",),
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 2),
        )
        assert [trade.official_name for trade in result] == ["Hon. Katie Britt"]
    assert calls == ["Katie Boyd Britt"]
    assert persistence.coverage.get("congress", identity_for("Britt").key, "senate")
    assert persistence.congress_trades.query()[0].official_name == "Hon. Katie Britt"


@pytest.mark.parametrize("query", ["Katie Boyd Britt", "britt katie", "Britt"])
def test_repository_matches_legacy_names_with_source_and_date_bounds(
    persistence, member_file, query
):
    expected = congress_trade(
        official_name="Hon. Katie Britt", source="senate", filing_date=date(2026, 10, 1)
    )
    persistence.congress_trades.upsert(
        [
            expected,
            congress_trade(official_name="Katie Britton", source="senate"),
            congress_trade(official_name="Katie Britt", source="house"),
            congress_trade(
                official_name="Katie Britt",
                source="senate",
                filing_date=date(2026, 9, 1),
            ),
        ]
    )
    assert persistence.congress_trades.query(
        query, source="senate", start_date=date(2026, 10, 1), end_date=date(2026, 10, 1)
    ) == [expected]


def test_sector_filter_resolves_source_spelling(member_file):
    trade = congress_trade(official_name="Hon. Katie Britt")
    assert filter_congress_trades(
        [trade], sector="Finance", member_sectors={"Katie Boyd Britt": ["Finance"]}
    ) == [trade]

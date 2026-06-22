"""Persistence repository behavior tests."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from itertools import permutations
from typing import Any

import pytest
from sqlalchemy import event

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UpsertResult,
    UsTradeRepository,
)


@pytest.fixture
def engine(tmp_path):
    engine = create_sqlite_engine(tmp_path / "repositories.sqlite3")
    bootstrap_database(engine)
    yield engine
    engine.dispose()


def _us(**overrides) -> InsiderTrade:
    values = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "trade_type": "Sell",
        "trade_date": date(2026, 1, 5),
        "filing_date": date(2026, 1, 7),
        "shares": 105.0,
        "price": 200.0,
        "value": 21_000.0,
        "shares_owned_after": 500_000.0,
        "source": "secform4",
        "edgar_url": "",
        "is_congress": False,
        "congress_member": "",
    }
    values.update(overrides)
    return InsiderTrade(**values)


def _congress(**overrides) -> CongressTrade:
    values = {
        "official_name": "Jane Doe",
        "chamber": "House",
        "party": "I",
        "filing_date": date(2026, 2, 2),
        "doc_id": "20012345",
        "source_url": "https://example.test/ptr",
        "trade_date": date(2026, 1, 30),
        "asset_description": "Example Corp",
        "ticker": "EXM",
        "trade_type": "Purchase",
        "owner": "Spouse",
        "amount_range": "$1,001 - $15,000",
        "amount_low": 1001.0,
        "amount_high": 15_000.0,
        "comment": "Joint account",
        "source": "house",
    }
    values.update(overrides)
    return CongressTrade(**values)


def _eu(**overrides) -> EuropeanInsiderTrade:
    values = {
        "isin": "GB0002875804",
        "issuer_name": "Example PLC",
        "country": "UK",
        "regulatory_body": "FCA",
        "insider_name": "Jane Doe",
        "position": "Director",
        "trade_date": date(2026, 3, 4),
        "filing_date": date(2026, 3, 5),
        "trade_type": "Buy",
        "instrument_type": "Share",
        "volume": 100.0,
        "price": 2.5,
        "currency": "GBP",
        "total_value": 250.0,
        "source": "rns",
        "source_url": "https://example.test/rns",
    }
    values.update(overrides)
    return EuropeanInsiderTrade(**values)


def _synchronize_table_reads(engine, table_name: str):
    barrier = threading.Barrier(2)

    def synchronize(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        normalized = statement.upper()
        if normalized.lstrip().startswith(
            "SELECT"
        ) and f"FROM {table_name.upper()}" in (normalized):
            try:
                barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

    event.listen(engine, "after_cursor_execute", synchronize)
    return synchronize


def test_us_round_trip_filters_order_and_returns_fresh_instances(engine):
    repo = UsTradeRepository(engine)
    repo.upsert(
        [
            _us(filing_date=date(2026, 1, 7)),
            _us(
                ticker="MSFT",
                insider_name="Satya Nadella",
                filing_date=date(2026, 1, 8),
                source="openinsider",
            ),
            _us(
                insider_name="Other Insider",
                filing_date=date(2026, 1, 9),
                source="edgar",
            ),
        ]
    )

    first = repo.query(
        " aapl ",
        sources={"edgar", "secform4"},
        start_date=date(2026, 1, 7),
        end_date=date(2026, 1, 9),
    )
    second = repo.query("AAPL")

    assert [trade.filing_date for trade in first] == [
        date(2026, 1, 9),
        date(2026, 1, 7),
    ]
    assert first[0] == second[0]
    assert first[0] is not second[0]
    assert repo.query("AAPL", sources="edgar") == [first[0]]


def test_trade_queries_filter_trade_dates_independently_of_filing_dates(engine):
    us_repository = UsTradeRepository(engine)
    us_visible = _us(
        insider_name="Visible Insider",
        trade_date=date(2026, 1, 5),
        filing_date=date(2027, 1, 2),
    )
    us_repository.upsert(
        [
            us_visible,
            _us(
                insider_name="Old Insider",
                trade_date=date(2025, 12, 31),
                filing_date=date(2026, 1, 2),
            ),
        ]
    )

    congress_repository = CongressTradeRepository(engine)
    congress_visible = _congress(
        doc_id="visible",
        trade_date=date(2026, 1, 6),
        filing_date=date(2027, 1, 2),
    )
    congress_repository.upsert(
        [
            congress_visible,
            _congress(
                doc_id="old",
                trade_date=date(2025, 12, 31),
                filing_date=date(2026, 1, 2),
            ),
        ]
    )

    assert us_repository.query(
        "AAPL",
        trade_start_date=date(2026, 1, 1),
        trade_end_date=date(2026, 12, 31),
    ) == [us_visible]
    assert congress_repository.query(
        trade_start_date=date(2026, 1, 1),
        trade_end_date=date(2026, 12, 31),
    ) == [congress_visible]


def test_upsert_reports_insert_update_and_unchanged(engine):
    repository = UsTradeRepository(engine)

    inserted = repository.upsert((_us(),))
    unchanged = repository.upsert((_us(),))
    updated = repository.upsert((_us(insider_title="CFO"),))

    assert (inserted.inserted, inserted.updated, inserted.skipped) == (1, 0, 0)
    assert (unchanged.inserted, unchanged.updated, unchanged.skipped) == (0, 0, 1)
    assert (updated.inserted, updated.updated, updated.skipped) == (0, 1, 0)


@pytest.mark.parametrize("richer_first", [True, False])
def test_us_upsert_is_order_independent_and_preserves_source_provenance(
    engine, richer_first
):
    repo = UsTradeRepository(engine)
    poorer = _us(source="secform4", company="", edgar_url="")
    richer = _us(
        source="edgar",
        company="Apple Inc.",
        edgar_url="https://sec.test/filing",
        is_congress=True,
        congress_member="Representative Doe",
    )
    originals = deepcopy((poorer, richer))

    repo.upsert([richer, poorer] if richer_first else [poorer, richer])

    assert repo.query("AAPL", sources={"secform4"}) == repo.query(
        "AAPL", sources={"edgar"}
    )
    result = repo.query("AAPL")[0]
    assert result.company == "Apple Inc."
    assert result.edgar_url == "https://sec.test/filing"
    assert result.is_congress is True
    assert result.congress_member == "Representative Doe"
    assert (poorer, richer) == originals


def test_us_upsert_is_order_independent_for_three_enriching_duplicates(tmp_path):
    trades = (
        _us(
            source="secform4",
            insider_title="Chief Executive Officer",
            edgar_url="",
        ),
        _us(
            source="edgar",
            company="",
            insider_title="",
            filing_date=None,
            price=0.0,
            value=0.0,
            edgar_url="https://sec.test/filing",
            is_congress=True,
            congress_member="Representative Doe",
        ),
        _us(
            source="openinsider",
            company="Alternate Apple",
            insider_title="",
            edgar_url="https://sec.test/filing",
        ),
    )
    expected = None

    for index, ordered in enumerate(permutations(trades)):
        permutation_engine = create_sqlite_engine(
            tmp_path / f"permutation-{index}.sqlite3"
        )
        bootstrap_database(permutation_engine)
        repo = UsTradeRepository(permutation_engine)
        try:
            repo.upsert(ordered)
            result = repo.query("AAPL")[0]
            assert all(repo.query("AAPL", sources=trade.source) for trade in trades)
            if expected is None:
                expected = result
            assert result == expected
        finally:
            permutation_engine.dispose()

    assert expected is not None
    assert expected.company == "Apple Inc."
    assert expected.insider_title == "Chief Executive Officer"
    assert expected.edgar_url == "https://sec.test/filing"
    assert expected.is_congress is True
    assert expected.congress_member == "Representative Doe"


def test_us_upsert_rolls_back_batch_and_wraps_sqlalchemy_error(engine):
    repo = UsTradeRepository(engine)

    with pytest.raises(PersistenceError) as exc_info:
        repo.upsert([_us(), _us(insider_name="Invalid", shares=-1)])

    assert exc_info.value.__cause__ is not None
    assert repo.query("AAPL") == []


def test_us_concurrent_upserts_preserve_all_source_provenance(engine):
    repo = UsTradeRepository(engine)
    repo.upsert([_us(source="baseline")])
    synchronize = _synchronize_table_reads(engine, "us_trades")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    UsTradeRepository(engine).upsert,
                    [_us(source=source)],
                )
                for source in ("secform4", "edgar")
            ]
            for future in futures:
                future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", synchronize)

    assert repo.query("AAPL", sources="baseline")
    assert repo.query("AAPL", sources="secform4")
    assert repo.query("AAPL", sources="edgar")


def test_repository_queries_reject_reverse_ranges(engine):
    with pytest.raises(ValueError, match="start_date"):
        UsTradeRepository(engine).query(
            "AAPL",
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 1),
        )


def test_congress_round_trip_all_filters_order_and_idempotency(engine):
    repo = CongressTradeRepository(engine)
    empty = CongressTrade()
    house = _congress()
    senate = _congress(
        official_name="John Roe",
        chamber="Senate",
        source="senate",
        doc_id="S-9",
        filing_date=date(2026, 2, 3),
    )

    repo.upsert([empty, house, senate, house])

    assert repo.query("all") == [senate, house, empty]
    assert repo.query(
        "Jane Doe",
        source="house",
        chamber="House",
        start_date=date(2026, 2, 2),
        end_date=date(2026, 2, 2),
    ) == [house]


def test_congress_upsert_is_monotonic_for_all_duplicate_permutations(tmp_path):
    trades = (
        _congress(
            chamber="",
            party="",
            source_url="",
            amount_low=0.0,
            amount_high=0.0,
            comment="",
        ),
        _congress(
            chamber="House",
            party="D",
            source_url="https://example.test/complete-ptr",
            amount_low=0.0,
            amount_high=0.0,
            comment="",
        ),
        _congress(
            chamber="",
            party="",
            source_url="",
            amount_low=1001.0,
            amount_high=15_000.0,
            comment="Joint account",
        ),
    )
    originals = deepcopy(trades)
    expected = None

    for index, ordered in enumerate(permutations(trades)):
        permutation_engine = create_sqlite_engine(
            tmp_path / f"congress-permutation-{index}.sqlite3"
        )
        bootstrap_database(permutation_engine)
        try:
            repo = CongressTradeRepository(permutation_engine)
            repo.upsert(ordered)
            result = repo.query("Jane Doe")[0]
            if expected is None:
                expected = result
            assert result == expected
        finally:
            permutation_engine.dispose()

    assert expected is not None
    assert expected.chamber == "House"
    assert expected.party == "D"
    assert expected.source_url == "https://example.test/complete-ptr"
    assert expected.amount_low == 1001.0
    assert expected.amount_high == 15_000.0
    assert expected.comment == "Joint account"
    assert trades == originals


def test_congress_poorer_duplicate_cannot_erase_richer_existing_fields(engine):
    repo = CongressTradeRepository(engine)
    richer = _congress()
    poorer = _congress(
        chamber="",
        party="",
        source_url="",
        amount_low=0.0,
        amount_high=0.0,
        comment="",
    )
    originals = deepcopy((richer, poorer))

    repo.upsert([richer])
    repo.upsert([poorer])

    assert repo.query("Jane Doe") == [richer]
    assert (richer, poorer) == originals


def test_european_round_trip_filters_order_and_missing_date_idempotency(engine):
    repo = EuropeanTradeRepository(engine)
    missing = EuropeanInsiderTrade(isin="NL0000009165", insider_name="No Date")
    uk = _eu()
    later = _eu(
        isin="DE0000000001",
        insider_name="Max Mustermann",
        country="DE",
        regulatory_body="BaFin",
        source="bafin",
        trade_date=date(2026, 3, 6),
    )

    repo.upsert([missing, uk, later, missing])

    assert repo.query("NL0000009165") == [missing]
    assert repo.query(
        "GB0002875804",
        country="UK",
        source="rns",
        start_date=date(2026, 3, 4),
        end_date=date(2026, 3, 4),
    ) == [uk]
    assert repo.query("DE0000000001") == [later]


@pytest.mark.parametrize(
    ("overrides", "expected_count"),
    [
        ({}, 1),
        ({"source_url": "https://example.test/rns/other"}, 2),
        ({"volume": 101.0}, 2),
        ({"price": 2.75}, 2),
        ({"total_value": 275.0}, 2),
    ],
)
def test_european_undated_identity_is_idempotent_without_colliding(
    engine, overrides, expected_count
):
    repo = EuropeanTradeRepository(engine)
    original = _eu(trade_date=None)
    candidate = _eu(trade_date=None, **overrides)

    repo.upsert([original, candidate])

    assert len(repo.query(original.isin)) == expected_count


def test_european_upsert_keeps_first_priority_and_coalesces_without_mutation(engine):
    repo = EuropeanTradeRepository(engine)
    primary = _eu(issuer_name="Preferred", position="", volume=None, source_url="")
    secondary = _eu(
        issuer_name="Secondary",
        position="Director",
        volume=100.0,
        source="amf",
        source_url="https://example.test/amf",
    )
    originals = deepcopy((primary, secondary))

    repo.upsert([primary, secondary])

    result = repo.query(primary.isin)[0]
    assert result.issuer_name == "Preferred"
    assert result.position == "Director"
    assert result.volume == 100.0
    assert result.source == "rns"
    assert (primary, secondary) == originals


def test_european_dated_duplicate_preserves_cross_source_provenance(engine):
    repo = EuropeanTradeRepository(engine)
    rns = _eu(position="", volume=None, source="rns")
    amf = _eu(
        position="Director",
        volume=100.0,
        source="amf",
        source_url="https://example.test/amf",
    )
    originals = deepcopy((rns, amf))

    repo.upsert([rns, amf])

    merged = repo.query(rns.isin)
    assert len(merged) == 1
    assert merged[0].position == "Director"
    assert merged[0].volume == 100.0
    assert repo.query(rns.isin, source="rns") == merged
    assert repo.query(rns.isin, source="amf") == merged
    assert (rns, amf) == originals


def test_european_concurrent_upserts_preserve_complementary_enrichment(engine):
    repo = EuropeanTradeRepository(engine)
    repo.upsert([_eu(position="", volume=None, source_url="")])
    synchronize = _synchronize_table_reads(engine, "european_trades")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(EuropeanTradeRepository(engine).upsert, [trade])
                for trade in (
                    _eu(position="Director", volume=None, source_url=""),
                    _eu(
                        position="", volume=100.0, source_url="https://example.test/rns"
                    ),
                )
            ]
            for future in futures:
                future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", synchronize)

    result = repo.query("GB0002875804")[0]
    assert result.position == "Director"
    assert result.volume == 100.0
    assert result.source_url == "https://example.test/rns"


# ---------------------------------------------------------------------------
# SEC identity tests
# ---------------------------------------------------------------------------

_ACC_A = "0001234567-26-000001"
_ROW_A = "0001234567-26-000001:nonDerivative:0"
_ACC_B = "0001234567-26-000002"
_ROW_B = "0001234567-26-000002:nonDerivative:0"


def _sec_us(**overrides) -> InsiderTrade:
    """Minimal valid InsiderTrade with SEC identity fields."""
    values: dict[str, Any] = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "insider_name": "Tim Cook",
        "insider_title": "CEO",
        "trade_type": "Sell",
        "trade_date": date(2026, 1, 5),
        "filing_date": date(2026, 1, 7),
        "shares": 100.0,
        "price": 200.0,
        "value": 20_000.0,
        "shares_owned_after": 500_000.0,
        "source": "sec_edgar",
        "edgar_url": "",
        "is_congress": False,
        "congress_member": "",
        "accession_number": _ACC_A,
        "sec_row_id": _ROW_A,
        "form_type": "4",
        "cik_issuer": "0000320193",
        "cik_insider": "0001513925",
        "is_amendment": False,
        "is_derivative": False,
        "transaction_code": "S",
        "acquired_disposed": "D",
        "direct_or_indirect": "D",
        "sec_detail_json": '{"v": 1}',
    }
    values.update(overrides)
    return InsiderTrade(**values)


def test_sec_identity_upsert_is_idempotent(engine):
    """Re-ingesting the identical SEC trade must SKIP (no new row)."""
    repo = UsTradeRepository(engine)
    trade = _sec_us()

    first = repo.upsert([trade])
    second = repo.upsert([trade])

    assert first == UpsertResult(inserted=1)
    assert second == UpsertResult(skipped=1)
    assert len(repo.query("AAPL", sources={"sec_edgar"})) == 1


def test_sec_identity_richer_reparsed_trade_updates_row(engine):
    """Re-ingesting the same row_id with richer data must UPDATE the stored row."""
    repo = UsTradeRepository(engine)
    sparse = _sec_us(price=0.0, value=0.0)
    richer = _sec_us(price=200.0, value=20_000.0, insider_title="Chief Executive")

    first = repo.upsert([sparse])
    second = repo.upsert([richer])

    assert first == UpsertResult(inserted=1)
    assert second == UpsertResult(updated=1)
    rows = repo.query("AAPL", sources={"sec_edgar"})
    assert len(rows) == 1
    assert rows[0].price == 200.0
    assert rows[0].insider_title == "Chief Executive"


def test_sec_identity_key_unchanged_after_update(engine):
    """Updating a SEC row must not change its canonical_key."""
    from insider_scanner.persistence.mappings import us_canonical_key

    repo = UsTradeRepository(engine)
    sparse = _sec_us(price=0.0, value=0.0)
    richer = _sec_us(price=200.0, value=20_000.0)

    repo.upsert([sparse])
    repo.upsert([richer])

    rows = repo.query("AAPL", sources={"sec_edgar"})
    assert len(rows) == 1
    # canonical key of the round-tripped trade must equal the SEC identity key
    assert us_canonical_key(rows[0]) == us_canonical_key(sparse)


def test_sec_and_third_party_trade_coexist(engine):
    """SEC row (accession+row_id) and fuzzy third-party row occupy separate slots."""
    repo = UsTradeRepository(engine)
    sec_trade = _sec_us()
    third_party = _us(
        source="secform4",
        accession_number="",
        sec_row_id="",
    )

    result = repo.upsert([sec_trade, third_party])

    assert result.inserted == 2
    assert len(repo.query("AAPL")) == 2


def test_sec_amendment_coexists_with_original(engine):
    """Original filing and its amendment are stored as separate rows."""
    repo = UsTradeRepository(engine)
    original = _sec_us(
        accession_number=_ACC_A,
        sec_row_id=_ROW_A,
        is_amendment=False,
    )
    amendment = _sec_us(
        accession_number=_ACC_B,
        sec_row_id=_ROW_B,
        is_amendment=True,
    )

    result = repo.upsert([original, amendment])

    assert result.inserted == 2
    assert len(repo.query("AAPL", sources={"sec_edgar"})) == 2


def test_sec_amendment_re_upsert_is_skipped(engine):
    """Re-ingesting the amendment produces SKIP, not a new row."""
    repo = UsTradeRepository(engine)
    amendment = _sec_us(
        accession_number=_ACC_B,
        sec_row_id=_ROW_B,
        is_amendment=True,
    )

    repo.upsert([amendment])
    second = repo.upsert([amendment])

    assert second == UpsertResult(skipped=1)
    assert len(repo.query("AAPL", sources={"sec_edgar"})) == 1


def test_sec_query_filters_by_source(engine):
    """query(..., sources={'sec_edgar'}) returns only SEC rows."""
    repo = UsTradeRepository(engine)
    sec_trade = _sec_us()
    third_party = _us(source="secform4", accession_number="", sec_row_id="")

    repo.upsert([sec_trade, third_party])

    sec_only = repo.query("AAPL", sources={"sec_edgar"})
    third_only = repo.query("AAPL", sources={"secform4"})

    assert len(sec_only) == 1
    assert sec_only[0].source == "sec_edgar"
    assert len(third_only) == 1
    assert third_only[0].source == "secform4"


def test_sec_query_latest_filters_by_source(engine):
    """query_latest(..., sources={'sec_edgar'}) returns only SEC rows."""
    repo = UsTradeRepository(engine)
    sec_trade = _sec_us()
    third_party = _us(source="secform4", accession_number="", sec_row_id="")

    repo.upsert([sec_trade, third_party])

    sec_latest = repo.query_latest(10, sources={"sec_edgar"})
    assert len(sec_latest) == 1
    assert sec_latest[0].source == "sec_edgar"

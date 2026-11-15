"""Regression checks for bounded feed reads and stable page cursors."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event

from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedQuery,
    FeedRepository,
    FeedSortField,
)
from insider_scanner.persistence.refresh import RefreshStateRepository
from insider_scanner.persistence.schema import (
    congress_trades,
    european_trades,
    us_trades,
)


def _seed(engine):
    with engine.begin() as connection:
        for index in range(1, 14):
            connection.execute(
                us_trades.insert().values(
                    canonical_key=f"us-{index}",
                    ticker="AAPL",
                    company="MÜLLER AG",
                    insider_name="Straße",
                    trade_type="Buy",
                    source="openinsider",
                    trade_date=None if index > 10 else date(2026, 6, index % 3 + 1),
                    filing_date=None if index > 11 else date(2026, 6, 10),
                    value=100,
                    value_missing=index == 13,
                )
            )
        connection.execute(
            congress_trades.insert().values(
                canonical_key="congress-1",
                official_name="Straße",
                chamber="House",
                ticker="AAPL",
                asset_description="MÜLLER AG",
                source="house",
                trade_type="Purchase",
                trade_date=date(2026, 6, 1),
                filing_date=date(2026, 6, 10),
                amount_low=100,
                amount_high=100,
            )
        )
        connection.execute(
            european_trades.insert().values(
                canonical_key="europe-1",
                isin="DE0000000001",
                issuer_name="MÜLLER AG",
                insider_name="Straße",
                source="bafin",
                country="DE",
                regulatory_body="BaFin",
                trade_type="Buy",
                trade_date=None,
                filing_date=None,
                total_value=None,
            )
        )


def _capture(engine):
    statements = []

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    return statements, listener


def test_criteria_validates_sort_and_finite_values_once():
    with pytest.raises(ValueError, match="sort_field"):
        FeedCriteria(sort_field="invalid")
    for invalid in (float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            FeedCriteria(value_min=invalid)


@pytest.mark.parametrize("field", list(FeedSortField))
@pytest.mark.parametrize("descending", [True, False])
def test_cursor_walk_preserves_ties_nulls_and_numeric_ids(engine, field, descending):
    _seed(engine)
    repository = FeedRepository(engine)
    criteria = FeedCriteria(sort_field=field, descending=descending)
    us_filing = [f"us:{index}" for index in range(1, 12)]
    filing_order = [*us_filing, "congress:1", "us:12", "us:13", "europe:1"]
    if field == FeedSortField.TRANSACTION_DATE:
        day_one = ["us:3", "us:6", "us:9", "congress:1"]
        day_two = ["us:1", "us:4", "us:7", "us:10"]
        day_three = ["us:2", "us:5", "us:8"]
        groups = (
            [day_three, day_two, day_one]
            if descending
            else [day_one, day_two, day_three]
        )
        expected = [key for group in groups for key in group] + [
            "us:11",
            "us:12",
            "us:13",
            "europe:1",
        ]
    elif field == FeedSortField.MARKET:
        us_keys = [*us_filing, "us:12", "us:13"]
        expected = (
            [*us_keys, "europe:1", "congress:1"]
            if descending
            else ["congress:1", "europe:1", *us_keys]
        )
    else:
        expected = filing_order
    assert [
        row.key for row in repository.query(criteria.to_query()).records
    ] == expected
    page = repository.query(criteria.to_query(limit=2))
    assert hasattr(page, "next_cursor"), "feed pages need a stable continuation cursor"
    seen = list(page.records)
    while page.has_more:
        page = repository.query(criteria.to_query(limit=2, cursor=page.next_cursor))
        assert page.total_count is None
        seen.extend(page.records)
    assert [row.key for row in seen] == expected
    assert len({row.key for row in seen}) == 15


def test_cursor_excludes_intervening_inserts_before_and_after_anchor(engine):
    _seed(engine)
    repository = FeedRepository(engine)
    criteria = FeedCriteria(markets=(FeedMarket.US,))
    baseline = repository.query(criteria.to_query()).records
    page = repository.query(criteria.to_query(limit=2))
    assert hasattr(page, "next_cursor"), "feed pages need an insertion snapshot"
    with engine.begin() as connection:
        for day in (1, 20):
            connection.execute(
                us_trades.insert().values(
                    canonical_key=f"new-{day}",
                    ticker="NEW",
                    trade_type="Buy",
                    source="openinsider",
                    trade_date=date(2026, 6, day),
                )
            )
    seen = list(page.records)
    while page.has_more:
        page = repository.query(criteria.to_query(limit=2, cursor=page.next_cursor))
        seen.extend(page.records)
    assert [row.key for row in seen] == [row.key for row in baseline]
    with pytest.raises(ValueError, match="criteria"):
        repository.query(FeedQuery(search="changed", cursor=page.next_cursor))


def test_continuation_omits_counts_and_union_sorts(engine):
    _seed(engine)
    repository = FeedRepository(engine)
    first = repository.query(FeedQuery(limit=2))
    assert hasattr(first, "next_cursor"), "feed pages need bounded continuations"
    statements, listener = _capture(engine)
    try:
        page = repository.query(FeedQuery(limit=2, cursor=first.next_cursor))
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert page.total_count is None
    assert not any("count(" in statement.lower() for statement in statements)
    assert not any("UNION ALL" in statement for statement in statements)
    assert all(
        "LIMIT" in statement for statement in statements if "ORDER BY" in statement
    )


def test_feed_and_entity_queries_fold_unicode_consistently(engine):
    _seed(engine)
    repository = FeedRepository(engine)
    assert len(repository.query(FeedQuery(search="müller")).records) == 15
    assert len(repository.query(FeedQuery(search="STRASSE")).records) == 15
    assert len(repository.recent_by_person("STRASSE", limit=50)) == 15
    assert len(repository.recent_by_issuer("aapl", limit=50)) == 14
    assert repository.search_entities("müller").companies
    assert repository.search_entities("STRASSE").insiders


def test_detail_uses_one_primary_key_select(engine):
    _seed(engine)
    statements, listener = _capture(engine)
    try:
        detail = FeedRepository(engine).detail("us:12")
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert detail is not None
    selects = [
        statement for statement in statements if statement.lstrip().startswith("SELECT")
    ]
    assert len(selects) == 1
    assert "UNION ALL" not in selects[0]
    assert "us_trades.id =" in selects[0]
    assert "congress_trades" not in selects[0]


def test_freshness_tracks_relevant_refreshes_including_empty_runs(engine):
    _seed(engine)
    repository = FeedRepository(engine)
    now = datetime(2026, 11, 15, tzinfo=UTC)
    unknown = repository.query(FeedQuery(), now=now)
    assert unknown.freshness_at is None
    assert unknown.is_stale
    refresh = RefreshStateRepository(engine)
    refresh.set("us", "*", "openinsider", "latest:50", now - timedelta(days=2))
    refresh.set("eu", "*", "bafin", "latest:50", now)
    scoped = repository.query(FeedQuery(markets=(FeedMarket.US,)), now=now)
    assert scoped.freshness_at == now - timedelta(days=2)
    assert scoped.is_stale
    refresh.set("us", "*", "openinsider", "latest:50", now)
    fresh = repository.query(FeedQuery(markets=(FeedMarket.US,)), now=now)
    assert fresh.freshness_at == now
    assert not fresh.is_stale


def test_page_controls_reject_incompatible_and_unbounded_requests(engine):
    _seed(engine)
    first = FeedRepository(engine).query(FeedQuery(limit=2))
    assert hasattr(first, "next_cursor"), (
        "cursor validation must reject mixed pagination"
    )
    with pytest.raises(ValueError, match="offset"):
        FeedQuery(cursor=first.next_cursor, offset=1)
    with pytest.raises(ValueError, match="limit"):
        FeedQuery(limit=True)
    with pytest.raises(ValueError, match="criteria"):
        FeedQuery(markets=(FeedMarket.US,), cursor=first.next_cursor)
    assert replace(FeedCriteria(search=" apple "), descending=False).search == "apple"

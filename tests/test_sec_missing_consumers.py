"""Missing SEC numbers remain unknown in filters, feed detail and alerts."""

from datetime import date

import pytest

from insider_scanner.core.merger import filter_trades
from insider_scanner.core.models import InsiderTrade
from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedQuery,
    FeedRepository,
    FeedSortField,
)
from insider_scanner.persistence.repositories import UsTradeRepository
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.alerts import evaluate_alerts


def _trades() -> list[InsiderTrade]:
    return [
        InsiderTrade(
            ticker="UNKNOWN",
            trade_date=date(2024, 1, 1),
            source="sec_edgar",
            accession_number="0000000001-24-000001",
            sec_row_id="unknown",
            shares_missing=True,
            price_missing=True,
            value_missing=True,
            shares_owned_after_missing=True,
        ),
        InsiderTrade(
            ticker="ZERO",
            trade_date=date(2024, 1, 1),
            source="sec_edgar",
            accession_number="0000000001-24-000002",
            sec_row_id="zero",
        ),
        InsiderTrade(
            ticker="PRICED",
            trade_date=date(2024, 1, 1),
            source="sec_edgar",
            accession_number="0000000001-24-000003",
            sec_row_id="priced",
            shares=5,
            price=10,
            value=50,
            shares_owned_after=100,
        ),
    ]


def test_explicit_zero_threshold_excludes_unknown_but_retains_reported_zero():
    trades = _trades()
    assert filter_trades(trades) == trades
    assert [trade.ticker for trade in filter_trades(trades, min_value=0)] == [
        "ZERO",
        "PRICED",
    ]
    assert [trade.ticker for trade in filter_trades(trades, min_value=1)] == ["PRICED"]


def test_feed_unknown_numbers_and_holdings_differ_from_reported_zero(engine):
    UsTradeRepository(engine).upsert(_trades())
    repository = FeedRepository(engine)
    records = {
        record.identifier: record for record in repository.query(FeedQuery()).records
    }

    unknown = records["UNKNOWN"]
    assert (unknown.quantity, unknown.price, unknown.value_sort) == (None, None, None)
    assert repository.detail(unknown.key).shares_owned_after is None

    zero = records["ZERO"]
    assert (zero.quantity, zero.price, zero.value_sort) == (0, 0, 0)
    assert repository.detail(zero.key).shares_owned_after == 0

    priced = records["PRICED"]
    assert (priced.quantity, priced.price, priced.value_sort) == (5, 10, 50)
    assert repository.detail(priced.key).shares_owned_after == 100


@pytest.mark.parametrize("descending", [False, True])
def test_feed_value_sort_places_unknown_after_known_numbers(engine, descending):
    UsTradeRepository(engine).upsert(_trades())
    page = FeedRepository(engine).query(
        FeedQuery(sort_field=FeedSortField.VALUE, descending=descending)
    )
    expected_known = ["PRICED", "ZERO"] if descending else ["ZERO", "PRICED"]
    assert [record.identifier for record in page.records] == [
        *expected_known,
        "UNKNOWN",
    ]


def test_value_alert_criteria_do_not_treat_unknown_as_zero(engine):
    UsTradeRepository(engine).upsert(_trades())
    store = AlertStore(
        rules=(
            AlertRule(
                name="Reported zero",
                criteria=FeedCriteria(value_min=0, value_max=0),
            ),
        )
    )
    hits = evaluate_alerts(FeedRepository(engine), store, WatchlistStore())
    assert len(hits) == 1
    assert [record.identifier for record in hits[0].records] == ["ZERO"]

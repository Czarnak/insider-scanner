"""Tests for the alert evaluator — pure, FakeRepository-driven, no Qt."""

from __future__ import annotations

from datetime import UTC, datetime

from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedPage,
    FeedRecord,
)
from insider_scanner.persistence.watchlists import (
    WatchEntry,
    Watchlist,
    WatchlistStore,
)
from insider_scanner.services.alerts import AlertHit, evaluate_alerts

_OLD = datetime(2024, 1, 1, tzinfo=UTC)
_NEW = datetime(2024, 6, 1, tzinfo=UTC)


def _record(
    key: str,
    *,
    created_at: datetime | None = _NEW,
    identifier: str = "AAPL",
    person: str = "Jane Director",
    market: FeedMarket = FeedMarket.US,
) -> FeedRecord:
    return FeedRecord(
        key=key,
        market=market,
        transaction_type="Buy",
        issuer="Example Corp",
        identifier=identifier,
        person=person,
        role="CEO",
        transaction_date=None,
        filing_date=None,
        quantity=None,
        price=None,
        value_display="",
        value_sort=200_000.0,
        currency="USD",
        source="test",
        source_url="",
        created_at=created_at,
    )


class FakeRepo:
    """Minimal repository double for alert evaluation."""

    def __init__(self, *, query_records=(), by_issuer=None, by_person=None) -> None:
        self._query_records = tuple(query_records)
        self._by_issuer = by_issuer or {}
        self._by_person = by_person or {}
        self.query_calls: list = []

    def query(self, q, *, now=None) -> FeedPage:
        self.query_calls.append(q)
        return FeedPage(
            records=self._query_records,
            total_count=len(self._query_records),
            freshness_at=None,
            is_stale=False,
            has_more=False,
        )

    def recent_by_issuer(self, identifier, *, markets=(), limit=8, exclude_key=None):
        return self._by_issuer.get(identifier.casefold(), ())

    def recent_by_person(self, person, *, markets=(), limit=8, exclude_key=None):
        return self._by_person.get(person.casefold(), ())


class TestEvaluateAlerts:
    def test_empty_store_returns_no_hits(self) -> None:
        result = evaluate_alerts(FakeRepo(), AlertStore(), WatchlistStore())
        assert result == ()

    def test_disabled_rule_skipped(self) -> None:
        repo = FakeRepo(query_records=(_record("us:1"),))
        store = AlertStore(rules=(AlertRule(name="A", enabled=False),))
        assert evaluate_alerts(repo, store, WatchlistStore()) == ()
        assert repo.query_calls == []  # never queried

    def test_criteria_rule_emits_hit_with_records(self) -> None:
        repo = FakeRepo(query_records=(_record("us:1"), _record("us:2")))
        store = AlertStore(
            rules=(AlertRule(name="Big", criteria=FeedCriteria(value_min=100_000)),)
        )
        hits = evaluate_alerts(repo, store, WatchlistStore())
        assert len(hits) == 1
        assert isinstance(hits[0], AlertHit)
        assert hits[0].rule_name == "Big"
        assert hits[0].match_count == 2
        assert {r.key for r in hits[0].records} == {"us:1", "us:2"}

    def test_watermark_filters_old_records(self) -> None:
        repo = FakeRepo(
            query_records=(
                _record("us:old", created_at=_OLD),
                _record("us:new", created_at=_NEW),
            )
        )
        store = AlertStore(
            rules=(
                AlertRule(
                    name="Big",
                    criteria=FeedCriteria(),
                    last_seen_at=datetime(2024, 3, 1, tzinfo=UTC),
                ),
            )
        )
        hits = evaluate_alerts(repo, store, WatchlistStore())
        assert len(hits) == 1
        assert {r.key for r in hits[0].records} == {"us:new"}

    def test_rule_with_no_new_matches_is_not_a_hit(self) -> None:
        repo = FakeRepo(query_records=(_record("us:old", created_at=_OLD),))
        store = AlertStore(rules=(AlertRule(name="Big", last_seen_at=_NEW),))
        assert evaluate_alerts(repo, store, WatchlistStore()) == ()

    def test_watchlist_rule_merges_and_dedups(self) -> None:
        shared = _record("us:1", identifier="AAPL", person="Jane Director")
        repo = FakeRepo(
            by_issuer={"aapl": (shared, _record("us:2", identifier="AAPL"))},
            by_person={"jane director": (shared,)},  # duplicate key us:1
        )
        watchlists = WatchlistStore(
            watchlists=(
                Watchlist(
                    name="Tech",
                    entries=(
                        WatchEntry(
                            kind="company", market=FeedMarket.US, identifier="AAPL"
                        ),
                        WatchEntry(
                            kind="insider", market=FeedMarket.US, person="Jane Director"
                        ),
                    ),
                ),
            )
        )
        store = AlertStore(rules=(AlertRule(name="WL", watchlist="Tech"),))
        hits = evaluate_alerts(repo, store, watchlists)
        assert len(hits) == 1
        keys = {r.key for r in hits[0].records}
        assert keys == {"us:1", "us:2"}  # deduped, not 3

    def test_watchlist_rule_unknown_watchlist_no_hit(self) -> None:
        repo = FakeRepo()
        store = AlertStore(rules=(AlertRule(name="WL", watchlist="Missing"),))
        assert evaluate_alerts(repo, store, WatchlistStore()) == ()

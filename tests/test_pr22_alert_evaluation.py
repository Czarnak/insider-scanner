"""Alert watermarks acknowledge only bounded, presented ingestion progress."""

from dataclasses import replace
from datetime import UTC, datetime

from insider_scanner.persistence.alerts import (
    AlertRule,
    AlertStore,
    rule_from_dict,
)
from insider_scanner.persistence.feed import FeedCriteria, FeedMarket, FeedPage
from insider_scanner.persistence.watchlists import WatchEntry, Watchlist, WatchlistStore
from insider_scanner.services.alerts import evaluate_alerts
from tests.test_alert_evaluation import _record


class IngestionRepo:
    def __init__(self):
        self.calls = []

    def query(self, query):
        return FeedPage(
            records=(_record("us:1"),),
            total_count=1,
            freshness_at=None,
            is_stale=False,
            has_more=False,
        )

    def recent_by_issuer(self, *args, **kwargs):
        return (_record("us:1"),)

    def recent_by_person(self, *args, **kwargs):
        return (_record("us:1"),)

    def query_alerts(self, criteria, **kwargs):
        self.calls.append((criteria, kwargs))
        return FeedPage(
            records=(
                replace(_record("us:1"), ingestion_sequence=21),
                replace(_record("us:2"), ingestion_sequence=24),
            ),
            total_count=251,
            freshness_at=None,
            is_stale=False,
            has_more=True,
        )


def test_evaluation_uses_ingestion_page_and_only_presented_sequence():
    repo = IngestionRepo()
    rule = rule_from_dict({"name": "New", "last_seen_sequence": 17})
    hits = evaluate_alerts(repo, AlertStore(rules=(rule,)), WatchlistStore())
    assert hits[0].match_count == 251
    assert hits[0].last_seen_sequence == 24
    assert len(hits[0].records) == 2
    criteria, options = repo.calls[0]
    assert criteria == FeedCriteria()
    assert options["after_sequence"] == 17
    assert options["created_after"] is None
    assert options["limit"] == 200


def test_legacy_timestamp_is_only_used_before_first_sequence_acknowledgement():
    repo = IngestionRepo()
    at = datetime(2024, 6, 1, 12, 0, 0, 100, tzinfo=UTC)
    store = AlertStore(rules=(AlertRule(name="Legacy", last_seen_at=at),))
    evaluate_alerts(repo, store, WatchlistStore())
    assert len(repo.calls) == 1
    assert repo.calls[0][1]["created_after"] == at
    assert repo.calls[0][1]["after_sequence"] == 0
    store = store.marked_seen_sequence("Legacy", 24)
    evaluate_alerts(repo, store, WatchlistStore())
    assert repo.calls[1][1]["created_after"] is None
    assert repo.calls[1][1]["after_sequence"] == 24


def test_watchlist_evaluation_passes_exact_market_scoped_entries():
    repo = IngestionRepo()
    entries = (
        WatchEntry(kind="company", market=FeedMarket.US, identifier="AAPL"),
        WatchEntry(kind="insider", market=FeedMarket.CONGRESS, person="Jane Doe"),
    )
    lists = WatchlistStore(watchlists=(Watchlist(name="Tech", entries=entries),))
    evaluate_alerts(
        repo, AlertStore(rules=(AlertRule(name="WL", watchlist=" tech "),)), lists
    )
    assert len(repo.calls) == 1
    assert repo.calls[0][1]["watch_entries"] == entries


def test_missing_watchlist_never_runs_broad_query():
    repo = IngestionRepo()
    assert (
        evaluate_alerts(
            repo,
            AlertStore(rules=(AlertRule(name="WL", watchlist="Gone"),)),
            WatchlistStore(),
        )
        == ()
    )
    assert repo.calls == []

"""In-app alert evaluation against the locally persisted transaction feed.

This is a pure, side-effect-free pass over a repository: given the saved alert
rules and watchlists, it returns the *new* matching transactions per enabled
rule. New matches follow committed ingestion sequence rather than trade date.
Each hit carries progress through its presented page for acknowledgement.

Rules are either criteria-based (a saved :class:`FeedCriteria`) or watchlist-based
(referencing a watchlist by name); a rule with a watchlist set is evaluated as
watchlist-based.
"""

from __future__ import annotations

from dataclasses import dataclass

from insider_scanner.persistence.alerts import AlertStore
from insider_scanner.persistence.feed import DEFAULT_PAGE_SIZE, FeedCriteria, FeedRecord
from insider_scanner.persistence.watchlists import WatchlistStore


@dataclass(frozen=True)
class AlertHit:
    """The new matches produced by a single triggered alert rule."""

    rule_name: str
    records: tuple[FeedRecord, ...]
    match_count: int
    last_seen_sequence: int | None = None


def evaluate_alerts(
    repository,
    store: AlertStore,
    watchlists: WatchlistStore,
) -> tuple[AlertHit, ...]:
    """Return one :class:`AlertHit` per enabled rule that has new matches.

    ``match_count`` includes matches beyond the bounded displayed page. A rule's
    acknowledgement advances only through ``last_seen_sequence`` of that page.
    """
    hits: list[AlertHit] = []
    for rule in store.rules:
        if not rule.enabled or rule.error:
            continue
        entries = None
        if rule.watchlist is not None:
            watchlist = next(
                (
                    item
                    for item in watchlists.watchlists
                    if item.name.strip().casefold() == rule.watchlist.strip().casefold()
                ),
                None,
            )
            if watchlist is None or not watchlist.entries:
                continue
            entries = watchlist.entries
        page = repository.query_alerts(
            FeedCriteria() if entries is not None else rule.criteria,
            after_sequence=rule.last_seen_sequence or 0,
            created_after=rule.last_seen_at
            if rule.last_seen_sequence is None
            else None,
            watch_entries=entries,
            limit=DEFAULT_PAGE_SIZE,
        )
        if not page.records:
            continue
        sequences = [
            r.ingestion_sequence
            for r in page.records
            if r.ingestion_sequence is not None
        ]
        hits.append(
            AlertHit(
                rule_name=rule.name,
                records=page.records,
                match_count=page.total_count
                if page.total_count is not None
                else len(page.records),
                last_seen_sequence=max(sequences) if sequences else None,
            )
        )
    return tuple(hits)

"""In-app alert evaluation against the locally persisted transaction feed.

This is a pure, side-effect-free pass over a repository: given the saved alert
rules and watchlists, it returns the *new* matching transactions per enabled
rule. "New" means a record whose ``created_at`` is later than the rule's
``last_seen_at`` watermark (or any record when the rule has never been seen).

Rules are either criteria-based (a saved :class:`FeedCriteria`) or watchlist-based
(referencing a watchlist by name); a rule with a watchlist set is evaluated as
watchlist-based.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import DEFAULT_PAGE_SIZE, FeedRecord
from insider_scanner.persistence.watchlists import WatchlistStore

_PER_ENTRY_LIMIT = 100


@dataclass(frozen=True)
class AlertHit:
    """The new matches produced by a single triggered alert rule."""

    rule_name: str
    records: tuple[FeedRecord, ...]
    match_count: int


def _is_new(record: FeedRecord, watermark: datetime | None) -> bool:
    """Return True if *record* counts as new relative to *watermark*."""
    if watermark is None:
        return True
    if record.created_at is None:
        return True  # cannot prove it is old — surface it
    try:
        return record.created_at > watermark
    except TypeError:
        # Mismatched naive/aware datetimes — fail open so nothing is silently lost.
        return True


def _dedup_preserve_order(records: tuple[FeedRecord, ...]) -> tuple[FeedRecord, ...]:
    seen: set[str] = set()
    out: list[FeedRecord] = []
    for r in records:
        if r.key in seen:
            continue
        seen.add(r.key)
        out.append(r)
    return tuple(out)


def _candidates_for_watchlist(
    repository, watchlists: WatchlistStore, name: str
) -> tuple[FeedRecord, ...]:
    watchlist = watchlists.get(name)
    if watchlist is None:
        return ()
    collected: list[FeedRecord] = []
    for entry in watchlist.entries:
        if entry.kind == "company" and entry.identifier:
            collected.extend(
                repository.recent_by_issuer(entry.identifier, limit=_PER_ENTRY_LIMIT)
            )
        elif entry.kind == "insider" and entry.person:
            collected.extend(
                repository.recent_by_person(entry.person, limit=_PER_ENTRY_LIMIT)
            )
    return _dedup_preserve_order(tuple(collected))


def _candidates_for_criteria(repository, rule: AlertRule) -> tuple[FeedRecord, ...]:
    page = repository.query(rule.criteria.to_query(limit=DEFAULT_PAGE_SIZE))
    return page.records


def evaluate_alerts(
    repository,
    store: AlertStore,
    watchlists: WatchlistStore,
) -> tuple[AlertHit, ...]:
    """Return one :class:`AlertHit` per enabled rule that has new matches.

    Rules with no new matches (or that are disabled) produce no hit, so the total
    number of new transactions is ``sum(hit.match_count for hit in result)``.
    """
    hits: list[AlertHit] = []
    for rule in store.rules:
        if not rule.enabled:
            continue
        if rule.watchlist is not None:
            candidates = _candidates_for_watchlist(
                repository, watchlists, rule.watchlist
            )
        else:
            candidates = _candidates_for_criteria(repository, rule)

        new_records = tuple(r for r in candidates if _is_new(r, rule.last_seen_at))
        if not new_records:
            continue
        hits.append(
            AlertHit(
                rule_name=rule.name,
                records=new_records,
                match_count=len(new_records),
            )
        )
    return tuple(hits)

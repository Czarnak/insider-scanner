"""Unified Feed page widget tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from PySide6.QtCore import Qt, QThreadPool

from insider_scanner.gui.feed_page import FeedPageWidget
from insider_scanner.persistence.feed import (
    FeedMarket,
    FeedPage,
    FeedQuery,
    FeedRecord,
)


class CapturingPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


class FakeFeedRepository:
    def __init__(self, pages):
        self.pages = list(pages)
        self.queries = []

    def query(self, query):
        self.queries.append(query)
        return self.pages.pop(0)


def _record(key: str, identifier: str) -> FeedRecord:
    return FeedRecord(
        key=key,
        market=FeedMarket.US,
        transaction_type="Buy",
        issuer="Example Corp",
        identifier=identifier,
        person="Jane Director",
        role="Director",
        transaction_date=date(2026, 6, 14),
        filing_date=date(2026, 6, 15),
        quantity=10,
        price=25,
        value_display="",
        value_sort=250,
        currency="USD",
        source="secform4",
        source_url="https://example.test/filing",
    )


def _page(records, *, total=None, has_more=False, stale=False):
    return FeedPage(
        records=tuple(records),
        total_count=len(records) if total is None else total,
        freshness_at=datetime(2026, 6, 15, 10, tzinfo=UTC),
        is_stale=stale,
        has_more=has_more,
    )


def _complete(pool, index=0):
    pool.workers[index].run()


def test_feed_page_loads_first_page_and_exposes_accessible_controls(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)

    assert page.table.accessibleName() == "Unified transaction feed"
    assert page.load_more_button.accessibleName() == "Load more transactions"
    assert page.state_label.text() == "Loading transactions..."

    _complete(pool)

    assert page.model.rowCount() == 1
    assert page.model.record_at(0).identifier == "AAPL"
    assert page.state_label.isHidden()
    assert repository.queries == [FeedQuery()]


def test_new_search_replaces_rows_and_uses_normalized_query(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository(
        [
            _page([_record("us:1", "AAPL")]),
            _page([_record("us:2", "MSFT")]),
        ]
    )
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    page.set_search("  microsoft  ")
    _complete(pool, 1)

    assert page.model.rowCount() == 1
    assert page.model.record_at(0).identifier == "MSFT"
    assert repository.queries[-1].search == "microsoft"
    assert repository.queries[-1].offset == 0


def test_load_more_appends_rows_without_resetting_existing_data(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository(
        [
            _page([_record("us:1", "AAPL")], total=2, has_more=True),
            _page([_record("us:2", "MSFT")], total=2),
        ]
    )
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    qtbot.mouseClick(page.load_more_button, Qt.MouseButton.LeftButton)
    _complete(pool, 1)

    assert [page.model.record_at(i).identifier for i in range(2)] == [
        "AAPL",
        "MSFT",
    ]
    assert repository.queries[-1].offset == 1
    assert page.load_more_button.isHidden()


def test_feed_page_distinguishes_empty_matches_stale_and_error(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository(
        [
            _page([_record("us:1", "AAPL")]),
            _page([], stale=True),
        ]
    )
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)
    page.set_search("missing")
    _complete(pool, 1)

    assert page.state_label.text() == "No transactions match your search."
    assert "stale" in page.freshness_text().lower()

    page._on_error(2, (RuntimeError, RuntimeError("database offline"), None))
    assert page.state_label.text() == "Could not load local transactions."


def test_feed_page_retains_worker_until_real_thread_pool_delivers_result(qtbot):
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    pool = QThreadPool()
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)

    qtbot.waitUntil(lambda: page.model.rowCount() == 1, timeout=5_000)

    assert page.state_label.isHidden()
    assert page._workers == {}
    assert pool.waitForDone(5_000)

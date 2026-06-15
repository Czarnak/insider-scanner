"""Unified Feed page widget tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from PySide6.QtCore import Qt, QThreadPool

from insider_scanner.gui.feed_page import FeedPageWidget
from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedPage,
    FeedQuery,
    FeedRecord,
    FeedSortField,
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


# ---------------------------------------------------------------------------
# Chunk D — new tests
# ---------------------------------------------------------------------------


def test_initial_criteria_issues_one_query_and_reflects_in_chips_and_panel(qtbot):
    """Construction with initial_criteria fires one query carrying those fields."""
    pool = CapturingPool()
    initial = FeedCriteria(markets=(FeedMarket.US,), search="x")
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool, initial_criteria=initial)
    qtbot.addWidget(page)

    # Exactly one query was issued
    assert len(pool.workers) == 1
    _complete(pool, 0)

    # The query carries the initial criteria's fields
    assert repository.queries[0].search == "x"
    assert repository.queries[0].markets == (FeedMarket.US,)

    # Chips reflect the active criteria (search + market chip)
    assert page.chips.chip_count() >= 1

    # Panel reflects the markets
    assert page.filter_panel.cb_market_us.isChecked()


def test_filters_applied_merges_search_and_sort_keeps_filter_fields(qtbot):
    """_on_filters_applied: current search/sort preserved; filter fields applied."""
    pool = CapturingPool()
    repository = FakeFeedRepository(
        [
            _page([_record("us:1", "AAPL")]),
            _page([_record("us:2", "MSFT")]),
        ]
    )
    initial = FeedCriteria(
        search="apple", sort_field=FeedSortField.ISSUER, descending=False
    )
    page = FeedPageWidget(repository, thread_pool=pool, initial_criteria=initial)
    qtbot.addWidget(page)
    _complete(pool, 0)

    emitted: list[FeedCriteria] = []
    page.criteriaChanged.connect(emitted.append)

    # Emit filtersApplied with a filter-only criteria (search="" and default sort per spec)
    filter_criteria = FeedCriteria(markets=(FeedMarket.CONGRESS,))
    page.filter_panel.filtersApplied.emit(filter_criteria)
    _complete(pool, 1)

    # criteriaChanged was emitted
    assert len(emitted) == 1
    merged = emitted[0]

    # Filter fields applied
    assert merged.markets == (FeedMarket.CONGRESS,)

    # Search + sort preserved from previous criteria
    assert merged.search == "apple"
    assert merged.sort_field == FeedSortField.ISSUER
    assert merged.descending is False

    # New query was issued at offset 0
    assert repository.queries[-1].offset == 0
    assert repository.queries[-1].markets == (FeedMarket.CONGRESS,)


def test_chip_removed_market_updates_criteria_and_requeries(qtbot):
    """Removing market:US chip leaves only CONGRESS and triggers a new query."""
    pool = CapturingPool()
    repository = FakeFeedRepository(
        [
            _page([_record("us:1", "AAPL")]),
            _page([_record("us:2", "MSFT")]),
        ]
    )
    initial = FeedCriteria(markets=(FeedMarket.US, FeedMarket.CONGRESS))
    page = FeedPageWidget(repository, thread_pool=pool, initial_criteria=initial)
    qtbot.addWidget(page)
    _complete(pool, 0)

    page.chips.chipRemoved.emit("market:US")
    _complete(pool, 1)

    assert page.criteria().markets == (FeedMarket.CONGRESS,)
    assert repository.queries[-1].markets == (FeedMarket.CONGRESS,)


def test_set_criteria_replaces_state_emits_changed_and_requeries(qtbot):
    """set_criteria stores the new criteria, emits criteriaChanged, re-queries offset 0."""
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

    emitted: list[FeedCriteria] = []
    page.criteriaChanged.connect(emitted.append)

    new_criteria = FeedCriteria(search="tesla", markets=(FeedMarket.US,))
    page.set_criteria(new_criteria)
    _complete(pool, 1)

    assert page.criteria() == new_criteria
    assert len(emitted) == 1
    assert emitted[0] == new_criteria
    assert repository.queries[-1].search == "tesla"
    assert repository.queries[-1].offset == 0


def test_sort_requested_updates_criteria_emits_changed_and_requeries(qtbot):
    """_on_sort_requested updates sort fields in criteria and re-queries."""
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

    emitted: list[FeedCriteria] = []
    page.criteriaChanged.connect(emitted.append)

    page._on_sort_requested(FeedSortField.VALUE, False)
    _complete(pool, 1)

    assert len(emitted) == 1
    assert emitted[0].sort_field == FeedSortField.VALUE
    assert emitted[0].descending is False
    assert page.criteria().sort_field == FeedSortField.VALUE
    assert repository.queries[-1].sort_field == FeedSortField.VALUE


def test_filter_panel_reset_emits_reset_requested_without_clearing_criteria(qtbot):
    """resetRequested from filter_panel is re-emitted; criteria unchanged."""
    pool = CapturingPool()
    initial = FeedCriteria(markets=(FeedMarket.US,), search="x")
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool, initial_criteria=initial)
    qtbot.addWidget(page)
    _complete(pool, 0)

    reset_signals: list[bool] = []
    page.resetRequested.connect(lambda: reset_signals.append(True))

    page.filter_panel.resetRequested.emit()

    assert len(reset_signals) == 1
    # Criteria must NOT have been cleared
    assert page.criteria().markets == (FeedMarket.US,)
    assert page.criteria().search == "x"


def test_chips_reset_emits_reset_requested_without_clearing_criteria(qtbot):
    """resetRequested from chips is re-emitted; criteria unchanged."""
    pool = CapturingPool()
    initial = FeedCriteria(markets=(FeedMarket.CONGRESS,))
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool, initial_criteria=initial)
    qtbot.addWidget(page)
    _complete(pool, 0)

    reset_signals: list[bool] = []
    page.resetRequested.connect(lambda: reset_signals.append(True))

    page.chips.resetRequested.emit()

    assert len(reset_signals) == 1
    assert page.criteria().markets == (FeedMarket.CONGRESS,)


def test_column_layout_round_trips_without_error(qtbot):
    """restore_column_layout(column_layout()) restores state without raising."""
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    layout_text = page.column_layout()
    assert layout_text is not None and len(layout_text) > 0

    # Should not raise
    page.restore_column_layout(layout_text)


def test_restore_column_layout_with_corrupt_blob_does_not_raise(qtbot):
    """restore_column_layout with invalid base64 logs a warning but never raises."""
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    # Must not raise
    page.restore_column_layout("not-valid-base64!!")
    page.restore_column_layout(None)
    page.restore_column_layout("")


# ---------------------------------------------------------------------------
# Investigation drawer integration
# ---------------------------------------------------------------------------


def test_drawer_hidden_until_a_row_is_selected(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    assert page.drawer.isHidden()


def test_selecting_row_shows_drawer_with_that_record(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    shown: list = []
    page.drawer.show_record = lambda record: shown.append(record)

    page.table.selectRow(0)

    assert len(shown) == 1
    assert shown[0].identifier == "AAPL"
    assert not page.drawer.isHidden()


def test_closing_drawer_hides_it(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)
    page.drawer.show_record = lambda record: None
    page.table.selectRow(0)
    assert not page.drawer.isHidden()

    page.drawer.closeRequested.emit()

    assert page.drawer.isHidden()


def test_clearing_selection_hides_drawer(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)
    page.drawer.show_record = lambda record: None
    page.table.selectRow(0)
    assert not page.drawer.isHidden()

    page.table.clearSelection()

    assert page.drawer.isHidden()


def test_drawer_deep_link_signals_are_forwarded(qtbot):
    pool = CapturingPool()
    repository = FakeFeedRepository([_page([_record("us:1", "AAPL")])])
    page = FeedPageWidget(repository, thread_pool=pool)
    qtbot.addWidget(page)
    _complete(pool, 0)

    company: list = []
    insider: list = []
    page.openCompanyRequested.connect(
        lambda ident, market: company.append((ident, market))
    )
    page.openInsiderRequested.connect(
        lambda person, market: insider.append((person, market))
    )

    page.drawer.openCompanyRequested.emit("AAPL", FeedMarket.US)
    page.drawer.openInsiderRequested.emit("Jane Director", FeedMarket.US)

    assert company == [("AAPL", FeedMarket.US)]
    assert insider == [("Jane Director", FeedMarket.US)]

"""Entity views must identify only their current request and bound their summary."""

from __future__ import annotations

import pytest

from insider_scanner.gui.entity_pages import CompanyPage, InsiderPage
from insider_scanner.persistence.feed import FeedMarket
from tests.test_entity_pages import FakeRepository, InlinePool, _record


class CapturingPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.mark.parametrize("page_type", [CompanyPage, InsiderPage])
def test_loading_new_entity_clears_old_data_immediately(qtbot, page_type):
    record = _record()
    pool = CapturingPool()
    page = page_type(
        FakeRepository(issuer_records=(record,), person_records=(record,)),
        thread_pool=pool,
    )
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)
    pool.workers[0].run()
    assert page.model.rowCount() == 1

    page.load("New entity", FeedMarket.CONGRESS)

    assert page.header_label.text() == "New entity · Congress"
    assert page.model.rowCount() == 0
    assert page.summary_label.text() == ""
    assert not page.progress.isHidden()
    error = RuntimeError("unavailable")
    pool.workers[1].signals.error.emit((RuntimeError, error, None))
    pool.workers[1].signals.finished.emit()
    assert page.model.rowCount() == 0
    assert page.summary_label.text() == ""
    assert page.header_label.text() == "New entity · Congress"
    assert "Could not load" in page.state_label.text()
    assert page.progress.isHidden()


@pytest.mark.parametrize("page_type", [CompanyPage, InsiderPage])
def test_unavailable_repository_keeps_only_requested_identity(qtbot, page_type):
    records = (_record(),)
    page = page_type(
        FakeRepository(issuer_records=records, person_records=records),
        thread_pool=InlinePool(),
    )
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)
    page._repository = None

    page.load("New entity", FeedMarket.EUROPE)

    assert page.header_label.text() == "New entity · Europe"
    assert page.model.rowCount() == 0
    assert page.summary_label.text() == ""
    assert page.progress.isHidden()
    assert "unavailable" in page.state_label.text()


@pytest.mark.parametrize("page_type", [CompanyPage, InsiderPage])
def test_capped_summary_explicitly_describes_displayed_history(qtbot, page_type):
    records = tuple(_record(key=f"us:{i}") for i in range(200))
    page = page_type(
        FakeRepository(issuer_records=records, person_records=records),
        thread_pool=InlinePool(),
    )
    qtbot.addWidget(page)

    page.load("AAPL", FeedMarket.US)

    assert page.model.rowCount() == 200
    assert page.summary_label.text().startswith("Showing latest 200 transactions ·")


@pytest.mark.parametrize("page_type", [CompanyPage, InsiderPage])
def test_cancellation_hides_loading_and_suppresses_all_late_callbacks(qtbot, page_type):
    pool = CapturingPool()
    page = page_type(FakeRepository(), thread_pool=pool)
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)

    page.request_cancellation()

    assert page.progress.isHidden()
    pool.workers[0].signals.result.emit((_record(),))
    pool.workers[0].signals.error.emit((RuntimeError, RuntimeError("old"), None))
    pool.workers[0].signals.finished.emit()
    assert page.model.rowCount() == 0
    assert page.summary_label.text() == ""
    assert page.header_label.text() == "AAPL · US"

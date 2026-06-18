"""Entity research page tests (CompanyPage, InsiderPage).

TDD: tests written BEFORE implementation — all tests must fail (RED) on first run.
"""

from __future__ import annotations

from datetime import date

import pytest

from insider_scanner.persistence.feed import FeedMarket, FeedRecord


# ---------------------------------------------------------------------------
# Test helpers / fakes
# ---------------------------------------------------------------------------


class InlinePool:
    """Synchronous pool — worker.run() is called immediately in the same thread."""

    def start(self, worker) -> None:
        worker.run()


class FakeRepository:
    """Records calls and returns preset results for recent_by_issuer / recent_by_person."""

    def __init__(
        self,
        issuer_records: tuple[FeedRecord, ...] = (),
        person_records: tuple[FeedRecord, ...] = (),
    ) -> None:
        self.issuer_records = issuer_records
        self.person_records = person_records
        self.issuer_calls: list[tuple] = []
        self.person_calls: list[tuple] = []

    def recent_by_issuer(
        self,
        identifier: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        self.issuer_calls.append((identifier, markets, limit, exclude_key))
        return self.issuer_records

    def recent_by_person(
        self,
        person: str,
        *,
        markets: tuple[FeedMarket, ...] = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        self.person_calls.append((person, markets, limit, exclude_key))
        return self.person_records


class ErrorRepository:
    """Always raises RuntimeError on every query method."""

    def recent_by_issuer(self, identifier: str, **kwargs) -> tuple[FeedRecord, ...]:
        raise RuntimeError("DB offline")

    def recent_by_person(self, person: str, **kwargs) -> tuple[FeedRecord, ...]:
        raise RuntimeError("DB offline")


def _record(
    key: str = "us:1",
    *,
    issuer: str = "Apple Inc.",
    identifier: str = "AAPL",
    person: str = "Tim Cook",
    transaction_type: str = "Buy",
    transaction_date: date | None = date(2026, 5, 10),
    filing_date: date | None = date(2026, 5, 12),
    market: FeedMarket = FeedMarket.US,
) -> FeedRecord:
    return FeedRecord(
        key=key,
        market=market,
        transaction_type=transaction_type,
        issuer=issuer,
        identifier=identifier,
        person=person,
        role="CEO",
        transaction_date=transaction_date,
        filing_date=filing_date,
        quantity=1000.0,
        price=180.0,
        value_display="",
        value_sort=180_000.0,
        currency="USD",
        source="secform4",
        source_url="https://example.test/filing",
    )


# ---------------------------------------------------------------------------
# CompanyPage tests
# ---------------------------------------------------------------------------


class TestCompanyPageInit:
    def test_constructs_without_error(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository()
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

    def test_shows_empty_prompt_on_construction(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository()
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        state_text = page.state_label.text()
        assert "ticker" in state_text.lower() or "search" in state_text.lower()

    def test_table_is_accessible(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository()
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        assert page.table.accessibleName() != ""

    def test_object_names_set(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository()
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        assert page.header_label.objectName() == "entityHeader"
        assert page.summary_label.objectName() == "entitySummary"


class TestCompanyPageLoad:
    def test_calls_recent_by_issuer_with_correct_args(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository(issuer_records=(_record(),))
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert len(repo.issuer_calls) == 1
        identifier, markets, limit, _ = repo.issuer_calls[0]
        assert identifier == "AAPL"
        assert markets == (FeedMarket.US,)
        assert limit == 200

    def test_header_shows_issuer_name_and_market(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository(
            issuer_records=(_record(issuer="Apple Inc.", identifier="AAPL"),)
        )
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        header = page.header_label.text()
        assert "Apple Inc." in header
        assert "AAPL" in header
        assert "US" in header

    def test_header_falls_back_to_value_when_no_records(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository(issuer_records=())
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        # Empty result => empty state shown, but header fallback logic tested via
        # _header_text directly via a subclass call (we use one record for header path)
        repo2 = FakeRepository(issuer_records=(_record(issuer="", identifier="AAPL"),))
        page2 = CompanyPage(repo2, thread_pool=InlinePool())
        qtbot.addWidget(page2)
        page2.load("AAPL", FeedMarket.US)
        assert "AAPL" in page2.header_label.text()

    def test_summary_line_buys_sells_and_date(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        records = (
            _record(
                key="us:1", transaction_type="Buy", transaction_date=date(2026, 5, 10)
            ),
            _record(
                key="us:2",
                transaction_type="Purchase",
                transaction_date=date(2026, 5, 9),
            ),
            _record(
                key="us:3", transaction_type="Sell", transaction_date=date(2026, 5, 8)
            ),
        )
        repo = FakeRepository(issuer_records=records)
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        summary = page.summary_label.text()
        assert "3 transactions" in summary
        assert "2 buys" in summary
        assert "1 sells" in summary
        assert "2026-05-10" in summary

    def test_summary_date_falls_back_to_filing_date(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        records = (
            _record(
                key="us:1",
                transaction_type="Buy",
                transaction_date=None,
                filing_date=date(2026, 4, 1),
            ),
        )
        repo = FakeRepository(issuer_records=records)
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        summary = page.summary_label.text()
        assert "2026-04-01" in summary

    def test_summary_date_is_dash_when_no_dates(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        records = (
            _record(
                key="us:1",
                transaction_type="Buy",
                transaction_date=None,
                filing_date=None,
            ),
        )
        repo = FakeRepository(issuer_records=records)
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        summary = page.summary_label.text()
        assert "—" in summary

    def test_table_row_count_equals_records(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        records = tuple(_record(key=f"us:{i}") for i in range(5))
        repo = FakeRepository(issuer_records=records)
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert page.model.rowCount() == 5

    def test_empty_result_shows_no_activity_state(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository(issuer_records=())
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert page.model.rowCount() == 0

    def test_error_shows_error_state(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        page = CompanyPage(ErrorRepository(), thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert "company" in page.state_label.text().lower()

    def test_empty_value_shows_empty_prompt_without_querying(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository()
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("   ", FeedMarket.US)

        assert len(repo.issuer_calls) == 0
        assert not page.state_label.isHidden()

    def test_none_repository_shows_unavailable(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        page = CompanyPage(None, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert "unavailable" in page.state_label.text().lower()

    def test_stale_request_id_result_is_ignored(self, qtbot):
        """Result arriving after a new load() call (stale request) must not update the model."""
        from insider_scanner.gui.entity_pages import CompanyPage

        class CapturingPool:
            def __init__(self):
                self.workers: list = []

            def start(self, worker) -> None:
                self.workers.append(worker)

        records = tuple(_record(key=f"us:{i}") for i in range(3))
        repo = FakeRepository(issuer_records=records)
        pool = CapturingPool()
        page = CompanyPage(repo, thread_pool=pool)
        qtbot.addWidget(page)

        # First load — worker queued but not run yet
        page.load("AAPL", FeedMarket.US)

        # Second load before first finishes — bumps request_id
        repo2 = FakeRepository(issuer_records=())
        page._repository = repo2
        page.load("MSFT", FeedMarket.US)

        # Complete the FIRST (now stale) worker — result should be ignored
        pool.workers[0].run()

        assert page.model.rowCount() == 0  # second load returned empty, first ignored


class TestCompanyPageNullRepositoryConstruction:
    def test_construction_with_none_repo_shows_prompt(self, qtbot):
        from insider_scanner.gui.entity_pages import CompanyPage

        page = CompanyPage(None, thread_pool=InlinePool())
        qtbot.addWidget(page)

        # No crash; state label shows the empty prompt (not hidden)
        assert not page.state_label.isHidden()


# ---------------------------------------------------------------------------
# InsiderPage tests
# ---------------------------------------------------------------------------


class TestInsiderPageInit:
    def test_constructs_without_error(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository()
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

    def test_shows_empty_prompt_on_construction(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository()
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        state_text = page.state_label.text()
        assert "insider" in state_text.lower() or "search" in state_text.lower()

    def test_object_names_set(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository()
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        assert page.header_label.objectName() == "entityHeader"
        assert page.summary_label.objectName() == "entitySummary"


class TestInsiderPageLoad:
    def test_calls_recent_by_person_with_correct_args(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository(person_records=(_record(),))
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert len(repo.person_calls) == 1
        person, markets, limit, _ = repo.person_calls[0]
        assert person == "Tim Cook"
        assert markets == (FeedMarket.US,)
        assert limit == 200

    def test_header_shows_person_and_market(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository(person_records=(_record(person="Tim Cook"),))
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        header = page.header_label.text()
        assert "Tim Cook" in header
        assert "US" in header

    def test_summary_line_buys_sells_and_date(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        records = (
            _record(
                key="us:1", transaction_type="Sell", transaction_date=date(2026, 3, 1)
            ),
            _record(
                key="us:2", transaction_type="Sale", transaction_date=date(2026, 3, 2)
            ),
            _record(
                key="us:3", transaction_type="Buy", transaction_date=date(2026, 3, 3)
            ),
        )
        repo = FakeRepository(person_records=records)
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        summary = page.summary_label.text()
        assert "3 transactions" in summary
        assert "1 buys" in summary
        assert "2 sells" in summary
        assert "2026-03-03" in summary

    def test_table_row_count_equals_records(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        records = tuple(_record(key=f"us:{i}") for i in range(7))
        repo = FakeRepository(person_records=records)
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert page.model.rowCount() == 7

    def test_empty_result_shows_no_activity_state(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository(person_records=())
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert page.model.rowCount() == 0

    def test_error_shows_error_state(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        page = InsiderPage(ErrorRepository(), thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert "insider" in page.state_label.text().lower()

    def test_empty_value_shows_empty_prompt_without_querying(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository()
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("  ", FeedMarket.US)

        assert len(repo.person_calls) == 0
        assert not page.state_label.isHidden()

    def test_none_repository_shows_unavailable(self, qtbot):
        from insider_scanner.gui.entity_pages import InsiderPage

        page = InsiderPage(None, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert not page.state_label.isHidden()
        assert "unavailable" in page.state_label.text().lower()

    def test_stale_request_id_result_is_ignored(self, qtbot):
        """Result from a superseded load() must not populate the model."""
        from insider_scanner.gui.entity_pages import InsiderPage

        class CapturingPool:
            def __init__(self):
                self.workers: list = []

            def start(self, worker) -> None:
                self.workers.append(worker)

        records = tuple(_record(key=f"us:{i}") for i in range(4))
        repo = FakeRepository(person_records=records)
        pool = CapturingPool()
        page = InsiderPage(repo, thread_pool=pool)
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        repo2 = FakeRepository(person_records=())
        page._repository = repo2
        page.load("Nancy Pelosi", FeedMarket.CONGRESS)

        pool.workers[0].run()

        assert page.model.rowCount() == 0

    def test_does_not_call_issuer_method(self, qtbot):
        """InsiderPage must never call recent_by_issuer."""
        from insider_scanner.gui.entity_pages import InsiderPage

        repo = FakeRepository(person_records=(_record(),))
        page = InsiderPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("Tim Cook", FeedMarket.US)

        assert len(repo.issuer_calls) == 0


class TestCompanyPageDoesNotCallPerson:
    def test_does_not_call_person_method(self, qtbot):
        """CompanyPage must never call recent_by_person."""
        from insider_scanner.gui.entity_pages import CompanyPage

        repo = FakeRepository(issuer_records=(_record(),))
        page = CompanyPage(repo, thread_pool=InlinePool())
        qtbot.addWidget(page)

        page.load("AAPL", FeedMarket.US)

        assert len(repo.person_calls) == 0


# ---------------------------------------------------------------------------
# Market label tests (both pages)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("market", list(FeedMarket))
def test_company_page_header_contains_market_value(qtbot, market):
    from insider_scanner.gui.entity_pages import CompanyPage

    repo = FakeRepository(issuer_records=(_record(market=market, identifier="X"),))
    page = CompanyPage(repo, thread_pool=InlinePool())
    qtbot.addWidget(page)

    page.load("X", market)

    assert market.value in page.header_label.text()


@pytest.mark.parametrize("market", list(FeedMarket))
def test_insider_page_header_contains_market_value(qtbot, market):
    from insider_scanner.gui.entity_pages import InsiderPage

    repo = FakeRepository(person_records=(_record(market=market),))
    page = InsiderPage(repo, thread_pool=InlinePool())
    qtbot.addWidget(page)

    page.load("Tim Cook", market)

    assert market.value in page.header_label.text()

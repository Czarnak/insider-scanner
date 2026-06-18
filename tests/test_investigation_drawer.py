"""InvestigationDrawer widget tests — TDD RED phase written first."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from insider_scanner.gui.investigation_drawer import InvestigationDrawer
from insider_scanner.persistence.feed import (
    FeedDetail,
    FeedMarket,
    FeedRecord,
)


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


class InlinePool:
    """Synchronous thread pool: runs worker immediately on start()."""

    def start(self, worker) -> None:
        worker.run()


class FakeRepository:
    """Preset-response repository that records every call."""

    def __init__(
        self,
        *,
        detail_return: FeedDetail | None = None,
        by_person: tuple[FeedRecord, ...] = (),
        by_issuer: tuple[FeedRecord, ...] = (),
        detail_error: Exception | None = None,
    ) -> None:
        self._detail_return = detail_return
        self._by_person = by_person
        self._by_issuer = by_issuer
        self._detail_error = detail_error
        self.detail_calls: list[str] = []
        self.by_person_calls: list[tuple] = []
        self.by_issuer_calls: list[tuple] = []

    def detail(self, key: str) -> FeedDetail | None:
        self.detail_calls.append(key)
        if self._detail_error is not None:
            raise self._detail_error
        return self._detail_return

    def recent_by_person(
        self,
        person: str,
        *,
        markets: tuple = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        self.by_person_calls.append((person, markets, limit, exclude_key))
        return self._by_person

    def recent_by_issuer(
        self,
        identifier: str,
        *,
        markets: tuple = (),
        limit: int = 8,
        exclude_key: str | None = None,
    ) -> tuple[FeedRecord, ...]:
        self.by_issuer_calls.append((identifier, markets, limit, exclude_key))
        return self._by_issuer


def _record(
    *,
    key: str = "us:1",
    market: FeedMarket = FeedMarket.US,
    transaction_type: str = "Buy",
    issuer: str = "Example Corp",
    identifier: str = "AAPL",
    person: str = "Jane Director",
    role: str = "Director",
    transaction_date: date | None = date(2026, 6, 14),
    filing_date: date | None = date(2026, 6, 15),
    quantity: float | None = 500.0,
    price: float | None = 210.50,
    value_display: str = "",
    value_sort: float | None = 105250.0,
    currency: str = "USD",
    source: str = "secform4",
    source_url: str = "https://example.test/filing",
) -> FeedRecord:
    return FeedRecord(
        key=key,
        market=market,
        transaction_type=transaction_type,
        issuer=issuer,
        identifier=identifier,
        person=person,
        role=role,
        transaction_date=transaction_date,
        filing_date=filing_date,
        quantity=quantity,
        price=price,
        value_display=value_display,
        value_sort=value_sort,
        currency=currency,
        source=source,
        source_url=source_url,
    )


def _detail(
    record: FeedRecord | None = None,
    *,
    shares_owned_after: float | None = 2500.0,
    footnotes: str = "",
    reference: str = "https://example.test/filing",
    metadata: tuple[tuple[str, str], ...] = (),
) -> FeedDetail:
    if record is None:
        record = _record()
    return FeedDetail(
        record=record,
        shares_owned_after=shares_owned_after,
        footnotes=footnotes,
        reference=reference,
        metadata=metadata,
    )


def _all_text(widget) -> str:
    """Collect all visible text from child QLabel widgets recursively."""
    from PySide6.QtWidgets import QLabel

    parts: list[str] = []
    for label in widget.findChildren(QLabel):
        t = label.text()
        if t:
            parts.append(t)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_drawer_constructs_without_error(self, qtbot):
        # Arrange + Act
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Assert — no crash; accessible name set
        assert drawer.accessibleName() == "Investigation details"

    def test_initial_state_shows_empty_prompt(self, qtbot):
        # Arrange + Act
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Assert — state label contains the empty prompt
        text = _all_text(drawer)
        assert "Select a transaction to investigate" in text

    def test_drawer_starts_hidden_or_showing_prompt_not_content(self, qtbot):
        # Arrange + Act
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Assert — no "Jane Director" in the blank state
        text = _all_text(drawer)
        assert "Jane Director" not in text


class TestShowRecord:
    def test_show_record_renders_person_role_issuer_identifier(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record()

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "Jane Director" in text
        assert "Director" in text
        assert "Example Corp" in text
        assert "AAPL" in text

    def test_show_record_renders_market_label(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(market=FeedMarket.CONGRESS, key="congress:1"))

        # Assert — market value appears
        text = _all_text(drawer)
        assert "Congress" in text

    def test_show_record_buy_uses_purchase_color(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record(transaction_type="Buy")

        # Act
        drawer.show_record(rec)

        # Assert — expose the chosen QColor via test accessor
        color: QColor = drawer.last_transaction_color()
        assert color.isValid()
        # Purchase color in light theme is #1A7F4B (green-ish)
        # We only assert it's not the sale (red) color
        assert color.red() < color.green(), (
            f"Expected a greenish purchase color, got RGB({color.red()},{color.green()},{color.blue()})"
        )

    def test_show_record_sell_uses_sale_color(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record(transaction_type="Sell")

        # Act
        drawer.show_record(rec)

        # Assert — sale color is reddish
        color: QColor = drawer.last_transaction_color()
        assert color.isValid()
        assert color.red() > color.green(), (
            f"Expected a reddish sale color, got RGB({color.red()},{color.green()},{color.blue()})"
        )

    def test_show_record_purchase_synonym_uses_purchase_color(self, qtbot):
        # Arrange — "Purchase" is a synonym for buy
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(transaction_type="Purchase"))

        # Assert
        color: QColor = drawer.last_transaction_color()
        assert color.red() < color.green()

    def test_show_record_sale_synonym_uses_sale_color(self, qtbot):
        # Arrange — "Sale" is a synonym for sell
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(transaction_type="Sale"))

        # Assert
        color: QColor = drawer.last_transaction_color()
        assert color.red() > color.green()

    def test_transaction_type_text_is_always_visible(self, qtbot):
        """Color must not be the only indicator — text must also be rendered."""
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(transaction_type="Buy"))

        # Assert
        text = _all_text(drawer)
        assert "Buy" in text

    def test_show_record_trade_date_and_filing_date_visible(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(
            _record(transaction_date=date(2026, 6, 14), filing_date=date(2026, 6, 15))
        )

        # Assert
        text = _all_text(drawer)
        assert "2026-06-14" in text
        assert "2026-06-15" in text

    def test_quantity_formatted_with_two_decimal_places(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(quantity=500.0))

        # Assert — "500.00" in output
        text = _all_text(drawer)
        assert "500.00" in text

    def test_quantity_none_shows_dash(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(quantity=None))

        # Assert
        text = _all_text(drawer)
        assert "—" in text

    def test_value_display_used_when_set(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(value_display="$1,001 - $15,000", value_sort=None))

        # Assert
        text = _all_text(drawer)
        assert "$1,001 - $15,000" in text

    def test_value_sort_formatted_with_currency_when_no_display(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(
            _record(value_display="", value_sort=105250.0, currency="USD")
        )

        # Assert
        text = _all_text(drawer)
        assert "105,250.00" in text
        assert "USD" in text

    def test_value_none_shows_dash(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record(value_display="", value_sort=None))

        # Assert
        text = _all_text(drawer)
        assert "—" in text


class TestOwnershipBlock:
    def test_ownership_shows_number_when_shares_owned_after_present(self, qtbot):
        # Arrange
        rec = _record()
        detail = _detail(rec, shares_owned_after=2500.0)
        repo = FakeRepository(detail_return=detail)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "2,500" in text

    def test_ownership_shows_not_disclosed_when_shares_owned_after_none(self, qtbot):
        # Arrange
        rec = _record()
        detail = _detail(rec, shares_owned_after=None)
        repo = FakeRepository(detail_return=detail)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "Not disclosed" in text

    def test_ownership_not_disclosed_when_no_detail(self, qtbot):
        # Arrange — repository returns no detail
        repo = FakeRepository(detail_return=None)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "Not disclosed" in text


class TestFootnotesBlock:
    def test_footnotes_shown_when_present(self, qtbot):
        # Arrange
        rec = _record(key="congress:1", market=FeedMarket.CONGRESS)
        detail = _detail(rec, footnotes="Traded via spouse account.")
        repo = FakeRepository(detail_return=detail)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "Traded via spouse account." in text

    def test_no_footnotes_shown_when_empty(self, qtbot):
        # Arrange
        rec = _record()
        detail = _detail(rec, footnotes="")
        repo = FakeRepository(detail_return=detail)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "No footnotes" in text


class TestRecentActivity:
    def test_by_person_list_renders_one_entry(self, qtbot):
        # Arrange
        other_rec = _record(
            key="us:2",
            issuer="Other Corp",
            transaction_type="Sell",
            transaction_date=date(2026, 5, 1),
        )
        repo = FakeRepository(
            detail_return=_detail(),
            by_person=(other_rec,),
        )
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "Other Corp" in text

    def test_by_person_empty_shows_no_other_recent_activity(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail(), by_person=())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "No other recent activity" in text

    def test_by_issuer_list_renders_one_entry(self, qtbot):
        # Arrange — use a unique issuer so we can distinguish the entry
        other_rec = _record(
            key="us:3",
            issuer="DistinctCorp",
            person="Bob CFO",
            transaction_type="Buy",
            transaction_date=date(2026, 4, 10),
        )
        repo = FakeRepository(
            detail_return=_detail(),
            by_issuer=(other_rec,),
        )
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert — the line format includes issuer
        text = _all_text(drawer)
        assert "DistinctCorp" in text

    def test_by_issuer_empty_shows_no_other_recent_activity(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail(), by_issuer=())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        # There are two sections; at least one "No other recent activity." must appear
        assert "No other recent activity" in text


class TestKeyboardAndSignals:
    def test_escape_emits_close_requested(self, qtbot):
        # Arrange
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Act + Assert
        with qtbot.waitSignal(drawer.closeRequested, timeout=1_000):
            qtbot.keyPress(drawer, Qt.Key.Key_Escape)

    def test_open_company_emits_with_identifier_and_market(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record(identifier="AAPL", market=FeedMarket.US)
        drawer.show_record(rec)

        emitted: list[tuple] = []
        drawer.openCompanyRequested.connect(
            lambda ident, mkt: emitted.append((ident, mkt))
        )

        # Act

        btn = _find_button(drawer, "Open company")
        assert btn is not None, "Could not find 'Open company' button"
        btn.click()

        # Assert
        assert len(emitted) == 1
        assert emitted[0] == ("AAPL", FeedMarket.US)

    def test_open_insider_emits_with_person_and_market(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record(person="Jane Director", market=FeedMarket.US)
        drawer.show_record(rec)

        emitted: list[tuple] = []
        drawer.openInsiderRequested.connect(lambda p, m: emitted.append((p, m)))

        # Act
        btn = _find_button(drawer, "Open insider")
        assert btn is not None, "Could not find 'Open insider' button"
        btn.click()

        # Assert
        assert len(emitted) == 1
        assert emitted[0] == ("Jane Director", FeedMarket.US)

    def test_alert_compare_buttons_disabled(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        drawer.show_record(_record())

        # Assert — Alert/Compare remain placeholders
        for name in ("Alert", "Compare"):
            btn = _find_button(drawer, name)
            assert btn is not None, f"Could not find '{name}' button"
            assert btn.isEnabled() is False, f"'{name}' button should be disabled"

    def test_watch_button_enabled_and_emits_record(self, qtbot):
        # Arrange
        repo = FakeRepository(detail_return=_detail())
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)
        rec = _record(key="us:7", identifier="MSFT", person="Bob Smith")
        drawer.show_record(rec)

        emitted: list = []
        drawer.watchRequested.connect(emitted.append)

        # Act
        watch_btn = _find_button(drawer, "Watch")
        assert watch_btn is not None
        assert watch_btn.isEnabled() is True
        watch_btn.click()

        # Assert
        assert emitted == [rec]


class TestErrorPath:
    def test_error_in_repository_shows_error_state(self, qtbot):
        # Arrange — repository.detail raises
        repo = FakeRepository(detail_error=RuntimeError("DB unavailable"))
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "Could not load investigation details" in text

    def test_stale_request_id_result_is_dropped(self, qtbot):
        """A result arriving for an old request id must not overwrite the current state."""

        # Arrange — capturing pool that stores but does not run workers
        class CapturingPool:
            def __init__(self):
                self.workers: list = []

            def start(self, worker) -> None:
                self.workers.append(worker)

        repo = FakeRepository(detail_return=_detail())
        pool = CapturingPool()
        drawer = InvestigationDrawer(repository=repo, thread_pool=pool)
        qtbot.addWidget(drawer)

        rec1 = _record(key="us:1")
        rec2 = _record(key="us:2", issuer="NewCorp", person="Bob Smith")

        # Start first request — worker queued but not run
        drawer.show_record(rec1)
        first_worker = pool.workers[0]

        # Start second request — bumps the request id
        drawer.show_record(rec2)

        # Run the first (now stale) worker AFTER the second request has been issued
        first_worker.run()

        # The result for rec1 must be dropped — "NewCorp" must NOT appear yet
        # (the second worker is still pending)
        text = _all_text(drawer)
        assert "Example Corp" not in text or "NewCorp" not in text
        # Specifically rec1's issuer "Example Corp" should NOT be rendered
        # because we are now waiting for rec2's worker
        # The drawer must still be in "Loading" state
        assert "Loading" in text


class TestNullRepository:
    def test_show_record_with_null_repository_renders_summary(self, qtbot):
        # Arrange — no repository
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert — basic summary still shows
        text = _all_text(drawer)
        assert "Jane Director" in text
        assert "Example Corp" in text

    def test_show_record_with_null_repository_shows_not_disclosed(self, qtbot):
        # Arrange
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "Not disclosed" in text

    def test_show_record_with_null_repository_shows_no_footnotes(self, qtbot):
        # Arrange
        drawer = InvestigationDrawer(repository=None)
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(_record())

        # Assert
        text = _all_text(drawer)
        assert "No footnotes" in text


class TestMetadata:
    def test_metadata_rows_rendered(self, qtbot):
        # Arrange
        rec = _record(key="congress:1", market=FeedMarket.CONGRESS)
        detail = _detail(rec, metadata=(("Party", "Independent"), ("Owner", "Self")))
        repo = FakeRepository(detail_return=detail)
        drawer = InvestigationDrawer(repository=repo, thread_pool=InlinePool())
        qtbot.addWidget(drawer)

        # Act
        drawer.show_record(rec)

        # Assert
        text = _all_text(drawer)
        assert "Party" in text
        assert "Independent" in text
        assert "Owner" in text
        assert "Self" in text


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _find_button(widget, text: str):
    """Find the first QPushButton whose text matches exactly."""
    from PySide6.QtWidgets import QPushButton

    for btn in widget.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None

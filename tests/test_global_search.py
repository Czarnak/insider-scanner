"""Tests for GlobalSearchController and GlobalSearchPopup — written FIRST (TDD RED)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from insider_scanner.gui.global_search import GlobalSearchController, GlobalSearchPopup
from insider_scanner.persistence.feed import (
    EntityHit,
    EntitySearchResults,
    FeedMarket,
)


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


class InlinePool:
    """Synchronous thread pool — runs worker immediately on the calling thread."""

    def start(self, worker) -> None:
        worker.run()


class FakeRepository:
    """Minimal duck-typed repository with a preset search_entities response."""

    def __init__(self, results: EntitySearchResults) -> None:
        self._results = results
        self.calls: list[str] = []

    def search_entities(self, text: str, *, limit: int = 8) -> EntitySearchResults:
        self.calls.append(text)
        return self._results


def _make_results() -> EntitySearchResults:
    """Build a canonical EntitySearchResults with one company and one insider."""
    company = EntityHit(
        kind="company",
        label="Apple Inc.",
        identifier="AAPL",
        person="",
        market=FeedMarket.US,
    )
    insider = EntityHit(
        kind="insider",
        label="Jane Director",
        identifier="",
        person="Jane Director",
        market=FeedMarket.US,
    )
    return EntitySearchResults(companies=(company,), insiders=(insider,))


def _make_controller(
    qtbot,
    results: EntitySearchResults | None = None,
    *,
    repository=None,
) -> tuple[QLineEdit, GlobalSearchController]:
    """Construct a controller with an inline pool and optional fake repository."""
    line_edit = QLineEdit()
    qtbot.addWidget(line_edit)

    repo = repository
    if repo is None and results is not None:
        repo = FakeRepository(results)

    pool = InlinePool()
    controller = GlobalSearchController(
        line_edit,
        repo,
        thread_pool=pool,
    )
    qtbot.addWidget(controller.popup)
    return line_edit, controller


# ---------------------------------------------------------------------------
# GlobalSearchPopup tests
# ---------------------------------------------------------------------------


class TestGlobalSearchPopup:
    def test_list_widget_accessible_name(self, qtbot):
        # Arrange / Act
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)

        # Assert
        assert popup.list_widget.accessibleName() == "Search results"

    def test_populate_builds_expected_item_count(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        results = _make_results()

        # Act
        popup.populate("app", results)

        # Assert: feed-search row + Companies header + 1 company + Insiders header + 1 insider = 5
        assert popup.list_widget.count() == 5

    def test_populate_first_row_is_feed_search_action(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)

        # Act
        popup.populate("app", _make_results())

        # Assert
        item = popup.list_widget.item(0)
        assert item is not None
        assert "app" in item.text()
        data = item.data(Qt.ItemDataRole.UserRole)
        assert isinstance(data, tuple) and data[0] == "feed"
        assert data[1] == "app"

    def test_populate_company_item_stores_entity_hit(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        results = _make_results()

        # Act
        popup.populate("app", results)

        # Assert: row 2 (index 2) is the company item (after feed-search + header)
        company_item = popup.list_widget.item(2)
        hit = company_item.data(Qt.ItemDataRole.UserRole)
        assert isinstance(hit, EntityHit)
        assert hit.kind == "company"
        assert hit.identifier == "AAPL"

    def test_populate_insider_item_stores_entity_hit(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        results = _make_results()

        # Act
        popup.populate("app", results)

        # Assert: row 4 (index 4) is the insider item (feed-search + header + company + header)
        insider_item = popup.list_widget.item(4)
        hit = insider_item.data(Qt.ItemDataRole.UserRole)
        assert isinstance(hit, EntityHit)
        assert hit.kind == "insider"
        assert hit.person == "Jane Director"

    def test_populate_header_items_are_not_selectable_or_enabled(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)

        # Act
        popup.populate("app", _make_results())

        # Assert: row 1 = Companies header, row 3 = Insiders header
        for header_index in (1, 3):
            item = popup.list_widget.item(header_index)
            flags = item.flags()
            assert not (flags & Qt.ItemFlag.ItemIsSelectable), (
                f"header at {header_index} should not be selectable"
            )
            assert not (flags & Qt.ItemFlag.ItemIsEnabled), (
                f"header at {header_index} should not be enabled"
            )

    def test_populate_omits_company_section_when_empty(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        results = EntitySearchResults(
            companies=(),
            insiders=(
                EntityHit(
                    kind="insider",
                    label="Jane Director",
                    identifier="",
                    person="Jane Director",
                    market=FeedMarket.US,
                ),
            ),
        )

        # Act
        popup.populate("jane", results)

        # Assert: feed-search row + Insiders header + 1 insider = 3
        assert popup.list_widget.count() == 3

    def test_populate_omits_insider_section_when_empty(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        results = EntitySearchResults(
            companies=(
                EntityHit(
                    kind="company",
                    label="Apple Inc.",
                    identifier="AAPL",
                    person="",
                    market=FeedMarket.US,
                ),
            ),
            insiders=(),
        )

        # Act
        popup.populate("aapl", results)

        # Assert: feed-search row + Companies header + 1 company = 3
        assert popup.list_widget.count() == 3

    def test_populate_with_empty_text_clears_list(self, qtbot):
        # Arrange
        popup = GlobalSearchPopup()
        qtbot.addWidget(popup)
        popup.populate("app", _make_results())

        # Act — empty text: nothing should be rendered (the controller hides popup
        # instead of calling populate, but the widget itself must handle it gracefully)
        popup.populate("", EntitySearchResults((), ()))

        # Assert
        assert popup.list_widget.count() == 0


# ---------------------------------------------------------------------------
# GlobalSearchController — search_now tests
# ---------------------------------------------------------------------------


class TestSearchNow:
    def test_search_now_calls_repository_with_stripped_text(self, qtbot):
        # Arrange
        results = _make_results()
        repo = FakeRepository(results)
        _, controller = _make_controller(qtbot, repository=repo)

        # Act
        controller.search_now("  app  ")

        # Assert
        assert repo.calls == ["app"]

    def test_search_now_populates_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())

        # Act
        controller.search_now("app")

        # Assert: 5 items (feed-search + Companies header + company + Insiders header + insider)
        assert controller.popup.list_widget.count() == 5

    def test_search_now_empty_text_does_not_query_repository(self, qtbot):
        # Arrange
        results = _make_results()
        repo = FakeRepository(results)
        _, controller = _make_controller(qtbot, repository=repo)

        # Act
        controller.search_now("")

        # Assert
        assert repo.calls == []

    def test_search_now_empty_text_hides_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")  # ensure something is populated first

        # Act
        controller.search_now("")

        # Assert: popup is hidden (or list is cleared)
        assert not controller.popup.isVisible()

    def test_search_now_none_repository_does_not_crash(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, repository=None)

        # Act / Assert — must not raise
        controller.search_now("app")

    def test_search_now_none_repository_hides_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, repository=None)

        # Act
        controller.search_now("app")

        # Assert
        assert not controller.popup.isVisible()

    def test_search_now_stale_request_id_discards_result(self, qtbot):
        """Simulate a race: bump _request_id after the worker is created but
        before it completes.  The result must be discarded."""

        # Arrange
        call_count = 0
        original_results = _make_results()

        class SlowRepo:
            def search_entities(
                self, text: str, *, limit: int = 8
            ) -> EntitySearchResults:
                nonlocal call_count
                call_count += 1
                return original_results

        class BumpingPool:
            """Bumps controller._request_id before running the worker."""

            def __init__(self, ctrl_ref: list) -> None:
                self._ctrl_ref = ctrl_ref

            def start(self, worker) -> None:
                # Simulate a newer request arriving and bumping the id
                self._ctrl_ref[0]._request_id += 1
                worker.run()

        ctrl_ref: list = [None]
        line_edit = QLineEdit()
        qtbot.addWidget(line_edit)
        pool = BumpingPool(ctrl_ref)
        controller = GlobalSearchController(line_edit, SlowRepo(), thread_pool=pool)
        qtbot.addWidget(controller.popup)
        ctrl_ref[0] = controller

        # Act
        controller.search_now("app")

        # Assert: result is discarded — popup list stays empty
        assert controller.popup.list_widget.count() == 0


# ---------------------------------------------------------------------------
# GlobalSearchController — signal emission tests
# ---------------------------------------------------------------------------


class TestSignalEmission:
    def test_activate_company_item_emits_company_chosen(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        company_item = controller.popup.list_widget.item(2)

        # Act / Assert
        with qtbot.waitSignal(controller.companyChosen, timeout=1000) as blocker:
            controller._activate_item(company_item)

        identifier, market = blocker.args
        assert identifier == "AAPL"
        assert market == FeedMarket.US

    def test_activate_insider_item_emits_insider_chosen(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("jane")
        insider_item = controller.popup.list_widget.item(4)

        # Act / Assert
        with qtbot.waitSignal(controller.insiderChosen, timeout=1000) as blocker:
            controller._activate_item(insider_item)

        person, market = blocker.args
        assert person == "Jane Director"
        assert market == FeedMarket.US

    def test_activate_feed_search_row_emits_feed_search_requested(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        feed_item = controller.popup.list_widget.item(0)

        # Act / Assert
        with qtbot.waitSignal(controller.feedSearchRequested, timeout=1000) as blocker:
            controller._activate_item(feed_item)

        (text,) = blocker.args
        assert text == "app"

    def test_activate_company_item_hides_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        company_item = controller.popup.list_widget.item(2)

        # Act
        controller._activate_item(company_item)

        # Assert
        assert not controller.popup.isVisible()

    def test_activate_insider_item_hides_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("jane")
        insider_item = controller.popup.list_widget.item(4)

        # Act
        controller._activate_item(insider_item)

        # Assert
        assert not controller.popup.isVisible()

    def test_activate_feed_search_item_hides_popup(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        feed_item = controller.popup.list_widget.item(0)

        # Act
        controller._activate_item(feed_item)

        # Assert
        assert not controller.popup.isVisible()

    def test_activate_header_item_emits_no_signal(self, qtbot):
        # Arrange
        _, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        header_item = controller.popup.list_widget.item(1)  # Companies header

        # Act — should silently ignore the header row
        # Use a list to accumulate any wrongly emitted signals
        fired: list[str] = []
        controller.companyChosen.connect(lambda *_: fired.append("company"))
        controller.insiderChosen.connect(lambda *_: fired.append("insider"))
        controller.feedSearchRequested.connect(lambda *_: fired.append("feed"))

        controller._activate_item(header_item)

        # Assert
        assert fired == []

    def test_activate_item_with_none_data_emits_no_signal(self, qtbot):
        # Arrange
        from PySide6.QtWidgets import QListWidgetItem

        _, controller = _make_controller(qtbot, _make_results())
        empty_item = QListWidgetItem("no data")
        # UserRole data is None by default

        fired: list[str] = []
        controller.companyChosen.connect(lambda *_: fired.append("company"))
        controller.insiderChosen.connect(lambda *_: fired.append("insider"))
        controller.feedSearchRequested.connect(lambda *_: fired.append("feed"))

        # Act
        controller._activate_item(empty_item)

        # Assert
        assert fired == []


# ---------------------------------------------------------------------------
# GlobalSearchController — returnPressed behaviour
# ---------------------------------------------------------------------------


class TestReturnPressed:
    def test_return_pressed_with_text_and_no_selection_emits_feed_search(self, qtbot):
        # Arrange
        line_edit, controller = _make_controller(qtbot, _make_results())
        line_edit.setText("apple")

        # Act / Assert
        with qtbot.waitSignal(controller.feedSearchRequested, timeout=1000) as blocker:
            line_edit.returnPressed.emit()

        (text,) = blocker.args
        assert text == "apple"

    def test_return_pressed_with_empty_text_emits_no_signal(self, qtbot):
        # Arrange
        line_edit, controller = _make_controller(qtbot, _make_results())
        line_edit.setText("")

        fired: list[str] = []
        controller.feedSearchRequested.connect(lambda *_: fired.append("fired"))

        # Act
        line_edit.returnPressed.emit()

        # Assert
        assert fired == []

    def test_return_pressed_with_empty_text_does_not_query_repository(self, qtbot):
        # Arrange
        results = _make_results()
        repo = FakeRepository(results)
        line_edit, controller = _make_controller(qtbot, repository=repo)
        line_edit.setText("")

        # Act
        line_edit.returnPressed.emit()

        # Assert
        assert repo.calls == []

    def test_return_pressed_with_selection_activates_selected_item(self, qtbot):
        # Arrange
        line_edit, controller = _make_controller(qtbot, _make_results())
        controller.search_now("app")
        # Manually select the company item (index 2)
        company_item = controller.popup.list_widget.item(2)
        controller.popup.list_widget.setCurrentItem(company_item)
        line_edit.setText("app")

        # Act / Assert
        with qtbot.waitSignal(controller.companyChosen, timeout=1000) as blocker:
            line_edit.returnPressed.emit()

        identifier, market = blocker.args
        assert identifier == "AAPL"

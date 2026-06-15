"""Feed-first application shell tests."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.feed import FeedCriteria, FeedMarket
from insider_scanner.persistence.feed_state import (
    FeedState,
    SavedScreen,
    load_feed_state,
    save_feed_state,
)
from insider_scanner.services.context import PersistenceContext
from insider_scanner.persistence.coverage import CoverageRepository
from insider_scanner.persistence.refresh import RefreshStateRepository
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UsTradeRepository,
)


def _services(tmp_path):
    engine = create_sqlite_engine(tmp_path / "shell.sqlite3")
    bootstrap_database(engine)
    persistence = PersistenceContext(
        engine=engine,
        us_trades=UsTradeRepository(engine),
        congress_trades=CongressTradeRepository(engine),
        european_trades=EuropeanTradeRepository(engine),
        coverage=CoverageRepository(engine),
        refresh=RefreshStateRepository(engine),
    )
    return SimpleNamespace(
        persistence=persistence,
        us=None,
        congress=None,
        european=None,
    )


def test_shell_starts_on_feed_and_exposes_only_functional_destinations(qtbot, tmp_path):
    from insider_scanner.gui.main_window import MainWindow

    services = _services(tmp_path)
    window = MainWindow(services, state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    try:
        assert window.page_stack.currentWidget() is window.feed_page
        assert list(window.navigation_buttons) == [
            "Feed",
            "Insider Scan",
            "Congress Scan",
            "European Scan",
            "Analysis",
        ]
        assert window.page_title.text() == "Feed"
        assert window.global_search.isVisibleTo(window)
        assert window.global_search.accessibleName() == "Search unified feed"
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_navigation_switches_workspace_and_hides_feed_search(qtbot, tmp_path):
    from insider_scanner.gui.main_window import MainWindow

    services = _services(tmp_path)
    window = MainWindow(services, state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    window.show()
    try:
        qtbot.mouseClick(
            window.navigation_buttons["Congress Scan"],
            Qt.MouseButton.LeftButton,
        )

        assert window.page_stack.currentWidget() is window.congress_tab
        assert window.page_title.text() == "Congress Scan"
        assert window.global_search.isHidden()

        qtbot.mouseClick(
            window.navigation_buttons["Feed"],
            Qt.MouseButton.LeftButton,
        )
        assert window.page_stack.currentWidget() is window.feed_page
        assert window.global_search.isVisible()
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_top_bar_search_and_reload_delegate_to_feed(qtbot):
    from insider_scanner.gui.main_window import MainWindow

    window = MainWindow(services=None)
    qtbot.addWidget(window)
    calls = []
    window.feed_page.set_search = lambda value: calls.append(("search", value))
    window.feed_page.reload = lambda: calls.append(("reload", None))

    window.global_search.setText("AAPL")
    window._apply_global_search()
    qtbot.mouseClick(window.reload_button, Qt.MouseButton.LeftButton)

    assert calls == [("search", "AAPL"), ("reload", None)]


# ---------------------------------------------------------------------------
# Chunk E + F tests
# ---------------------------------------------------------------------------


def test_restore_on_construction(qtbot, tmp_path):
    """State file criteria + search are restored when window opens."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    criteria = FeedCriteria(search="apple", markets=(FeedMarket.US,))
    save_feed_state(state_path, FeedState(criteria=criteria))

    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        assert window.feed_page.criteria().search == "apple"
        assert window.feed_page.criteria().markets == (FeedMarket.US,)
        assert window.global_search.text() == "apple"
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_save_current_screen(qtbot, tmp_path, monkeypatch):
    """Saving a screen persists it to the state file."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        nontrivial = FeedCriteria(search="TSLA", markets=(FeedMarket.US,))
        window.feed_page.set_criteria(nontrivial)

        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("My Screen", True),
        )
        window._save_current_screen()

        on_disk = load_feed_state(state_path)
        names = [s.name for s in on_disk.screens]
        assert "My Screen" in names
        saved = next(s for s in on_disk.screens if s.name == "My Screen")
        assert saved.criteria.search == "TSLA"
        assert saved.criteria.markets == (FeedMarket.US,)
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_apply_screen_with_confirm_yes(qtbot, tmp_path, monkeypatch):
    """Applying a screen with Yes confirmation updates the live criteria."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    screen_criteria = FeedCriteria(search="AAPL")
    state = FeedState(screens=(SavedScreen("Alpha", screen_criteria),))
    save_feed_state(state_path, state)

    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        # Set a nontrivial criteria different from the screen
        window.feed_page.set_criteria(FeedCriteria(markets=(FeedMarket.US,)))

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        window._apply_screen("Alpha")
        assert window.feed_page.criteria().search == "AAPL"
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_apply_screen_with_confirm_no(qtbot, tmp_path, monkeypatch):
    """Applying a screen with No confirmation leaves criteria unchanged."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    screen_criteria = FeedCriteria(search="AAPL")
    state = FeedState(screens=(SavedScreen("Alpha", screen_criteria),))
    save_feed_state(state_path, state)

    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        window.feed_page.set_criteria(FeedCriteria(markets=(FeedMarket.US,)))

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.No,
        )
        window._apply_screen("Alpha")
        assert window.feed_page.criteria().markets == (FeedMarket.US,)
        assert window.feed_page.criteria().search == ""
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_delete_screen(qtbot, tmp_path, monkeypatch):
    """Deleting a screen removes it from the on-disk state file."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    state = FeedState(
        screens=(
            SavedScreen("Alpha", FeedCriteria(search="AAPL")),
            SavedScreen("Beta", FeedCriteria(search="TSLA")),
        )
    )
    save_feed_state(state_path, state)

    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        window._delete_screen("Alpha")

        on_disk = load_feed_state(state_path)
        names = [s.name for s in on_disk.screens]
        assert "Alpha" not in names
        assert "Beta" in names
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_reset_over_nontrivial_prompts_and_clears(qtbot, tmp_path, monkeypatch):
    """Resetting with nontrivial criteria prompts, then clears filters but keeps sort."""
    from insider_scanner.gui.main_window import MainWindow
    from insider_scanner.persistence.feed import FeedSortField

    state_path = tmp_path / "feed_state.json"
    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        nontrivial = FeedCriteria(
            search="MSFT",
            markets=(FeedMarket.US,),
            sort_field=FeedSortField.ISSUER,
            descending=False,
        )
        window.feed_page.set_criteria(nontrivial)
        assert window.feed_page.criteria().is_nontrivial()

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        window._on_feed_reset()

        result = window.feed_page.criteria()
        assert result.search == ""
        assert result.markets == ()
        assert result.sort_field == FeedSortField.ISSUER
        assert result.descending is False
        assert not result.is_nontrivial()
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_reset_declined_keeps_criteria(qtbot, tmp_path, monkeypatch):
    """Declining the reset confirmation leaves the active criteria untouched."""
    from insider_scanner.gui.main_window import MainWindow
    from insider_scanner.persistence.feed import FeedSortField

    state_path = tmp_path / "feed_state.json"
    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        nontrivial = FeedCriteria(
            search="MSFT",
            markets=(FeedMarket.US,),
            sort_field=FeedSortField.ISSUER,
            descending=False,
        )
        window.feed_page.set_criteria(nontrivial)

        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.No,
        )
        window._on_feed_reset()

        assert window.feed_page.criteria() == nontrivial
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_close_event_persists_criteria(qtbot, tmp_path):
    """Closing the window flushes criteria to the state file."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)

    window.feed_page.set_criteria(FeedCriteria(search="close-test"))
    window.close()

    on_disk = load_feed_state(state_path)
    assert on_disk.criteria.search == "close-test"
    services.persistence.close()


def test_buttons_feed_only_visibility(qtbot, tmp_path):
    """filters_button and screens_button are visible on Feed, hidden on other pages."""
    from insider_scanner.gui.main_window import MainWindow

    services = _services(tmp_path)
    window = MainWindow(services, state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    window.show()
    try:
        # On Feed page initially
        assert window.filters_button.isVisible()
        assert window.screens_button.isVisible()

        # Navigate away
        window._select_page("Insider Scan")
        assert not window.filters_button.isVisible()
        assert not window.screens_button.isVisible()

        # Navigate back
        window._select_page("Feed")
        assert window.filters_button.isVisible()
        assert window.screens_button.isVisible()
    finally:
        window.shutdown_workers()
        services.persistence.close()


def test_corrupt_state_file_does_not_break_launch(qtbot, tmp_path):
    """A corrupt state file results in default criteria, not a crash."""
    from insider_scanner.gui.main_window import MainWindow

    state_path = tmp_path / "feed_state.json"
    state_path.write_text("{ not json", encoding="utf-8")

    services = _services(tmp_path)
    window = MainWindow(services, state_path=state_path)
    qtbot.addWidget(window)
    try:
        assert window.feed_page.criteria() == FeedCriteria()
    finally:
        window.shutdown_workers()
        services.persistence.close()

"""Feed-first application shell tests."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
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


def test_shell_starts_on_feed_and_exposes_only_functional_destinations(
    qtbot, tmp_path
):
    from insider_scanner.gui.main_window import MainWindow

    services = _services(tmp_path)
    window = MainWindow(services)
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
    window = MainWindow(services)
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

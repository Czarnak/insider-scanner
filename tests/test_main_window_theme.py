"""Tests for the MainWindow theme menu and status-bar indicator."""

from __future__ import annotations

import pytest

from insider_scanner.gui.theme import ThemeMode, get_theme_manager


@pytest.fixture
def main_window(qtbot):
    """Construct a MainWindow with no services and reset theme mode after."""
    from insider_scanner.gui.main_window import MainWindow

    window = MainWindow(services=None)
    qtbot.addWidget(window)
    try:
        yield window
    finally:
        # The theme manager is a process-wide singleton; restore the default
        # mode so this test cannot leak state into other tests.
        get_theme_manager().set_mode(ThemeMode.SYSTEM)


def test_view_menu_has_theme_submenu_with_three_actions(main_window):
    menu_titles = [action.text() for action in main_window.menuBar().actions()]
    assert "View" in menu_titles, "expected a 'View' menu"

    view_titles = [action.text() for action in main_window.view_menu.actions()]
    assert "Theme" in view_titles, "expected a 'Theme' submenu"

    labels = [action.text() for action in main_window.theme_menu.actions()]
    assert labels == ["System", "Light", "Dark"]
    assert all(action.isCheckable() for action in main_window.theme_menu.actions())


def test_exactly_one_theme_action_checked_in_exclusive_group(main_window):
    group = main_window._theme_action_group
    assert group.isExclusive() is True

    checked = [action for action in group.actions() if action.isChecked()]
    assert len(checked) == 1


def test_triggering_dark_updates_manager_and_indicator(main_window):
    dark_action = main_window._theme_actions[ThemeMode.DARK]

    dark_action.trigger()

    assert get_theme_manager().mode() == ThemeMode.DARK
    assert dark_action.isChecked() is True

    expected = get_theme_manager().palette().name
    assert main_window.theme_indicator.text() == f"Theme: {expected}"


def test_indicator_initial_text_reflects_palette(main_window):
    expected = get_theme_manager().palette().name
    assert main_window.theme_indicator.text() == f"Theme: {expected}"


def test_about_to_show_resyncs_checked_action(main_window):
    # Change the manager externally (not via the menu), then ensure the
    # aboutToShow re-sync re-checks the matching action.
    get_theme_manager().set_mode(ThemeMode.LIGHT)
    main_window._sync_theme_actions()

    assert main_window._theme_actions[ThemeMode.LIGHT].isChecked() is True

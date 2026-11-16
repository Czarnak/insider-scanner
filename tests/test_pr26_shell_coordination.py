"""Shell behavior stays independent of widget order and theme entry points."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QComboBox, QFrame

from insider_scanner.gui.main_window import MainWindow
from insider_scanner.gui.theme import ThemeMode, get_theme_manager


@pytest.fixture
def window(qtbot, tmp_path):
    window = MainWindow(state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(window)
    yield window
    window.shutdown_workers()


def test_navigation_selects_the_registered_widget_after_stack_reordering(window):
    window.page_stack.removeWidget(window.scan_tab)
    window.page_stack.insertWidget(0, window.scan_tab)

    window._select_page("Insider Scan")

    assert window.page_stack.currentWidget() is window.scan_tab
    assert window.navigation_buttons["Insider Scan"].isChecked()
    assert window.page_title.text() == "Insider Scan"


def test_shell_exposes_one_theme_control_in_view_menu(window):
    top_bar = window.findChild(QFrame, "topBar")
    selectors = [
        combo
        for combo in top_bar.findChildren(QComboBox)
        if combo.accessibleName() == "Application theme"
    ]

    assert selectors == []
    assert [action.text() for action in window.theme_menu.actions()] == [
        "System",
        "Light",
        "Dark",
    ]


def test_external_theme_switch_updates_checked_action_immediately(window):
    manager = get_theme_manager()
    manager.set_mode(ThemeMode.LIGHT)
    manager.set_mode(ThemeMode.DARK)

    assert window._theme_actions[ThemeMode.DARK].isChecked()
    assert not window._theme_actions[ThemeMode.LIGHT].isChecked()
    assert window.theme_indicator.text() == "Theme: dark"

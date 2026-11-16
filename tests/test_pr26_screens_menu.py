"""Saved-screen actions must remain current without retaining old submenus."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QMenu
from shiboken6 import isValid

from insider_scanner.gui.screens_menu import ScreensMenu
from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.feed_state import SavedScreen


def _screen(name: str) -> SavedScreen:
    return SavedScreen(name, FeedCriteria(search=name))


def test_empty_menu_keeps_save_action_available(qtbot):
    menu = ScreensMenu()
    qtbot.addWidget(menu)
    saved = []
    menu.saveRequested.connect(lambda: saved.append(True))

    assert [action.text() for action in menu.actions()] == [
        "Save current as screen…",
        "",
        "No saved screens",
    ]
    assert not menu.actions()[-1].isEnabled()
    menu.actions()[0].trigger()
    assert saved == [True]


def test_screen_actions_emit_the_selected_name(qtbot):
    menu = ScreensMenu()
    qtbot.addWidget(menu)
    menu.set_screens((_screen("Purchases"), _screen("Congress")))
    applied = []
    deleted = []
    menu.applyRequested.connect(applied.append)
    menu.deleteRequested.connect(deleted.append)

    first, second = [action.menu() for action in menu.actions()[2:]]
    assert first.title() == "Purchases"
    assert second.title() == "Congress"
    second.actions()[0].trigger()
    first.actions()[1].trigger()

    assert applied == ["Congress"]
    assert deleted == ["Purchases"]


def test_opening_or_reapplying_unchanged_screens_keeps_the_same_menus(qtbot):
    menu = ScreensMenu()
    qtbot.addWidget(menu)
    screens = (_screen("Purchases"), _screen("Congress"))
    menu.set_screens(screens)
    original = [action.menu() for action in menu.actions()[2:]]

    for _ in range(20):
        menu.aboutToShow.emit()
        menu.set_screens(list(screens))

    assert [action.menu() for action in menu.actions()[2:]] == original
    assert (
        len(menu.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly))
        == 2
    )


def test_replacing_screens_releases_old_menus_and_connections(qtbot, qapp):
    menu = ScreensMenu()
    qtbot.addWidget(menu)
    retired = []
    for index in range(20):
        menu.set_screens((_screen(f"Screen {index}"),))
        current = menu.actions()[-1].menu()
        if index < 19:
            retired.append(current)
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert all(not isValid(old_menu) for old_menu in retired)
    assert (
        len(menu.findChildren(QMenu, options=Qt.FindChildOption.FindDirectChildrenOnly))
        == 1
    )
    assert current.title() == "Screen 19"

    menu.set_screens(())
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(current)
    assert not menu.findChildren(QMenu)
    assert menu.actions()[-1].text() == "No saved screens"

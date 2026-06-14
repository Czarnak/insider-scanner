"""Shared pytest fixtures.

GUI tests build PySide6 widgets against a process-wide ``QApplication`` and a
singleton ``ThemeManager``.  Without isolation, windows from earlier tests linger
pending-deletion and the theme manager accumulates signal connections; a later
``ThemeManager.apply()`` (or pytest-qt's teardown event processing) then touches
those dangling C++ objects and crashes the interpreter natively.  The autouse
fixture below drains deferred deletions and resets the singleton after every
test, and no-ops when no ``QApplication`` exists (so non-GUI tests are
unaffected and PySide6 is only imported when an app is already live).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _gui_isolation():
    yield

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return

    from PySide6.QtCore import QEvent

    from insider_scanner.gui.theme import reset_theme_manager

    for widget in list(app.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    reset_theme_manager()

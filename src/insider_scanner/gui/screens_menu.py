"""Saved feed screen actions with a single owner for their Qt menus."""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QWidget

from insider_scanner.persistence.feed_state import SavedScreen


class ScreensMenu(QMenu):
    """Present saved screens and leave state changes to the shell controller."""

    saveRequested = Signal()
    applyRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._screens: tuple[SavedScreen, ...] | None = None
        self.set_screens(())

    def set_screens(self, screens: Iterable[SavedScreen]) -> None:
        """Refresh actions only when the supplied screen snapshot changes."""
        snapshot = tuple(screens)
        if snapshot == self._screens:
            return
        self._screens = snapshot
        # clear() removes menu actions, but leaves their child QMenu owners alive.
        for action in self.actions():
            submenu = action.menu()
            if submenu is not None:
                submenu.deleteLater()
        self.clear()
        save_action = self.addAction("Save current as screen…")
        save_action.triggered.connect(self.saveRequested.emit)
        self.addSeparator()
        if not snapshot:
            placeholder = self.addAction("No saved screens")
            placeholder.setEnabled(False)
            return
        for screen in snapshot:
            submenu = QMenu(screen.name, self)
            apply_action = submenu.addAction("Apply")
            apply_action.triggered.connect(
                partial(self.applyRequested.emit, screen.name)
            )
            delete_action = submenu.addAction("Delete")
            delete_action.triggered.connect(
                partial(self.deleteRequested.emit, screen.name)
            )
            self.addMenu(submenu)

"""Main window with default OS style and tabbed interface."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
)

from insider_scanner.gui.theme import ThemeMode, get_theme_manager
from insider_scanner.gui.theme.tokens import ThemePalette
from insider_scanner.utils.logging import get_logger

log = get_logger("gui.main_window")


class MainWindowInitializationError(RuntimeError):
    """Raised when the main window cannot be initialized safely."""


class MainWindow(QMainWindow):
    """Insider Scanner main window."""

    def __init__(self, services=None, *, shutdown_timeout_ms: int = 5_000):
        super().__init__()
        if shutdown_timeout_ms < 0:
            raise ValueError("shutdown_timeout_ms must not be negative")
        self._services = services
        self._thread_pool = QThreadPool(self)
        self._shutdown_timeout_ms = shutdown_timeout_ms
        self.setWindowTitle("Insider Scanner")
        self.setMinimumSize(900, 550)
        self.resize(1100, 650)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._initialize_tabs()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        self._init_theme_menu()
        self._init_theme_indicator()

    def _initialize_tabs(self) -> None:
        initializers = (
            ("Scan", self._init_scan_tab),
            ("Congress", self._init_congress_tab),
            ("European", self._init_european_tab),
            ("Analysis", self._init_analysis_tab),
        )
        for tab_name, initialize in initializers:
            try:
                initialize()
            except Exception as exc:
                log.exception("%s tab initialization failed", tab_name)
                raise MainWindowInitializationError(
                    "Could not initialize application window."
                ) from exc

    def _init_theme_menu(self) -> None:
        """Build the View > Theme submenu wired to the theme manager."""
        self.view_menu = self.menuBar().addMenu("View")
        self.theme_menu = self.view_menu.addMenu("Theme")

        self._theme_action_group = QActionGroup(self)
        self._theme_action_group.setExclusive(True)

        self._theme_actions: dict[ThemeMode, QAction] = {}
        entries = (
            ("System", ThemeMode.SYSTEM),
            ("Light", ThemeMode.LIGHT),
            ("Dark", ThemeMode.DARK),
        )
        for label, mode in entries:
            action = QAction(label, self)
            action.setCheckable(True)
            self._theme_action_group.addAction(action)
            self.theme_menu.addAction(action)
            self._theme_actions[mode] = action
            action.triggered.connect(
                lambda _checked=False, m=mode: get_theme_manager().set_mode(m)
            )

        self._sync_theme_actions()
        self.theme_menu.aboutToShow.connect(self._sync_theme_actions)

    def _sync_theme_actions(self) -> None:
        """Re-check the action matching the manager's current mode."""
        current = get_theme_manager().mode()
        action = self._theme_actions.get(current)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _init_theme_indicator(self) -> None:
        """Add a right-aligned permanent label showing the active theme."""
        manager = get_theme_manager()
        self.theme_indicator = QLabel(self._theme_indicator_text(manager.palette()))
        self.status_bar.addPermanentWidget(self.theme_indicator)
        manager.paletteChanged.connect(self._on_palette_changed)

    @staticmethod
    def _theme_indicator_text(palette: ThemePalette) -> str:
        return f"Theme: {palette.name}"

    def _on_palette_changed(self, palette: ThemePalette) -> None:
        """Slot: update the status-bar indicator when the palette changes."""
        self.theme_indicator.setText(self._theme_indicator_text(palette))

    def _init_scan_tab(self):
        from insider_scanner.gui.scan_tab import ScanTab

        service = None if self._services is None else self._services.us
        self.scan_tab = ScanTab(service, thread_pool=self._thread_pool)
        self.tabs.addTab(self.scan_tab, "Insider Scan")

    def _init_congress_tab(self):
        from insider_scanner.gui.congress_tab import CongressTab

        service = None if self._services is None else self._services.congress
        self.congress_tab = CongressTab(service, thread_pool=self._thread_pool)
        self.tabs.addTab(self.congress_tab, "Congress Scan")

    def _init_european_tab(self):
        from insider_scanner.gui.european_tab import EuropeanTab

        service = None if self._services is None else self._services.european
        self.european_tab = EuropeanTab(service, thread_pool=self._thread_pool)
        self.tabs.addTab(self.european_tab, "European Insiders")

    def _init_analysis_tab(self):
        try:
            from insider_scanner.gui.analysis_tab import AnalysisTab

            self.analysis_tab = AnalysisTab(thread_pool=self._thread_pool)
            self.tabs.addTab(self.analysis_tab, "Analysis")
        except Exception as exc:
            from PySide6.QtWidgets import QLabel

            self.tabs.addTab(
                QLabel(f"Analysis tab failed to load: {exc}"),
                "Analysis",
            )

    def request_cancellation(self) -> None:
        """Request cancellation from every tab before worker shutdown."""
        for name in ("scan_tab", "congress_tab", "european_tab", "analysis_tab"):
            tab = getattr(self, name, None)
            if tab is not None and hasattr(tab, "request_cancellation"):
                tab.request_cancellation()

    def shutdown_workers(self) -> bool:
        """Cancel and wait a bounded time for all window-owned workers."""
        self.request_cancellation()
        if self._thread_pool.waitForDone(self._shutdown_timeout_ms):
            return True
        log.warning(
            "Timed out waiting for GUI workers to stop after %d ms",
            self._shutdown_timeout_ms,
        )
        return False

    def closeEvent(self, event) -> None:
        """Keep the window open while a worker could still access persistence."""
        if self.shutdown_workers():
            event.accept()
        else:
            event.ignore()

    def log_status(self, message: str):
        self.status_bar.showMessage(message)

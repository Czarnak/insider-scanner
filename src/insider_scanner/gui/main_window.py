"""Feed-first application shell with navigation and legacy tool pages."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover

    def _is_valid(_obj: object) -> bool:
        return True


from insider_scanner.gui.feed_page import FeedPageWidget
from insider_scanner.gui.theme import ThemeMode, get_theme_manager
from insider_scanner.gui.theme.tokens import ThemePalette
from insider_scanner.persistence.feed import FeedRepository
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
        self.resize(1200, 720)

        self._build_shell()
        self._initialize_pages()
        self._init_status_bar()
        self._init_theme_menu()
        self._init_theme_indicator()
        self._select_page("Feed")

    def _build_shell(self) -> None:
        shell = QWidget()
        shell.setObjectName("applicationShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(184)
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(12, 16, 12, 12)
        self.sidebar_layout.setSpacing(6)

        brand = QLabel("INSIDER\nSCANNER")
        brand.setObjectName("sidebarBrand")
        self.sidebar_layout.addWidget(brand)
        self.sidebar_layout.addSpacing(16)

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        self.navigation_buttons: dict[str, QToolButton] = {}

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._build_top_bar())

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("workspace")
        workspace_layout.addWidget(self.page_stack, stretch=1)

        shell_layout.addWidget(self.sidebar)
        shell_layout.addWidget(workspace, stretch=1)
        self.setCentralWidget(shell)

    def _build_top_bar(self) -> QFrame:
        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        layout = QHBoxLayout(top_bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self.page_title = QLabel("Feed")
        self.page_title.setObjectName("pageTitle")
        layout.addWidget(self.page_title)

        self.global_search = QLineEdit()
        self.global_search.setAccessibleName("Search unified feed")
        self.global_search.setPlaceholderText(
            "Search ticker, company, insider, source..."
        )
        self.global_search.setMaxLength(100)
        self.global_search.setClearButtonEnabled(True)
        self.global_search.setMinimumWidth(280)
        layout.addWidget(self.global_search, stretch=1)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.global_search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self._apply_global_search)

        self.result_count_label = QLabel("0 transactions")
        self.result_count_label.setObjectName("resultCount")
        layout.addWidget(self.result_count_label)

        self.freshness_label = QLabel("Local data")
        self.freshness_label.setObjectName("freshnessLabel")
        layout.addWidget(self.freshness_label)

        self.reload_button = QPushButton("Reload")
        self.reload_button.setAccessibleName("Reload local transactions")
        self.reload_button.clicked.connect(self._reload_feed)
        layout.addWidget(self.reload_button)

        self.theme_selector = QComboBox()
        self.theme_selector.setAccessibleName("Application theme")
        self.theme_selector.addItems(["System", "Light", "Dark"])
        self.theme_selector.currentIndexChanged.connect(self._on_theme_selected)
        layout.addWidget(self.theme_selector)
        return top_bar

    def _initialize_pages(self) -> None:
        try:
            persistence = (
                None
                if self._services is None
                else getattr(self._services, "persistence", None)
            )
            engine = None if persistence is None else persistence.engine
            repository = FeedRepository(engine) if engine is not None else None
            self.feed_page = FeedPageWidget(
                repository,
                thread_pool=self._thread_pool,
            )
            self.feed_page.resultCountChanged.connect(self._set_result_count)
            self.feed_page.freshnessChanged.connect(self.freshness_label.setText)
            self._add_page("Feed", self.feed_page)
        except Exception as exc:
            log.exception("Feed page initialization failed")
            raise MainWindowInitializationError(
                "Could not initialize application window."
            ) from exc

        self._add_section_label("Tools")
        self._initialize_tool_page("Scan", self._init_scan_page)
        self._initialize_tool_page("Congress", self._init_congress_page)
        self._initialize_tool_page("European", self._init_european_page)
        self._init_analysis_page()
        self.sidebar_layout.addStretch(1)

    @staticmethod
    def _tool_initialization_error() -> MainWindowInitializationError:
        return MainWindowInitializationError(
            "Could not initialize application window."
        )

    def _initialize_tool_page(self, name: str, initializer) -> None:
        try:
            initializer()
        except Exception as exc:
            log.exception("%s tab initialization failed", name)
            raise self._tool_initialization_error() from exc

    def _add_section_label(self, text: str) -> None:
        label = QLabel(text.upper())
        label.setObjectName("navigationSection")
        self.sidebar_layout.addSpacing(12)
        self.sidebar_layout.addWidget(label)

    def _add_page(self, name: str, widget: QWidget) -> None:
        button = QToolButton()
        button.setText(name)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setAccessibleName(f"Open {name}")
        button.setObjectName("navigationButton")
        button.clicked.connect(partial(self._select_page, name))
        self.navigation_group.addButton(button)
        self.navigation_buttons[name] = button
        self.sidebar_layout.addWidget(button)
        self.page_stack.addWidget(widget)

    def _init_scan_page(self) -> None:
        from insider_scanner.gui.scan_tab import ScanTab

        service = None if self._services is None else self._services.us
        self.scan_tab = ScanTab(service, thread_pool=self._thread_pool)
        self._add_page("Insider Scan", self.scan_tab)

    def _init_congress_page(self) -> None:
        from insider_scanner.gui.congress_tab import CongressTab

        service = None if self._services is None else self._services.congress
        self.congress_tab = CongressTab(service, thread_pool=self._thread_pool)
        self._add_page("Congress Scan", self.congress_tab)

    def _init_european_page(self) -> None:
        from insider_scanner.gui.european_tab import EuropeanTab

        service = None if self._services is None else self._services.european
        self.european_tab = EuropeanTab(service, thread_pool=self._thread_pool)
        self._add_page("European Scan", self.european_tab)

    def _init_analysis_page(self) -> None:
        try:
            from insider_scanner.gui.analysis_tab import AnalysisTab

            self.analysis_tab = AnalysisTab(thread_pool=self._thread_pool)
        except Exception as exc:
            log.exception("Analysis page initialization failed")
            self.analysis_tab = QLabel(f"Analysis failed to load: {exc}")
        self._add_page("Analysis", self.analysis_tab)

    def _select_page(self, name: str) -> None:
        button = self.navigation_buttons[name]
        index = list(self.navigation_buttons).index(name)
        button.setChecked(True)
        self.page_stack.setCurrentIndex(index)
        self.page_title.setText(name)
        is_feed = name == "Feed"
        self.global_search.setVisible(is_feed)
        self.result_count_label.setVisible(is_feed)
        self.freshness_label.setVisible(is_feed)
        self.reload_button.setVisible(is_feed)

    def _apply_global_search(self) -> None:
        self.feed_page.set_search(self.global_search.text())

    def _reload_feed(self) -> None:
        self.feed_page.reload()

    def _set_result_count(self, count: int) -> None:
        noun = "transaction" if count == 1 else "transactions"
        self.result_count_label.setText(f"{count:,} {noun}")

    def log_status(self, message: str, timeout_ms: int = 0) -> None:
        """Show a concise user-facing status message."""
        self.status_bar.showMessage(str(message), timeout_ms)

    def _init_status_bar(self) -> None:
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _init_theme_menu(self) -> None:
        self.view_menu = self.menuBar().addMenu("View")
        self.theme_menu = self.view_menu.addMenu("Theme")
        self._theme_action_group = QActionGroup(self)
        self._theme_action_group.setExclusive(True)
        self._theme_actions: dict[ThemeMode, QAction] = {}
        for label, mode in (
            ("System", ThemeMode.SYSTEM),
            ("Light", ThemeMode.LIGHT),
            ("Dark", ThemeMode.DARK),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(partial(self._set_theme_mode, mode))
            self._theme_action_group.addAction(action)
            self.theme_menu.addAction(action)
            self._theme_actions[mode] = action
        self.theme_menu.aboutToShow.connect(self._sync_theme_actions)
        self._sync_theme_actions()

    def _set_theme_mode(self, mode: ThemeMode) -> None:
        get_theme_manager().set_mode(mode)
        self._sync_theme_actions()
        self._sync_theme_selector()

    def _on_theme_selected(self, index: int) -> None:
        modes = (ThemeMode.SYSTEM, ThemeMode.LIGHT, ThemeMode.DARK)
        if 0 <= index < len(modes) and get_theme_manager().mode() is not modes[index]:
            self._set_theme_mode(modes[index])

    def _sync_theme_actions(self) -> None:
        action = self._theme_actions.get(get_theme_manager().mode())
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _sync_theme_selector(self) -> None:
        modes = (ThemeMode.SYSTEM, ThemeMode.LIGHT, ThemeMode.DARK)
        index = modes.index(get_theme_manager().mode())
        if self.theme_selector.currentIndex() != index:
            self.theme_selector.blockSignals(True)
            self.theme_selector.setCurrentIndex(index)
            self.theme_selector.blockSignals(False)

    def _init_theme_indicator(self) -> None:
        manager = get_theme_manager()
        self._theme_manager = manager
        self.theme_indicator = QLabel(self._theme_indicator_text(manager.palette()))
        self.status_bar.addPermanentWidget(self.theme_indicator)
        self._theme_slot = self._on_palette_changed
        self._theme_connection = manager.paletteChanged.connect(self._theme_slot)
        self._sync_theme_selector()

    @staticmethod
    def _theme_indicator_text(palette: ThemePalette) -> str:
        return f"Theme: {palette.name}"

    def _on_palette_changed(self, palette: ThemePalette) -> None:
        if _is_valid(self.theme_indicator):
            self.theme_indicator.setText(self._theme_indicator_text(palette))
        self._sync_theme_selector()

    def request_cancellation(self) -> None:
        for name in (
            "feed_page",
            "scan_tab",
            "congress_tab",
            "european_tab",
            "analysis_tab",
        ):
            page = getattr(self, name, None)
            if page is not None and hasattr(page, "request_cancellation"):
                page.request_cancellation()

    def shutdown_workers(self) -> bool:
        self.request_cancellation()
        if self._thread_pool.waitForDone(self._shutdown_timeout_ms):
            return True
        log.warning(
            "Timed out waiting for GUI workers to stop after %d ms",
            self._shutdown_timeout_ms,
        )
        return False

    def closeEvent(self, event) -> None:
        if self.shutdown_workers():
            self._disconnect_theme()
            event.accept()
        else:
            event.ignore()

    def _disconnect_theme(self) -> None:
        connection = getattr(self, "_theme_connection", None)
        if connection is None or not connection:
            return
        QObject.disconnect(connection)
        self._theme_connection = None

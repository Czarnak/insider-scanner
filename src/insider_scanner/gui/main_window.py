"""Feed-first application shell with navigation and legacy tool pages."""

from __future__ import annotations

import dataclasses
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
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


from insider_scanner.gui.alerts_page import AlertsPage
from insider_scanner.gui.entity_pages import CompanyPage, InsiderPage
from insider_scanner.gui.feed_page import FeedPageWidget
from insider_scanner.gui.global_search import GlobalSearchController
from insider_scanner.gui.theme import ThemeMode, get_theme_manager
from insider_scanner.gui.theme.tokens import ThemePalette
from insider_scanner.gui.watchlist_page import WatchlistPage
from insider_scanner.persistence.alerts import (
    alerts_path,
    load_alerts,
    save_alerts,
)
from insider_scanner.persistence.errors import is_access_denied
from insider_scanner.persistence.feed import FeedCriteria, FeedRepository
from insider_scanner.persistence.feed_state import (
    SCHEMA_VERSION,
    FeedState,
    SavedScreen,
    feed_state_path,
    load_feed_state,
    save_feed_state,
)
from insider_scanner.persistence.watchlists import (
    WatchEntry,
    load_watchlists,
    save_watchlists,
    watchlists_path,
)
from insider_scanner.services.alerts import evaluate_alerts
from insider_scanner.utils.config import DEFAULT_PATHS
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("gui.main_window")


class MainWindowInitializationError(RuntimeError):
    """Raised when the main window cannot be initialized safely."""


class MainWindow(QMainWindow):
    """Insider Scanner main window."""

    def __init__(
        self,
        services=None,
        *,
        shutdown_timeout_ms: int = 5_000,
        state_path: Path | None = None,
    ):
        super().__init__()
        if shutdown_timeout_ms < 0:
            raise ValueError("shutdown_timeout_ms must not be negative")
        self._services = services
        self._thread_pool = QThreadPool(self)
        self._shutdown_timeout_ms = shutdown_timeout_ms
        self._state_path: Path = state_path or feed_state_path(DEFAULT_PATHS.data_dir)
        self._feed_state: FeedState = load_feed_state(self._state_path)
        # Watchlists and alerts live alongside the feed-state file.
        self._data_dir: Path = self._state_path.parent
        self._watchlists_path: Path = watchlists_path(self._data_dir)
        self._alerts_path: Path = alerts_path(self._data_dir)
        self._watchlists = load_watchlists(self._watchlists_path)
        self._alerts = load_alerts(self._alerts_path)
        self._alert_request_id = 0
        self.setWindowTitle("Insider Scanner")
        self.setMinimumSize(900, 550)
        self.resize(1200, 720)

        self._build_shell()
        self._initialize_pages()
        self._init_status_bar()
        self._init_theme_menu()
        self._init_theme_indicator()
        self._select_page("Feed")

        self.persist_timer = QTimer(self)
        self.persist_timer.setSingleShot(True)
        self.persist_timer.setInterval(500)
        self.persist_timer.timeout.connect(self._persist_feed_state)

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

        self.filters_button = QPushButton("Filters")
        self.filters_button.setAccessibleName("Toggle filter panel")
        self.filters_button.clicked.connect(self._toggle_filter_panel)
        layout.addWidget(self.filters_button)

        self.screens_button = QToolButton()
        self.screens_button.setText("Screens")
        self.screens_button.setAccessibleName("Saved screens")
        self.screens_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.screens_menu = QMenu(self)
        self.screens_button.setMenu(self.screens_menu)
        self.screens_menu.aboutToShow.connect(self._rebuild_screens_menu)
        layout.addWidget(self.screens_button)

        # Notification indicator — visible on every page, shows unseen alert count.
        self.alerts_button = QToolButton()
        self.alerts_button.setText("Alerts")
        self.alerts_button.setObjectName("alertsButton")
        self.alerts_button.setAccessibleName("Alerts and notifications")
        self.alerts_button.clicked.connect(partial(self._select_page, "Alerts"))
        layout.addWidget(self.alerts_button)

        self.theme_selector = QComboBox()
        self.theme_selector.setAccessibleName("Application theme")
        self.theme_selector.addItems(["System", "Light", "Dark"])
        self.theme_selector.currentIndexChanged.connect(self._on_theme_selected)
        layout.addWidget(self.theme_selector)

        self._rebuild_screens_menu()
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
            self._repository = repository
            self.feed_page = FeedPageWidget(
                repository,
                thread_pool=self._thread_pool,
                initial_criteria=self._feed_state.criteria,
            )
            self.feed_page.resultCountChanged.connect(self._set_result_count)
            self.feed_page.freshnessChanged.connect(self.freshness_label.setText)
            self.feed_page.restore_column_layout(self._feed_state.column_layout)
            self.feed_page.criteriaChanged.connect(self._on_criteria_changed)
            self.feed_page.resetRequested.connect(self._on_feed_reset)
            self.feed_page.openCompanyRequested.connect(self._open_company)
            self.feed_page.openInsiderRequested.connect(self._open_insider)
            self.global_search.blockSignals(True)
            self.global_search.setText(self._feed_state.criteria.search)
            self.global_search.blockSignals(False)
            self._add_page("Feed", self.feed_page)
        except Exception as exc:
            log.exception("Feed page initialization failed")
            raise MainWindowInitializationError(
                "Could not initialize application window."
            ) from exc

        self._init_monitor_pages()
        self._init_research_pages()

        self._add_section_label("Tools")
        self._initialize_tool_page("Scan", self._init_scan_page)
        self._initialize_tool_page("Congress", self._init_congress_page)
        self._initialize_tool_page("European", self._init_european_page)
        self._init_analysis_page()
        self.sidebar_layout.addStretch(1)

        self._init_global_search()
        self._check_alerts()

    @staticmethod
    def _tool_initialization_error() -> MainWindowInitializationError:
        return MainWindowInitializationError("Could not initialize application window.")

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

    def _init_research_pages(self) -> None:
        try:
            self._add_section_label("Research")
            self.company_page = CompanyPage(
                self._repository, thread_pool=self._thread_pool
            )
            self.insider_page = InsiderPage(
                self._repository, thread_pool=self._thread_pool
            )
            self._add_page("Companies", self.company_page)
            self._add_page("Insiders", self.insider_page)
        except Exception as exc:
            log.exception("Research pages initialization failed")
            raise self._tool_initialization_error() from exc

    def _init_monitor_pages(self) -> None:
        try:
            self._add_section_label("Monitor")
            self.watchlist_page = WatchlistPage(self._watchlists)
            self.watchlist_page.watchlistsChanged.connect(self._on_watchlists_changed)
            self.watchlist_page.openCompanyRequested.connect(self._open_company)
            self.watchlist_page.openInsiderRequested.connect(self._open_insider)
            self.watchlist_page.feedSearchRequested.connect(self._on_global_feed_search)
            self._add_page("Watchlists", self.watchlist_page)

            self.alerts_page = AlertsPage(
                self._alerts, criteria_provider=self.feed_page.criteria
            )
            self.alerts_page.rulesChanged.connect(self._on_alerts_changed)
            self.alerts_page.checkRequested.connect(self._check_alerts)
            self.alerts_page.openCompanyRequested.connect(self._open_company)
            self.alerts_page.openInsiderRequested.connect(self._open_insider)
            self._add_page("Alerts", self.alerts_page)

            self.feed_page.watchRequested.connect(self._on_watch_requested)
        except Exception as exc:
            log.exception("Monitor pages initialization failed")
            raise self._tool_initialization_error() from exc

    # ------------------------------------------------------------------
    # Watchlists & alerts wiring
    # ------------------------------------------------------------------

    def _on_watch_requested(self, record) -> None:
        """Add the record's company and insider to the active watchlist."""
        added = False
        if record.identifier:
            self.watchlist_page.add_entry(
                WatchEntry(
                    kind="company",
                    market=record.market,
                    identifier=record.identifier,
                    label=record.issuer or record.identifier,
                )
            )
            added = True
        if record.person:
            self.watchlist_page.add_entry(
                WatchEntry(
                    kind="insider",
                    market=record.market,
                    person=record.person,
                    label=record.person,
                )
            )
            added = True
        if added:
            self.log_status("Added to watchlist", timeout_ms=3_000)

    def _on_watchlists_changed(self, store) -> None:
        self._watchlists = store
        self._persist_watchlists()

    def _on_alerts_changed(self, store) -> None:
        self._alerts = store
        self._persist_alerts()

    def _persist_watchlists(self) -> None:
        try:
            save_watchlists(self._watchlists_path, self._watchlists)
        except Exception as exc:
            log.exception("Failed to persist watchlists")
            self._handle_save_failure("watchlists", exc)

    def _persist_alerts(self) -> None:
        try:
            save_alerts(self._alerts_path, self._alerts)
        except Exception as exc:
            log.exception("Failed to persist alerts")
            self._handle_save_failure("alerts", exc)

    def _handle_save_failure(self, what: str, exc: BaseException) -> None:
        """Surface a save failure without leaking technical detail.

        Access/permission failures get an actionable hint; everything else gets
        a generic message. The full exception is already in the logs.
        """
        if is_access_denied(exc):
            self.log_status(
                f"Couldn't save {what} — the data folder isn't writable.",
                timeout_ms=6_000,
            )
        else:
            self.log_status(f"Couldn't save {what}.", timeout_ms=6_000)

    def _check_alerts(self) -> None:
        """Evaluate enabled alert rules against the local feed off the UI thread."""
        if self._repository is None:
            return
        store = self.alerts_page.store()
        if not any(rule.enabled for rule in store.rules):
            self.alerts_page.display_hits(())
            self._update_alert_badge(0)
            return
        self._alert_request_id += 1
        request_id = self._alert_request_id
        watchlists = self.watchlist_page.store()
        worker = Worker(evaluate_alerts, self._repository, store, watchlists)
        worker.signals.result.connect(
            lambda hits: self._on_alerts_evaluated(request_id, hits)
        )
        worker.signals.error.connect(
            lambda error_info: self._on_alerts_error(request_id, error_info)
        )
        self._thread_pool.start(worker)

    def _on_alerts_evaluated(self, request_id: int, hits) -> None:
        if request_id != self._alert_request_id:
            return
        self.alerts_page.display_hits(hits)
        self._update_alert_badge(self.alerts_page.new_match_count())

    def _on_alerts_error(self, request_id: int, error_info: tuple) -> None:
        if request_id != self._alert_request_id:
            return
        log.error("Alert evaluation failed: %s", error_info[1])

    def _update_alert_badge(self, count: int) -> None:
        self.alerts_button.setText("Alerts" if count <= 0 else f"Alerts ({count})")

    def _init_global_search(self) -> None:
        self.global_search_controller = GlobalSearchController(
            self.global_search,
            self._repository,
            parent=self,
            thread_pool=self._thread_pool,
        )
        self.global_search_controller.companyChosen.connect(self._open_company)
        self.global_search_controller.insiderChosen.connect(self._open_insider)
        self.global_search_controller.feedSearchRequested.connect(
            self._on_global_feed_search
        )

    def _open_company(self, identifier, market) -> None:
        self.company_page.load(identifier, market)
        self._select_page("Companies")

    def _open_insider(self, person, market) -> None:
        self.insider_page.load(person, market)
        self._select_page("Insiders")

    def _on_global_feed_search(self, text) -> None:
        self.feed_page.set_search(text)
        self._select_page("Feed")

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
        self.global_search.setVisible(True)
        self.result_count_label.setVisible(is_feed)
        self.freshness_label.setVisible(is_feed)
        self.reload_button.setVisible(is_feed)
        self.filters_button.setVisible(is_feed)
        self.screens_button.setVisible(is_feed)

    def _toggle_filter_panel(self) -> None:
        self.feed_page.toggle_filter_panel()

    def _apply_global_search(self) -> None:
        self.feed_page.set_search(self.global_search.text())

    def _reload_feed(self) -> None:
        self.feed_page.reload()
        self._check_alerts()

    def _set_result_count(self, count: int) -> None:
        noun = "transaction" if count == 1 else "transactions"
        self.result_count_label.setText(f"{count:,} {noun}")

    # ------------------------------------------------------------------
    # Screens menu
    # ------------------------------------------------------------------

    def _rebuild_screens_menu(self) -> None:
        self.screens_menu.clear()
        save_action = self.screens_menu.addAction("Save current as screen…")
        save_action.triggered.connect(self._save_current_screen)
        self.screens_menu.addSeparator()
        screens = self._feed_state.screens
        if not screens:
            no_screens = self.screens_menu.addAction("No saved screens")
            no_screens.setEnabled(False)
            return
        for screen in screens:
            submenu = QMenu(screen.name, self.screens_menu)
            apply_action = submenu.addAction("Apply")
            apply_action.triggered.connect(partial(self._apply_screen, screen.name))
            delete_action = submenu.addAction("Delete")
            delete_action.triggered.connect(partial(self._delete_screen, screen.name))
            self.screens_menu.addMenu(submenu)

    def _save_current_screen(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Screen", "Screen name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        new_screen = SavedScreen(name, self.feed_page.criteria())
        existing = list(self._feed_state.screens)
        replaced = [s for s in existing if s.name != name]
        replaced.append(new_screen)
        capped = tuple(replaced[-200:])
        self._feed_state = dataclasses.replace(self._feed_state, screens=capped)
        self._persist_feed_state()
        self.log_status(f"Saved screen '{name}'")

    def _apply_screen(self, name: str) -> None:
        screen = next((s for s in self._feed_state.screens if s.name == name), None)
        if screen is None:
            return
        if self._confirm_discard(f"Apply screen '{name}'"):
            self.feed_page.set_criteria(screen.criteria)

    def _delete_screen(self, name: str) -> None:
        answer = QMessageBox.question(self, "Delete screen", f"Delete screen '{name}'?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        remaining = tuple(s for s in self._feed_state.screens if s.name != name)
        self._feed_state = dataclasses.replace(self._feed_state, screens=remaining)
        self._persist_feed_state()
        self._rebuild_screens_menu()
        self.log_status(f"Deleted screen '{name}'")

    def _confirm_discard(self, action_label: str) -> bool:
        if not self.feed_page.criteria().is_nontrivial():
            return True
        answer = QMessageBox.question(
            self,
            "Discard filters?",
            f"You have active filters. {action_label} and discard them?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_feed_reset(self) -> None:
        if self._confirm_discard("Reset"):
            current = self.feed_page.criteria()
            self.feed_page.set_criteria(
                FeedCriteria(
                    sort_field=current.sort_field,
                    descending=current.descending,
                )
            )

    # ------------------------------------------------------------------
    # Persistence wiring
    # ------------------------------------------------------------------

    def _on_criteria_changed(self, criteria: FeedCriteria) -> None:
        self.global_search.blockSignals(True)
        self.global_search.setText(criteria.search)
        self.global_search.blockSignals(False)
        self.persist_timer.start()

    def _persist_feed_state(self) -> None:
        try:
            state = FeedState(
                version=SCHEMA_VERSION,
                criteria=self.feed_page.criteria(),
                column_layout=self.feed_page.column_layout(),
                screens=self._feed_state.screens,
            )
            self._feed_state = state
            save_feed_state(self._state_path, state)
        except Exception as exc:
            log.exception("Failed to persist feed state")
            self._handle_save_failure("filters", exc)

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
        self._alert_request_id += 1
        for name in (
            "feed_page",
            "company_page",
            "insider_page",
            "global_search_controller",
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
            try:
                self.persist_timer.stop()
                self._persist_feed_state()
                self._persist_watchlists()
                self._persist_alerts()
            except Exception:
                log.exception("Failed to persist GUI state on close")
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

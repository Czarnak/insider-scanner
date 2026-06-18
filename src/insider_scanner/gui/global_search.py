"""Global entity-search popup and controller for the insider-scanner top bar."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.persistence.feed import EntityHit, EntitySearchResults
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("gui.global_search")

# Sentinel stored in UserRole to identify the "Filter feed" row.
_FEED_SENTINEL_KIND = "feed"


class GlobalSearchPopup(QWidget):
    """Frameless popup listing entity search results and a feed-search action."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setAccessibleName("Search results")
        layout.addWidget(self.list_widget)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, text: str, results: EntitySearchResults) -> None:
        """Rebuild popup contents from *text* and *results*."""
        self.list_widget.clear()

        if not text:
            return

        # Row 0: always-present feed-search action.
        feed_item = QListWidgetItem(f'Filter feed for "{text}"')
        feed_item.setData(Qt.ItemDataRole.UserRole, (_FEED_SENTINEL_KIND, text))
        self.list_widget.addItem(feed_item)

        if results.companies:
            self._add_header("Companies")
            for hit in results.companies:
                item = QListWidgetItem(hit.label)
                item.setData(Qt.ItemDataRole.UserRole, hit)
                self.list_widget.addItem(item)

        if results.insiders:
            self._add_header("Insiders")
            for hit in results.insiders:
                item = QListWidgetItem(hit.label)
                item.setData(Qt.ItemDataRole.UserRole, hit)
                self.list_widget.addItem(item)

    def show_below(self, anchor: QWidget) -> None:
        """Position the popup directly below *anchor* and show it."""
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        self.move(pos)
        self.resize(anchor.width(), self.sizeHint().height())
        self.show()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _add_header(self, label: str) -> None:
        """Add a non-interactive section header item."""
        item = QListWidgetItem(label)
        disabled_flags = (
            item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled
        )
        item.setFlags(disabled_flags)
        self.list_widget.addItem(item)


class GlobalSearchController(QObject):
    """Wire entity-navigation behaviour onto an existing QLineEdit."""

    companyChosen = Signal(object, object)  # (identifier: str, market: FeedMarket)
    insiderChosen = Signal(object, object)  # (person: str, market: FeedMarket)
    feedSearchRequested = Signal(object)  # (text: str)

    def __init__(
        self,
        line_edit: QLineEdit,
        repository: object,
        parent: QObject | None = None,
        *,
        thread_pool: QThreadPool | None = None,
        debounce_ms: int = 200,
    ) -> None:
        super().__init__(parent)
        self._line_edit = line_edit
        self._repository = repository
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._request_id = 0

        # Parent the popup to the line edit so its lifetime is tied to the
        # window — avoids a dangling top-level popup after teardown.
        self.popup = GlobalSearchPopup(line_edit)

        # Debounce timer — single-shot, restarted on every textChanged.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._on_debounce_timeout)

        line_edit.textChanged.connect(self._on_text_changed)
        line_edit.returnPressed.connect(self._on_return_pressed)

        self.popup.list_widget.itemActivated.connect(self._activate_item)
        self.popup.list_widget.itemClicked.connect(self._activate_item)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def search_now(self, text: str) -> None:
        """Execute an entity search for *text* immediately (bypasses debounce)."""
        stripped = text.strip()
        if not stripped:
            self.popup.hide()
            return
        if self._repository is None:
            self.popup.hide()
            return

        self._request_id += 1
        request_id = self._request_id

        worker = Worker(self._repository.search_entities, stripped)
        worker.signals.result.connect(
            lambda results: self._on_search_result(request_id, stripped, results)
        )
        worker.signals.error.connect(
            lambda error_info: self._on_search_error(request_id, error_info)
        )
        self._thread_pool.start(worker)

    def request_cancellation(self) -> None:
        """Invalidate any in-flight search and hide the popup on teardown."""
        self._request_id += 1
        self.popup.hide()

    def _activate_item(self, item: QListWidgetItem) -> None:
        """Emit the appropriate navigation signal for the chosen list item."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return

        if (
            isinstance(data, tuple)
            and len(data) == 2
            and data[0] == _FEED_SENTINEL_KIND
        ):
            _, search_text = data
            self.popup.hide()
            self.feedSearchRequested.emit(search_text)
            return

        if isinstance(data, EntityHit):
            self.popup.hide()
            if data.kind == "company":
                self.companyChosen.emit(data.identifier, data.market)
            elif data.kind == "insider":
                self.insiderChosen.emit(data.person, data.market)

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        self._debounce.start()

    def _on_debounce_timeout(self) -> None:
        self.search_now(self._line_edit.text())

    def _on_return_pressed(self) -> None:
        current = self.popup.list_widget.currentItem()
        if current is not None and bool(current.flags() & Qt.ItemFlag.ItemIsSelectable):
            self._activate_item(current)
            return

        text = self._line_edit.text().strip()
        if text:
            self.popup.hide()
            self.feedSearchRequested.emit(text)

    def _on_search_result(
        self,
        request_id: int,
        text: str,
        results: EntitySearchResults,
    ) -> None:
        if request_id != self._request_id:
            return
        self.popup.populate(text, results)
        self.popup.show_below(self._line_edit)

    def _on_search_error(self, request_id: int, error_info: tuple) -> None:
        if request_id != self._request_id:
            return
        log.error("Entity search failed: %s", error_info[1])
        self.popup.hide()

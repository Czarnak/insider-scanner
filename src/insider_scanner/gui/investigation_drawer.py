"""Investigation drawer widget — contextual detail panel for a FeedRecord."""

from __future__ import annotations

import webbrowser
from functools import partial

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.gui.theme import get_theme_manager
from insider_scanner.persistence.feed import (
    FeedMarket,
    FeedRecord,
    InvestigationBundle,
    load_investigation,
)
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("gui.investigation_drawer")

_PURCHASE_TYPES: frozenset[str] = frozenset({"buy", "purchase"})
_SALE_TYPES: frozenset[str] = frozenset({"sell", "sale"})


class InvestigationDrawer(QWidget):
    """Slide-in detail panel that investigates a single FeedRecord.

    Parameters
    ----------
    repository:
        Duck-typed object with ``detail``, ``recent_by_person``,
        ``recent_by_issuer`` methods.  Pass ``None`` to render a
        summary-only view without async loading.
    thread_pool:
        Defaults to ``QThreadPool.globalInstance()``.
    """

    # Signals
    openCompanyRequested = Signal(
        object, object
    )  # (identifier: str, market: FeedMarket)
    openInsiderRequested = Signal(object, object)  # (person: str, market: FeedMarket)
    watchRequested = Signal(object)  # (record: FeedRecord)
    closeRequested = Signal()

    def __init__(
        self,
        repository,
        parent: QWidget | None = None,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._request_id: int = 0
        self._record: FeedRecord | None = None
        self._bundle: InvestigationBundle | None = None
        self._palette = get_theme_manager().palette()
        self._mono_font = QFont("JetBrains Mono")
        self._mono_font.setStyleHint(QFont.StyleHint.Monospace)
        # Expose the last chosen transaction QColor for tests
        self._last_transaction_color: QColor = QColor()

        self.setAccessibleName("Investigation details")
        get_theme_manager().paletteChanged.connect(self._on_palette_changed)

        self._build_ui()
        self._show_state("Select a transaction to investigate.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_record(self, record: FeedRecord) -> None:
        """Begin loading investigation data for *record*."""
        self._record = record
        self._request_id += 1
        request_id = self._request_id

        if self._repository is None:
            # Render from record alone — no async fetch
            bundle = InvestigationBundle(detail=None, by_person=(), by_issuer=())
            self._render(record, bundle)
            return

        self._show_state("Loading…")

        worker = Worker(load_investigation, self._repository, record)
        worker.signals.result.connect(
            lambda bundle: self._on_result(request_id, record, bundle)
        )
        worker.signals.error.connect(
            lambda error_info: self._on_error(request_id, error_info)
        )
        self._thread_pool.start(worker)

    def last_transaction_color(self) -> QColor:
        """Return the QColor last applied to the transaction-type label.

        Exposed for tests so they can assert semantic coloring without
        inspecting stylesheet strings.
        """
        return self._last_transaction_color

    def request_cancellation(self) -> None:
        """Invalidate any in-flight investigation load so its result is dropped."""
        self._request_id += 1

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # State label (empty prompt / loading / error)
        self._state_label = QLabel()
        self._state_label.setObjectName("drawerState")
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_label.setWordWrap(True)
        self._state_label.setAccessibleName("Investigation status")
        outer.addWidget(self._state_label)

        # Scroll area for the detail content
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.hide()
        outer.addWidget(self._scroll, stretch=1)

        # Content container placed inside scroll area
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(8)
        self._scroll.setWidget(self._content)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_result(
        self, request_id: int, record: FeedRecord, bundle: InvestigationBundle
    ) -> None:
        if request_id != self._request_id:
            return
        self._render(record, bundle)

    def _on_error(self, request_id: int, error_info: tuple) -> None:
        if request_id != self._request_id:
            return
        log.error("Investigation load failed: %s", error_info[1])
        self._show_state("Could not load investigation details.")

    def _on_palette_changed(self, palette) -> None:
        self._palette = palette
        if self._record is not None and self._bundle is not None:
            self._render(self._record, self._bundle)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self, record: FeedRecord, bundle: InvestigationBundle) -> None:
        """Populate the scroll area with investigation content."""
        self._bundle = bundle
        self._clear_content()

        self._add_identity_line(record)
        self._add_separator()
        self._add_transaction_summary(record)
        self._add_separator()
        self._add_ownership_block(bundle)
        self._add_footnotes_block(bundle)
        if bundle.detail and bundle.detail.metadata:
            self._add_metadata_rows(bundle.detail.metadata)
        self._add_filing_block(record, bundle)
        self._add_separator()
        self._add_recent_section("Recent activity by this insider", bundle.by_person)
        self._add_recent_section("Recent activity at this company", bundle.by_issuer)
        self._add_separator()
        self._add_action_buttons(record)

        # Stretch to push content to the top
        self._content_layout.addStretch()

        self._state_label.hide()
        self._scroll.show()

    def _clear_content(self) -> None:
        """Remove all widgets from the content layout."""
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_identity_line(self, record: FeedRecord) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)

        name_label = QLabel(record.person or "—")
        name_label.setTextFormat(Qt.TextFormat.PlainText)
        font = name_label.font()
        font.setBold(True)
        name_label.setFont(font)
        name_label.setAccessibleName("Insider name")
        row.addWidget(name_label)

        if record.role:
            role_label = QLabel(record.role)
            role_label.setTextFormat(Qt.TextFormat.PlainText)
            role_label.setAccessibleName("Insider role")
            row.addWidget(role_label)

        market_label = QLabel(f"[{record.market.value}]")
        market_label.setTextFormat(Qt.TextFormat.PlainText)
        market_label.setAccessibleName("Market")
        row.addWidget(market_label)
        row.addStretch()

        container = QWidget()
        container.setLayout(row)
        self._content_layout.addWidget(container)

    def _add_transaction_summary(self, record: FeedRecord) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(4)

        # Transaction type with semantic color
        type_label = QLabel(record.transaction_type)
        type_label.setTextFormat(Qt.TextFormat.PlainText)
        color = self._transaction_color(record.transaction_type)
        self._last_transaction_color = color
        type_label.setStyleSheet(f"color: {color.name()};")
        type_label.setAccessibleName("Transaction type")
        layout.addWidget(type_label)

        # Issuer
        if record.issuer:
            layout.addWidget(self._kv_label("Issuer", record.issuer))

        # Identifier (mono)
        layout.addWidget(self._kv_mono("Ticker / ISIN", record.identifier or "—"))

        # Dates
        trade_date = str(record.transaction_date) if record.transaction_date else "—"
        filing_date = str(record.filing_date) if record.filing_date else "—"
        layout.addWidget(self._kv_label("Trade date", trade_date))
        layout.addWidget(self._kv_label("Filing date", filing_date))

        # Quantity (mono)
        qty = f"{record.quantity:,.2f}" if record.quantity is not None else "—"
        layout.addWidget(self._kv_mono("Quantity", qty))

        # Price (mono)
        if record.price is not None:
            suffix = f" {record.currency}" if record.currency else ""
            price_str = f"{record.price:,.2f}{suffix}"
        else:
            price_str = "—"
        layout.addWidget(self._kv_mono("Price", price_str))

        # Value
        value_str = self._format_value(record)
        layout.addWidget(self._kv_mono("Value", value_str))

        container = QWidget()
        container.setLayout(layout)
        self._content_layout.addWidget(container)

    def _add_ownership_block(self, bundle: InvestigationBundle) -> None:
        detail = bundle.detail
        if detail is not None and detail.shares_owned_after is not None:
            text = f"Shares owned after: {detail.shares_owned_after:,.0f}"
        else:
            text = "Ownership: Not disclosed"
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAccessibleName("Ownership information")
        self._content_layout.addWidget(label)

    def _add_footnotes_block(self, bundle: InvestigationBundle) -> None:
        detail = bundle.detail
        if detail is not None and detail.footnotes:
            text = detail.footnotes
        else:
            text = "No footnotes"
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAccessibleName("Footnotes")
        self._content_layout.addWidget(label)

    def _add_metadata_rows(self, metadata: tuple[tuple[str, str], ...]) -> None:
        for label_text, value_text in metadata:
            self._content_layout.addWidget(self._kv_label(label_text, value_text))

    def _add_filing_block(
        self, record: FeedRecord, bundle: InvestigationBundle
    ) -> None:
        layout = QHBoxLayout()
        layout.setSpacing(6)

        reference = ""
        if bundle.detail and bundle.detail.reference:
            reference = bundle.detail.reference
        ref_text = reference or "—"
        ref_label = QLabel(ref_text)
        ref_label.setTextFormat(Qt.TextFormat.PlainText)
        ref_label.setFont(self._mono_font)
        ref_label.setAccessibleName("Filing reference")
        layout.addWidget(ref_label)

        open_btn = QPushButton("Open filing")
        open_btn.setAccessibleName("Open filing URL")
        url = record.source_url or ""
        valid_url = url.startswith(("https://", "http://"))
        open_btn.setEnabled(valid_url)
        if valid_url:
            open_btn.clicked.connect(partial(webbrowser.open, url))
        layout.addWidget(open_btn)
        layout.addStretch()

        container = QWidget()
        container.setLayout(layout)
        self._content_layout.addWidget(container)

    def _add_recent_section(self, title: str, records: tuple[FeedRecord, ...]) -> None:
        header = QLabel(title)
        header.setTextFormat(Qt.TextFormat.PlainText)
        header_font = header.font()
        header_font.setBold(True)
        header.setFont(header_font)
        header.setAccessibleName(title)
        self._content_layout.addWidget(header)

        if not records:
            empty = QLabel("No other recent activity.")
            empty.setTextFormat(Qt.TextFormat.PlainText)
            empty.setAccessibleName(f"No activity for {title}")
            self._content_layout.addWidget(empty)
            return

        for rec in records:
            line = self._recent_record_line(rec)
            self._content_layout.addWidget(line)

    def _recent_record_line(self, rec: FeedRecord) -> QLabel:
        date_str = str(rec.transaction_date) if rec.transaction_date else "—"
        value_str = self._format_value(rec)
        text = f"{date_str} · {rec.transaction_type} · {rec.issuer} · {value_str}"
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        color = self._transaction_color(rec.transaction_type)
        label.setStyleSheet(f"color: {color.name()};")
        return label

    def _add_action_buttons(self, record: FeedRecord) -> None:
        layout = QHBoxLayout()
        layout.setSpacing(6)

        # Open company
        company_btn = QPushButton("Open company")
        company_btn.setAccessibleName("Open company page")
        company_btn.setEnabled(bool(record.identifier))
        company_btn.clicked.connect(
            partial(self._emit_company, record.identifier, record.market)
        )
        layout.addWidget(company_btn)

        # Open insider
        insider_btn = QPushButton("Open insider")
        insider_btn.setAccessibleName("Open insider page")
        insider_btn.setEnabled(bool(record.person))
        insider_btn.clicked.connect(
            partial(self._emit_insider, record.person, record.market)
        )
        layout.addWidget(insider_btn)

        # Watch — adds the record's entity to a watchlist via the host window.
        watch_btn = QPushButton("Watch")
        watch_btn.setAccessibleName("Add to watchlist")
        watch_btn.setToolTip("Add this company and insider to a watchlist")
        watch_btn.clicked.connect(partial(self._emit_watch, record))
        layout.addWidget(watch_btn)

        # Placeholder buttons — not yet implemented
        _placeholder_tooltip = "Available in a future update"
        for name in ("Alert", "Compare"):
            btn = QPushButton(name)
            btn.setAccessibleName(name)
            btn.setEnabled(False)
            btn.setToolTip(_placeholder_tooltip)
            layout.addWidget(btn)

        layout.addStretch()

        container = QWidget()
        container.setLayout(layout)
        self._content_layout.addWidget(container)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _show_state(self, message: str) -> None:
        self._state_label.setText(message)
        self._state_label.show()
        self._scroll.hide()

    def _transaction_color(self, transaction_type: str) -> QColor:
        normalized = transaction_type.casefold()
        if normalized in _PURCHASE_TYPES:
            return QColor(self._palette.purchase)
        if normalized in _SALE_TYPES:
            return QColor(self._palette.sale)
        return QColor(self._palette.text_primary)

    @staticmethod
    def _format_value(record: FeedRecord) -> str:
        if record.value_display:
            return record.value_display
        if record.value_sort is None:
            return "—"
        suffix = f" {record.currency}" if record.currency else ""
        return f"{record.value_sort:,.2f}{suffix}"

    def _kv_label(self, key: str, value: str) -> QLabel:
        label = QLabel(f"{key}: {value}")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    def _kv_mono(self, key: str, value: str) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        key_label = QLabel(f"{key}:")
        key_label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(key_label)

        val_label = QLabel(value)
        val_label.setTextFormat(Qt.TextFormat.PlainText)
        val_label.setFont(self._mono_font)
        row.addWidget(val_label)
        row.addStretch()

        container = QWidget()
        container.setLayout(row)
        return container

    def _add_separator(self) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self._content_layout.addWidget(line)

    def _emit_company(self, identifier: str, market: FeedMarket) -> None:
        self.openCompanyRequested.emit(identifier, market)

    def _emit_insider(self, person: str, market: FeedMarket) -> None:
        self.openInsiderRequested.emit(person, market)

    def _emit_watch(self, record: FeedRecord) -> None:
        self.watchRequested.emit(record)

    # ------------------------------------------------------------------
    # Event overrides
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

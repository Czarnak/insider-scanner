"""Investigation drawer widget — contextual detail panel for a FeedRecord."""

from __future__ import annotations

import webbrowser
from functools import partial

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QHideEvent, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.core.trade_types import TransactionDirection, classify_direction
from insider_scanner.gui.theme import get_theme_manager
from insider_scanner.gui.trade_display import format_value
from insider_scanner.persistence.feed import (
    FeedRecord,
    InvestigationBundle,
    load_investigation,
)
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import LatestRequest

log = get_logger("gui.investigation_drawer")


class InvestigationDrawer(QWidget):
    """Show the selected record immediately and debounce its bounded detail query."""

    openCompanyRequested = Signal(object, object)
    openInsiderRequested = Signal(object, object)
    watchRequested = Signal(object)
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
        self._requests = LatestRequest(self, self._thread_pool)
        self._record: FeedRecord | None = None
        self._bundle: InvestigationBundle | None = None
        self._needs_details = False
        self._palette = get_theme_manager().palette()
        self._mono_font = QFont("JetBrains Mono")
        self._mono_font.setStyleHint(QFont.StyleHint.Monospace)
        self._last_transaction_color = QColor()
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(150)
        self._load_timer.timeout.connect(self._load_details)
        self.setAccessibleName("Investigation details")
        self._build_ui()
        self._on_palette_changed(self._palette)
        self._show_state("Select a transaction to investigate.")
        get_theme_manager().paletteChanged.connect(self._on_palette_changed)

    def show_record(self, record: FeedRecord) -> None:
        """Replace all prior detail now; only the final selection starts a query."""
        self.request_cancellation()
        self._render(record, None)
        self._needs_details = self._repository is not None
        if self._repository is not None:
            self._show_state("Loading…", keep_content=True)
            self._load_timer.start()

    def _load_details(self) -> None:
        record = self._record
        if record is None:
            return
        self._requests.start(
            load_investigation,
            args=(self._repository, record),
            on_result=partial(self._on_result, record),
            on_error=self._on_error,
        )

    def last_transaction_color(self) -> QColor:
        """Return the semantic color applied to the current transaction label."""
        return self._last_transaction_color

    def request_cancellation(self) -> None:
        """Stop pending dispatch and suppress callbacks from submitted detail loads."""
        self._load_timer.stop()
        self._requests.invalidate()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._state_label = QLabel()
        self._state_label.setObjectName("drawerState")
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_label.setWordWrap(True)
        self._state_label.setAccessibleName("Investigation status")
        outer.addWidget(self._state_label)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll, stretch=1)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(8)
        self._scroll.setWidget(self._content)
        identity = QHBoxLayout()
        self._person_label = self._label("Insider name", identity, bold=True)
        self._role_label = self._label("Insider role", identity)
        self._market_label = self._label("Market", identity)
        identity.addStretch()
        self._content_layout.addLayout(identity)
        self._add_separator()
        self._type_label = self._label("Transaction type")
        self._summary_labels = {
            name: self._label(name)
            for name in (
                "Issuer",
                "Ticker / ISIN",
                "Trade date",
                "Filing date",
                "Quantity",
                "Price",
                "Value",
            )
        }
        for name in ("Ticker / ISIN", "Quantity", "Price", "Value"):
            self._summary_labels[name].setFont(self._mono_font)
        self._add_separator()
        self._ownership_label = self._label("Ownership information")
        self._footnotes_label = self._label("Footnotes")
        self._metadata_label = self._label("Filing metadata")
        filing = QHBoxLayout()
        self._reference_label = self._label("Filing reference", filing)
        self._reference_label.setFont(self._mono_font)
        self._filing_button = QPushButton("Open filing")
        self._filing_button.setAccessibleName("Open filing URL")
        self._filing_button.clicked.connect(self._open_filing)
        filing.addWidget(self._filing_button)
        filing.addStretch()
        self._content_layout.addLayout(filing)
        self._add_separator()
        self._recent_person = self._build_recent("Recent activity by this insider")
        self._recent_issuer = self._build_recent("Recent activity at this company")
        self._add_separator()
        self._build_actions()
        self._content_layout.addStretch()

    def _label(
        self, name: str, layout: QHBoxLayout | None = None, *, bold: bool = False
    ) -> QLabel:
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAccessibleName(name)
        if bold:
            font = label.font()
            font.setBold(True)
            label.setFont(font)
        (layout if layout is not None else self._content_layout).addWidget(label)
        return label

    def _build_recent(self, title: str) -> tuple[QLabel, tuple[QLabel, ...]]:
        self._label(title, bold=True).setText(title)
        empty = self._label(f"No activity for {title}")
        empty.setText("No other recent activity.")
        # load_investigation requests at most eight rows for each section.
        rows = tuple(self._label(f"{title} {i + 1}") for i in range(8))
        return empty, rows

    def _build_actions(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._company_button = QPushButton("Open company")
        self._company_button.setAccessibleName("Open company page")
        self._company_button.clicked.connect(self._emit_company)
        row.addWidget(self._company_button)
        self._insider_button = QPushButton("Open insider")
        self._insider_button.setAccessibleName("Open insider page")
        self._insider_button.clicked.connect(self._emit_insider)
        row.addWidget(self._insider_button)
        watch = QPushButton("Watch")
        watch.setAccessibleName("Add to watchlist")
        watch.setToolTip("Add this company and insider to a watchlist")
        watch.clicked.connect(self._emit_watch)
        row.addWidget(watch)
        for name in ("Alert", "Compare"):
            button = QPushButton(name)
            button.setAccessibleName(name)
            button.setEnabled(False)
            button.setToolTip("Available in a future update")
            row.addWidget(button)
        row.addStretch()
        self._content_layout.addLayout(row)

    def _on_result(self, record: FeedRecord, bundle: InvestigationBundle) -> None:
        self._needs_details = False
        self._render(record, bundle)

    def _on_error(self, error_info: tuple) -> None:
        self._needs_details = False
        log.error("Investigation load failed: %s", error_info[1])
        self._show_state("Could not load investigation details.", keep_content=True)

    def _on_palette_changed(self, palette) -> None:
        self._palette = palette
        # One stylesheet per drawer/theme, rather than per label/selection. The
        # property selectors override the application's QWidget color rule.
        self.setStyleSheet(
            f'QLabel[drawerDirection="buy"] {{ color: {palette.purchase}; }}'
            f'QLabel[drawerDirection="sell"] {{ color: {palette.sale}; }}'
            f'QLabel[drawerDirection="other"] {{ color: {palette.text_primary}; }}'
        )
        if self._record is not None:
            self._last_transaction_color = self._transaction_color(
                self._record.transaction_type
            )

    def _render(self, record: FeedRecord, bundle: InvestigationBundle | None) -> None:
        """Update the existing content tree; a missing bundle clears old details."""
        self._record = record
        self._bundle = bundle
        self._person_label.setText(record.person or "—")
        self._role_label.setText(record.role)
        self._role_label.setVisible(bool(record.role))
        self._market_label.setText(f"[{record.market.value}]")
        self._type_label.setText(record.transaction_type)
        self._set_direction(self._type_label, record.transaction_type)
        self._last_transaction_color = self._transaction_color(record.transaction_type)
        suffix = f" {record.currency}" if record.currency else ""
        values = {
            "Issuer": record.issuer,
            "Ticker / ISIN": record.identifier or "—",
            "Trade date": str(record.transaction_date)
            if record.transaction_date
            else "—",
            "Filing date": str(record.filing_date) if record.filing_date else "—",
            "Quantity": f"{record.quantity:,.2f}"
            if record.quantity is not None
            else "—",
            "Price": f"{record.price:,.2f}{suffix}"
            if record.price is not None
            else "—",
            "Value": self._format_value(record),
        }
        for key, value in values.items():
            self._summary_labels[key].setText(f"{key}: {value}")
        self._summary_labels["Issuer"].setVisible(bool(record.issuer))
        detail = bundle.detail if bundle else None
        ownership = (
            f"Shares owned after: {detail.shares_owned_after:,.0f}"
            if detail is not None and detail.shares_owned_after is not None
            else "Ownership: Not disclosed"
        )
        self._ownership_label.setText(ownership)
        self._footnotes_label.setText(
            detail.footnotes if detail and detail.footnotes else "No footnotes"
        )
        metadata = (
            "\n".join(f"{key}: {value}" for key, value in detail.metadata)
            if detail
            else ""
        )
        self._metadata_label.setText(metadata)
        self._metadata_label.setVisible(bool(metadata))
        self._reference_label.setText(
            detail.reference if detail and detail.reference else "—"
        )
        self._filing_button.setEnabled(
            record.source_url.startswith(("https://", "http://"))
        )
        self._company_button.setEnabled(bool(record.identifier))
        self._insider_button.setEnabled(bool(record.person))
        self._update_recent(self._recent_person, bundle.by_person if bundle else ())
        self._update_recent(self._recent_issuer, bundle.by_issuer if bundle else ())
        self._state_label.setText("")
        self._state_label.hide()
        self._scroll.show()

    def _update_recent(
        self,
        section: tuple[QLabel, tuple[QLabel, ...]],
        records: tuple[FeedRecord, ...],
    ) -> None:
        empty, rows = section
        empty.setVisible(not records)
        for i, label in enumerate(rows):
            record = records[i] if i < len(records) else None
            if record is None:
                label.clear()
                label.hide()
            else:
                date_str = (
                    str(record.transaction_date) if record.transaction_date else "—"
                )
                label.setText(
                    f"{date_str} · {record.transaction_type} · {record.issuer} · {self._format_value(record)}"
                )
                self._set_direction(label, record.transaction_type)
                label.show()

    @staticmethod
    def _set_direction(label: QLabel, transaction_type: str) -> None:
        direction = classify_direction(transaction_type).value.lower()
        if label.property("drawerDirection") != direction:
            label.setProperty("drawerDirection", direction)
            label.style().unpolish(label)
            label.style().polish(label)

    def _show_state(self, message: str, *, keep_content: bool = False) -> None:
        self._state_label.setText(message)
        self._state_label.show()
        self._scroll.setVisible(keep_content)

    def _transaction_color(self, transaction_type: str) -> QColor:
        direction = classify_direction(transaction_type)
        if direction == TransactionDirection.BUY:
            return QColor(self._palette.purchase)
        if direction == TransactionDirection.SELL:
            return QColor(self._palette.sale)
        return QColor(self._palette.text_primary)

    @staticmethod
    def _format_value(record: FeedRecord) -> str:
        return format_value(record)

    def _add_separator(self) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self._content_layout.addWidget(line)

    def _open_filing(self) -> None:
        if self._record and self._record.source_url.startswith(("https://", "http://")):
            webbrowser.open(self._record.source_url)

    def _emit_company(self) -> None:
        if self._record:
            self.openCompanyRequested.emit(self._record.identifier, self._record.market)

    def _emit_insider(self) -> None:
        if self._record:
            self.openInsiderRequested.emit(self._record.person, self._record.market)

    def _emit_watch(self) -> None:
        if self._record:
            self.watchRequested.emit(self._record)

    def hideEvent(self, event: QHideEvent) -> None:
        self.request_cancellation()
        super().hideEvent(event)

    def showEvent(self, event: QShowEvent) -> None:
        if self._needs_details:
            self._load_timer.start()
        super().showEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.closeRequested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

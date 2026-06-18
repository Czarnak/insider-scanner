"""Entity research pages: CompanyPage and InsiderPage.

Each page shows a header, a one-line summary, and a history table driven by
FeedTableModel (reused verbatim from feed_page).  Background data fetching
mirrors the Worker / request-id guard pattern used in FeedPageWidget.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.gui.feed_page import FeedTableModel
from insider_scanner.persistence.feed import FeedMarket, FeedRecord
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import Worker

log = get_logger("gui.entity_pages")

_BUY_TERMS: frozenset[str] = frozenset({"buy", "purchase"})
_SELL_TERMS: frozenset[str] = frozenset({"sell", "sale"})


def _latest_date(records: tuple[FeedRecord, ...]) -> date | None:
    """Return the maximum non-None transaction_date, falling back to filing_date."""
    dates: list[date] = []
    for r in records:
        d = r.transaction_date or r.filing_date
        if d is not None:
            dates.append(d)
    return max(dates) if dates else None


def _build_summary(records: tuple[FeedRecord, ...]) -> str:
    """Build summary line: "{n} transactions · {buys} buys · {sells} sells · latest {date}"."""
    n = len(records)
    buys = sum(1 for r in records if r.transaction_type.casefold() in _BUY_TERMS)
    sells = sum(1 for r in records if r.transaction_type.casefold() in _SELL_TERMS)
    latest = _latest_date(records)
    date_str = latest.isoformat() if latest is not None else "—"
    return f"{n} transactions · {buys} buys · {sells} sells · latest {date_str}"


class EntityResearchPage(QWidget):
    """Base widget for entity-focused transaction history pages.

    Subclasses implement ``_fetch``, ``_header_text``, ``_noun``, and
    ``_empty_prompt`` to specialize for companies or insiders.
    """

    def __init__(
        self,
        repository,
        parent: QWidget | None = None,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._thread_pool: QThreadPool | object = (
            thread_pool or QThreadPool.globalInstance()
        )
        self._request_id: int = 0
        self._build_ui()
        self._show_state(self._empty_prompt())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)

        self.header_label = QLabel()
        self.header_label.setObjectName("entityHeader")
        self.header_label.setTextFormat(Qt.TextFormat.PlainText)
        bold_font = QFont()
        bold_font.setBold(True)
        self.header_label.setFont(bold_font)
        layout.addWidget(self.header_label)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("entitySummary")
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Loading entity activity")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.state_label = QLabel()
        self.state_label.setObjectName("entityState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setAccessibleName("Entity status")
        self.state_label.hide()
        layout.addWidget(self.state_label)

        self.model = FeedTableModel(self)
        self.table = QTableView()
        self.table.setAccessibleName("Entity transaction history")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate(
            (70, 90, 170, 100, 150, 100, 90, 90, 90, 90, 120, 80)
        ):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, value: str, market: FeedMarket) -> None:
        """Load transaction history for *value* in the given *market*.

        Shows an empty prompt immediately if *value* is blank.
        Shows unavailable if *repository* is None.
        """
        if not value.strip():
            self._show_state(self._empty_prompt())
            return

        if self._repository is None:
            self._show_state("Local transaction data is unavailable.")
            return

        self._request_id += 1
        request_id = self._request_id

        self.progress.show()
        self._show_state(f"Loading {self._noun()} activity…")

        worker = Worker(self._fetch, value, market)
        worker.signals.result.connect(
            lambda records: self._on_result(request_id, value, market, records)
        )
        worker.signals.error.connect(
            lambda error_info: self._on_error(request_id, error_info)
        )
        worker.signals.finished.connect(self.progress.hide)
        self._thread_pool.start(worker)

    # ------------------------------------------------------------------
    # Abstract hooks (override in subclasses)
    # ------------------------------------------------------------------

    def _fetch(self, value: str, market: FeedMarket) -> tuple[FeedRecord, ...]:
        """Fetch records from the repository.  Implemented by subclasses."""
        raise NotImplementedError

    def _header_text(
        self, value: str, market: FeedMarket, records: tuple[FeedRecord, ...]
    ) -> str:
        """Return the text for the header label.  Implemented by subclasses."""
        raise NotImplementedError

    def _noun(self) -> str:
        """Human-readable entity noun, e.g. 'company' or 'insider'."""
        raise NotImplementedError

    def _empty_prompt(self) -> str:
        """Text shown when no value has been searched yet."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_result(
        self,
        request_id: int,
        value: str,
        market: FeedMarket,
        records: tuple[FeedRecord, ...],
    ) -> None:
        if request_id != self._request_id:
            return
        self._populate(value, market, records)

    def _on_error(self, request_id: int, error_info: tuple) -> None:
        if request_id != self._request_id:
            return
        log.error("Entity page load failed: %s", error_info[1])
        self.progress.hide()
        self._show_state(f"Could not load {self._noun()} activity.")

    def _populate(
        self,
        value: str,
        market: FeedMarket,
        records: tuple[FeedRecord, ...],
    ) -> None:
        """Update header, summary, and table model from *records*."""
        self.header_label.setText(self._header_text(value, market, records))
        if not records:
            self.model.replace_records(())
            self.summary_label.clear()
            self._show_state(f"No {self._noun()} activity found.")
            return
        self.model.replace_records(records)
        self.summary_label.setText(_build_summary(records))
        self.state_label.hide()

    def _show_state(self, message: str) -> None:
        self.state_label.setText(message)
        self.state_label.show()

    def request_cancellation(self) -> None:
        """Invalidate any in-flight load so its result is dropped on teardown."""
        self._request_id += 1


# ---------------------------------------------------------------------------
# Concrete pages
# ---------------------------------------------------------------------------


class CompanyPage(EntityResearchPage):
    """Shows insider transaction history for a single company / ticker."""

    def _fetch(self, value: str, market: FeedMarket) -> tuple[FeedRecord, ...]:
        return self._repository.recent_by_issuer(value, markets=(market,), limit=200)

    def _header_text(
        self, value: str, market: FeedMarket, records: tuple[FeedRecord, ...]
    ) -> str:
        if records and records[0].issuer:
            name = records[0].issuer
            return f"{name} ({value}) · {market.value}"
        return f"{value} · {market.value}"

    def _noun(self) -> str:
        return "company"

    def _empty_prompt(self) -> str:
        return "Search a ticker or company in the top bar to research it."


class InsiderPage(EntityResearchPage):
    """Shows transaction history for a single insider / official."""

    def _fetch(self, value: str, market: FeedMarket) -> tuple[FeedRecord, ...]:
        return self._repository.recent_by_person(value, markets=(market,), limit=200)

    def _header_text(
        self, value: str, market: FeedMarket, records: tuple[FeedRecord, ...]
    ) -> str:
        return f"{value} · {market.value}"

    def _noun(self) -> str:
        return "insider"

    def _empty_prompt(self) -> str:
        return "Search an insider name in the top bar to research them."

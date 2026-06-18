"""Feed filter panel and active-filter chips for the unified transaction feed."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    FeedSortField,
    TransactionDirection,
)

_VALUE_MAX = 1_000_000_000_000.0  # 1 trillion


# ---------------------------------------------------------------------------
# FeedFilterPanel
# ---------------------------------------------------------------------------


class FeedFilterPanel(QWidget):
    """Collapsible advanced-filter drawer (visibility toggled by the caller)."""

    filtersApplied = Signal(object)
    resetRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Markets row
        layout.addWidget(self._build_markets_row())

        # Directions row
        layout.addWidget(self._build_directions_row())

        # Date range row
        layout.addWidget(self._build_date_range_row())

        # Value range row
        layout.addWidget(self._build_value_range_row())

        # Buttons + error row
        layout.addWidget(self._build_buttons_row())

    def _build_markets_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Markets:"))

        self.cb_market_us = QCheckBox("US")
        self.cb_market_us.setAccessibleName("Filter by market: US")
        h.addWidget(self.cb_market_us)

        self.cb_market_congress = QCheckBox("Congress")
        self.cb_market_congress.setAccessibleName("Filter by market: Congress")
        h.addWidget(self.cb_market_congress)

        self.cb_market_europe = QCheckBox("Europe")
        self.cb_market_europe.setAccessibleName("Filter by market: Europe")
        h.addWidget(self.cb_market_europe)

        h.addStretch()
        return row

    def _build_directions_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Directions:"))

        self.cb_dir_buy = QCheckBox("Buy")
        self.cb_dir_buy.setAccessibleName("Filter by direction: Buy")
        h.addWidget(self.cb_dir_buy)

        self.cb_dir_sell = QCheckBox("Sell")
        self.cb_dir_sell.setAccessibleName("Filter by direction: Sell")
        h.addWidget(self.cb_dir_sell)

        self.cb_dir_other = QCheckBox("Other")
        self.cb_dir_other.setAccessibleName("Filter by direction: Other")
        h.addWidget(self.cb_dir_other)

        h.addStretch()
        return row

    def _build_date_range_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Trade date:"))

        self.cb_date_from = QCheckBox("From")
        self.cb_date_from.setAccessibleName("Enable trade date from")
        h.addWidget(self.cb_date_from)

        self.de_date_from = QDateEdit()
        self.de_date_from.setCalendarPopup(True)
        self.de_date_from.setDate(QDate.currentDate())
        self.de_date_from.setEnabled(False)
        self.de_date_from.setAccessibleName("Trade date from")
        h.addWidget(self.de_date_from)

        self.cb_date_to = QCheckBox("To")
        self.cb_date_to.setAccessibleName("Enable trade date to")
        h.addWidget(self.cb_date_to)

        self.de_date_to = QDateEdit()
        self.de_date_to.setCalendarPopup(True)
        self.de_date_to.setDate(QDate.currentDate())
        self.de_date_to.setEnabled(False)
        self.de_date_to.setAccessibleName("Trade date to")
        h.addWidget(self.de_date_to)

        h.addStretch()
        return row

    def _build_value_range_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Value:"))

        self.cb_value_min = QCheckBox("Min")
        self.cb_value_min.setAccessibleName("Enable minimum value")
        h.addWidget(self.cb_value_min)

        self.sb_value_min = QDoubleSpinBox()
        self.sb_value_min.setRange(0.0, _VALUE_MAX)
        self.sb_value_min.setDecimals(2)
        self.sb_value_min.setEnabled(False)
        self.sb_value_min.setAccessibleName("Minimum value")
        h.addWidget(self.sb_value_min)

        self.cb_value_max = QCheckBox("Max")
        self.cb_value_max.setAccessibleName("Enable maximum value")
        h.addWidget(self.cb_value_max)

        self.sb_value_max = QDoubleSpinBox()
        self.sb_value_max.setRange(0.0, _VALUE_MAX)
        self.sb_value_max.setDecimals(2)
        self.sb_value_max.setEnabled(False)
        self.sb_value_max.setAccessibleName("Maximum value")
        h.addWidget(self.sb_value_max)

        h.addStretch()
        return row

    def _build_buttons_row(self) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setAccessibleName("Apply filters")
        h.addWidget(self.btn_apply)

        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setAccessibleName("Reset filters")
        h.addWidget(self.btn_reset)

        self.lbl_error = QLabel()
        self.lbl_error.setVisible(False)
        self.lbl_error.setStyleSheet("color: red;")
        h.addWidget(self.lbl_error)

        h.addStretch()
        return row

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.cb_date_from.toggled.connect(self.de_date_from.setEnabled)
        self.cb_date_to.toggled.connect(self.de_date_to.setEnabled)
        self.cb_value_min.toggled.connect(self.sb_value_min.setEnabled)
        self.cb_value_max.toggled.connect(self.sb_value_max.setEnabled)

        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_reset.clicked.connect(self._on_reset)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_apply(self) -> None:
        try:
            criteria = self.current_filters()
        except ValueError as exc:
            self.lbl_error.setText(str(exc))
            self.lbl_error.setVisible(True)
            return
        self.lbl_error.setVisible(False)
        self.filtersApplied.emit(criteria)

    def _on_reset(self) -> None:
        self._clear_controls()
        self.resetRequested.emit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_filters(self) -> FeedCriteria:
        """Build a FeedCriteria from current control state."""
        markets: list[FeedMarket] = []
        if self.cb_market_us.isChecked():
            markets.append(FeedMarket.US)
        if self.cb_market_congress.isChecked():
            markets.append(FeedMarket.CONGRESS)
        if self.cb_market_europe.isChecked():
            markets.append(FeedMarket.EUROPE)

        directions: list[TransactionDirection] = []
        if self.cb_dir_buy.isChecked():
            directions.append(TransactionDirection.BUY)
        if self.cb_dir_sell.isChecked():
            directions.append(TransactionDirection.SELL)
        if self.cb_dir_other.isChecked():
            directions.append(TransactionDirection.OTHER)

        date_from: date | None = None
        if self.cb_date_from.isChecked():
            qd = self.de_date_from.date()
            date_from = date(qd.year(), qd.month(), qd.day())

        date_to: date | None = None
        if self.cb_date_to.isChecked():
            qd = self.de_date_to.date()
            date_to = date(qd.year(), qd.month(), qd.day())

        value_min: float | None = None
        if self.cb_value_min.isChecked():
            value_min = self.sb_value_min.value()

        value_max: float | None = None
        if self.cb_value_max.isChecked():
            value_max = self.sb_value_max.value()

        return FeedCriteria(
            search="",
            sort_field=FeedSortField.TRANSACTION_DATE,
            descending=True,
            markets=tuple(markets),
            directions=tuple(directions),
            transaction_date_from=date_from,
            transaction_date_to=date_to,
            value_min=value_min,
            value_max=value_max,
        )

    def set_filters(self, criteria: FeedCriteria) -> None:
        """Populate controls from criteria; block child signals while populating."""
        widgets = [
            self.cb_market_us,
            self.cb_market_congress,
            self.cb_market_europe,
            self.cb_dir_buy,
            self.cb_dir_sell,
            self.cb_dir_other,
            self.cb_date_from,
            self.de_date_from,
            self.cb_date_to,
            self.de_date_to,
            self.cb_value_min,
            self.sb_value_min,
            self.cb_value_max,
            self.sb_value_max,
        ]
        for w in widgets:
            w.blockSignals(True)

        try:
            self.cb_market_us.setChecked(FeedMarket.US in criteria.markets)
            self.cb_market_congress.setChecked(FeedMarket.CONGRESS in criteria.markets)
            self.cb_market_europe.setChecked(FeedMarket.EUROPE in criteria.markets)

            self.cb_dir_buy.setChecked(TransactionDirection.BUY in criteria.directions)
            self.cb_dir_sell.setChecked(
                TransactionDirection.SELL in criteria.directions
            )
            self.cb_dir_other.setChecked(
                TransactionDirection.OTHER in criteria.directions
            )

            has_date_from = criteria.transaction_date_from is not None
            self.cb_date_from.setChecked(has_date_from)
            self.de_date_from.setEnabled(has_date_from)
            if criteria.transaction_date_from is not None:
                d = criteria.transaction_date_from
                self.de_date_from.setDate(QDate(d.year, d.month, d.day))

            has_date_to = criteria.transaction_date_to is not None
            self.cb_date_to.setChecked(has_date_to)
            self.de_date_to.setEnabled(has_date_to)
            if criteria.transaction_date_to is not None:
                d = criteria.transaction_date_to
                self.de_date_to.setDate(QDate(d.year, d.month, d.day))

            has_value_min = criteria.value_min is not None
            self.cb_value_min.setChecked(has_value_min)
            self.sb_value_min.setEnabled(has_value_min)
            if criteria.value_min is not None:
                self.sb_value_min.setValue(criteria.value_min)

            has_value_max = criteria.value_max is not None
            self.cb_value_max.setChecked(has_value_max)
            self.sb_value_max.setEnabled(has_value_max)
            if criteria.value_max is not None:
                self.sb_value_max.setValue(criteria.value_max)
        finally:
            for w in widgets:
                w.blockSignals(False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_controls(self) -> None:
        for cb in (
            self.cb_market_us,
            self.cb_market_congress,
            self.cb_market_europe,
            self.cb_dir_buy,
            self.cb_dir_sell,
            self.cb_dir_other,
            self.cb_date_from,
            self.cb_date_to,
            self.cb_value_min,
            self.cb_value_max,
        ):
            cb.setChecked(False)

        self.sb_value_min.setValue(0.0)
        self.sb_value_max.setValue(0.0)
        self.lbl_error.setVisible(False)


# ---------------------------------------------------------------------------
# _Chip — a single removable chip widget
# ---------------------------------------------------------------------------


class _Chip(QFrame):
    """A single labelled chip with an × remove button."""

    def __init__(
        self,
        key: str,
        label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self._build(label)

    def _build(self, label: str) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)

        lbl = QLabel(label)
        h.addWidget(lbl)

        self.btn_remove = QToolButton()
        self.btn_remove.setText("×")
        self.btn_remove.setAccessibleName(f"Remove filter {label}")
        h.addWidget(self.btn_remove)


# ---------------------------------------------------------------------------
# FeedFilterChips
# ---------------------------------------------------------------------------


class FeedFilterChips(QWidget):
    """Horizontal row of removable active-criteria summary chips."""

    chipRemoved = Signal(str)
    resetRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chips: dict[str, _Chip] = {}
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)

        self._btn_clear_all = QPushButton("Clear all")
        self._btn_clear_all.setAccessibleName("Clear all active filters")
        self._btn_clear_all.clicked.connect(self.resetRequested)
        self._layout.addWidget(self._btn_clear_all)

        self._layout.addStretch()
        self.setVisible(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_criteria(self, criteria: FeedCriteria) -> None:
        """Rebuild chips from criteria. Hides the bar when no active criteria."""
        self._remove_all_chips()

        chips_to_add: list[tuple[str, str]] = []

        for market in criteria.markets:
            key = f"market:{market.value}"
            chips_to_add.append((key, f"Market: {market.value}"))

        for direction in criteria.directions:
            key = f"direction:{direction.value}"
            chips_to_add.append((key, f"Direction: {direction.value}"))

        if criteria.transaction_date_from is not None:
            chips_to_add.append(
                ("date_from", f"From: {criteria.transaction_date_from.isoformat()}")
            )

        if criteria.transaction_date_to is not None:
            chips_to_add.append(
                ("date_to", f"To: {criteria.transaction_date_to.isoformat()}")
            )

        if criteria.value_min is not None:
            chips_to_add.append(("value_min", f"Min: {criteria.value_min:,.0f}"))

        if criteria.value_max is not None:
            chips_to_add.append(("value_max", f"Max: {criteria.value_max:,.0f}"))

        if criteria.search:
            chips_to_add.append(("search", f'Search: "{criteria.search}"'))

        for key, label in chips_to_add:
            self._add_chip(key, label)

        self.setVisible(bool(chips_to_add))

    def chip_count(self) -> int:
        """Return the number of active chips (not counting Clear all)."""
        return len(self._chips)

    def remove_chip(self, key: str) -> None:
        """Programmatically trigger removal of the chip with the given key."""
        if key in self._chips:
            self._chips[key].btn_remove.click()

    def click_clear_all(self) -> None:
        """Programmatically click the Clear all button."""
        self._btn_clear_all.click()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_chip(self, key: str, label: str) -> None:
        chip = _Chip(key, label, parent=self)
        chip.btn_remove.clicked.connect(
            lambda _checked=False, k=key: self._on_chip_removed(k)
        )
        # Insert chips before the Clear all button (index 0) and stretch (last).
        # count() - 2 positions chips before both the clear button and the stretch.
        self._layout.insertWidget(self._layout.count() - 2, chip)
        self._chips[key] = chip
        chip.show()

    def _on_chip_removed(self, key: str) -> None:
        chip = self._chips.pop(key, None)
        if chip is not None:
            self._layout.removeWidget(chip)
            chip.setParent(None)  # type: ignore[call-overload]
        self.chipRemoved.emit(key)

    def _remove_all_chips(self) -> None:
        for chip in list(self._chips.values()):
            self._layout.removeWidget(chip)
            chip.setParent(None)  # type: ignore[call-overload]
        self._chips.clear()

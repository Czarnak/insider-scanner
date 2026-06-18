"""Tests for FeedFilterPanel and FeedFilterChips widgets."""

from __future__ import annotations

from datetime import date

import pytest
from PySide6.QtCore import QDate

from insider_scanner.gui.feed_filters import FeedFilterChips, FeedFilterPanel
from insider_scanner.persistence.feed import (
    FeedCriteria,
    FeedMarket,
    TransactionDirection,
)


# ---------------------------------------------------------------------------
# FeedFilterPanel tests
# ---------------------------------------------------------------------------


def test_apply_emits_filters_applied_with_correct_criteria(qtbot):
    """Applying with markets + directions + date range + value range emits once."""
    panel = FeedFilterPanel()
    qtbot.addWidget(panel)

    # Markets: check US and Europe
    panel.cb_market_us.setChecked(True)
    panel.cb_market_europe.setChecked(True)

    # Directions: check Buy
    panel.cb_dir_buy.setChecked(True)

    # Date range: enable both and set values
    panel.cb_date_from.setChecked(True)
    panel.de_date_from.setDate(QDate(2026, 1, 1))
    panel.cb_date_to.setChecked(True)
    panel.de_date_to.setDate(QDate(2026, 6, 30))

    # Value range: enable min only
    panel.cb_value_min.setChecked(True)
    panel.sb_value_min.setValue(1000.0)

    with qtbot.waitSignal(panel.filtersApplied, timeout=1000) as blocker:
        panel.btn_apply.click()

    criteria: FeedCriteria = blocker.args[0]
    assert FeedMarket.US in criteria.markets
    assert FeedMarket.EUROPE in criteria.markets
    assert FeedMarket.CONGRESS not in criteria.markets
    assert criteria.directions == (TransactionDirection.BUY,)
    assert criteria.transaction_date_from == date(2026, 1, 1)
    assert criteria.transaction_date_to == date(2026, 6, 30)
    assert criteria.value_min == pytest.approx(1000.0)
    assert criteria.value_max is None
    assert criteria.search == ""


def test_apply_with_inverted_date_range_shows_error_and_does_not_emit(qtbot):
    """date_from > date_to must show error label and must NOT emit filtersApplied."""
    panel = FeedFilterPanel()
    qtbot.addWidget(panel)

    panel.cb_date_from.setChecked(True)
    panel.de_date_from.setDate(QDate(2026, 12, 31))
    panel.cb_date_to.setChecked(True)
    panel.de_date_to.setDate(QDate(2026, 1, 1))

    signal_emitted = []
    panel.filtersApplied.connect(lambda c: signal_emitted.append(c))

    panel.btn_apply.click()

    # Process any pending events
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    assert len(signal_emitted) == 0, (
        "filtersApplied must NOT be emitted on invalid input"
    )
    assert not panel.lbl_error.isHidden(), "Error label must be shown on ValueError"


def test_set_filters_populates_controls_without_emitting(qtbot):
    """set_filters populates controls silently; subsequent current_filters round-trips."""
    panel = FeedFilterPanel()
    qtbot.addWidget(panel)

    criteria = FeedCriteria(
        markets=(FeedMarket.CONGRESS,),
        directions=(TransactionDirection.SELL,),
        transaction_date_from=date(2025, 3, 1),
        transaction_date_to=date(2025, 9, 30),
        value_min=500.0,
        value_max=99000.0,
    )

    emitted: list[FeedCriteria] = []
    panel.filtersApplied.connect(emitted.append)

    panel.set_filters(criteria)

    assert len(emitted) == 0, "set_filters must not emit filtersApplied"

    result = panel.current_filters()
    assert FeedMarket.CONGRESS in result.markets
    assert FeedMarket.US not in result.markets
    assert TransactionDirection.SELL in result.directions
    assert result.transaction_date_from == date(2025, 3, 1)
    assert result.transaction_date_to == date(2025, 9, 30)
    assert result.value_min == pytest.approx(500.0)
    assert result.value_max == pytest.approx(99000.0)


def test_reset_clears_controls_and_emits_reset_requested(qtbot):
    """Clicking Reset clears all controls and emits resetRequested."""
    panel = FeedFilterPanel()
    qtbot.addWidget(panel)

    # Set some state first
    panel.cb_market_us.setChecked(True)
    panel.cb_dir_buy.setChecked(True)
    panel.cb_date_from.setChecked(True)

    with qtbot.waitSignal(panel.resetRequested, timeout=1000):
        panel.btn_reset.click()

    # All checkboxes should be unchecked after reset
    assert not panel.cb_market_us.isChecked()
    assert not panel.cb_market_congress.isChecked()
    assert not panel.cb_market_europe.isChecked()
    assert not panel.cb_dir_buy.isChecked()
    assert not panel.cb_dir_sell.isChecked()
    assert not panel.cb_dir_other.isChecked()
    assert not panel.cb_date_from.isChecked()
    assert not panel.cb_date_to.isChecked()
    assert not panel.cb_value_min.isChecked()
    assert not panel.cb_value_max.isChecked()

    result = panel.current_filters()
    assert result.markets == ()
    assert result.directions == ()
    assert result.transaction_date_from is None
    assert result.transaction_date_to is None
    assert result.value_min is None
    assert result.value_max is None


# ---------------------------------------------------------------------------
# FeedFilterChips tests
# ---------------------------------------------------------------------------


def test_set_criteria_with_active_filters_renders_correct_chips_and_is_visible(qtbot):
    """set_criteria with multiple active filters renders one chip per criterion."""
    chips = FeedFilterChips()
    qtbot.addWidget(chips)

    criteria = FeedCriteria(
        markets=(FeedMarket.US, FeedMarket.CONGRESS),
        directions=(TransactionDirection.BUY,),
        value_min=1000.0,
        search="apple",
    )
    chips.set_criteria(criteria)

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    assert chips.isVisible()
    # Expect chips: market:US, market:Congress, direction:Buy, value_min, search = 5
    assert chips.chip_count() == 5


def test_set_criteria_with_no_filters_hides_bar(qtbot):
    """set_criteria with empty FeedCriteria hides the widget."""
    chips = FeedFilterChips()
    qtbot.addWidget(chips)
    chips.show()

    chips.set_criteria(FeedCriteria())

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    assert not chips.isVisible() or chips.chip_count() == 0


def test_chip_remove_button_emits_correct_key(qtbot):
    """Clicking a chip's remove button emits chipRemoved with the correct key."""
    chips = FeedFilterChips()
    qtbot.addWidget(chips)

    criteria = FeedCriteria(
        markets=(FeedMarket.US, FeedMarket.EUROPE),
        directions=(TransactionDirection.SELL,),
    )
    chips.set_criteria(criteria)

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    with qtbot.waitSignal(chips.chipRemoved, timeout=1000) as blocker:
        chips.remove_chip("market:US")

    assert blocker.args[0] == "market:US"


def test_clear_all_emits_reset_requested(qtbot):
    """Clicking the Clear all button emits resetRequested."""
    chips = FeedFilterChips()
    qtbot.addWidget(chips)

    criteria = FeedCriteria(markets=(FeedMarket.CONGRESS,))
    chips.set_criteria(criteria)

    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()

    with qtbot.waitSignal(chips.resetRequested, timeout=1000):
        chips.click_clear_all()

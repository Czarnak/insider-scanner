"""Tests for stylesheet.py — QSS builder.

TDD: Written BEFORE implementation.
"""

from __future__ import annotations

import pytest

from insider_scanner.gui.theme.stylesheet import build_stylesheet
from insider_scanner.gui.theme.tokens import DARK, LIGHT

# Every selector the spec requires
REQUIRED_SELECTORS = [
    "QWidget",
    "QMainWindow",
    "QFrame#sidebar",
    "QFrame#topBar",
    "QToolButton#navigationButton",
    "QToolButton#navigationButton:checked",
    "QLabel#feedState",
    "QTabWidget::pane",
    "QTabBar::tab",
    "QTabBar::tab:selected",
    "QGroupBox",
    "QGroupBox::title",
    "QPushButton",
    "QPushButton:hover",
    "QPushButton:pressed",
    "QPushButton:disabled",
    "QPushButton:focus",
    "QLineEdit",
    "QComboBox",
    "QComboBox QAbstractItemView",
    "QDateEdit",
    "QSpinBox",
    "QDoubleSpinBox",
    "QCheckBox::indicator",
    "QCalendarWidget",
    "QTableView",
    "QTableView::item:selected",
    "QHeaderView::section",
    "QProgressBar",
    "QProgressBar::chunk",
    "QStatusBar",
    "QScrollBar",
    "QToolTip",
    "QMenuBar",
    "QMenu",
    "QSplitter::handle",
]


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_build_stylesheet_light_returns_string() -> None:
    result = build_stylesheet(LIGHT)
    assert isinstance(result, str)
    assert len(result) > 100  # non-trivial content


def test_build_stylesheet_dark_returns_string() -> None:
    result = build_stylesheet(DARK)
    assert isinstance(result, str)
    assert len(result) > 100


def test_build_stylesheet_does_not_raise_for_light() -> None:
    # Should never raise
    build_stylesheet(LIGHT)


def test_build_stylesheet_does_not_raise_for_dark() -> None:
    build_stylesheet(DARK)


# ---------------------------------------------------------------------------
# Required selectors presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("selector", REQUIRED_SELECTORS)
def test_light_stylesheet_contains_selector(selector: str) -> None:
    sheet = build_stylesheet(LIGHT)
    assert selector in sheet, f"Selector {selector!r} missing from LIGHT stylesheet"


@pytest.mark.parametrize("selector", REQUIRED_SELECTORS)
def test_dark_stylesheet_contains_selector(selector: str) -> None:
    sheet = build_stylesheet(DARK)
    assert selector in sheet, f"Selector {selector!r} missing from DARK stylesheet"


# ---------------------------------------------------------------------------
# Palette values are injected
# ---------------------------------------------------------------------------


def test_light_stylesheet_contains_accent_hex() -> None:
    sheet = build_stylesheet(LIGHT)
    assert LIGHT.accent.lower() in sheet.lower(), (
        f"LIGHT.accent ({LIGHT.accent}) not found in stylesheet"
    )


def test_dark_stylesheet_contains_accent_hex() -> None:
    sheet = build_stylesheet(DARK)
    assert DARK.accent.lower() in sheet.lower(), (
        f"DARK.accent ({DARK.accent}) not found in stylesheet"
    )


def test_light_stylesheet_contains_bg_page() -> None:
    sheet = build_stylesheet(LIGHT)
    assert LIGHT.bg_page.lower() in sheet.lower()


def test_dark_stylesheet_contains_bg_page() -> None:
    sheet = build_stylesheet(DARK)
    assert DARK.bg_page.lower() in sheet.lower()


# ---------------------------------------------------------------------------
# Structural checks — focus ring, border-bottom on selected tab
# ---------------------------------------------------------------------------


def test_stylesheet_contains_focus_ring() -> None:
    sheet = build_stylesheet(LIGHT)
    # focus rule must reference the focus_ring token value
    assert LIGHT.focus_ring.lower() in sheet.lower()


def test_stylesheet_selected_tab_has_border() -> None:
    # The spec says selected tab gets a 2px accent bottom-border
    sheet = build_stylesheet(LIGHT)
    assert "border-bottom" in sheet or "border_bottom" in sheet


def test_stylesheet_table_has_alternate_background() -> None:
    sheet = build_stylesheet(LIGHT)
    assert "alternate-background-color" in sheet


def test_stylesheet_table_has_selection() -> None:
    sheet = build_stylesheet(LIGHT)
    assert "selection-background-color" in sheet
    assert "selection-color" in sheet


# ---------------------------------------------------------------------------
# LIGHT and DARK produce different outputs
# ---------------------------------------------------------------------------


def test_light_dark_stylesheets_differ() -> None:
    assert build_stylesheet(LIGHT) != build_stylesheet(DARK)

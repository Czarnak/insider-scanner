"""QSS stylesheet builder for the Insider Scanner theme.

``build_stylesheet`` is a pure function: no Qt import, never raises.
"""

from __future__ import annotations

from insider_scanner.gui.theme.fonts import SANS_STACK
from insider_scanner.gui.theme.tokens import (
    FONT_SIZE_BASE,
    FONT_SIZE_SM,
    RADIUS_INPUT,
    RADIUS_PANEL,
    SPACING,
    ThemePalette,
)

# Convenient spacing aliases from the scale
_S1 = SPACING[0]  # 4
_S2 = SPACING[1]  # 8
_S3 = SPACING[2]  # 12
_S4 = SPACING[3]  # 16


def build_stylesheet(palette: ThemePalette) -> str:
    """Build and return a global QSS string for *palette*.

    The function is intentionally pure: it performs only string formatting
    and never imports or calls Qt APIs.  It will not raise for any valid
    ``ThemePalette`` instance.
    """
    p = palette  # short alias

    return f"""
/* ===== Global reset ===== */
QWidget {{
    background-color: {p.bg_page};
    color: {p.text_primary};
    font-family: {SANS_STACK};
    font-size: {FONT_SIZE_BASE}px;
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_fg};
    border: none;
    outline: none;
}}

QMainWindow {{
    background-color: {p.bg_page};
}}

/* ===== Tab Widget ===== */
QTabWidget::pane {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_PANEL}px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: {p.surface_elevated};
    color: {p.text_secondary};
    padding: {_S2}px {_S4}px;
    margin-right: 2px;
    border: 1px solid {p.border};
    border-bottom: none;
    border-top-left-radius: {RADIUS_INPUT}px;
    border-top-right-radius: {RADIUS_INPUT}px;
    font-size: {FONT_SIZE_SM}px;
}}

QTabBar::tab:selected {{
    background-color: {p.surface};
    color: {p.text_primary};
    border-color: {p.border};
    border-bottom: 2px solid {p.accent};
}}

QTabBar::tab:hover:!selected {{
    background-color: {p.surface_hover};
    color: {p.text_primary};
}}

/* ===== GroupBox ===== */
QGroupBox {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_PANEL}px;
    margin-top: {_S3}px;
    padding: {_S3}px {_S2}px {_S2}px {_S2}px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {_S2}px;
    top: 4px;
    color: {p.text_secondary};
    font-size: {FONT_SIZE_SM}px;
    background-color: transparent;
}}

/* ===== Push Button ===== */
QPushButton {{
    background-color: {p.accent};
    color: {p.accent_contrast};
    border: 1px solid {p.accent};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S3}px;
    font-size: {FONT_SIZE_BASE}px;
    min-height: 28px;
}}

QPushButton:hover {{
    background-color: {p.surface_hover};
    color: {p.text_primary};
    border-color: {p.border_strong};
}}

QPushButton:pressed {{
    background-color: {p.border};
    color: {p.text_primary};
    border-color: {p.border_strong};
}}

QPushButton:disabled {{
    background-color: {p.surface_elevated};
    color: {p.text_muted};
    border-color: {p.border};
}}

QPushButton:focus {{
    border: 2px solid {p.focus_ring};
    outline: none;
}}

/* ===== Line Edit ===== */
QLineEdit {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_fg};
    min-height: 28px;
}}

QLineEdit:focus {{
    border: 2px solid {p.focus_ring};
}}

QLineEdit:disabled {{
    background-color: {p.surface_elevated};
    color: {p.text_muted};
}}

/* ===== ComboBox ===== */
QComboBox {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    min-height: 28px;
}}

QComboBox:focus {{
    border: 2px solid {p.focus_ring};
}}

QComboBox:hover {{
    border-color: {p.border_strong};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_INPUT}px;
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_fg};
    outline: none;
}}

/* ===== DateEdit / SpinBox / DoubleSpinBox ===== */
QDateEdit {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    min-height: 28px;
}}

QDateEdit:focus {{
    border: 2px solid {p.focus_ring};
}}

QSpinBox {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    min-height: 28px;
}}

QSpinBox:focus {{
    border: 2px solid {p.focus_ring};
}}

QDoubleSpinBox {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    min-height: 28px;
}}

QDoubleSpinBox:focus {{
    border: 2px solid {p.focus_ring};
}}

/* ===== CheckBox ===== */
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {p.border_strong};
    border-radius: 3px;
    background-color: {p.surface};
}}

QCheckBox::indicator:checked {{
    background-color: {p.accent};
    border-color: {p.accent};
}}

QCheckBox::indicator:hover {{
    border-color: {p.accent};
}}

/* ===== Calendar Widget ===== */
QCalendarWidget {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_PANEL}px;
}}

QCalendarWidget QToolButton {{
    background-color: transparent;
    color: {p.text_primary};
    border: none;
    padding: {_S1}px;
}}

QCalendarWidget QMenu {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
}}

QCalendarWidget QSpinBox {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
}}

/* ===== Table View ===== */
QTableView {{
    background-color: {p.surface};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_PANEL}px;
    gridline-color: {p.border};
    alternate-background-color: {p.row_alt};
    selection-background-color: {p.selection_bg};
    selection-color: {p.selection_fg};
    outline: none;
}}

QTableView::item {{
    padding: {_S1}px {_S2}px;
    border: none;
}}

QTableView::item:selected {{
    background-color: {p.selection_bg};
    color: {p.selection_fg};
}}

QTableView::item:hover {{
    background-color: {p.surface_hover};
}}

/* ===== Header View ===== */
QHeaderView::section {{
    background-color: {p.surface_elevated};
    color: {p.text_secondary};
    border: none;
    border-right: 1px solid {p.border_strong};
    border-bottom: 1px solid {p.border_strong};
    padding: {_S1}px {_S2}px;
    font-size: {FONT_SIZE_SM}px;
    font-weight: bold;
}}

QHeaderView::section:first {{
    border-left: none;
}}

QHeaderView::section:checked {{
    background-color: {p.surface_hover};
}}

/* ===== Progress Bar ===== */
QProgressBar {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: {RADIUS_INPUT}px;
    text-align: center;
    min-height: 8px;
}}

QProgressBar::chunk {{
    background-color: {p.accent};
    border-radius: {RADIUS_INPUT}px;
}}

/* ===== Status Bar ===== */
QStatusBar {{
    background-color: {p.surface_elevated};
    color: {p.text_secondary};
    border-top: 1px solid {p.border};
    padding: 2px {_S2}px;
    font-size: {FONT_SIZE_SM}px;
}}

QStatusBar::item {{
    border: none;
}}

/* ===== Scroll Bar ===== */
QScrollBar {{
    background-color: {p.surface_elevated};
    border: none;
    border-radius: 4px;
}}

QScrollBar:vertical {{
    width: 8px;
    margin: 0;
}}

QScrollBar:horizontal {{
    height: 8px;
    margin: 0;
}}

QScrollBar::handle {{
    background-color: {p.border_strong};
    border-radius: 4px;
    min-height: 20px;
    min-width: 20px;
}}

QScrollBar::handle:hover {{
    background-color: {p.text_muted};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

/* ===== Tool Tip ===== */
QToolTip {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_INPUT}px;
    padding: {_S1}px {_S2}px;
    font-size: {FONT_SIZE_SM}px;
}}

/* ===== Menu Bar ===== */
QMenuBar {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
    border-bottom: 1px solid {p.border};
    padding: 2px;
}}

QMenuBar::item {{
    background-color: transparent;
    padding: {_S1}px {_S2}px;
    border-radius: {RADIUS_INPUT}px;
}}

QMenuBar::item:selected {{
    background-color: {p.surface_hover};
    color: {p.text_primary};
}}

QMenuBar::item:pressed {{
    background-color: {p.selection_bg};
    color: {p.selection_fg};
}}

/* ===== Menu ===== */
QMenu {{
    background-color: {p.surface_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_PANEL}px;
    padding: {_S1}px;
}}

QMenu::item {{
    padding: {_S2}px {_S4}px;
    border-radius: {RADIUS_INPUT}px;
}}

QMenu::item:selected {{
    background-color: {p.selection_bg};
    color: {p.selection_fg};
}}

QMenu::item:disabled {{
    color: {p.text_muted};
}}

QMenu::separator {{
    height: 1px;
    background-color: {p.border};
    margin: {_S1}px {_S2}px;
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background-color: {p.border};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background-color: {p.accent};
}}
"""

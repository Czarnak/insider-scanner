"""Insider Scanner GUI theme package.

Public API — import everything from here:

    from insider_scanner.gui.theme import (
        ThemeMode, ThemePalette, LIGHT, DARK,
        REQUIRED_TOKEN_GROUPS,
        build_stylesheet,
        ThemeManager, get_theme_manager,
        SANS_STACK, MONO_STACK,
        FONT_SIZE_SM, FONT_SIZE_BASE, FONT_SIZE_LG, FONT_SIZE_XL,
        SPACING, RADIUS_INPUT, RADIUS_PANEL, RADIUS_PILL,
        ROW_HEIGHT, CONTROL_MIN_HEIGHT, TARGET_HEIGHT,
        NUMERIC_COLS, IDENTIFIER_COLS, TEXT_COLS,
        column_alignment, column_is_monospace, cell_foreground,
    )
"""

from __future__ import annotations

from insider_scanner.gui.theme.fonts import MONO_STACK, SANS_STACK
from insider_scanner.gui.theme.manager import (
    ThemeManager,
    get_theme_manager,
    reset_theme_manager,
)
from insider_scanner.gui.theme.stylesheet import build_stylesheet
from insider_scanner.gui.theme.table_style import (
    IDENTIFIER_COLS,
    NUMERIC_COLS,
    TEXT_COLS,
    cell_foreground,
    column_alignment,
    column_is_monospace,
)
from insider_scanner.gui.theme.tokens import (
    CONTROL_MIN_HEIGHT,
    DARK,
    FONT_SIZE_BASE,
    FONT_SIZE_LG,
    FONT_SIZE_SM,
    FONT_SIZE_XL,
    LIGHT,
    RADIUS_INPUT,
    RADIUS_PANEL,
    RADIUS_PILL,
    REQUIRED_TOKEN_GROUPS,
    ROW_HEIGHT,
    SPACING,
    TARGET_HEIGHT,
    ThemeMode,
    ThemePalette,
)

__all__ = [
    # Core types
    "ThemeMode",
    "ThemePalette",
    # Palette instances
    "LIGHT",
    "DARK",
    # Token metadata
    "REQUIRED_TOKEN_GROUPS",
    # Stylesheet
    "build_stylesheet",
    # Manager
    "ThemeManager",
    "get_theme_manager",
    "reset_theme_manager",
    # Font stacks
    "SANS_STACK",
    "MONO_STACK",
    # Type scale
    "FONT_SIZE_SM",
    "FONT_SIZE_BASE",
    "FONT_SIZE_LG",
    "FONT_SIZE_XL",
    # Spacing / radii / layout
    "SPACING",
    "RADIUS_INPUT",
    "RADIUS_PANEL",
    "RADIUS_PILL",
    "ROW_HEIGHT",
    "CONTROL_MIN_HEIGHT",
    "TARGET_HEIGHT",
    # Table/cell helpers
    "NUMERIC_COLS",
    "IDENTIFIER_COLS",
    "TEXT_COLS",
    "column_alignment",
    "column_is_monospace",
    "cell_foreground",
]

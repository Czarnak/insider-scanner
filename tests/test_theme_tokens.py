"""Tests for tokens.py — ThemePalette, constants, and scale values.

TDD: Written BEFORE implementation.
"""

from __future__ import annotations

import re

import pytest

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

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ---------------------------------------------------------------------------
# REQUIRED_TOKEN_GROUPS completeness
# ---------------------------------------------------------------------------


def test_required_token_groups_non_empty() -> None:
    assert len(REQUIRED_TOKEN_GROUPS) > 0


def test_required_token_groups_excludes_name() -> None:
    assert "name" not in REQUIRED_TOKEN_GROUPS


def test_required_token_groups_are_valid_palette_fields() -> None:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(ThemePalette)}
    for token in REQUIRED_TOKEN_GROUPS:
        assert token in field_names, f"{token!r} not a ThemePalette field"


# ---------------------------------------------------------------------------
# LIGHT and DARK palettes have all required tokens as hex colors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", list(REQUIRED_TOKEN_GROUPS))
def test_light_palette_token_is_hex(token: str) -> None:
    value = getattr(LIGHT, token)
    assert isinstance(value, str), f"LIGHT.{token} is not a str: {value!r}"
    assert HEX_RE.match(value), (
        f"LIGHT.{token} = {value!r} is not a valid #RRGGBB hex color"
    )


@pytest.mark.parametrize("token", list(REQUIRED_TOKEN_GROUPS))
def test_dark_palette_token_is_hex(token: str) -> None:
    value = getattr(DARK, token)
    assert isinstance(value, str), f"DARK.{token} is not a str: {value!r}"
    assert HEX_RE.match(value), (
        f"DARK.{token} = {value!r} is not a valid #RRGGBB hex color"
    )


def test_light_and_dark_differ_on_bg_page() -> None:
    assert LIGHT.bg_page != DARK.bg_page


def test_light_name() -> None:
    assert LIGHT.name == "light"


def test_dark_name() -> None:
    assert DARK.name == "dark"


# ---------------------------------------------------------------------------
# ThemePalette is frozen (immutable)
# ---------------------------------------------------------------------------


def test_palette_is_frozen() -> None:
    with pytest.raises((AttributeError, TypeError)):
        LIGHT.bg_page = "#000000"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ThemePalette.color() returns a QColor
# ---------------------------------------------------------------------------


def test_palette_color_returns_qcolor(qapp) -> None:
    from PySide6.QtGui import QColor

    result = LIGHT.color("accent")
    assert isinstance(result, QColor)
    assert result.isValid()


def test_palette_color_matches_hex(qapp) -> None:
    from PySide6.QtGui import QColor

    expected = QColor(LIGHT.accent)
    result = LIGHT.color("accent")
    assert result.name().lower() == expected.name().lower()


def test_palette_color_invalid_token_raises(qapp) -> None:
    with pytest.raises((AttributeError, KeyError)):
        LIGHT.color("nonexistent_token_xyz")


# ---------------------------------------------------------------------------
# SPACING constant
# ---------------------------------------------------------------------------


def test_spacing_exact() -> None:
    assert SPACING == (4, 8, 12, 16, 24, 32)


def test_spacing_is_tuple() -> None:
    assert isinstance(SPACING, tuple)


# ---------------------------------------------------------------------------
# Radius constants
# ---------------------------------------------------------------------------


def test_radius_input() -> None:
    assert RADIUS_INPUT == 5


def test_radius_panel() -> None:
    assert RADIUS_PANEL == 8


def test_radius_pill() -> None:
    assert RADIUS_PILL == 9999


def test_radius_ordering() -> None:
    assert RADIUS_INPUT <= RADIUS_PANEL <= RADIUS_PILL


# ---------------------------------------------------------------------------
# Font size scale (monotonic, ~1.125 ratio)
# ---------------------------------------------------------------------------


def test_font_size_base() -> None:
    assert FONT_SIZE_BASE == 13


def test_font_size_scale_monotonic() -> None:
    assert FONT_SIZE_SM <= FONT_SIZE_BASE <= FONT_SIZE_LG <= FONT_SIZE_XL


def test_font_size_sm_smaller_than_base() -> None:
    assert FONT_SIZE_SM < FONT_SIZE_BASE


def test_font_size_lg_larger_than_base() -> None:
    assert FONT_SIZE_LG > FONT_SIZE_BASE


def test_font_size_xl_larger_than_lg() -> None:
    assert FONT_SIZE_XL > FONT_SIZE_LG


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------


def test_row_height() -> None:
    assert ROW_HEIGHT == 28


def test_control_min_height() -> None:
    assert CONTROL_MIN_HEIGHT == 28


def test_target_height() -> None:
    assert TARGET_HEIGHT == 44


# ---------------------------------------------------------------------------
# ThemeMode enum
# ---------------------------------------------------------------------------


def test_theme_mode_members() -> None:
    names = {m.name for m in ThemeMode}
    assert "SYSTEM" in names
    assert "LIGHT" in names
    assert "DARK" in names

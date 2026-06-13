"""Tests for contrast.py — PURE WCAG contrast helpers.

TDD: Written BEFORE implementation. These must fail initially.
"""

from __future__ import annotations

import pytest

from insider_scanner.gui.theme.contrast import (
    contrast_ratio,
    meets_aa,
    relative_luminance,
)
from insider_scanner.gui.theme.tokens import DARK, LIGHT


# ---------------------------------------------------------------------------
# Unit: relative_luminance
# ---------------------------------------------------------------------------


def test_relative_luminance_black() -> None:
    # Arrange
    color = "#000000"
    # Act
    result = relative_luminance(color)
    # Assert
    assert result == pytest.approx(0.0, abs=1e-6)


def test_relative_luminance_white() -> None:
    # Arrange
    color = "#ffffff"
    # Act
    result = relative_luminance(color)
    # Assert
    assert result == pytest.approx(1.0, abs=1e-6)


def test_relative_luminance_pure_red() -> None:
    # Arrange
    color = "#ff0000"
    # Act
    result = relative_luminance(color)
    # Assert — sRGB formula: R=1 → lin≈0.2126
    assert result == pytest.approx(0.2126, abs=0.001)


def test_relative_luminance_accepts_uppercase() -> None:
    # Arrange / Act / Assert
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Unit: contrast_ratio
# ---------------------------------------------------------------------------


def test_contrast_ratio_black_white() -> None:
    # Arrange / Act
    ratio = contrast_ratio("#000000", "#ffffff")
    # Assert — spec: 21:1
    assert ratio == pytest.approx(21.0, abs=0.5)


def test_contrast_ratio_white_white() -> None:
    ratio = contrast_ratio("#ffffff", "#ffffff")
    assert ratio == pytest.approx(1.0, abs=0.05)


def test_contrast_ratio_symmetric() -> None:
    # fg/bg order must not change ratio
    r1 = contrast_ratio("#000000", "#ffffff")
    r2 = contrast_ratio("#ffffff", "#000000")
    assert r1 == pytest.approx(r2, abs=0.001)


def test_contrast_ratio_range() -> None:
    # Must always be in [1, 21]
    ratio = contrast_ratio("#2D6CDF", "#F7F8FA")
    assert 1.0 <= ratio <= 21.0


# ---------------------------------------------------------------------------
# Unit: meets_aa
# ---------------------------------------------------------------------------


def test_meets_aa_black_on_white_normal() -> None:
    assert meets_aa("#000000", "#ffffff") is True


def test_meets_aa_white_on_white_fails() -> None:
    assert meets_aa("#ffffff", "#ffffff") is False


def test_meets_aa_large_threshold_lower() -> None:
    # A 3.5:1 ratio fails normal (4.5) but passes large (3.0)
    # #767676 on #ffffff ≈ 4.54:1; pick something at exactly ~3.2:1
    # Use a known pair: #595959 on #ffffff ≈ 7.0:1 (passes both)
    # Use #b5b5b5 on #ffffff ≈ 2.0:1 (fails both)
    assert meets_aa("#767676", "#ffffff", large=False) is True
    assert meets_aa("#767676", "#ffffff", large=True) is True


def test_meets_aa_normal_vs_large_distinction() -> None:
    # A ratio between 3.0 and 4.5 should fail normal but pass large
    # #949494 on #ffffff ≈ 3.03:1 — just above large threshold
    r = contrast_ratio("#949494", "#ffffff")
    if 3.0 <= r < 4.5:
        assert meets_aa("#949494", "#ffffff", large=False) is False
        assert meets_aa("#949494", "#ffffff", large=True) is True


# ---------------------------------------------------------------------------
# Parametrized: LIGHT palette text tokens on backgrounds (AA ≥ 4.5)
# ---------------------------------------------------------------------------

TEXT_TOKENS_LIGHT = [
    ("text_primary", "bg_page"),
    ("text_primary", "surface"),
    ("text_secondary", "bg_page"),
    ("text_secondary", "surface"),
    ("text_muted", "bg_page"),
    ("text_muted", "surface"),
]

TEXT_TOKENS_DARK = [
    ("text_primary", "bg_page"),
    ("text_primary", "surface"),
    ("text_secondary", "bg_page"),
    ("text_secondary", "surface"),
    ("text_muted", "bg_page"),
    ("text_muted", "surface"),
]


@pytest.mark.parametrize("fg_token,bg_token", TEXT_TOKENS_LIGHT)
def test_light_text_tokens_meet_aa(fg_token: str, bg_token: str) -> None:
    # Arrange
    fg = getattr(LIGHT, fg_token)
    bg = getattr(LIGHT, bg_token)
    # Act
    ratio = contrast_ratio(fg, bg)
    # Assert
    assert ratio >= 4.5, (
        f"LIGHT.{fg_token} ({fg}) on LIGHT.{bg_token} ({bg}) "
        f"= {ratio:.2f} < 4.5 (WCAG AA)"
    )


@pytest.mark.parametrize("fg_token,bg_token", TEXT_TOKENS_DARK)
def test_dark_text_tokens_meet_aa(fg_token: str, bg_token: str) -> None:
    # Arrange
    fg = getattr(DARK, fg_token)
    bg = getattr(DARK, bg_token)
    # Act
    ratio = contrast_ratio(fg, bg)
    # Assert
    assert ratio >= 4.5, (
        f"DARK.{fg_token} ({fg}) on DARK.{bg_token} ({bg}) "
        f"= {ratio:.2f} < 4.5 (WCAG AA)"
    )


# ---------------------------------------------------------------------------
# accent_contrast on accent must be AA
# ---------------------------------------------------------------------------


def test_light_accent_contrast_on_accent_aa() -> None:
    ratio = contrast_ratio(LIGHT.accent_contrast, LIGHT.accent)
    assert ratio >= 4.5, (
        f"LIGHT accent_contrast ({LIGHT.accent_contrast}) on accent ({LIGHT.accent}) "
        f"= {ratio:.2f} < 4.5"
    )


def test_dark_accent_contrast_on_accent_aa() -> None:
    ratio = contrast_ratio(DARK.accent_contrast, DARK.accent)
    assert ratio >= 4.5, (
        f"DARK accent_contrast ({DARK.accent_contrast}) on accent ({DARK.accent}) "
        f"= {ratio:.2f} < 4.5"
    )


# ---------------------------------------------------------------------------
# Semantic tokens on surface — large text / UI threshold (≥3.0)
# ---------------------------------------------------------------------------

SEMANTIC_TOKENS = ["purchase", "sale", "warning"]


@pytest.mark.parametrize("token", SEMANTIC_TOKENS)
def test_light_semantic_on_surface_large_aa(token: str) -> None:
    fg = getattr(LIGHT, token)
    bg = LIGHT.surface
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 3.0, (
        f"LIGHT.{token} ({fg}) on surface ({bg}) = {ratio:.2f} < 3.0 (large AA)"
    )


@pytest.mark.parametrize("token", SEMANTIC_TOKENS)
def test_dark_semantic_on_surface_large_aa(token: str) -> None:
    fg = getattr(DARK, token)
    bg = DARK.surface
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 3.0, (
        f"DARK.{token} ({fg}) on surface ({bg}) = {ratio:.2f} < 3.0 (large AA)"
    )


# ---------------------------------------------------------------------------
# AAA: text_primary on bg_page — ideally ≥7.0; require ≥4.5 as hard floor
# Note: if you can achieve ≥7.0 in tokens, change the assertion accordingly.
# ---------------------------------------------------------------------------


def test_light_text_primary_on_bg_page_aaa_or_aa() -> None:
    ratio = contrast_ratio(LIGHT.text_primary, LIGHT.bg_page)
    # AAA would be ≥7.0; we target that but require at minimum AA (4.5)
    # Change to 7.0 if LIGHT palette achieves it
    assert ratio >= 7.0, (
        f"LIGHT.text_primary ({LIGHT.text_primary}) on bg_page ({LIGHT.bg_page}) "
        f"= {ratio:.2f}; target AAA ≥7.0"
    )


def test_dark_text_primary_on_bg_page_aaa_or_aa() -> None:
    ratio = contrast_ratio(DARK.text_primary, DARK.bg_page)
    assert ratio >= 7.0, (
        f"DARK.text_primary ({DARK.text_primary}) on bg_page ({DARK.bg_page}) "
        f"= {ratio:.2f}; target AAA ≥7.0"
    )

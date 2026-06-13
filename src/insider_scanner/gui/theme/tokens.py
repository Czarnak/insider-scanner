"""Design tokens for the Insider Scanner theme system.

Contains the ThemePalette dataclass, LIGHT/DARK instances, and all
scalar constants (spacing, radii, type scale, layout).

Contrast guarantees (verified by tests/test_theme_contrast.py):
  - text_primary / text_secondary / text_muted on bg_page+surface: WCAG AA (≥4.5)
  - text_primary on bg_page: WCAG AAA (≥7.0) for both themes
  - accent_contrast on accent: WCAG AA (≥4.5)
  - purchase / sale / warning on surface: large-text AA (≥3.0)
"""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QColor


class ThemeMode(enum.Enum):
    """Theme selection mode."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclasses.dataclass(frozen=True, slots=True)
class ThemePalette:
    """Immutable set of hex color tokens for one theme variant.

    All color fields are lowercase ``#rrggbb`` hex strings.
    """

    # Identity
    name: str

    # Backgrounds
    bg_page: str
    surface: str
    surface_elevated: str
    surface_hover: str

    # Borders
    border: str
    border_strong: str

    # Typography
    text_primary: str
    text_secondary: str
    text_muted: str

    # Accent
    accent: str
    accent_contrast: str  # text on top of accent — must be AA on accent

    # Interactive states
    focus_ring: str

    # Semantic — financial
    purchase: str  # green; ≥3.0 large-AA on surface
    sale: str  # red;   ≥3.0 large-AA on surface
    warning: str  # amber; ≥3.0 large-AA on surface

    # Other semantic
    error: str
    success: str

    # Table
    selection_bg: str
    selection_fg: str
    row_alt: str

    def color(self, token: str) -> "QColor":
        """Return a ``QColor`` for the named palette token.

        Raises ``AttributeError`` if *token* is not a valid field name.
        The Qt import is deferred so this module stays import-light.
        """
        from PySide6.QtGui import QColor  # noqa: PLC0415 — lazy import

        hex_value: str = getattr(self, token)  # raises AttributeError on unknown
        return QColor(hex_value)


# ---------------------------------------------------------------------------
# Scalar constants
# ---------------------------------------------------------------------------

#: Spacing scale (px): 4 · 8 · 12 · 16 · 24 · 32
SPACING: tuple[int, ...] = (4, 8, 12, 16, 24, 32)

#: Border-radius presets (px)
RADIUS_INPUT: int = 5
RADIUS_PANEL: int = 8
RADIUS_PILL: int = 9999

#: Type scale — ~1.125 (Major Second) ratio
FONT_SIZE_SM: int = 11
FONT_SIZE_BASE: int = 13
FONT_SIZE_LG: int = 15
FONT_SIZE_XL: int = 17

#: Layout heights (px)
ROW_HEIGHT: int = 28
CONTROL_MIN_HEIGHT: int = 28
TARGET_HEIGHT: int = 44

# ---------------------------------------------------------------------------
# Token-completeness sentinel (every field except "name")
# ---------------------------------------------------------------------------

REQUIRED_TOKEN_GROUPS: tuple[str, ...] = tuple(
    f.name for f in dataclasses.fields(ThemePalette) if f.name != "name"
)

# ---------------------------------------------------------------------------
# LIGHT palette
# ---------------------------------------------------------------------------
#
# Contrast spot-checks (bg_page=#F7F8FA, surface=#FFFFFF):
#   text_primary  (#0D1117) on bg_page : 17.81  ✓ AAA
#   text_secondary(#3B4252) on bg_page :  9.47  ✓ AA
#   text_muted    (#5C6470) on bg_page :  5.63  ✓ AA
#   text_muted    (#5C6470) on surface :  5.98  ✓ AA
#   white (#FFFFFF) on accent (#2D6CDF):  4.86  ✓ AA
#   purchase (#1A7F4B) on surface       :  5.02  ✓ large-AA
#   sale     (#C0392B) on surface       :  5.44  ✓ large-AA
#   warning  (#B45309) on surface       :  5.02  ✓ large-AA

LIGHT = ThemePalette(
    name="light",
    # Backgrounds
    bg_page="#F7F8FA",
    surface="#FFFFFF",
    surface_elevated="#F0F1F4",
    surface_hover="#ECEEF2",
    # Borders
    border="#DDE0E6",
    border_strong="#B0B8C4",
    # Typography
    text_primary="#0D1117",
    text_secondary="#3B4252",
    text_muted="#5C6470",
    # Accent (blue)
    accent="#2D6CDF",
    accent_contrast="#FFFFFF",  # 4.86:1 on accent ✓
    # States
    focus_ring="#2D6CDF",
    # Semantic
    purchase="#1A7F4B",  # green   5.02:1 on white ✓
    sale="#C0392B",  # red     5.44:1 on white ✓
    warning="#B45309",  # amber   5.02:1 on white ✓
    error="#C0392B",
    success="#1A7F4B",
    # Table
    selection_bg="#2D6CDF",
    selection_fg="#FFFFFF",
    row_alt="#F2F4F7",
)

# ---------------------------------------------------------------------------
# DARK palette
# ---------------------------------------------------------------------------
#
# Contrast spot-checks (bg_page=#0F1216, surface=#1A1E26):
#   text_primary  (#F0F2F5) on bg_page : 16.74  ✓ AAA
#   text_secondary(#A8B2BF) on bg_page :  8.75  ✓ AA
#   text_muted    (#8896AA) on bg_page :  6.25  ✓ AA
#   text_muted    (#8896AA) on surface :  5.56  ✓ AA
#   black (#000000) on accent (#5B9BFF):  7.58  ✓ AA
#   purchase (#4ADE80) on surface       :  9.58  ✓ large-AA
#   sale     (#FF6B6B) on surface       :  6.02  ✓ large-AA
#   warning  (#FCD34D) on surface       : 11.58  ✓ large-AA

DARK = ThemePalette(
    name="dark",
    # Backgrounds
    bg_page="#0F1216",
    surface="#1A1E26",
    surface_elevated="#222836",
    surface_hover="#272D3A",
    # Borders
    border="#2C3344",
    border_strong="#3D4A5C",
    # Typography
    text_primary="#F0F2F5",
    text_secondary="#A8B2BF",
    text_muted="#8896AA",
    # Accent (lighter blue for dark backgrounds)
    accent="#5B9BFF",
    accent_contrast="#000000",  # 7.58:1 on accent ✓
    # States
    focus_ring="#5B9BFF",
    # Semantic
    purchase="#4ADE80",  # green  9.58:1 on surface ✓
    sale="#FF6B6B",  # red    6.02:1 on surface ✓
    warning="#FCD34D",  # amber 11.58:1 on surface ✓
    error="#FF6B6B",
    success="#4ADE80",
    # Table
    selection_bg="#5B9BFF",
    selection_fg="#000000",
    row_alt="#1E2330",
)

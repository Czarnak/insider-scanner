"""WCAG contrast helpers — PURE, no Qt/PySide6 imports.

All functions operate on hex color strings (#RRGGBB).
"""

from __future__ import annotations


def _parse_hex(hex_color: str) -> tuple[int, int, int]:
    """Parse #RRGGBB (or #rrggbb) → (r, g, b) ints 0-255."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _linearize(channel: int) -> float:
    """Convert a single 8-bit sRGB channel to linear light (WCAG formula)."""
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a hex color, in [0, 1].

    Uses the standard sRGB → linear → luminance formula from
    https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
    """
    r, g, b = _parse_hex(hex_color)
    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio in [1.0, 21.0].

    Argument order does not matter — the function returns the same value
    regardless of which color is foreground vs. background.
    """
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def meets_aa(fg: str, bg: str, large: bool = False) -> bool:
    """Return True when the fg/bg pair passes WCAG AA.

    Args:
        fg: Foreground hex color.
        bg: Background hex color.
        large: If True, use large-text threshold (≥3.0); otherwise normal (≥4.5).
    """
    ratio = contrast_ratio(fg, bg)
    threshold = 3.0 if large else 4.5
    return ratio >= threshold

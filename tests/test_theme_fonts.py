"""Tests for fonts.py — font stacks and font loader.

TDD: Written BEFORE implementation.
"""

from __future__ import annotations

from insider_scanner.gui.theme.fonts import (
    MONO_STACK,
    SANS_STACK,
    load_application_fonts,
)


# ---------------------------------------------------------------------------
# Font stack constants
# ---------------------------------------------------------------------------


def test_sans_stack_contains_segoe_ui() -> None:
    assert "Segoe UI" in SANS_STACK


def test_sans_stack_contains_inter() -> None:
    assert "Inter" in SANS_STACK


def test_sans_stack_contains_sans_serif_fallback() -> None:
    assert "sans-serif" in SANS_STACK


def test_mono_stack_contains_consolas() -> None:
    assert "Consolas" in MONO_STACK


def test_mono_stack_contains_jetbrains_mono() -> None:
    assert "JetBrains Mono" in MONO_STACK


def test_mono_stack_contains_monospace_fallback() -> None:
    assert "monospace" in MONO_STACK


# ---------------------------------------------------------------------------
# load_application_fonts
# ---------------------------------------------------------------------------


def test_load_application_fonts_returns_list(qapp) -> None:
    result = load_application_fonts()
    assert isinstance(result, list)


def test_load_application_fonts_does_not_raise_when_no_fonts(qapp) -> None:
    # Even if the fonts package/dir doesn't exist, must return [] gracefully
    result = load_application_fonts()
    # It's valid to be empty (no bundled fonts yet)
    assert result is not None


def test_load_application_fonts_all_strings(qapp) -> None:
    result = load_application_fonts()
    for item in result:
        assert isinstance(item, str), f"Expected str family name, got {type(item)}"


# ---------------------------------------------------------------------------
# Font loader with mocked font files (covers lines 34, 39-54)
# ---------------------------------------------------------------------------


def test_load_application_fonts_skips_non_ttf(qapp, tmp_path, monkeypatch) -> None:
    """Non-.ttf entries are silently skipped; list stays empty."""
    import importlib.resources as pkg_resources

    # Build a fake Traversable entry for a .json file
    class FakeEntry:
        name = "metadata.json"

        def read_bytes(self) -> bytes:  # pragma: no cover
            return b""

    class FakePkg:
        def iterdir(self):
            return iter([FakeEntry()])

    monkeypatch.setattr(pkg_resources, "files", lambda *_: FakePkg())
    result = load_application_fonts()
    assert result == []


def test_load_application_fonts_handles_bad_font_data(qapp, monkeypatch) -> None:
    """A .ttf entry that raises on read_bytes is caught; loader continues."""
    import importlib.resources as pkg_resources

    class BadEntry:
        name = "broken.ttf"

        def read_bytes(self) -> bytes:
            raise OSError("simulated read error")

    class FakePkg:
        def iterdir(self):
            return iter([BadEntry()])

    monkeypatch.setattr(pkg_resources, "files", lambda *_: FakePkg())
    result = load_application_fonts()
    # Must not raise and return empty (bad data was skipped)
    assert isinstance(result, list)


def test_load_application_fonts_handles_rejected_font(qapp, monkeypatch) -> None:
    """Qt returning font_id == -1 is handled gracefully (logs warning, no crash)."""
    import importlib.resources as pkg_resources

    class GoodEntry:
        name = "dummy.ttf"

        def read_bytes(self) -> bytes:
            # 4 bytes of junk — Qt will reject it (font_id == -1)
            return b"\x00\x01\x02\x03"

    class FakePkg:
        def iterdir(self):
            return iter([GoodEntry()])

    monkeypatch.setattr(pkg_resources, "files", lambda *_: FakePkg())
    result = load_application_fonts()
    assert isinstance(result, list)

"""Font stacks and application font loader."""

from __future__ import annotations

from insider_scanner.utils.logging import get_logger

_log = get_logger("gui.theme.fonts")

SANS_STACK: str = '"Inter", "Segoe UI", "Helvetica Neue", sans-serif'
MONO_STACK: str = (
    '"JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New", monospace'
)


def load_application_fonts() -> list[str]:
    """Register any bundled ``*.ttf`` files with Qt's font database.

    Iterates ``insider_scanner.resources.fonts`` via *importlib.resources*,
    registers each file with ``QFontDatabase.addApplicationFontFromData``, and
    returns the list of resolved family names.

    Never raises: per-file errors are logged as warnings and skipped.
    Returns an empty list when the fonts package or directory does not exist.
    """
    from PySide6.QtGui import QFontDatabase  # noqa: PLC0415 — lazy Qt import

    families: list[str] = []

    try:
        import importlib.resources as pkg_resources

        fonts_pkg = pkg_resources.files("insider_scanner.resources.fonts")
        # files() returns a Traversable; .iterdir() may raise if absent
        font_files = list(fonts_pkg.iterdir())
    except (ModuleNotFoundError, FileNotFoundError, TypeError, AttributeError):
        # Package or directory does not exist yet — graceful no-op
        return families

    for entry in font_files:
        name = getattr(entry, "name", str(entry))
        if not name.lower().endswith(".ttf"):
            continue
        try:
            data = entry.read_bytes()
            font_id = QFontDatabase.addApplicationFontFromData(data)
            if font_id >= 0:
                for family in QFontDatabase.applicationFontFamilies(font_id):
                    families.append(family)
            else:
                _log.warning("Qt rejected font file: %s", name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not load font %s: %s", name, exc)

    return families

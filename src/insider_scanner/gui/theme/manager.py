"""ThemeManager — applies and persists the active theme.

Singleton access via ``get_theme_manager()``.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

try:  # shiboken6 ships with PySide6; guard so non-Qt envs still import.
    from shiboken6 import isValid as _is_valid
except ImportError:  # pragma: no cover - PySide6 always provides shiboken6

    def _is_valid(_obj: object) -> bool:
        return True


from insider_scanner.gui.theme.fonts import load_application_fonts
from insider_scanner.gui.theme.stylesheet import build_stylesheet
from insider_scanner.gui.theme.tokens import DARK, LIGHT, ThemeMode, ThemePalette
from insider_scanner.utils.logging import get_logger

_log = get_logger("gui.theme.manager")


class ThemeManager(QObject):
    """Manages the application-wide visual theme.

    Responsibilities:
    - Hold the current ``ThemeMode`` and resolved ``ThemePalette``.
    - Apply QSS + re-polish all top-level widgets on demand.
    - Persist and restore the user's mode choice via ``QSettings``.
    - React to OS color-scheme changes when mode is ``SYSTEM``.

    ``install(app)`` must be called once during startup.  Every public method
    is fully guarded and will never propagate an exception.
    """

    paletteChanged = Signal(object)  # emits ThemePalette

    def __init__(self) -> None:
        super().__init__()
        self._mode: ThemeMode = ThemeMode.SYSTEM
        self._palette: ThemePalette = LIGHT  # safe sentinel until apply()
        self._style_hints = None  # set in install() when colorSchemeChanged connects

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self, app: object) -> None:  # noqa: C901
        """One-time setup — tolerates stub app objects (only setApplicationName required).

        Guards every call with ``hasattr`` so a ``DummyApp`` (test stub) never
        triggers an ``AttributeError``.
        """
        try:
            if hasattr(app, "setStyle"):
                app.setStyle("Fusion")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            _log.warning("setStyle failed: %s", exc)

        try:
            if hasattr(app, "setPalette"):
                fusion_palette = self._build_fusion_palette()
                if fusion_palette is not None:
                    app.setPalette(fusion_palette)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            _log.warning("setPalette failed: %s", exc)

        try:
            load_application_fonts()
        except Exception as exc:  # noqa: BLE001
            _log.warning("load_application_fonts failed: %s", exc)

        # Restore persisted mode
        try:
            stored = QSettings().value("theme/mode")
            if stored and stored in ThemeMode.__members__:
                self._mode = ThemeMode[stored]
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not read theme/mode from QSettings: %s", exc)

        # Connect OS color-scheme changes (Qt 6.5+)
        try:
            if hasattr(app, "styleHints"):
                hints = app.styleHints()  # type: ignore[union-attr]
                if hasattr(hints, "colorSchemeChanged"):
                    hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
                    self._style_hints = hints
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not connect colorSchemeChanged: %s", exc)

        try:
            self.apply()
        except Exception as exc:  # noqa: BLE001
            _log.warning("apply() in install() failed: %s", exc)

    def set_mode(self, mode: ThemeMode) -> None:
        """Switch to *mode*, persist, and re-apply the theme."""
        self._mode = mode
        try:
            QSettings().setValue("theme/mode", mode.name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("QSettings.setValue failed: %s", exc)
        self.apply()

    def mode(self) -> ThemeMode:
        """Return the currently configured ``ThemeMode``."""
        return self._mode

    def palette(self) -> ThemePalette:
        """Return the last resolved ``ThemePalette``.

        Safe to call with no running ``QApplication`` — returns ``LIGHT``.
        """
        return self._palette

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self) -> ThemePalette:
        """Map the current mode to a concrete ``ThemePalette``."""
        if self._mode is ThemeMode.LIGHT:
            return LIGHT
        if self._mode is ThemeMode.DARK:
            return DARK

        # SYSTEM — read OS preference
        try:
            from PySide6.QtCore import Qt  # noqa: PLC0415

            app = QApplication.instance()
            if app is None:
                return LIGHT
            hints = getattr(app, "styleHints", None)
            if hints is None:
                return LIGHT
            sh = hints()
            scheme = getattr(sh, "colorScheme", None)
            if scheme is None:
                return LIGHT
            if scheme() == Qt.ColorScheme.Dark:
                return DARK
        except Exception as exc:  # noqa: BLE001
            _log.debug("_resolve SYSTEM fallback: %s", exc)

        return LIGHT

    def apply(self) -> None:
        """Resolve the palette, set QSS, and re-polish top-level widgets."""
        try:
            self._palette = self._resolve()
        except Exception as exc:  # noqa: BLE001
            _log.warning("_resolve failed: %s", exc)
            self._palette = LIGHT

        try:
            app = QApplication.instance()
            if app is not None and hasattr(app, "setStyleSheet"):
                app.setStyleSheet(build_stylesheet(self._palette))
        except Exception as exc:  # noqa: BLE001
            _log.warning("setStyleSheet failed: %s", exc)

        try:
            app = QApplication.instance()
            if app is not None and hasattr(app, "topLevelWidgets"):
                for widget in list(app.topLevelWidgets()):
                    if not _is_valid(widget) or not widget.isVisible():
                        continue
                    try:
                        widget.style().unpolish(widget)
                        widget.style().polish(widget)
                        widget.update()
                    except Exception as exc:  # noqa: BLE001
                        _log.debug("Re-polish skipped for widget: %s", exc)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Re-polish loop failed: %s", exc)

        try:
            self.paletteChanged.emit(self._palette)
        except Exception as exc:  # noqa: BLE001
            _log.warning("paletteChanged.emit failed: %s", exc)

    def _on_color_scheme_changed(self) -> None:
        """Slot: re-apply when the OS switches color scheme (SYSTEM mode only)."""
        if self._mode is ThemeMode.SYSTEM:
            self.apply()

    def _disconnect_color_scheme(self) -> None:
        """Disconnect the OS ``colorSchemeChanged`` slot, if connected."""
        hints = self._style_hints
        self._style_hints = None
        if hints is None or not _is_valid(hints):
            return
        try:
            hints.colorSchemeChanged.disconnect(self._on_color_scheme_changed)
        except (RuntimeError, TypeError) as exc:
            _log.debug("colorSchemeChanged disconnect skipped: %s", exc)

    def _build_fusion_palette(self):  # type: ignore[return]
        """Build a QPalette from palette tokens for the Fusion style.

        Returns ``None`` on any error so the caller can guard.
        """
        try:
            from PySide6.QtGui import QColor, QPalette  # noqa: PLC0415

            p = self._resolve()
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, QColor(p.bg_page))
            pal.setColor(QPalette.ColorRole.WindowText, QColor(p.text_primary))
            pal.setColor(QPalette.ColorRole.Base, QColor(p.surface))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.row_alt))
            pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface_elevated))
            pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text_primary))
            pal.setColor(QPalette.ColorRole.Text, QColor(p.text_primary))
            pal.setColor(QPalette.ColorRole.Button, QColor(p.surface_elevated))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor(p.text_primary))
            pal.setColor(QPalette.ColorRole.BrightText, QColor(p.accent_contrast))
            pal.setColor(QPalette.ColorRole.Link, QColor(p.accent))
            pal.setColor(QPalette.ColorRole.Highlight, QColor(p.selection_bg))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.selection_fg))
            return pal
        except Exception as exc:  # noqa: BLE001
            _log.warning("_build_fusion_palette failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_manager: ThemeManager | None = None


def get_theme_manager() -> ThemeManager:
    """Return the process-wide ``ThemeManager`` singleton.

    Creates the instance on first call.  Safe to call even before a
    ``QApplication`` is constructed (returns manager with LIGHT palette).
    """
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = ThemeManager()
    return _manager


def reset_theme_manager() -> None:
    """Tear down the process-wide singleton.

    Disconnects the OS color-scheme slot and drops the instance so the next
    ``get_theme_manager()`` builds a clean manager.  Primarily used by tests to
    isolate the singleton between cases and avoid signal accumulation.
    """
    global _manager  # noqa: PLW0603
    if _manager is not None:
        _manager._disconnect_color_scheme()
        _manager = None

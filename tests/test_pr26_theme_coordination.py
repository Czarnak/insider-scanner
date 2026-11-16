"""Regression coverage for theme coordination across native and custom widgets."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPalette

from insider_scanner.gui.alerts_page import AlertsPage
from insider_scanner.gui.feed_filters import FeedFilterPanel
from insider_scanner.gui.theme import DARK, LIGHT, ThemeMode, get_theme_manager
from insider_scanner.gui.theme.manager import ThemeManager
from insider_scanner.persistence.feed import FeedMarket, FeedRecord
from insider_scanner.services.alerts import AlertHit


@pytest.fixture
def theme_settings(monkeypatch):
    values: dict[str, str] = {}

    class Settings:
        def value(self, key):
            return values.get(key)

        def setValue(self, key, value):
            values[key] = value

    monkeypatch.setattr("insider_scanner.gui.theme.manager.QSettings", Settings)
    return values


def _assert_native_palette(qapp, tokens):
    native = qapp.palette()
    for role, expected in (
        (QPalette.ColorRole.Window, tokens.bg_page),
        (QPalette.ColorRole.Base, tokens.surface),
        (QPalette.ColorRole.Text, tokens.text_primary),
        (QPalette.ColorRole.Highlight, tokens.selection_bg),
    ):
        assert native.color(role).name() == expected.lower()
    assert f"background-color: {tokens.bg_page};" in qapp.styleSheet()


def test_install_restores_saved_mode_before_first_native_palette(
    qapp, monkeypatch, theme_settings
):
    theme_settings["theme/mode"] = "DARK"
    manager = ThemeManager()
    monkeypatch.setattr(
        manager, "_resolve", lambda: DARK if manager.mode() is ThemeMode.DARK else LIGHT
    )
    palettes = []
    apply_native = qapp.setPalette

    def capture(palette):
        palettes.append(palette.color(QPalette.ColorRole.Window).name())
        apply_native(palette)

    monkeypatch.setattr(qapp, "setPalette", capture)
    manager.install(qapp)
    try:
        assert manager.mode() is ThemeMode.DARK
        assert palettes == [DARK.bg_page.lower()]
        _assert_native_palette(qapp, DARK)
    finally:
        manager._disconnect_color_scheme()


def test_explicit_theme_switch_updates_native_palette_and_stylesheet(
    qapp, theme_settings
):
    manager = ThemeManager()
    for mode, tokens in (
        (ThemeMode.DARK, DARK),
        (ThemeMode.LIGHT, LIGHT),
        (ThemeMode.DARK, DARK),
    ):
        manager.set_mode(mode)
        _assert_native_palette(qapp, tokens)


def test_system_theme_change_updates_native_palette(qapp, monkeypatch, theme_settings):
    class Hints(QObject):
        colorSchemeChanged = Signal(object)
        scheme = Qt.ColorScheme.Light

        def colorScheme(self):
            return self.scheme

    hints = Hints()
    monkeypatch.setattr(qapp, "styleHints", lambda: hints)
    manager = ThemeManager()
    manager.install(qapp)
    try:
        _assert_native_palette(qapp, LIGHT)
        hints.scheme = Qt.ColorScheme.Dark
        hints.colorSchemeChanged.emit(hints.scheme)
        _assert_native_palette(qapp, DARK)
        manager.set_mode(ThemeMode.LIGHT)
        hints.colorSchemeChanged.emit(hints.scheme)
        _assert_native_palette(qapp, LIGHT)
    finally:
        manager._disconnect_color_scheme()


def _record(kind):
    return FeedRecord(
        key=f"us:{kind}",
        market=FeedMarket.US,
        transaction_type=kind,
        issuer="Example Corp",
        identifier="EXM",
        person="Jane Director",
        role="CEO",
        transaction_date=None,
        filing_date=None,
        quantity=None,
        price=None,
        value_display="",
        value_sort=1000,
        currency="USD",
        source="test",
        source_url="",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_existing_and_new_alert_results_follow_theme(qtbot, theme_settings):
    manager = get_theme_manager()
    manager.set_mode(ThemeMode.LIGHT)
    page = AlertsPage()
    qtbot.addWidget(page)
    hits = (AlertHit("Trading", (_record("Buy"), _record("Sell")), match_count=2),)
    page.display_hits(hits)

    manager.set_mode(ThemeMode.DARK)
    assert (
        page.results_table.item(0, 2).foreground().color().name()
        == DARK.purchase.lower()
    )
    assert (
        page.results_table.item(1, 2).foreground().color().name() == DARK.sale.lower()
    )

    page.display_hits(hits)
    assert (
        page.results_table.item(0, 2).foreground().color().name()
        == DARK.purchase.lower()
    )
    manager.set_mode(ThemeMode.LIGHT)
    assert (
        page.results_table.item(0, 2).foreground().color().name()
        == LIGHT.purchase.lower()
    )


def test_filter_validation_message_uses_theme_error_token(qtbot, qapp, theme_settings):
    manager = get_theme_manager()
    panel = FeedFilterPanel()
    qtbot.addWidget(panel)
    panel.cb_value_min.setChecked(True)
    panel.cb_value_max.setChecked(True)
    panel.sb_value_min.setValue(200)
    panel.sb_value_max.setValue(100)
    panel.show()
    panel.btn_apply.click()
    assert panel.lbl_error.text()

    for mode, tokens in ((ThemeMode.DARK, DARK), (ThemeMode.LIGHT, LIGHT)):
        manager.set_mode(mode)
        qapp.processEvents()
        assert panel.lbl_error.palette().color(
            QPalette.ColorRole.WindowText
        ).name() == (tokens.error.lower())

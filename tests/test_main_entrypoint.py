"""Tests for the GUI application entrypoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import insider_scanner.main as app_main


class DummyApp:
    def __init__(self, argv):
        self.argv = argv
        self.application_name = None
        self.organization_name = None

    def setApplicationName(self, value):
        self.application_name = value

    def setOrganizationName(self, value):
        self.organization_name = value

    def exec(self):
        return 7


class DummyWindow:
    def __init__(self):
        self.shown = False

    def show(self):
        self.shown = True


def test_main_initialises_app():
    dummy_window = DummyWindow()
    created_apps: list[DummyApp] = []

    def build_app(argv):
        app = DummyApp(argv)
        created_apps.append(app)
        return app

    with (
        patch("insider_scanner.utils.logging.setup_logging") as setup_logging,
        patch("insider_scanner.utils.config.ensure_dirs") as ensure_dirs,
        patch("insider_scanner.core.senate.init_default_congress_file") as init_default,
        patch("PySide6.QtWidgets.QApplication", side_effect=build_app),
        patch("insider_scanner.gui.main_window.MainWindow", return_value=dummy_window),
    ):
        with pytest.raises(SystemExit) as exc:
            app_main.main()

    assert exc.value.code == 7
    setup_logging.assert_called_once()
    ensure_dirs.assert_called_once()
    init_default.assert_called_once()
    app = created_apps[0]
    assert app.application_name == "Insider Scanner"
    assert app.organization_name == "InsiderScanner"
    assert dummy_window.shown is True

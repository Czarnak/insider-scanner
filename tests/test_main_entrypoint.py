"""Tests for the GUI application entrypoint."""

from __future__ import annotations

import builtins
from unittest.mock import Mock, patch

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
    def __init__(self, services):
        self.services = services
        self.shown = False

    def show(self):
        self.shown = True

    def shutdown_workers(self):
        return True


@pytest.fixture(autouse=True)
def _stub_theme_manager(monkeypatch: pytest.MonkeyPatch) -> Mock:
    manager = Mock()
    monkeypatch.setattr(
        "insider_scanner.gui.theme.get_theme_manager",
        lambda: manager,
    )
    return manager


def test_run_reports_gui_extra_install_when_pyside6_is_missing(capsys):
    real_import = builtins.__import__

    def import_without_pyside6(name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6."):
            raise ModuleNotFoundError("No module named 'PySide6'", name="PySide6")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=import_without_pyside6):
        result = app_main.run()

    assert result == 1
    assert 'pip install "insider-scanner[gui]"' in capsys.readouterr().err


def test_main_verify_install_exits_without_starting_application(monkeypatch):
    verify = Mock(return_value=0)
    run = Mock(return_value=7)
    monkeypatch.setattr(app_main, "verify_gui_install", verify)
    monkeypatch.setattr(app_main, "run", run)

    with pytest.raises(SystemExit) as exc:
        app_main.main(["--verify-install"])

    assert exc.value.code == 0
    verify.assert_called_once_with()
    run.assert_not_called()


def test_main_initialises_app():
    services = object()
    dummy_window = DummyWindow(services)
    created_apps: list[DummyApp] = []

    def build_app(argv):
        app = DummyApp(argv)
        created_apps.append(app)
        return app

    with (
        patch("insider_scanner.utils.logging.setup_logging") as setup_logging,
        patch("insider_scanner.utils.config.ensure_dirs") as ensure_dirs,
        patch("insider_scanner.core.senate.init_default_congress_file") as init_default,
        patch(
            "insider_scanner.services.application.open_application_services",
            return_value=services,
        ) as open_services,
        patch("PySide6.QtWidgets.QApplication", side_effect=build_app),
        patch(
            "insider_scanner.gui.main_window.MainWindow",
            side_effect=lambda value: dummy_window,
        ),
    ):
        with pytest.raises(SystemExit) as exc:
            app_main.main()

    assert exc.value.code == 7
    setup_logging.assert_called_once()
    ensure_dirs.assert_called_once()
    init_default.assert_called_once()
    open_services.assert_called_once()
    app = created_apps[0]
    assert app.application_name == "Insider Scanner"
    assert app.organization_name == "InsiderScanner"
    assert dummy_window.shown is True


def test_main_bootstrap_failure_exits_before_showing_window(capsys):
    with (
        patch("insider_scanner.utils.logging.setup_logging"),
        patch("insider_scanner.utils.config.ensure_dirs"),
        patch("insider_scanner.core.senate.init_default_congress_file"),
        patch(
            "insider_scanner.services.application.open_application_services",
            side_effect=RuntimeError("database unavailable"),
        ),
        patch("PySide6.QtWidgets.QApplication", side_effect=DummyApp),
        patch("PySide6.QtWidgets.QMessageBox.critical") as critical,
        patch("insider_scanner.gui.main_window.MainWindow") as window,
    ):
        with pytest.raises(SystemExit) as exc:
            app_main.main()

    assert exc.value.code == 1
    assert "Could not initialize local database." in capsys.readouterr().err
    critical.assert_called_once()
    window.assert_not_called()


@pytest.mark.parametrize("failure_point", ["construct", "show"])
def test_main_window_startup_failure_closes_services_and_exits_cleanly(
    failure_point, capsys
):
    services = Mock()
    app = DummyApp([])
    app.exec = Mock(return_value=7)
    secret = "database-password=super-secret"

    if failure_point == "construct":
        window_factory = Mock(side_effect=RuntimeError(secret))
    else:
        failed_window = DummyWindow(services)
        failed_window.show = Mock(side_effect=RuntimeError(secret))
        window_factory = Mock(return_value=failed_window)

    with (
        patch("insider_scanner.utils.logging.setup_logging"),
        patch("insider_scanner.utils.config.ensure_dirs"),
        patch("insider_scanner.core.senate.init_default_congress_file"),
        patch(
            "insider_scanner.services.application.open_application_services",
            return_value=services,
        ),
        patch("PySide6.QtWidgets.QApplication", return_value=app),
        patch("PySide6.QtWidgets.QMessageBox.critical") as critical,
        patch(
            "insider_scanner.gui.main_window.MainWindow",
            window_factory,
        ),
        patch.object(app_main.log, "exception") as log_exception,
    ):
        result = app_main.run()

    captured = capsys.readouterr()
    assert result == 1
    assert "Could not initialize application window." in captured.err
    assert secret not in captured.err
    critical.assert_called_once_with(
        None,
        "Startup Error",
        "Could not initialize the application window. See logs for details.",
    )
    log_exception.assert_called_once_with("Application window startup failed")
    services.close.assert_called_once_with()
    app.exec.assert_not_called()


def test_main_does_not_close_services_when_workers_fail_to_drain(capsys):
    services = Mock()
    failed_window = DummyWindow(services)
    failed_window.shutdown_workers = Mock(return_value=False)

    with (
        patch("insider_scanner.utils.logging.setup_logging"),
        patch("insider_scanner.utils.config.ensure_dirs"),
        patch("insider_scanner.core.senate.init_default_congress_file"),
        patch(
            "insider_scanner.services.application.open_application_services",
            return_value=services,
        ),
        patch("PySide6.QtWidgets.QApplication", side_effect=DummyApp),
        patch(
            "insider_scanner.gui.main_window.MainWindow",
            return_value=failed_window,
        ),
    ):
        result = app_main.run()

    assert result == 1
    services.close.assert_not_called()
    assert "Could not stop background work cleanly." in capsys.readouterr().err

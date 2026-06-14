"""Insider Scanner GUI entry point."""

from __future__ import annotations

import sys

from insider_scanner.utils.logging import get_logger

log = get_logger("main")

GUI_INSTALL_COMMAND = 'pip install "insider-scanner[gui]"'


def _load_gui_components():
    from PySide6.QtWidgets import QApplication, QMessageBox
    from insider_scanner.gui.main_window import MainWindow

    return QApplication, QMessageBox, MainWindow


def _report_missing_gui_dependency(exc: ModuleNotFoundError) -> bool:
    missing_package = (exc.name or "").split(".", maxsplit=1)[0]
    if missing_package not in {"PySide6", "pyqtgraph"}:
        return False
    print(
        f"Desktop GUI dependencies are not installed. Run: {GUI_INSTALL_COMMAND}",
        file=sys.stderr,
    )
    return True


def verify_gui_install() -> int:
    """Verify GUI dependencies without opening an application window."""
    try:
        from importlib.resources import files

        _load_gui_components()

        font_root = files("insider_scanner.resources.fonts")
        font_names = ("Inter-Regular.ttf", "JetBrainsMono-Regular.ttf")
        if not all(font_root.joinpath(name).is_file() for name in font_names):
            print("Bundled GUI fonts are missing.", file=sys.stderr)
            return 1
    except ModuleNotFoundError as exc:
        if not _report_missing_gui_dependency(exc):
            raise
        return 1
    print("GUI launcher dependencies and resources are available.")
    return 0


def _close_services(services) -> None:
    close = getattr(services, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        log.exception("Application service shutdown failed")


def run() -> int:
    """Launch the Insider Scanner desktop application."""
    from insider_scanner.utils.config import ensure_dirs
    from insider_scanner.utils.logging import setup_logging
    from insider_scanner.core.senate import init_default_congress_file

    try:
        QApplication, QMessageBox, MainWindow = _load_gui_components()
    except ModuleNotFoundError as exc:
        if not _report_missing_gui_dependency(exc):
            raise
        return 1

    from insider_scanner.services.application import open_application_services

    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Insider Scanner")
    app.setOrganizationName("InsiderScanner")

    from insider_scanner.gui.theme import get_theme_manager

    get_theme_manager().install(app)

    try:
        ensure_dirs()
        init_default_congress_file()
        services = open_application_services()
    except Exception:
        log.exception("Application startup failed")
        print("Could not initialize local database.", file=sys.stderr)
        QMessageBox.critical(
            None,
            "Startup Error",
            "Could not initialize the local database. See logs for details.",
        )
        return 1

    window = None
    try:
        window = MainWindow(services)
        window.show()
    except Exception:
        log.exception("Application window startup failed")
        print("Could not initialize application window.", file=sys.stderr)
        QMessageBox.critical(
            None,
            "Startup Error",
            "Could not initialize the application window. See logs for details.",
        )
        if window is None or window.shutdown_workers():
            _close_services(services)
        else:
            log.error("Persistence left open because GUI workers did not stop")
        return 1

    try:
        exit_code = app.exec()
    except Exception:
        log.exception("Application event loop failed")
        exit_code = 1
    if window.shutdown_workers():
        _close_services(services)
        return exit_code
    log.error("Persistence left open because GUI workers did not stop")
    print("Could not stop background work cleanly.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    if "--verify-install" in arguments:
        sys.exit(verify_gui_install())
    sys.exit(run())


if __name__ == "__main__":
    main()

"""Shared pytest fixtures.

GUI tests build PySide6 widgets against a process-wide ``QApplication`` and a
singleton ``ThemeManager``.  Without isolation, windows from earlier tests linger
pending-deletion and the theme manager accumulates signal connections; a later
``ThemeManager.apply()`` (or pytest-qt's teardown event processing) then touches
those dangling C++ objects and crashes the interpreter natively.  The autouse
fixture below drains deferred deletions and resets the singleton after every
test, and no-ops when no ``QApplication`` exists (so non-GUI tests are
unaffected and PySide6 is only imported when an app is already live).
"""

from __future__ import annotations

import gc
import sys
import tempfile
from pathlib import Path

import pytest

# Install overrides during conftest import, before collection imports modules
# that bind DEFAULT_PATHS or its compatibility aliases. Never inherit a user's
# configured profile, even when pytest itself was launched with overrides.
_runtime_root = tempfile.TemporaryDirectory(prefix="insider-scanner-tests-")
_runtime_environment = pytest.MonkeyPatch()
_runtime_environment.setenv(
    "INSIDER_SCANNER_DATA_DIR", str(Path(_runtime_root.name) / "data")
)
_runtime_environment.setenv(
    "INSIDER_SCANNER_CACHE_DIR", str(Path(_runtime_root.name) / "cache")
)
_runtime_environment.setenv(
    "INSIDER_SCANNER_LOG_DIR", str(Path(_runtime_root.name) / "logs")
)


def pytest_unconfigure(config):
    from insider_scanner.utils.logging import shutdown_logging

    shutdown_logging()
    _runtime_environment.undo()
    _runtime_root.cleanup()


@pytest.fixture(scope="session")
def runtime_paths():
    """The disposable profile bound by application imports in this session."""
    from insider_scanner.utils.config import DEFAULT_PATHS

    return DEFAULT_PATHS


@pytest.fixture
def engine(tmp_path):
    """A fresh, initialized SQLite engine for repository tests."""
    from insider_scanner.persistence import bootstrap_database, create_sqlite_engine

    engine = create_sqlite_engine(tmp_path / "test.sqlite3")
    try:
        bootstrap_database(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def persistence(tmp_path):
    """A separate initialized persistence context, disposed after each test."""
    from insider_scanner.services.context import open_persistence

    context = open_persistence(tmp_path / "persistence.sqlite3")
    try:
        yield context
    finally:
        context.close()


@pytest.fixture(autouse=True)
def _gui_isolation(request):
    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    app = qt_widgets.QApplication.instance() if qt_widgets is not None else None
    manage_gc = app is not None and (request is None or "qapp" in request.fixturenames)
    restore_gc = manage_gc and gc.isenabled()
    if restore_gc:
        # Automatic cyclic GC can run on a worker, destroying Qt wrappers while
        # the GUI thread holds the GIL and waits for a Qt mutex. Collect on the
        # GUI thread after cleanup instead. qapp is session-scoped, so it exists
        # before this function-scoped fixture for tests requesting qtbot/qapp.
        gc.disable()
    try:
        yield
    finally:
        try:
            qt_widgets = sys.modules.get("PySide6.QtWidgets")
            app = qt_widgets.QApplication.instance() if qt_widgets is not None else None
            if app is not None:
                from PySide6.QtCore import QEvent

                from insider_scanner.gui.theme import reset_theme_manager

                for widget in list(app.topLevelWidgets()):
                    widget.close()
                    widget.deleteLater()
                app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
                reset_theme_manager()
                if manage_gc:
                    gc.collect()
        finally:
            if restore_gc:
                gc.enable()

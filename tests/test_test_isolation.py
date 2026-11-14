"""Verify the test harness in a fresh interpreter before app imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).with_name("conftest.py")


def test_collection_isolates_default_state_from_existing_profile(tmp_path):
    profile = tmp_path / "existing-profile"
    profile.mkdir()
    sentinel = profile / "watchlists.json"
    sentinel.write_bytes(b"existing user state")
    env = dict(os.environ)
    env["INSIDER_SCANNER_DATA_DIR"] = str(profile)
    env["INSIDER_SCANNER_CACHE_DIR"] = str(profile / "cache")
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from pathlib import Path
import runpy
import sys

namespace = runpy.run_path(sys.argv[1])
try:
    from insider_scanner.utils import config
    from insider_scanner.services import context
    from insider_scanner.core import edgar
    from PySide6.QtWidgets import QApplication
    from insider_scanner.gui.main_window import MainWindow

    assert config.DATA_DIR == Path(os.environ['INSIDER_SCANNER_DATA_DIR'])
    assert config.CACHE_DIR == Path(os.environ['INSIDER_SCANNER_CACHE_DIR'])
    assert config.DATA_DIR != Path(sys.argv[2])
    assert config.DATABASE_FILE == context.DATABASE_FILE
    assert config.EDGAR_CACHE_DIR == edgar.EDGAR_CACHE_DIR
    app = QApplication([])
    window = MainWindow()
    assert window.close()
    app.processEvents()
    for name in ('feed_state.json', 'watchlists.json', 'alerts.json'):
        assert (config.DATA_DIR / name).is_file(), name
finally:
    namespace['pytest_unconfigure'](None)
""",
            str(CONFTEST),
            str(profile),
        ],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(profile.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"existing user state"


def test_non_gui_teardown_does_not_import_qt():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import runpy
import sys

namespace = runpy.run_path(sys.argv[1])
try:
    assert 'PySide6.QtWidgets' not in sys.modules
    fixture = namespace['_gui_isolation'].__wrapped__(None)
    next(fixture)
    assert next(fixture, 'finished') == 'finished'
    assert 'PySide6.QtWidgets' not in sys.modules
finally:
    namespace['pytest_unconfigure'](None)
""",
            str(CONFTEST),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_gui_cycles_are_collected_on_the_main_thread(tmp_path):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import gc
import runpy
import sys
import threading
import weakref
from types import SimpleNamespace

namespace = runpy.run_path(sys.argv[1])
try:
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    gc.enable()
    # A session-wide QApplication must not change GC for pure Python tests.
    fixture = namespace['_gui_isolation'].__wrapped__(
        SimpleNamespace(fixturenames=[])
    )
    collect = gc.collect
    def unexpected_collection():
        raise AssertionError('pure tests must not force a full collection')
    gc.collect = unexpected_collection
    try:
        next(fixture)
        assert gc.isenabled()
        assert next(fixture, 'finished') == 'finished'
        assert gc.isenabled()
    finally:
        gc.collect = collect

    request = SimpleNamespace(fixturenames=['qapp'])
    fixture = namespace['_gui_isolation'].__wrapped__(request)
    next(fixture)
    assert not gc.isenabled(), 'Qt cycles must not be collected by workers'

    destroyed_on = []
    cycle = QObject()
    cycle.cycle = cycle
    cycle.destroyed.connect(
        lambda: destroyed_on.append(threading.get_ident()),
        Qt.ConnectionType.DirectConnection,
    )
    ref = weakref.ref(cycle)
    del cycle

    # Allocate enough tracked containers on a worker to trigger automatic GC
    # under the normal thresholds. The fixture defers that collection.
    worker = threading.Thread(target=lambda: [[] for _ in range(5000)])
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert ref() is not None
    assert not destroyed_on

    assert next(fixture, 'finished') == 'finished'
    assert destroyed_on == [threading.get_ident()]
    assert ref() is None
    assert gc.isenabled(), 'the original GC state must be restored'

    gc.disable()
    fixture = namespace['_gui_isolation'].__wrapped__(request)
    next(fixture)
    assert next(fixture, 'finished') == 'finished'
    assert not gc.isenabled(), 'an already disabled GC must stay disabled'
finally:
    gc.enable()
    namespace['pytest_unconfigure'](None)
""",
            str(CONFTEST),
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

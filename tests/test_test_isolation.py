"""Verify the test harness in a fresh interpreter before app imports."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


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
    fixture = namespace['_gui_isolation'].__wrapped__()
    next(fixture)
    assert next(fixture, 'finished') == 'finished'
    assert 'PySide6.QtWidgets' not in sys.modules
finally:
    namespace['pytest_unconfigure'](None)
""",
            str(CONFTEST),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr

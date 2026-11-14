"""Exercise the project's test defaults without contacting live services."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _run_pytest(tmp_path, source, *args):
    test_file = tmp_path / "test_policy_sample.py"
    test_file.write_text(source, encoding="utf-8")
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTEST_ADDOPTS"] = ""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_timeout",
            "-c",
            str(CONFIG),
            str(test_file),
            *args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), "1 passed, 1 deselected"),
        (("-m", "live"), "1 passed, 1 deselected"),
        (("-m", ""), "2 passed"),
    ],
)
def test_live_tests_require_explicit_selection(tmp_path, args, expected):
    result = _run_pytest(
        tmp_path,
        """
import pytest

def test_offline(pytestconfig):
    assert pytestconfig.getoption('markexpr') in ('not live', '')
    assert pytestconfig.getini('timeout') == '60'
    assert pytestconfig.getini('timeout_method') == 'thread'

@pytest.mark.live
def test_live(pytestconfig):
    assert pytestconfig.getoption('markexpr') in ('live', '')
""",
        *args,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout


def test_misspelled_live_marker_fails_collection(tmp_path):
    result = _run_pytest(
        tmp_path,
        "import pytest\n@pytest.mark.lvie\ndef test_mislabeled(): pass\n",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "lvie" in result.stdout


def test_timeout_stops_a_stalled_test_process(tmp_path):
    result = _run_pytest(
        tmp_path,
        """
import time
import pytest

@pytest.mark.timeout(0.1)
def test_stalled():
    time.sleep(30)
""",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Timeout" in result.stdout

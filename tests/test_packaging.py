"""Regression tests for distributable package contents."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


EXPECTED_SEEDS = {
    "insider_scanner/resources/seeds/congress_members.json",
    "insider_scanner/resources/seeds/tickers_watchlist.txt",
    "insider_scanner/resources/seeds/eu_watchlist.txt",
}


def test_built_wheel_contains_all_packaged_seeds(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(tmp_path.glob("*.whl"))

    assert len(wheels) == 1
    with ZipFile(wheels[0]) as wheel:
        assert EXPECTED_SEEDS <= set(wheel.namelist())

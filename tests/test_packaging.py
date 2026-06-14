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

EXPECTED_FONTS = {
    "insider_scanner/resources/fonts/Inter-Regular.ttf",
    "insider_scanner/resources/fonts/JetBrainsMono-Regular.ttf",
}


def _build_wheel(tmp_path) -> ZipFile:
    project_root = Path(__file__).resolve().parents[1]
    try:
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
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Build failed!\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}"
        ) from e
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    return ZipFile(wheels[0])


def test_built_wheel_contains_all_packaged_seeds(tmp_path):
    with _build_wheel(tmp_path) as wheel:
        assert EXPECTED_SEEDS <= set(wheel.namelist())


def test_built_wheel_contains_bundled_fonts(tmp_path):
    with _build_wheel(tmp_path) as wheel:
        assert EXPECTED_FONTS <= set(wheel.namelist())

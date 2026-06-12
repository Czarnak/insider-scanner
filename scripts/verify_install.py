"""Verify a clean insider-scan installation.

Run inside an environment where the built wheel is installed:
    python scripts/verify_install.py
"""

from __future__ import annotations

import os
import subprocess
import sys


def check_cli() -> None:
    result = subprocess.run(
        ["insider-scan-cli", "--help"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CLI --help failed: {result.stderr}")
    if "usage" not in result.stdout.lower():
        raise RuntimeError("CLI help missing usage text")
    print("[ok] insider-scan-cli --help")


def check_gui_import() -> None:
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import insider_scanner.gui.main_window; print('gui import ok')",
        ],
        check=True,
        env=environment,
    )
    print("[ok] GUI module imports headless")


def check_resources() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.resources import files; "
            "seeds = files('insider_scanner.resources.seeds'); "
            "assert (seeds / 'congress_members.json').is_file(); "
            "assert (seeds / 'tickers_watchlist.txt').is_file(); "
            "print('seeds ok')",
        ],
        check=True,
    )
    print("[ok] packaged seeds resolve")


def main() -> None:
    check_cli()
    check_gui_import()
    check_resources()
    print("ALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()

"""Verify a clean insider-scan installation.

Run inside an environment where the built wheel is installed:
    python scripts/verify_install.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def check_cli() -> None:
    cli_path = shutil.which(
        "insider-scan-cli",
        path=os.path.dirname(sys.executable),
    )
    if cli_path is None:
        raise RuntimeError("insider-scan-cli is not installed beside this Python")
    result = subprocess.run(
        [cli_path, "--help"],
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
            "from importlib.resources import files\n"
            "seeds = files('insider_scanner.resources.seeds')\n"
            "names = (\n"
            "    'congress_members.json',\n"
            "    'tickers_watchlist.txt',\n"
            "    'eu_watchlist.txt',\n"
            ")\n"
            "missing = [name for name in names if not (seeds / name).is_file()]\n"
            "if missing:\n"
            "    raise SystemExit(f'missing packaged seeds: {missing}')\n"
            "print('seeds ok')\n",
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

"""Verify a clean insider-scanner installation.

Run inside an environment where the built wheel is installed:
    python scripts/verify_install.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def check_cli() -> None:
    cli_path = shutil.which(
        "insider-scanner-cli",
        path=os.path.dirname(sys.executable),
    )
    if cli_path is None:
        raise RuntimeError("insider-scanner-cli is not installed beside this Python")
    result = subprocess.run(
        [cli_path, "--help"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"CLI --help failed: {result.stderr}")
    if "usage" not in result.stdout.lower():
        raise RuntimeError("CLI help missing usage text")
    print("[ok] insider-scanner-cli --help")


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


def check_gui_launcher() -> None:
    gui_path = shutil.which(
        "insider-scanner",
        path=os.path.dirname(sys.executable),
    )
    if gui_path is None:
        raise RuntimeError("insider-scanner is not installed beside this Python")
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    subprocess.run(
        [gui_path, "--verify-install"],
        check=True,
        env=environment,
    )
    print("[ok] insider-scanner --verify-install")


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


def check_fonts() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.resources import files\n"
            "fonts = files('insider_scanner.resources.fonts')\n"
            "names = (\n"
            "    'Inter-Regular.ttf',\n"
            "    'JetBrainsMono-Regular.ttf',\n"
            ")\n"
            "missing = [name for name in names if not (fonts / name).is_file()]\n"
            "if missing:\n"
            "    raise SystemExit(f'missing packaged fonts: {missing}')\n"
            "print('fonts ok')\n",
        ],
        check=True,
    )
    print("[ok] packaged fonts resolve")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("base", "gui"),
        default="base",
        help="verify the base CLI install or the full GUI install",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    check_cli()
    check_resources()
    if args.mode == "gui":
        check_gui_import()
        check_fonts()
        check_gui_launcher()
    print("ALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main(sys.argv[1:])

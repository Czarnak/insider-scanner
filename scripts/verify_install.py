"""Verify a clean insider-scanner installation.

Run inside an environment where the built wheel is installed:
    python scripts/verify_install.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
from contextlib import closing
from importlib.metadata import version

# Python >=3.11.5 is required; this legacy Python compatibility rule does not apply.
# nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2
from importlib.resources import files
from pathlib import Path


def _console_script_dirs() -> list[str]:
    """Directories holding this interpreter's console scripts.

    POSIX installs entry-point scripts next to the interpreter (``bin/``),
    while Windows installs them into a separate ``Scripts\\`` directory, so
    both locations must be searched.
    """
    candidates = [os.path.dirname(sys.executable), sysconfig.get_path("scripts")]
    dirs: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _which_console_script(name: str) -> str | None:
    return shutil.which(name, path=os.pathsep.join(_console_script_dirs()))


def check_cli() -> None:
    cli_path = _which_console_script("insider-scanner-cli")
    if cli_path is None:
        raise RuntimeError("insider-scanner-cli is not installed beside this Python")
    result = subprocess.run(
        [cli_path, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CLI --help failed ({result.returncode}): {result.stdout}{result.stderr}"
        )
    if "usage" not in result.stdout.lower():
        raise RuntimeError("CLI help missing usage text")
    print("[ok] insider-scanner-cli --help")
    result = subprocess.run(
        [cli_path, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    expected = f"insider-scanner-cli {version('insider-scanner')}"
    if result.returncode != 0 or result.stdout.strip() != expected:
        raise RuntimeError("CLI version does not match installed metadata")
    print("[ok] insider-scanner-cli --version matches installed metadata")


def check_cli_bootstrap() -> None:
    """Exercise offline startup against a disposable user profile."""
    from insider_scanner.persistence.migrations import MIGRATIONS

    cli_path = _which_console_script("insider-scanner-cli")
    if cli_path is None:
        raise RuntimeError("insider-scanner-cli is not installed beside this Python")
    with tempfile.TemporaryDirectory(prefix="insider-scanner-install-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        fixture = root / "empty-legacy.json"
        fixture.write_text("[]", encoding="utf-8")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        environment.update(
            INSIDER_SCANNER_DATA_DIR=str(data_dir),
            INSIDER_SCANNER_CACHE_DIR=str(root / "cache"),
            INSIDER_SCANNER_LOG_DIR=str(root / "logs"),
        )
        result = subprocess.run(
            [cli_path, "import-legacy", str(fixture)],
            check=False,
            capture_output=True,
            text=True,
            cwd=root,
            env=environment,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"CLI offline startup failed: {result.stderr}")
        database = data_dir / "insider_scanner.sqlite3"
        if not database.is_file():
            raise RuntimeError("CLI startup did not create the local database")
        with closing(
            sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        ) as connection:
            history = connection.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            if history != [(migration.version,) for migration in MIGRATIONS]:
                raise RuntimeError("CLI startup did not apply all database migrations")
            connection.execute("SELECT COUNT(*) FROM us_trades").fetchone()
        seeds = files("insider_scanner.resources.seeds")
        for name in (
            "congress_members.json",
            "tickers_watchlist.txt",
            "eu_watchlist.txt",
        ):
            target = data_dir / name
            if (
                not target.is_file()
                or target.read_bytes() != (seeds / name).read_bytes()
            ):
                raise RuntimeError(f"CLI startup did not copy packaged seed: {name}")
        if not (root / "cache" / "edgar").is_dir():
            raise RuntimeError("CLI startup did not create the isolated cache")
    print("[ok] CLI offline startup, migrations and seed copying")


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
    gui_path = _which_console_script("insider-scanner")
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
            (
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
                "print('seeds ok')\n"
            ),
        ],
        check=True,
    )
    print("[ok] packaged seeds resolve")


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
    check_cli_bootstrap()
    check_resources()
    if args.mode == "gui":
        check_gui_import()
        check_gui_launcher()
    print("ALL SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main(sys.argv[1:])

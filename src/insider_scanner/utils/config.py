"""Application-wide paths and SEC compliance constants."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from platformdirs import PlatformDirs


APP_NAME = "Insider Scanner"
APP_AUTHOR = "Insider Scanner"
DATABASE_FILENAME = "insider_scanner.sqlite3"
SEED_PACKAGE = "insider_scanner.resources.seeds"


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved writable paths for one application runtime."""

    data_dir: Path
    cache_dir: Path
    database_file: Path
    outputs_dir: Path
    scan_outputs_dir: Path
    edgar_cache_dir: Path
    scraper_cache_dir: Path
    eu_cache_dir: Path
    congress_file: Path
    tickers_file: Path
    eu_watchlist_file: Path
    house_disclosures_dir: Path


def resolve_runtime_paths(
    *,
    data_dir: Path | None = None,
    cache_dir: Path | None = None,
    platform_dirs: PlatformDirs | None = None,
) -> RuntimePaths:
    """Resolve writable paths, allowing deterministic root injection in tests."""
    dirs = platform_dirs or PlatformDirs(APP_NAME, APP_AUTHOR)
    resolved_data_dir = Path(data_dir or dirs.user_data_path)
    resolved_cache_dir = Path(cache_dir or dirs.user_cache_path)
    outputs_dir = resolved_data_dir / "exports"

    return RuntimePaths(
        data_dir=resolved_data_dir,
        cache_dir=resolved_cache_dir,
        database_file=resolved_data_dir / DATABASE_FILENAME,
        outputs_dir=outputs_dir,
        scan_outputs_dir=outputs_dir / "scans",
        edgar_cache_dir=resolved_cache_dir / "edgar",
        scraper_cache_dir=resolved_cache_dir / "scrapers",
        eu_cache_dir=resolved_cache_dir / "eu_scrapers",
        congress_file=resolved_data_dir / "congress_members.json",
        tickers_file=resolved_data_dir / "tickers_watchlist.txt",
        eu_watchlist_file=resolved_data_dir / "eu_watchlist.txt",
        house_disclosures_dir=resolved_data_dir / "house_disclosures",
    )


DEFAULT_PATHS = resolve_runtime_paths()

# Backward-compatible constants used by existing scanner, GUI, and CLI modules.
PROJECT_ROOT: Path = DEFAULT_PATHS.data_dir
DATA_DIR: Path = DEFAULT_PATHS.data_dir
CACHE_DIR: Path = DEFAULT_PATHS.cache_dir
OUTPUTS_DIR: Path = DEFAULT_PATHS.outputs_dir
DATABASE_FILE: Path = DEFAULT_PATHS.database_file
DB_PATH: Path = DEFAULT_PATHS.database_file

EDGAR_CACHE_DIR: Path = DEFAULT_PATHS.edgar_cache_dir
SCRAPER_CACHE_DIR: Path = DEFAULT_PATHS.scraper_cache_dir
EU_CACHE_DIR: Path = DEFAULT_PATHS.eu_cache_dir
SCAN_OUTPUTS_DIR: Path = DEFAULT_PATHS.scan_outputs_dir

CONGRESS_FILE: Path = DEFAULT_PATHS.congress_file
TICKERS_FILE: Path = DEFAULT_PATHS.tickers_file
EU_WATCHLIST_FILE: Path = DEFAULT_PATHS.eu_watchlist_file
HOUSE_DISCLOSURES_DIR: Path = DEFAULT_PATHS.house_disclosures_dir

# SEC EDGAR compliance: https://www.sec.gov/os/accessing-edgar-data
SEC_USER_AGENT: str = os.getenv(
    "SEC_USER_AGENT",
    "InsiderScanner/0.1 (research; contact@example.com)",
)
SEC_MAX_REQUESTS_PER_SECOND: int = 10

# Cache expiry defaults (seconds)
DEFAULT_CACHE_TTL: int = 3600  # 1 hour


def _copy_seed_if_missing(source: Traversable, target: Path) -> None:
    """Atomically publish a seed without replacing an existing user file."""
    if target.exists():
        return

    temp_path: Path | None = None
    try:
        with source.open("rb") as source_file:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                shutil.copyfileobj(source_file, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

        try:
            os.link(temp_path, target)
        except FileExistsError:
            pass
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def ensure_dirs(paths: RuntimePaths | None = None) -> None:
    """Create runtime directories and copy packaged seeds when absent."""
    resolved = paths or DEFAULT_PATHS
    for directory in (
        resolved.data_dir,
        resolved.cache_dir,
        resolved.edgar_cache_dir,
        resolved.scraper_cache_dir,
        resolved.eu_cache_dir,
        resolved.outputs_dir,
        resolved.scan_outputs_dir,
        resolved.house_disclosures_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    seed_root = files(SEED_PACKAGE)
    for target in (
        resolved.congress_file,
        resolved.tickers_file,
        resolved.eu_watchlist_file,
    ):
        _copy_seed_if_missing(seed_root.joinpath(target.name), target)


def load_watchlist(path: Path | None = None) -> list[str]:
    """Load ticker symbols from the watchlist file.

    Returns a list of uppercase ticker strings, skipping blank lines
    and comments (lines starting with #).
    """
    p = path or TICKERS_FILE
    if not p.exists():
        return []

    tickers = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.append(line.upper())
    return tickers


def load_eu_watchlist(path: Path | None = None) -> list[str]:
    """Load ISIN codes from the European watchlist file.

    Returns a list of uppercase ISIN strings (12 characters each),
    skipping blank lines and comments (lines starting with #).
    Invalid-length entries are logged and skipped.
    """
    import logging

    log = logging.getLogger("config")

    p = path or EU_WATCHLIST_FILE
    if not p.exists():
        return []

    isins = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        isin = line.upper()
        if len(isin) != 12:
            log.warning("Skipping invalid ISIN (expected 12 chars): %r", isin)
            continue
        isins.append(isin)
    return isins

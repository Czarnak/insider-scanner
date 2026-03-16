"""Application-wide paths and SEC compliance constants."""

from __future__ import annotations

import os
from pathlib import Path


def _find_project_root() -> Path:
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT: Path = _find_project_root()

DATA_DIR: Path = PROJECT_ROOT / "data"
CACHE_DIR: Path = PROJECT_ROOT / "cache"
OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"

EDGAR_CACHE_DIR: Path = CACHE_DIR / "edgar"
SCRAPER_CACHE_DIR: Path = CACHE_DIR / "scrapers"
EU_CACHE_DIR: Path = CACHE_DIR / "eu_scrapers"
SCAN_OUTPUTS_DIR: Path = OUTPUTS_DIR / "scans"

CONGRESS_FILE: Path = DATA_DIR / "congress_members.json"
TICKERS_FILE: Path = DATA_DIR / "tickers_watchlist.txt"
EU_WATCHLIST_FILE: Path = DATA_DIR / "eu_watchlist.txt"
HOUSE_DISCLOSURES_DIR: Path = DATA_DIR / "house_disclosures"

# SEC EDGAR compliance: https://www.sec.gov/os/accessing-edgar-data
SEC_USER_AGENT: str = os.getenv(
    "SEC_USER_AGENT",
    "InsiderScanner/0.1 (research; contact@example.com)",
)
SEC_MAX_REQUESTS_PER_SECOND: int = 10

# Cache expiry defaults (seconds)
DEFAULT_CACHE_TTL: int = 3600  # 1 hour


def ensure_dirs() -> None:
    """Create all required runtime directories."""
    for d in (
        CACHE_DIR,
        EDGAR_CACHE_DIR,
        SCRAPER_CACHE_DIR,
        EU_CACHE_DIR,
        OUTPUTS_DIR,
        SCAN_OUTPUTS_DIR,
        DATA_DIR,
        HOUSE_DISCLOSURES_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


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

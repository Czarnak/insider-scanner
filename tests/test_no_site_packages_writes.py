"""Regression tests preventing runtime writes inside the installed package."""

from dataclasses import fields
from importlib.resources import files
from pathlib import Path

import insider_scanner
from insider_scanner.utils import config


SEED_TARGETS = (
    "congress_file",
    "tickers_file",
    "eu_watchlist_file",
)

EXPORTED_RUNTIME_PATHS = {
    "PROJECT_ROOT": "data_dir",
    "DATA_DIR": "data_dir",
    "CACHE_DIR": "cache_dir",
    "DATABASE_FILE": "database_file",
    "OUTPUTS_DIR": "outputs_dir",
    "SCAN_OUTPUTS_DIR": "scan_outputs_dir",
    "EDGAR_CACHE_DIR": "edgar_cache_dir",
    "SCRAPER_CACHE_DIR": "scraper_cache_dir",
    "EU_CACHE_DIR": "eu_cache_dir",
    "CONGRESS_FILE": "congress_file",
    "TICKERS_FILE": "tickers_file",
    "EU_WATCHLIST_FILE": "eu_watchlist_file",
    "HOUSE_DISCLOSURES_DIR": "house_disclosures_dir",
}


def _is_under(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _snapshot_tree(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_runtime_paths_resolve_outside_the_installed_package(tmp_path):
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    paths = config.resolve_runtime_paths(data_dir=data_dir, cache_dir=cache_dir)
    package_dir = Path(insider_scanner.__file__).resolve().parent

    for field in fields(paths):
        runtime_path = getattr(paths, field.name)
        assert package_dir != runtime_path.resolve()
        assert package_dir not in runtime_path.resolve().parents
        assert _is_under(runtime_path, data_dir) or _is_under(runtime_path, cache_dir)

    assert paths.outputs_dir == data_dir / "exports"
    assert paths.scan_outputs_dir == data_dir / "exports" / "scans"
    assert paths.eu_cache_dir == cache_dir / "eu_scrapers"
    assert paths.eu_watchlist_file == data_dir / "eu_watchlist.txt"


def test_default_runtime_paths_resolve_outside_the_installed_package():
    package_dir = Path(insider_scanner.__file__).resolve().parent

    for field in fields(config.DEFAULT_PATHS):
        runtime_path = getattr(config.DEFAULT_PATHS, field.name).resolve()
        assert package_dir != runtime_path
        assert package_dir not in runtime_path.parents

    for exported_name, field_name in EXPORTED_RUNTIME_PATHS.items():
        assert getattr(config, exported_name) == getattr(
            config.DEFAULT_PATHS,
            field_name,
        )


def test_ensure_dirs_only_writes_user_roots_and_copies_packaged_seeds(tmp_path):
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    paths = config.resolve_runtime_paths(data_dir=data_dir, cache_dir=cache_dir)
    package_dir = Path(insider_scanner.__file__).resolve().parent
    seed_root = files(config.SEED_PACKAGE)
    packaged_seeds = {
        target_name: seed_root.joinpath(getattr(paths, target_name).name).read_bytes()
        for target_name in SEED_TARGETS
    }
    package_before = _snapshot_tree(package_dir)

    config.ensure_dirs(paths)

    created_paths = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
    }
    assert created_paths == {
        Path("cache"),
        Path("cache/edgar"),
        Path("cache/eu_scrapers"),
        Path("cache/scrapers"),
        Path("data"),
        Path("data/congress_members.json"),
        Path("data/eu_watchlist.txt"),
        Path("data/exports"),
        Path("data/exports/scans"),
        Path("data/house_disclosures"),
        Path("data/tickers_watchlist.txt"),
    }

    for target_name, expected_content in packaged_seeds.items():
        user_seed = getattr(paths, target_name)
        assert _is_under(user_seed, data_dir)
        assert user_seed.read_bytes() == expected_content

    user_edit = b"user-managed watchlist\n"
    paths.tickers_file.write_bytes(user_edit)
    config.ensure_dirs(paths)

    assert paths.tickers_file.read_bytes() == user_edit
    assert _snapshot_tree(package_dir) == package_before

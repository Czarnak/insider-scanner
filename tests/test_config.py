"""Tests for config utilities."""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from insider_scanner.utils.config import (
    RuntimePaths,
    ensure_dirs,
    load_eu_watchlist,
    load_watchlist,
    resolve_runtime_paths,
)


class TestRuntimePaths:
    def test_resolve_runtime_paths_uses_injected_data_and_cache_roots(self, tmp_path):
        data_dir = tmp_path / "data"
        cache_dir = tmp_path / "cache"

        paths = resolve_runtime_paths(data_dir=data_dir, cache_dir=cache_dir)

        assert isinstance(paths, RuntimePaths)
        assert paths.data_dir == data_dir
        assert paths.database_file == data_dir / "insider_scanner.sqlite3"
        assert paths.outputs_dir == data_dir / "exports"
        assert paths.scan_outputs_dir == data_dir / "exports" / "scans"
        assert paths.house_disclosures_dir == data_dir / "house_disclosures"
        assert paths.congress_file == data_dir / "congress_members.json"
        assert paths.tickers_file == data_dir / "tickers_watchlist.txt"
        assert paths.eu_watchlist_file == data_dir / "eu_watchlist.txt"
        assert paths.cache_dir == cache_dir
        assert paths.edgar_cache_dir == cache_dir / "edgar"
        assert paths.scraper_cache_dir == cache_dir / "scrapers"
        assert paths.eu_cache_dir == cache_dir / "eu_scrapers"

    def test_runtime_paths_are_immutable(self, tmp_path):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )

        try:
            paths.data_dir = tmp_path / "other"
        except AttributeError:
            pass
        else:
            raise AssertionError("RuntimePaths must be immutable")


class TestPackagedSeeds:
    def test_ensure_dirs_copies_all_missing_seed_files(self, tmp_path):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )

        ensure_dirs(paths)

        seed_root = files("insider_scanner.resources.seeds")
        for target in (
            paths.congress_file,
            paths.tickers_file,
            paths.eu_watchlist_file,
        ):
            expected = seed_root.joinpath(target.name).read_bytes()
            assert target.read_bytes() == expected

    def test_ensure_dirs_never_overwrites_user_edits(self, tmp_path):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )
        ensure_dirs(paths)
        paths.tickers_file.write_text("CUSTOM\n", encoding="utf-8")

        ensure_dirs(paths)

        assert paths.tickers_file.read_text(encoding="utf-8") == "CUSTOM\n"

    def test_concurrent_ensure_dirs_atomically_publishes_complete_seeds(
        self,
        tmp_path,
    ):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        )
        seed_root = files("insider_scanner.resources.seeds")
        expected = {
            target.name: seed_root.joinpath(target.name).read_bytes()
            for target in (
                paths.congress_file,
                paths.tickers_file,
                paths.eu_watchlist_file,
            )
        }
        copy_started = threading.Event()
        release_copy = threading.Event()
        real_copyfileobj = shutil.copyfileobj

        def slow_copy(source, destination):
            copy_started.set()
            assert release_copy.wait(timeout=5)
            real_copyfileobj(source, destination, length=1024)

        observed_partial_files: list[Path] = []

        def observe_published_files():
            while not release_copy.is_set():
                for target in (
                    paths.congress_file,
                    paths.tickers_file,
                    paths.eu_watchlist_file,
                ):
                    if target.exists() and target.read_bytes() != expected[target.name]:
                        observed_partial_files.append(target)
                time.sleep(0.001)

        with patch(
            "insider_scanner.utils.config.shutil.copyfileobj",
            side_effect=slow_copy,
        ):
            with ThreadPoolExecutor(max_workers=9) as executor:
                writers = [executor.submit(ensure_dirs, paths) for _ in range(8)]
                assert copy_started.wait(timeout=5)
                observer = executor.submit(observe_published_files)
                time.sleep(0.05)
                release_copy.set()
                for future in writers:
                    future.result(timeout=10)
                observer.result(timeout=5)

        assert observed_partial_files == []
        for target in (
            paths.congress_file,
            paths.tickers_file,
            paths.eu_watchlist_file,
        ):
            assert target.read_bytes() == expected[target.name]


class TestLoadWatchlist:
    def test_load_basic(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("AAPL\nMSFT\nGOOGL\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT", "GOOGL"]

    def test_uppercase_conversion(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("aapl\nmsft\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_skip_blank_lines(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("AAPL\n\n\nMSFT\n\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_skip_comments(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("# My watchlist\nAAPL\n# Tech stocks\nMSFT\n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_strip_whitespace(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("  AAPL  \n  MSFT  \n")
        result = load_watchlist(f)
        assert result == ["AAPL", "MSFT"]

    def test_missing_file(self, tmp_path):
        result = load_watchlist(tmp_path / "nonexistent.txt")
        assert result == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "tickers.txt"
        f.write_text("")
        result = load_watchlist(f)
        assert result == []


class TestLoadEuWatchlist:
    def test_load_valid_isins(self, tmp_path):
        f = tmp_path / "isins.txt"
        f.write_text("gb0002875804\nNL0000009165\n")
        result = load_eu_watchlist(f)
        assert result == ["GB0002875804", "NL0000009165"]

    def test_skip_invalid_entries(self, tmp_path, caplog):
        f = tmp_path / "isins.txt"
        f.write_text("SHORT\nFR0000131104\n")
        result = load_eu_watchlist(f)
        assert result == ["FR0000131104"]
        assert "Skipping invalid ISIN" in caplog.text

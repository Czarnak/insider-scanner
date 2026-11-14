"""Tests for config utilities."""

from __future__ import annotations

import errno
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from insider_scanner.utils.config import (
    RuntimePaths,
    ensure_dirs,
    load_eu_watchlist,
    load_watchlist,
    resolve_runtime_paths,
)


class TestRuntimePaths:
    def test_environment_overrides_both_runtime_roots(self, monkeypatch, tmp_path):
        monkeypatch.setenv("INSIDER_SCANNER_DATA_DIR", str(tmp_path / "profile"))
        monkeypatch.setenv("INSIDER_SCANNER_CACHE_DIR", str(tmp_path / "downloads"))

        paths = resolve_runtime_paths()

        assert paths.data_dir == tmp_path / "profile"
        assert paths.database_file == tmp_path / "profile/insider_scanner.sqlite3"
        assert paths.cache_dir == tmp_path / "downloads"
        assert paths.edgar_cache_dir == tmp_path / "downloads/edgar"

    def test_explicit_roots_take_precedence_over_environment(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("INSIDER_SCANNER_DATA_DIR", str(tmp_path / "env-data"))
        monkeypatch.setenv("INSIDER_SCANNER_CACHE_DIR", str(tmp_path / "env-cache"))

        paths = resolve_runtime_paths(
            data_dir=tmp_path / "explicit-data", cache_dir=tmp_path / "explicit-cache"
        )

        assert paths.data_dir == tmp_path / "explicit-data"
        assert paths.cache_dir == tmp_path / "explicit-cache"

    @pytest.mark.parametrize("override", [None, ""])
    def test_unset_or_empty_overrides_preserve_platform_paths(
        self, monkeypatch, tmp_path, override
    ):
        for name in ("INSIDER_SCANNER_DATA_DIR", "INSIDER_SCANNER_CACHE_DIR"):
            if override is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, override)
        platform_dirs = SimpleNamespace(
            user_data_path=tmp_path / "existing-data",
            user_cache_path=tmp_path / "existing-cache",
        )

        paths = resolve_runtime_paths(platform_dirs=platform_dirs)

        assert paths.data_dir == tmp_path / "existing-data"
        assert paths.cache_dir == tmp_path / "existing-cache"

    def test_overriding_data_keeps_independent_cache_default(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("INSIDER_SCANNER_DATA_DIR", str(tmp_path / "profile"))
        monkeypatch.delenv("INSIDER_SCANNER_CACHE_DIR", raising=False)
        platform_dirs = SimpleNamespace(
            user_data_path=tmp_path / "existing-data",
            user_cache_path=tmp_path / "existing-cache",
        )

        paths = resolve_runtime_paths(platform_dirs=platform_dirs)

        assert paths.data_dir == tmp_path / "profile"
        assert paths.cache_dir == tmp_path / "existing-cache"

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

        with pytest.raises(AttributeError):
            paths.data_dir = tmp_path / "other"


class TestPackagedSeeds:
    def test_failed_exclusive_copy_cleans_up_even_when_close_raises(
        self, monkeypatch, tmp_path
    ):
        from insider_scanner.utils import config

        staged = tmp_path / "staged"
        staged.write_bytes(b"packaged seed")
        target = tmp_path / "seed.txt"
        real_open = Path.open

        class CloseFails:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

            def fileno(self):
                return self.handle.fileno()

            def close(self):
                self.handle.close()
                raise OSError("close failed")

        def open_with_failing_close(path, mode="r", *args, **kwargs):
            handle = real_open(path, mode, *args, **kwargs)
            return CloseFails(handle) if path == target and mode == "xb" else handle

        def interrupted_copy(*args):
            raise OSError("copy interrupted")

        monkeypatch.setattr(Path, "open", open_with_failing_close)
        monkeypatch.setattr(
            config,
            "os",
            SimpleNamespace(name="posix", fsync=os.fsync, fstat=os.fstat, path=os.path),
        )
        monkeypatch.setattr(config.shutil, "copyfileobj", interrupted_copy)

        with pytest.raises(OSError, match="close failed"):
            config._publish_seed_without_link(staged, target)

        assert not target.exists()

    @pytest.mark.parametrize("replace_target", [False, True])
    def test_failed_exclusive_copy_only_removes_its_own_file(
        self, monkeypatch, tmp_path, replace_target
    ):
        from insider_scanner.utils import config

        staged = tmp_path / "staged"
        staged.write_bytes(b"packaged seed")
        target = tmp_path / "seed.txt"

        def interrupted_copy(source, destination):
            # Closing first lets Windows reproduce POSIX pathname replacement
            # while the writer still retains the identity of its original file.
            destination.close()
            if replace_target:
                replacement = tmp_path / "replacement"
                replacement.write_bytes(b"new user content")
                replacement.replace(target)
            raise OSError("copy interrupted")

        monkeypatch.setattr(
            config,
            "os",
            SimpleNamespace(name="posix", fsync=os.fsync, fstat=os.fstat, path=os.path),
        )
        monkeypatch.setattr(config.shutil, "copyfileobj", interrupted_copy)

        with pytest.raises(OSError, match="copy interrupted"):
            config._publish_seed_without_link(staged, target)

        if replace_target:
            assert target.read_bytes() == b"new user content"
        else:
            assert not target.exists()

    def test_ensure_dirs_supports_filesystems_without_hardlinks(
        self, monkeypatch, tmp_path
    ):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
        )

        def unsupported_link(*args):
            raise OSError(errno.ENOTSUP, "Hard links unavailable")

        monkeypatch.setattr("insider_scanner.utils.config.os.link", unsupported_link)

        ensure_dirs(paths)

        seed_root = files("insider_scanner.resources.seeds")
        for target in (
            paths.congress_file,
            paths.tickers_file,
            paths.eu_watchlist_file,
        ):
            assert target.read_bytes() == seed_root.joinpath(target.name).read_bytes()
        assert not list(paths.data_dir.glob(".*.tmp"))

    def test_fallback_never_replaces_a_concurrently_created_user_seed(
        self, monkeypatch, tmp_path
    ):
        paths = resolve_runtime_paths(
            data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"
        )

        def concurrent_user_write(source, target):
            target.write_bytes(b"concurrent user content")
            raise OSError(errno.ENOTSUP, "Hard links unavailable")

        monkeypatch.setattr(
            "insider_scanner.utils.config.os.link", concurrent_user_write
        )

        ensure_dirs(paths)

        for target in (
            paths.congress_file,
            paths.tickers_file,
            paths.eu_watchlist_file,
        ):
            assert target.read_bytes() == b"concurrent user content"
        assert not list(paths.data_dir.glob(".*.tmp"))

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

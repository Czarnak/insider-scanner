"""Tests for the file-based caching system."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, local
from types import SimpleNamespace

import pytest

from insider_scanner.utils import atomic_file, caching, http
from insider_scanner.utils.caching import (
    cache_key,
    clear_cache,
    get_cached,
    prune_expired_cache,
    set_cached,
)


class TestCacheKey:
    def test_deterministic(self):
        assert cache_key("https://example.com") == cache_key("https://example.com")

    def test_different_urls(self):
        assert cache_key("https://a.com") != cache_key("https://b.com")

    def test_length(self):
        key = cache_key("https://example.com/some/long/path?query=1")
        assert len(key) == 16


class TestGetSetCached:
    def test_roundtrip(self, tmp_path):
        set_cached(tmp_path, "testkey", "hello world")
        result = get_cached(tmp_path, "testkey", ttl=3600)
        assert result == "hello world"

    def test_missing_key(self, tmp_path):
        result = get_cached(tmp_path, "nonexistent", ttl=3600)
        assert result is None

    def test_expired(self, tmp_path):
        set_cached(tmp_path, "expkey", "data")
        path = tmp_path / "expkey.response-cache"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["timestamp"] = time.time() - 7200
        path.write_text(json.dumps(data), encoding="utf-8")
        result = get_cached(tmp_path, "expkey", ttl=3600)
        assert result is None

    def test_not_expired(self, tmp_path):
        set_cached(tmp_path, "freshkey", "data")
        result = get_cached(tmp_path, "freshkey", ttl=3600)
        assert result == "data"

    def test_corrupted_envelope(self, tmp_path):
        set_cached(tmp_path, "corruptkey", "data")
        path = tmp_path / "corruptkey.response-cache"
        path.write_text("not json", encoding="utf-8")
        result = get_cached(tmp_path, "corruptkey", ttl=3600)
        assert result is None


class TestClearCache:
    def test_clear(self, tmp_path):
        set_cached(tmp_path, "key1", "a")
        set_cached(tmp_path, "key2", "b")
        count = clear_cache(tmp_path)
        assert count == 2
        assert list(tmp_path.iterdir()) == []

    def test_clear_empty(self, tmp_path):
        count = clear_cache(tmp_path)
        assert count == 0

    def test_clear_includes_legacy_and_leaves_unrelated_files(self, tmp_path):
        (tmp_path / "legacy.txt").write_text("body", encoding="utf-8")
        (tmp_path / "legacy.meta").write_text("{}", encoding="utf-8")
        unrelated = tmp_path / "unrelated.json"
        unrelated.write_text("{}", encoding="utf-8")
        set_cached(tmp_path, "new", "body")
        assert clear_cache(tmp_path) == 3
        assert list(tmp_path.iterdir()) == [unrelated]


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"version": True, "timestamp": 1, "content": "body"},
        {"version": 2, "timestamp": 1, "content": "body"},
        {"version": 1, "timestamp": None, "content": "body"},
        {"version": 1, "timestamp": "1", "content": "body"},
        {"version": 1, "timestamp": True, "content": "body"},
        {"version": 1, "timestamp": float("nan"), "content": "body"},
        {"version": 1, "timestamp": float("inf"), "content": "body"},
        {"version": 1, "timestamp": -1, "content": "body"},
        {"version": 1, "timestamp": 10**400, "content": "body"},
        {"version": 1, "timestamp": 1, "content": []},
        {"version": 1, "timestamp": 1, "ttl": None, "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": True, "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": "1", "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": -1, "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": float("nan"), "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": float("inf"), "content": "body"},
        {"version": 1, "timestamp": 1, "ttl": 10**400, "content": "body"},
    ],
)
def test_malformed_envelope_is_a_miss(tmp_path, monkeypatch, data):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 1))
    if isinstance(data, dict) and data:
        data = {"ttl": 3600, **data}
    (tmp_path / "key.response-cache").write_text(json.dumps(data), encoding="utf-8")
    assert get_cached(tmp_path, "key") is None


def test_invalid_utf8_is_a_miss(tmp_path):
    (tmp_path / "key.response-cache").write_bytes(b"\xff\xfe")
    assert get_cached(tmp_path, "key") is None


def test_deeply_nested_json_is_a_miss(tmp_path):
    payload = "[" * 200000 + "0" + "]" * 200000
    # Python 3.14 accepts the shallower nesting rejected by older interpreters.
    # Prove this fixture reaches the decoder's recursion boundary on this host.
    with pytest.raises(RecursionError):
        json.loads(payload)
    (tmp_path / "key.response-cache").write_text(payload, encoding="utf-8")
    assert get_cached(tmp_path, "key") is None
    assert prune_expired_cache(tmp_path) == 1


def test_envelope_without_lifetime_is_a_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 1))
    (tmp_path / "key.response-cache").write_text(
        json.dumps({"version": 1, "timestamp": 1, "content": "body"}),
        encoding="utf-8",
    )
    assert get_cached(tmp_path, "key") is None


def test_legacy_pairs_are_misses_even_with_fresh_metadata(tmp_path):
    (tmp_path / "key.txt").write_text("possibly partial", encoding="utf-8")
    (tmp_path / "key.meta").write_text(
        json.dumps({"timestamp": time.time()}), encoding="utf-8"
    )
    assert get_cached(tmp_path, "key") is None


def test_locked_read_is_a_miss(tmp_path, monkeypatch):
    set_cached(tmp_path, "key", "body")

    def locked(*args, **kwargs):
        raise PermissionError("locked cache")

    monkeypatch.setattr(Path, "read_text", locked)
    assert get_cached(tmp_path, "key") is None


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt, SystemExit])
def test_failed_publication_retains_previous_complete_entry(
    tmp_path, monkeypatch, failure, caplog
):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    set_cached(tmp_path, "key", "original")

    def fail(*args):
        raise failure("target locked or interrupted")

    monkeypatch.setattr(atomic_file.os, "replace", fail)
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 200))
    if issubclass(failure, OSError):
        set_cached(tmp_path, "key", "replacement")
        assert "Could not cache response" in caplog.text
    else:
        with pytest.raises(failure):
            set_cached(tmp_path, "key", "replacement")
    path = tmp_path / "key.response-cache"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "timestamp": 100,
        "ttl": caching.DEFAULT_CACHE_TTL,
        "content": "original",
    }
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_publication_keeps_timestamp_and_content_together(
    tmp_path, monkeypatch
):
    clock = local()
    monkeypatch.setattr(
        caching, "time", SimpleNamespace(time=lambda: getattr(clock, "timestamp", 0))
    )
    set_cached(tmp_path, "key", "0")
    ready = Barrier(4)
    path = tmp_path / "key.response-cache"

    def write(timestamp):
        clock.timestamp = timestamp
        ready.wait()
        for _ in range(20):
            set_cached(tmp_path, "key", str(timestamp))

    def read():
        ready.wait()
        observed = 0
        for _ in range(100):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                # Windows may deny opening an entry during replacement; the
                # public reader treats this as a cache miss too.
                continue
            data = json.loads(text)
            assert data["timestamp"] == int(data["content"])
            observed += 1
        assert observed > 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(write, timestamp) for timestamp in (1, 2, 3)]
        futures.append(executor.submit(read))
        for future in futures:
            future.result()
    assert list(tmp_path.iterdir()) == [path]


def test_prune_removes_unrequested_expired_malformed_and_legacy_entries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    set_cached(tmp_path, "old", "expired")
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 200))
    set_cached(tmp_path, "fresh", "fresh")
    (tmp_path / "bad.response-cache").write_bytes(b"\xff")
    (tmp_path / "legacy.txt").write_text("legacy", encoding="utf-8")
    (tmp_path / "legacy.meta").write_text("{}", encoding="utf-8")
    unrelated = tmp_path / "keep.json"
    unrelated.write_text("{}", encoding="utf-8")
    assert prune_expired_cache(tmp_path, ttl=50) == 4
    assert get_cached(tmp_path, "fresh", ttl=50) == "fresh"
    assert set(tmp_path.iterdir()) == {unrelated, tmp_path / "fresh.response-cache"}


def test_prune_respects_each_requests_lifetime(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    set_cached(tmp_path, "short", "scraper response", ttl=1800)
    set_cached(tmp_path, "long", "EDGAR response", ttl=86400)
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100 + 7200))
    assert prune_expired_cache(tmp_path) == 1
    assert get_cached(tmp_path, "long", ttl=86400) == "EDGAR response"
    assert set(tmp_path.iterdir()) == {tmp_path / "long.response-cache"}


def test_zero_lifetime_is_immediately_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    set_cached(tmp_path, "key", "body", ttl=0)
    assert prune_expired_cache(tmp_path) == 1


def test_reads_keep_the_callers_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    set_cached(tmp_path, "key", "body", ttl=86400)
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100 + 7200))
    assert get_cached(tmp_path, "key", ttl=1800) is None
    assert get_cached(tmp_path, "key", ttl=86400) == "body"


def test_http_forwards_custom_ttl_to_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100))
    monkeypatch.setattr(
        http,
        "_http_session",
        lambda: SimpleNamespace(
            get=lambda *args, **kwargs: SimpleNamespace(
                text="network response", encoding="utf-8"
            )
        ),
    )
    url = "https://example.com"
    assert (
        http.fetch_url(url, cache_dir=tmp_path, cache_ttl=86400) == "network response"
    )
    path = tmp_path / f"{cache_key(url)}.response-cache"
    assert json.loads(path.read_text(encoding="utf-8"))["ttl"] == 86400
    monkeypatch.setattr(caching, "time", SimpleNamespace(time=lambda: 100 + 7200))
    assert prune_expired_cache(tmp_path) == 0

    def unexpected_network(*args, **kwargs):
        raise AssertionError("Fresh cached response should avoid a network request")

    monkeypatch.setattr(http, "_http_session", unexpected_network)
    assert (
        http.fetch_url(url, cache_dir=tmp_path, cache_ttl=86400) == "network response"
    )


def test_prune_skips_locked_entry_and_continues(tmp_path, monkeypatch):
    for key in ("locked", "removable"):
        (tmp_path / f"{key}.response-cache").write_bytes(b"bad")
    unlink = Path.unlink

    def maybe_locked(path, *args, **kwargs):
        if path.name == "locked.response-cache":
            raise PermissionError("locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", maybe_locked)
    assert prune_expired_cache(tmp_path) == 1
    assert (tmp_path / "locked.response-cache").exists()


def test_prune_skips_entry_with_stat_failure_and_continues(tmp_path, monkeypatch):
    for key in ("locked", "removable"):
        (tmp_path / f"{key}.response-cache").write_bytes(b"bad")
    is_file = Path.is_file

    def maybe_locked(path, *args, **kwargs):
        if path.name == "locked.response-cache":
            raise PermissionError("locked")
        return is_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", maybe_locked)
    assert prune_expired_cache(tmp_path) == 1
    assert (tmp_path / "locked.response-cache").exists()


@pytest.mark.parametrize("cleanup", [prune_expired_cache, clear_cache])
def test_cleanup_handles_inaccessible_directory(tmp_path, monkeypatch, cleanup):
    def locked(*args):
        raise PermissionError("locked directory")

    monkeypatch.setattr(Path, "iterdir", locked)
    assert cleanup(tmp_path) == 0


def test_cache_write_failure_does_not_discard_network_response(tmp_path, monkeypatch):
    blocked = tmp_path / "cache"
    blocked.write_bytes(b"file blocks directory creation")
    monkeypatch.setattr(
        http,
        "_http_session",
        lambda: SimpleNamespace(
            get=lambda *args, **kwargs: SimpleNamespace(
                text="network response", encoding="utf-8"
            )
        ),
    )
    assert (
        http.fetch_url("https://example.com", cache_dir=blocked) == "network response"
    )

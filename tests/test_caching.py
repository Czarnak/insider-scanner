"""Tests for the file-based caching system."""

from __future__ import annotations

import json
import time

from insider_scanner.utils.caching import (
    cache_key,
    clear_cache,
    get_cached,
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
        # Manually set timestamp to the past
        meta_path = tmp_path / "expkey.meta"
        meta_path.write_text(json.dumps({"timestamp": time.time() - 7200}))
        result = get_cached(tmp_path, "expkey", ttl=3600)
        assert result is None

    def test_not_expired(self, tmp_path):
        set_cached(tmp_path, "freshkey", "data")
        result = get_cached(tmp_path, "freshkey", ttl=3600)
        assert result == "data"

    def test_corrupted_meta(self, tmp_path):
        set_cached(tmp_path, "corruptkey", "data")
        meta_path = tmp_path / "corruptkey.meta"
        meta_path.write_text("not json")
        result = get_cached(tmp_path, "corruptkey", ttl=3600)
        assert result is None


class TestClearCache:
    def test_clear(self, tmp_path):
        set_cached(tmp_path, "key1", "a")
        set_cached(tmp_path, "key2", "b")
        count = clear_cache(tmp_path)
        assert count == 4  # 2 .txt + 2 .meta

    def test_clear_empty(self, tmp_path):
        count = clear_cache(tmp_path)
        assert count == 0

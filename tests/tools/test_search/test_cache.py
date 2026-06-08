"""Tests for SearchCache."""

from __future__ import annotations

import time

from src.tools.search.cache import SearchCache


class TestSearchCache:
    def test_get_missing(self) -> None:
        cache = SearchCache(ttl=300)
        assert cache.get("nonexistent") is None

    def test_set_and_get(self) -> None:
        cache = SearchCache(ttl=300)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_ttl_expiry(self) -> None:
        cache = SearchCache(ttl=0.1)  # 100ms TTL
        cache.set("key", "value")
        assert cache.get("key") == "value"
        time.sleep(0.15)
        assert cache.get("key") is None

    def test_clear(self) -> None:
        cache = SearchCache(ttl=300)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_invalidate(self) -> None:
        cache = SearchCache(ttl=300)
        cache.set("key", "value")
        cache.invalidate("key")
        assert cache.get("key") is None

    def test_multiple_keys(self) -> None:
        cache = SearchCache(ttl=300)
        cache.set("a", "1")
        cache.set("b", "2")
        assert cache.get("a") == "1"
        assert cache.get("b") == "2"

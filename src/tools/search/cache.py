"""简单内存缓存，避免重复搜索相同 query。

借鉴 Claude Code WebSearch 的 15 分钟缓存设计，这里使用更短的 5 分钟 TTL。
"""

from __future__ import annotations

import time
from typing import Any

_DEFAULT_TTL = 300  # 5 分钟


class SearchCache:
    """线程安全的内存搜索缓存。"""

    def __init__(self, ttl: float = _DEFAULT_TTL) -> None:
        self._ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._data.clear()

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)


# 全局单例
_search_cache = SearchCache()


def get_search_cache() -> SearchCache:
    return _search_cache

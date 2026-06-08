"""DuckDuckGo Search Adapter。

使用 ``ddgs`` 库（duckduckgo-search 的继任包），
将原始结果转换为统一的 ``SearchResponse`` 格式。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ddgs import DDGS

from src.tools.search.adapter import SearchAdapter, SearchResponse, SearchResult

_MAX_RESULTS = 5
_DEFAULT_TIMEOUT = 8  # 秒
_HARD_TIMEOUT = _DEFAULT_TIMEOUT + 5


class DuckDuckGoAdapter(SearchAdapter):
    """DuckDuckGo 搜索引擎适配器。"""

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def search(self, query: str, max_results: int = _MAX_RESULTS) -> SearchResponse:
        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, self._do_search, query, max_results),
                timeout=_HARD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"DuckDuckGo 搜索超时（{_HARD_TIMEOUT}s）") from None
        except Exception as exc:
            raise RuntimeError(f"DuckDuckGo 搜索失败：{exc}") from exc

        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
                source="duckduckgo",
            )
            for r in raw
        ]
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            source="duckduckgo",
        )

    @staticmethod
    def _do_search(query: str, max_results: int) -> list[dict[str, Any]]:
        with DDGS(timeout=_DEFAULT_TIMEOUT) as ddgs:
            return list(ddgs.text(query, max_results=max_results))

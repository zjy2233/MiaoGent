"""Tavily Web Search API Adapter。

使用 Tavily Search API（专为 LLM Agent 优化的搜索引擎）。
需要配置 ``TAVILY_API_KEY`` 环境变量。
未配置时 adapter 自动标记为不可用。
"""

from __future__ import annotations

import os

from langsmith import traceable

from src.tools.search.adapter import SearchAdapter, SearchResponse, SearchResult

_ENV_KEY = "TAVILY_API_KEY"
_MAX_RESULTS = 5


class TavilyAdapter(SearchAdapter):
    """Tavily 搜索引擎适配器。

    如果 ``TAVILY_API_KEY`` 未设置，``available`` 返回 ``False``。
    """

    def __init__(self) -> None:
        self._api_key = os.getenv(_ENV_KEY, "").strip()

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @traceable(name="tavily_search")
    async def search(self, query: str, max_results: int = _MAX_RESULTS) -> SearchResponse:
        if not self._api_key:
            raise RuntimeError("Tavily API key 未配置，请设置 TAVILY_API_KEY 环境变量")

        import asyncio
        loop = asyncio.get_event_loop()

        def _do_search() -> dict:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self._api_key)
            return client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
            )

        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, _do_search),
                timeout=15,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Tavily 搜索超时（15s）") from None
        except Exception as exc:
            raise RuntimeError(f"Tavily 搜索失败：{exc}") from exc

        results_raw = raw.get("results", [])
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source="tavily",
            )
            for item in results_raw[:max_results]
        ]
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            source="tavily",
        )

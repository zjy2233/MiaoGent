"""Bing Web Search API Adapter。

使用 Microsoft Bing Web Search API v7。
需要配置 ``BING_API_KEY`` 环境变量（Azure 免费额度每月 1000 次调用）。
未配置时 adapter 自动标记为不可用。
"""

from __future__ import annotations

import os
from typing import Any

import requests

from src.tools.search.adapter import SearchAdapter, SearchResponse, SearchResult

_ENV_KEY = "BING_API_KEY"
_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"
_MAX_RESULTS = 5
_TIMEOUT = 8


class BingAdapter(SearchAdapter):
    """Bing 搜索引擎适配器。

    如果 ``BING_API_KEY`` 未设置，``available`` 返回 ``False``。
    """

    def __init__(self) -> None:
        self._api_key = os.getenv(_ENV_KEY, "").strip()

    @property
    def name(self) -> str:
        return "bing"

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, max_results: int = _MAX_RESULTS) -> SearchResponse:
        if not self._api_key:
            raise RuntimeError("Bing API key 未配置，请设置 BING_API_KEY 环境变量")

        import asyncio
        loop = asyncio.get_event_loop()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, self._do_request, query, max_results),
                timeout=_TIMEOUT + 5,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Bing 搜索超时") from None
        except requests.RequestException as exc:
            raise RuntimeError(f"Bing 搜索请求失败：{exc}") from exc

        web_pages = raw.get("webPages", {})
        values = web_pages.get("value", [])

        results = [
            SearchResult(
                title=item.get("name", ""),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
                source="bing",
            )
            for item in values[:max_results]
        ]
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            source="bing",
        )

    def _do_request(self, query: str, count: int) -> dict[str, Any]:
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        params = {"q": query, "count": count, "mkt": "zh-CN"}
        resp = requests.get(_ENDPOINT, headers=headers, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

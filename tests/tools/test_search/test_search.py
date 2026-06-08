"""Tests for the unified search tool (auto-fallback + cache + news mode)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.search import search
from src.tools.search.cache import get_search_cache


def _ainvoke(tool, **kwargs):
    """Helper to invoke a StructuredTool asynchronously."""
    return tool.ainvoke(kwargs)


class TestSearchTool:
    def test_is_langchain_tool(self) -> None:
        from langchain_core.tools import BaseTool

        assert isinstance(search, BaseTool)
        assert search.name == "search"

    @patch("src.tools.search.tavily.TavilyAdapter.search")
    @pytest.mark.asyncio
    async def test_text_search_delegates_to_adapter(self, mock_search: MagicMock) -> None:
        from src.tools.search.adapter import SearchResponse, SearchResult

        mock_search.return_value = SearchResponse(
            query="python",
            results=[SearchResult(title="Python", url="https://python.org")],
            source="tavily",
        )
        get_search_cache().clear()

        result = await search.ainvoke({"query": "python"})
        assert "Python" in result
        assert "python.org" in result

    @patch("src.tools.search.fetch_hot_search", return_value="百度热搜 Top 3：\n1. x")
    @pytest.mark.asyncio
    async def test_news_mode(self, mock_hot: MagicMock) -> None:
        result = await search.ainvoke({"query": "", "topic": "news"})
        mock_hot.assert_called_once()
        assert "百度热搜" in result

    @pytest.mark.asyncio
    async def test_empty_query_error(self) -> None:
        result = await search.ainvoke({"query": ""})
        assert "错误" in result and "搜索关键词" in result

    @pytest.mark.asyncio
    async def test_whitespace_query_error(self) -> None:
        result = await search.ainvoke({"query": "   "})
        assert "错误" in result and "搜索关键词" in result

    @patch("src.tools.search.tavily.TavilyAdapter.search")
    @pytest.mark.asyncio
    async def test_cache_hit(self, mock_search: MagicMock) -> None:
        from src.tools.search.adapter import SearchResponse, SearchResult

        mock_search.return_value = SearchResponse(
            query="cached",
            results=[SearchResult(title="Cached")],
            source="tavily",
        )
        get_search_cache().clear()

        # First call: should hit adapter
        result1 = await search.ainvoke({"query": "cached"})
        assert mock_search.call_count == 1

        # Second call: should use cache, not hit adapter
        result2 = await search.ainvoke({"query": "cached"})
        assert mock_search.call_count == 1  # no extra call
        assert result1 == result2

    @patch("src.tools.search.tavily.TavilyAdapter.search")
    @pytest.mark.asyncio
    async def test_adapter_failure_shows_error(self, mock_search: MagicMock) -> None:
        mock_search.side_effect = RuntimeError("network error")
        get_search_cache().clear()

        result = await search.ainvoke({"query": "fail"})
        assert "错误" in result
        assert "所有搜索引擎" in result


class TestSearchAdapterFallback:
    """验证当 Tavily + DuckDuckGo 都失败时是否尝试 Bing。"""

    @patch("src.tools.search.tavily.TavilyAdapter.search")
    @patch("src.tools.search.duckduckgo.DuckDuckGoAdapter.search")
    @patch("src.tools.search.bing.BingAdapter.search")
    @patch("src.tools.search.bing.BingAdapter.available", new_callable=lambda: True)
    @pytest.mark.asyncio
    async def test_fallback_to_bing(
        self,
        mock_bing_available,
        mock_bing_search: MagicMock,
        mock_ddg_search: MagicMock,
        mock_tavily_search: MagicMock,
    ) -> None:
        from src.tools.search.adapter import SearchResponse, SearchResult

        mock_tavily_search.side_effect = RuntimeError("Tavily failed")
        mock_ddg_search.side_effect = RuntimeError("DDG failed")
        mock_bing_search.return_value = SearchResponse(
            query="fallback",
            results=[SearchResult(title="Bing Fallback", source="bing")],
            source="bing",
        )
        get_search_cache().clear()

        result = await search.ainvoke({"query": "fallback"})
        assert "Bing Fallback" in result
        mock_tavily_search.assert_called_once()
        mock_ddg_search.assert_called_once()
        mock_bing_search.assert_called_once()

"""Tests for TavilyAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.search.tavily import TavilyAdapter


class TestTavilyAdapter:
    def test_name(self) -> None:
        adapter = TavilyAdapter()
        assert adapter.name == "tavily"

    @patch.dict("os.environ", {"TAVILY_API_KEY": ""})
    def test_not_available_without_api_key(self) -> None:
        adapter = TavilyAdapter()
        assert not adapter.available

    @patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-key"})
    def test_available_with_api_key(self) -> None:
        adapter = TavilyAdapter()
        assert adapter.available

    @patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-key"})
    @patch("tavily.TavilyClient")
    @pytest.mark.asyncio
    async def test_search_returns_formatted_response(
        self, mock_client: MagicMock
    ) -> None:
        mock_instance = MagicMock()
        mock_instance.search.return_value = {
            "results": [
                {
                    "title": "Tavily Result",
                    "url": "https://example.com/result",
                    "content": "A tavily search result with advanced depth",
                }
            ]
        }
        mock_client.return_value = mock_instance

        adapter = TavilyAdapter()
        response = await adapter.search("test query")
        assert response.source == "tavily"
        assert len(response.results) == 1
        assert response.results[0].title == "Tavily Result"
        assert response.results[0].url == "https://example.com/result"
        assert response.results[0].snippet == "A tavily search result with advanced depth"

    @patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-key"})
    @patch("tavily.TavilyClient")
    @pytest.mark.asyncio
    async def test_search_no_results(self, mock_client: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_instance.search.return_value = {"results": []}
        mock_client.return_value = mock_instance

        adapter = TavilyAdapter()
        response = await adapter.search("nothing")
        assert len(response.results) == 0

    @patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-key"})
    @patch("tavily.TavilyClient")
    @pytest.mark.asyncio
    async def test_search_timeout(self, mock_client: MagicMock) -> None:
        import time as time_module

        mock_instance = MagicMock()
        # 模拟长时间阻塞触发超时
        def slow_search(*args, **kwargs):
            raise TimeoutError("Tavily 搜索超时（15s）")

        mock_instance.search.side_effect = slow_search
        mock_client.return_value = mock_instance

        adapter = TavilyAdapter()
        with pytest.raises(TimeoutError, match="Tavily 搜索超时"):
            await adapter.search("test")

"""Tests for BingAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.search.bing import BingAdapter


class TestBingAdapter:
    def test_name(self) -> None:
        adapter = BingAdapter()
        assert adapter.name == "bing"

    def test_not_available_without_api_key(self) -> None:
        adapter = BingAdapter()
        assert not adapter.available

    @patch.dict("os.environ", {"BING_API_KEY": "test-key"})
    def test_available_with_api_key(self) -> None:
        adapter = BingAdapter()
        assert adapter.available

    @patch.dict("os.environ", {"BING_API_KEY": "test-key"})
    @patch("src.tools.search.bing.requests.get")
    @pytest.mark.asyncio
    async def test_search_returns_formatted_response(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "webPages": {
                "value": [
                    {
                        "name": "Bing Result",
                        "url": "https://bing.com/result",
                        "snippet": "A bing search result",
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        adapter = BingAdapter()
        response = await adapter.search("test query")
        assert response.source == "bing"
        assert len(response.results) == 1
        assert response.results[0].title == "Bing Result"
        assert response.results[0].url == "https://bing.com/result"
        assert response.results[0].snippet == "A bing search result"

    @patch.dict("os.environ", {"BING_API_KEY": "test-key"})
    @patch("src.tools.search.bing.requests.get")
    @pytest.mark.asyncio
    async def test_search_no_results(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_get.return_value = mock_resp

        adapter = BingAdapter()
        response = await adapter.search("nothing")
        assert len(response.results) == 0

    @patch.dict("os.environ", {"BING_API_KEY": "test-key"})
    @patch("src.tools.search.bing.requests.get")
    @pytest.mark.asyncio
    async def test_search_http_error(self, mock_get: MagicMock) -> None:
        import requests

        mock_get.side_effect = requests.RequestException("HTTP 403")

        adapter = BingAdapter()
        with pytest.raises(RuntimeError, match="Bing 搜索请求失败"):
            await adapter.search("test")

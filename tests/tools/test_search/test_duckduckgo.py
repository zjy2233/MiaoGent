"""Tests for DuckDuckGoAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.search.duckduckgo import DuckDuckGoAdapter


class TestDuckDuckGoAdapter:
    @pytest.fixture
    def adapter(self) -> DuckDuckGoAdapter:
        return DuckDuckGoAdapter()

    def test_name(self, adapter: DuckDuckGoAdapter) -> None:
        assert adapter.name == "duckduckgo"

    @patch("src.tools.search.duckduckgo.DDGS")
    @pytest.mark.asyncio
    async def test_search_returns_formatted_response(
        self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter
    ) -> None:
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__.return_value = mock_ddgs
        mock_ddgs.__exit__.return_value = False
        mock_ddgs.text.return_value = [
            {"title": "Python", "href": "https://python.org", "body": "Python language"},
        ]
        mock_ddgs_cls.return_value = mock_ddgs

        response = await adapter.search("python")
        assert response.source == "duckduckgo"
        assert len(response.results) == 1
        assert response.results[0].title == "Python"
        assert response.results[0].url == "https://python.org"
        assert response.results[0].snippet == "Python language"

    @patch("src.tools.search.duckduckgo.DDGS")
    @pytest.mark.asyncio
    async def test_search_empty_results(
        self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter
    ) -> None:
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__.return_value = mock_ddgs
        mock_ddgs.__exit__.return_value = False
        mock_ddgs.text.return_value = []
        mock_ddgs_cls.return_value = mock_ddgs

        response = await adapter.search("nonexistent")
        assert len(response.results) == 0
        assert response.total == 0

    @patch("src.tools.search.duckduckgo.DDGS")
    @pytest.mark.asyncio
    async def test_search_timeout(
        self, mock_ddgs_cls: MagicMock, adapter: DuckDuckGoAdapter
    ) -> None:
        """_do_search runs in executor, so a slow sync call triggers timeout."""

        def _slow(*args, **kwargs):
            import time
            time.sleep(100)

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__.return_value = mock_ddgs
        mock_ddgs.__exit__.return_value = False
        mock_ddgs.text.side_effect = _slow
        mock_ddgs_cls.return_value = mock_ddgs

        with pytest.raises(TimeoutError):
            await adapter.search("slow")

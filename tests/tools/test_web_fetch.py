"""Tests for WebFetch tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.web_fetch import fetch_page, web_fetch


class TestFetchPage:
    def test_empty_url_error(self) -> None:
        result = fetch_page("")
        assert "错误" in result

    def test_missing_protocol_error(self) -> None:
        result = fetch_page("example.com/page")
        assert "错误" in result

    @patch("src.tools.web_fetch.requests.get")
    def test_successful_fetch(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.iter_content.return_value = [
            b"<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        ]
        mock_get.return_value = mock_resp
        mock_resp.__enter__.return_value = mock_resp

        result = fetch_page("https://example.com")
        assert "Title" in result
        assert "Hello world" in result

    @patch("src.tools.web_fetch.requests.get")
    def test_removes_script_tags(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.iter_content.return_value = [
            b"<html><script>alert('xss')</script><body>Content</body></html>"
        ]
        mock_get.return_value = mock_resp
        mock_resp.__enter__.return_value = mock_resp

        result = fetch_page("https://example.com")
        assert "Content" in result
        assert "alert" not in result

    @patch("src.tools.web_fetch.requests.get")
    def test_truncates_long_content(self, mock_get: MagicMock) -> None:
        long_text = "A" * 10000
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.iter_content.return_value = [
            f"<html><body>{long_text}</body></html>".encode("utf-8")
        ]
        mock_get.return_value = mock_resp
        mock_resp.__enter__.return_value = mock_resp

        result = fetch_page("https://example.com")

        # Verify truncation: result should be shorter than the raw text
        assert len(result) < len(long_text)
        # Verify truncation message marker present
        assert "..." in result and "5000" in result

    @patch("src.tools.web_fetch.requests.get")
    def test_http_error(self, mock_get: MagicMock) -> None:
        import requests

        mock_get.side_effect = requests.RequestException("HTTP 404")

        result = fetch_page("https://example.com/404")
        assert "错误" in result

    @patch("src.tools.web_fetch.requests.get")
    def test_gbk_encoding(self, mock_get: MagicMock) -> None:
        """验证 GBK 编码页面正确解码。"""
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html; charset=gbk"}
        # "中文" in GBK encoding
        mock_resp.iter_content.return_value = [
            b"<html><body>\xd6\xd0\xce\xc4</body></html>"
        ]
        mock_get.return_value = mock_resp
        mock_resp.__enter__.return_value = mock_resp

        result = fetch_page("https://example.com")
        assert "中文" in result


class TestWebFetchTool:
    def test_is_langchain_tool(self) -> None:
        from langchain_core.tools import BaseTool

        assert isinstance(web_fetch, BaseTool)
        assert web_fetch.name == "web_fetch"
        assert "url" in web_fetch.args

    @patch("src.tools.web_fetch.fetch_page", return_value="page content")
    @pytest.mark.asyncio
    async def test_tool_calls_fetch_page(self, mock_fetch: MagicMock) -> None:
        result = await web_fetch.ainvoke({"url": "https://example.com"})
        assert result == "page content"
        mock_fetch.assert_called_once()

"""百度热搜抓取工具的单元测试：通过 mock requests.get 避免真实网络调用。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.hot_search import (
    BAIDU_HOT_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_N,
    fetch_hot_search,
)


def _baidu_html(titles: list[str]) -> str:
    """构造一个贴着百度热搜页面结构的最小 HTML。"""
    body = "\n".join(
        f'<div class="c-single-text-ellipsis">{t}</div>' for t in titles
    )
    return f"<html><body>{body}</body></html>"


def _mock_response(status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"HTTP {status_code}")
    return resp


class TestFetchHotSearch:
    @patch("src.tools.hot_search.requests.get")
    def test_returns_top_titles(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(
            200, _baidu_html(["词条 1", "词条 2", "词条 3"])
        )
        result = fetch_hot_search()
        assert "百度热搜" in result
        assert "词条 1" in result
        assert "词条 2" in result
        assert "词条 3" in result
        # 编号
        assert "1." in result
        assert "2." in result
        assert "3." in result

    @patch("src.tools.hot_search.requests.get")
    def test_respects_top_n(self, mock_get: MagicMock) -> None:
        titles = [f"词{i}" for i in range(50)]
        mock_get.return_value = _mock_response(200, _baidu_html(titles))
        result = fetch_hot_search(top_n=5)
        # 只有 5 条
        for i in range(5):
            assert f"词{i}" in result
        for i in range(5, 10):
            assert f"词{i}" not in result

    @patch("src.tools.hot_search.requests.get")
    def test_default_top_n_is_reasonable(self, mock_get: MagicMock) -> None:
        """默认值在 5-50 之间，避免占满 LLM 上下文。"""
        assert 5 <= DEFAULT_TOP_N <= 50

    @patch("src.tools.hot_search.requests.get")
    def test_strips_whitespace_and_drops_empty_titles(
        self, mock_get: MagicMock
    ) -> None:
        html = (
            '<div class="c-single-text-ellipsis">  有效词条  </div>'
            '<div class="c-single-text-ellipsis">   </div>'  # 空白
            '<div class="c-single-text-ellipsis">另一个</div>'
        )
        mock_get.return_value = _mock_response(200, html)
        result = fetch_hot_search()
        assert "有效词条" in result
        assert "另一个" in result
        # 空白条目不会出现
        assert "   " not in result

    @patch("src.tools.hot_search.requests.get")
    def test_returns_error_on_request_exception(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = RuntimeError("network unreachable")
        result = fetch_hot_search()
        assert "错误" in result
        assert "network unreachable" in result

    @patch("src.tools.hot_search.requests.get")
    def test_returns_error_on_http_error(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(503, "")
        result = fetch_hot_search()
        assert "错误" in result
        assert "HTTP 503" in result or "503" in result

    @patch("src.tools.hot_search.requests.get")
    def test_returns_error_on_empty_parse(self, mock_get: MagicMock) -> None:
        """HTML 结构变化时返回明确错误，便于监控告警。"""
        mock_get.return_value = _mock_response(200, "<html><body>变了</body></html>")
        result = fetch_hot_search()
        assert "错误" in result
        assert "页面结构变化" in result or "未解析到" in result

    @patch("src.tools.hot_search.requests.get")
    def test_url_param_is_used(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, _baidu_html(["x"]))
        fetch_hot_search(url="https://example.com/custom")
        args, kwargs = mock_get.call_args
        assert args[0] == "https://example.com/custom"

    @patch("src.tools.hot_search.requests.get")
    def test_timeout_param_is_passed(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, _baidu_html(["x"]))
        fetch_hot_search(timeout=3.5)
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("timeout") == 3.5

    @patch("src.tools.hot_search.requests.get")
    def test_user_agent_header_is_set(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, _baidu_html(["x"]))
        fetch_hot_search()
        headers = mock_get.call_args.kwargs.get("headers", {})
        assert "User-Agent" in headers
        # UA 至少 30 字符，是真 UA
        assert len(headers["User-Agent"]) > 30

    @patch("src.tools.hot_search.requests.get")
    def test_default_url_is_baidu(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, _baidu_html(["x"]))
        fetch_hot_search()
        assert BAIDU_HOT_URL in mock_get.call_args.args
        # 默认 timeout 合理
        assert 3 <= DEFAULT_TIMEOUT <= 30

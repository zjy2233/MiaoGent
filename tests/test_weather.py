"""天气工具的单元测试：通过 mock requests 避免真实网络调用。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests as _req

from src.tools.weather import fetch_weather, weather


def _wttr_payload() -> dict:
    """构造一个贴近 wttr.in j1 endpoint 结构的最小可用 payload。"""
    return {
        "current_condition": [
            {
                "temp_C": "25",
                "FeelsLikeC": "27",
                "humidity": "60",
                "windspeedKmph": "12",
                "weatherDesc": [{"value": "Sunny"}],
            }
        ],
        "nearest_area": [
            {
                "areaName": [{"value": "Beijing"}],
                "region": [{"value": "Beijing"}],
                "country": [{"value": "China"}],
            }
        ],
    }


def _mock_response(json_payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestFetchWeather:
    @patch("src.tools.weather.requests.get")
    def test_returns_text_from_wttr(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(_wttr_payload())
        result = fetch_weather("Beijing")
        assert "25" in result and "Sunny" in result
        mock_get.assert_called_once()
        called_url = mock_get.call_args.args[0]
        assert "Beijing" in called_url
        assert "wttr.in" in called_url

    @patch("src.tools.weather.requests.get")
    def test_handles_request_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = _req.RequestException("connection reset")
        result = fetch_weather("Tokyo")
        assert "Tokyo" in result and "错误" in result


class TestWeatherTool:
    def test_is_a_langchain_tool(self) -> None:
        from langchain_core.tools import BaseTool

        assert isinstance(weather, BaseTool)
        assert weather.name == "weather"
        assert "location" in weather.args

    @patch("src.tools.weather.fetch_weather", return_value="Shanghai: Rain +20C")
    def test_tool_invokes_fetch(self, mock_fetch: MagicMock) -> None:
        out = weather.invoke({"location": "Shanghai"})
        mock_fetch.assert_called_once_with("Shanghai")
        assert "Shanghai" in str(out)

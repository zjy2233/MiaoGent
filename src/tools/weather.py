"""天气查询工具：调用 wttr.in 的免费 API。

wttr.in 接受城市名作为路径，返回 ASCII 艺术或纯文本格式的当前天气。
我们用 ``format=j1`` 拿 JSON 一次，省得让 LLM 去解析 ASCII 图形。
"""

from __future__ import annotations

import urllib.parse

import requests
from langchain_core.tools import tool

WTTR_BASE_URL = "https://wttr.in"
DEFAULT_TIMEOUT = 10  # seconds


def _summarize(payload: dict, location: str) -> str:
    """把 wttr.in 的 JSON payload 压缩成 agent 友好的短文本。"""
    try:
        current = payload["current_condition"][0]
        area = payload.get("nearest_area", [{}])[0]
        area_name = (
            (area.get("areaName") or [{}])[0].get("value")
            or (area.get("region") or [{}])[0].get("value")
            or location
        )
        country = ((area.get("country") or [{}])[0].get("value") or "")
        desc = current["weatherDesc"][0]["value"]
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        humidity = current["humidity"]
        wind = current["windspeedKmph"]
        return (
            f"{area_name}{'，' + country if country else ''}：{desc}，"
            f"气温 {temp_c}°C（体感 {feels_like}°C），"
            f"湿度 {humidity}%，风速 {wind} km/h"
        )
    except (KeyError, IndexError, TypeError) as exc:
        return f"无法解析 {location} 的天气数据：{exc}"


def fetch_weather(location: str, *, timeout: float = DEFAULT_TIMEOUT) -> str:
    """获取指定地点的当前天气，返回简短中文描述。"""
    location = (location or "").strip()
    if not location:
        return "错误：请提供城市名"
    encoded = urllib.parse.quote(location)
    url = f"{WTTR_BASE_URL}/{encoded}?format=j1&lang=zh"
    try:
        resp = requests.get(url, timeout=timeout, headers={"Accept-Language": "zh"})
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        return f"错误：获取 {location} 天气失败（{exc}）"
    except ValueError as exc:
        return f"错误：解析 {location} 天气响应失败（{exc}）"
    return _summarize(payload, location)


@tool
def weather(location: str) -> str:
    """查询指定城市的当前天气。

    Args:
        location: 城市名，中英文均可，例如 ``Beijing``、``上海``、``Tokyo``。

    Returns:
        一句话天气描述，包含天气现象、气温、体感温度、湿度、风速。
        **仅返回当前观测，无法预报未来天气。**
    """
    return fetch_weather(location)

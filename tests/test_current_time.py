"""查看当前时间工具的单元测试。"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from src.tools.current_time import current_time, _now_string


class TestCurrentTimeTool:
    """@tool 装饰器把函数变成 BaseTool，工具名取自函数名。"""

    def test_is_a_langchain_tool(self) -> None:
        from langchain_core.tools import BaseTool

        assert isinstance(current_time, BaseTool)
        assert current_time.name == "current_time"
        # 不带参数，args 应该是空 schema
        assert current_time.args == {}

    def test_invoke_returns_string(self) -> None:
        result = current_time.invoke({})
        assert isinstance(result, str)
        # 必须同时包含本地时间和 UTC 时间
        assert "本地时间" in result
        assert "UTC 时间" in result

    def test_output_contains_parseable_timestamps(self) -> None:
        """输出里的两个时间戳都应当能解析回 datetime。"""
        result = current_time.invoke({})
        # 本地时间形如 "2026-06-01 14:23:45 CST"
        # UTC 时间形如 "2026-06-01 06:23:45"
        pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        matches = pattern.findall(result)
        assert len(matches) >= 2, f"输出里至少要有两个时间戳：{result!r}"
        # 至少有一个时间戳能解析（UTC 那一段无时区后缀，优先匹配）
        parsed = [datetime.strptime(m, "%Y-%m-%d %H:%M:%S") for m in matches]
        assert all(isinstance(p, datetime) for p in parsed)

    def test_output_changes_over_time(self) -> None:
        """两次调用应当得到时间上接近的结果（差值在合理范围内）。"""
        first = _now_string()
        # 不需要真的 sleep——只要两次的 UTC 秒数差 ≤ 5 就视为"同一次调用窗口"
        second = _now_string()
        m1 = re.search(r"UTC 时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", first)
        m2 = re.search(r"UTC 时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", second)
        assert m1 and m2
        t1 = datetime.strptime(m1.group(1), "%Y-%m-%d %H:%M:%S")
        t2 = datetime.strptime(m2.group(1), "%Y-%m-%d %H:%M:%S")
        delta = abs((t2 - t1).total_seconds())
        assert delta < 5, f"两次调用时间差过大：{delta}s"

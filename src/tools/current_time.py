"""查看当前时间的工具。

不带参数，返回本地时间（带时区缩写）和 UTC 时间，
方便 LLM 回答"现在几点 / 当前时间 / 现在几点了"之类的问题。
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool


def _now_string() -> str:
    """拼出 agent 友好的时间字符串。"""
    local = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    return (
        f"本地时间：{local.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
        f"UTC 时间：{utc.strftime('%Y-%m-%d %H:%M:%S')}"
    )


_TOOL_GUIDE = "用户问当前时间时必须使用 current_time 获取实际时间，不要凭印象回答。"


@tool
def current_time() -> str:
    """查看当前时间。

    Returns:
        包含本地时间和 UTC 时间的字符串。本地时间带时区缩写，
        UTC 时间用 ``YYYY-MM-DD HH:MM:SS`` 格式。
    """
    return _now_string()

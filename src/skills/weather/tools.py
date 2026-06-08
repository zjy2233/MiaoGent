"""天气查询工具包。"""

from langchain_core.tools import tool


@tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气。

    Args:
        city: 城市名称。

    Returns:
        当前天气信息。
    """
    return f"（{city} 的实时天气）"


@tool
def get_forecast(city: str, days: int = 3) -> str:
    """获取指定城市的天气预报。

    Args:
        city: 城市名称。
        days: 预报天数，默认 3 天。

    Returns:
        天气预报信息。
    """
    return f"（{city} 未来 {days} 天的天气预报）"


__tool_list__ = [get_weather, get_forecast]

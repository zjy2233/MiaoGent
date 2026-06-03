"""Agent 可调用的工具集合。

工具用 ``@tool`` 装饰器声明，本身就是 :class:`BaseTool` 实例，
可以直接传入 ``create_agent(tools=[...])``。
"""

from src.tools.calculator import calculator
from src.tools.current_time import current_time
from src.tools.weather import weather
from src.tools.web_search import web_search
from src.tools.shell import shell

__all__ = ["calculator", "current_time", "weather", "web_search", "shell"]

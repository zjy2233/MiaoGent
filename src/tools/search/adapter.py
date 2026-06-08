"""SearchAdapter 抽象基类 + 统一结果模型。

设计思路（借鉴 Claude Code WebSearch 的 Adapter 模式）：
- ``SearchResult`` 统一不同搜索引擎的返回格式
- ``SearchAdapter`` 定义统一接口，各搜索引擎实现自己的适配器
- 自动 fallback：主适配器失败 → 备选适配器
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """统一搜索结果模型。"""

    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""  # 搜索引擎标识，如 "duckduckgo"、"bing"


@dataclass
class SearchResponse:
    """统一搜索响应。"""

    query: str = ""
    results: list[SearchResult] = field(default_factory=list)
    total: int = 0
    source: str = ""


def _format_results(query: str, results: list[SearchResult], source: str) -> str:
    """将搜索结果格式化为 agent 可读文本。"""
    if not results:
        return f"未找到关于「{query}」的相关结果（{source}）"

    lines = [f"搜索「{query}」的结果（{source}，前 {len(results)} 条）：", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.title}")
        if r.url:
            lines.append(f"   链接：{r.url}")
        if r.snippet:
            lines.append(f"   摘要：{r.snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


class SearchAdapter(ABC):
    """搜索引擎适配器基类。

    子类需实现 ``search()`` 方法，返回 ``SearchResponse``。
    """

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> SearchResponse:
        """执行搜索。

        Args:
            query: 搜索关键词。
            max_results: 返回结果条数上限。

        Returns:
            SearchResponse: 包含 ``results`` 列表的响应。
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """搜索引擎名称。"""
        ...

"""统一搜索入口：Adapter 模式 + 缓存 + 自动 Fallback。

用法：:
    from src.tools.search import search

    result = await search("Python 教程")
    result = await search("今日热点", topic="news")

``topic="news"`` 模式仍然走 Baidu 热搜（独立于 adapter 架构）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from src.tools.hot_search import fetch_hot_search
from src.tools.search.adapter import _format_results
from src.tools.search.bing import BingAdapter
from src.tools.search.cache import get_search_cache
from src.tools.search.classifier import QueryComplexity, classify_query
from src.tools.search.duckduckgo import DuckDuckGoAdapter
from src.tools.search.progressive import ProgressiveSearchEngine
from src.tools.search.tavily import TavilyAdapter

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 5

# 适配器注册表（顺序 = fallback 优先级）
_ADAPTERS: list[Any] = [
    TavilyAdapter(),
    DuckDuckGoAdapter(),
    BingAdapter(),
]


_TOOL_GUIDE = (
    "search 是增强版搜索（Tavily + DuckDuckGo + Bing 自动 fallback + 缓存），优先于 web_search。"
    "用户问新闻/热搜时使用 search(topic='news')。"
    "对于复杂问题（比较、分析、原因等）会自动使用渐进式多轮搜索，无需额外参数。"
)


@tool
async def search(query: str, topic: str = "text") -> str:
    """联网搜索关键词或查询百度热搜榜。

    Args:
        query: 搜索关键词，中英文均可。``topic="news"`` 时此参数被忽略。
        topic: 搜索类型。
            - ``"text"``（默认）：通用网页搜索，自动选择可用的搜索引擎
            - ``"news"``：抓取百度热搜 Top 20

    Returns:
        格式化字符串。text 模式包含标题/链接/摘要；news 模式只含词条。
    """
    # news 模式：直接走百度热搜
    if topic == "news":
        return fetch_hot_search()

    query = (query or "").strip()
    if not query:
        return "错误：请提供搜索关键词"

    # 查询复杂度分类：简单→单轮（快+缓存），复杂→渐进式（准+多轮）
    complexity = classify_query(query)
    is_complex = complexity == QueryComplexity.COMPLEX

    # 简单查询：走缓存 + 单轮搜索
    if not is_complex:
        cache_key = f"text:{query}"
        cache = get_search_cache()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        errors: list[str] = []
        for adapter in _ADAPTERS:
            if hasattr(adapter, "available") and not adapter.available:
                continue
            try:
                response = await adapter.search(query, max_results=_DEFAULT_MAX_RESULTS)
                formatted = _format_results(query, response.results, response.source)
                cache.set(cache_key, formatted)
                return formatted
            except Exception as exc:
                logger.warning("search adapter %s failed: %s", adapter.name, exc)
                errors.append(f"{adapter.name}: {exc}")
                continue

        err_msg = f"错误：所有搜索引擎均不可用（{'；'.join(errors)}）"
        return err_msg

    # 复杂查询：走渐进式搜索（多轮迭代 + LLM 评估 + 兜底合成）
    logger.info("复杂查询「%s」进入渐进式搜索", query)
    try:
        engine = ProgressiveSearchEngine(_ADAPTERS)
        return await engine.search(query, max_results=_DEFAULT_MAX_RESULTS)
    except Exception as exc:
        logger.error("渐进式搜索失败：%s", exc)
        return f"错误：复杂查询搜索失败（{exc}）"

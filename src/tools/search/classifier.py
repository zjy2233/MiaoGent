"""Query Classifier — 基于启发式规则快速区分简单/复杂查询。

设计目标：
- 零外部依赖，纯规则匹配，纳秒级分类
- 简单查询 → 单轮搜索（快、便宜、走缓存）
- 复杂查询 → 渐进式搜索（准、贵、多轮迭代 + LLM 评估）
"""

from __future__ import annotations

import re
from enum import Enum


class QueryComplexity(Enum):
    """查询复杂度枚举。"""

    SIMPLE = "simple"
    COMPLEX = "complex"


# 复杂查询的模式特征
_COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    # 比较类
    re.compile(r"(?:compare|comparison|对比|比较|区别|差异|异同|vs\.?)", re.IGNORECASE),
    re.compile(r"(?:和|与|跟).{1,10}(?:有什么不同|有何区别|差异|比较)", re.IGNORECASE),
    re.compile(r"(?:pros? and cons|优缺点|利弊|优劣|好处.*坏处)", re.IGNORECASE),
    # 分析类
    re.compile(r"(?:analyze|analysis|分析|评估|评价|阐述|论述)", re.IGNORECASE),
    re.compile(r"(?:impact|影响|作用|效果|后果|副作用)", re.IGNORECASE),
    re.compile(r"(?:relationship|relation|关联|相关性|关系|联系)", re.IGNORECASE),
    re.compile(r"(?:trend|趋势|发展|演变|变化|演化)", re.IGNORECASE),
    re.compile(r"(?:cause|原因|导致|因素|根源|根因)", re.IGNORECASE),
    # 解释类
    re.compile(r"(?:why|为什么|为何|怎么|如何|怎样)", re.IGNORECASE),
    re.compile(r"(?:how does|how to|如何实现|如何做|怎样)", re.IGNORECASE),
    re.compile(r"(?:explain|解释|说明|描述|讲讲)", re.IGNORECASE),
    re.compile(r"(?:what is the|what are the|什么是|什么是)", re.IGNORECASE),
    # 综合类
    re.compile(r"(?:solution|解决方案|对策|措施|建议)", re.IGNORECASE),
    re.compile(r"(?:future|未来|前景|预测|展望|趋势)", re.IGNORECASE),
    re.compile(r"(?:summary|总结|概括|归纳|综述)", re.IGNORECASE),
    # 多主题并列（逗号/和分隔多个主题）
    re.compile(r"(?:和|与|及).{0,5}(?:和|与|及)"),
    re.compile(r".{10,}[,，].{5,}[,，]"),
]


def classify_query(query: str) -> QueryComplexity:
    """基于启发式规则快速分类查询复杂度。

    注意：中文无空格分词，``split()`` 对中文文本不可靠，
    因此同时使用字符数（``char_count``）作为补充信号。

    Args:
        query: 用户原始搜索查询。

    Returns:
        ``QueryComplexity.SIMPLE`` 或 ``QueryComplexity.COMPLEX``。
    """
    q = query.strip()

    # 空查询视为简单
    if not q:
        return QueryComplexity.SIMPLE

    words = q.split()
    char_count = len(q.replace(" ", ""))

    # 超短查询（<=3 英文词 且 <=5 字符，或 <=4 中文字符）→ 简单
    # 中文无空格，len(words) 会低估长度，用 char_count 兜底
    is_very_short = (len(words) <= 3 and char_count <= 5) or char_count <= 4
    if is_very_short and not _has_question_mark(q):
        return QueryComplexity.SIMPLE

    # 超长查询 → 复杂
    if len(words) > 20 or char_count > 50:
        return QueryComplexity.COMPLEX

    # 中等长度且有问号 → 复杂
    if char_count > 10 and _has_question_mark(q):
        return QueryComplexity.COMPLEX

    # 模式匹配（中文为主时依赖此阶段兜底分类）
    for pattern in _COMPLEX_PATTERNS:
        if pattern.search(q):
            return QueryComplexity.COMPLEX

    return QueryComplexity.SIMPLE


def _has_question_mark(text: str) -> bool:
    return "?" in text or "？" in text

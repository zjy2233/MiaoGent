"""ReWOO 意图判定：决定用户请求是否应使用规划-执行模式。

规则：
- 单工具任务 → 标准 ReAct
- 多独立子任务 + 工具数 ≥ 3 → ReWOO
- 包含 "first/second/third" 或中文"首先/然后/接着"等模式 → ReWOO
"""

from __future__ import annotations

import re

# 多步骤任务模式（只要命中任意模式即加分）
_COMPLEX_PATTERNS: list[tuple[str, int]] = [
    # 中文序列模式
    (r"首先.*然后|先.*再|再.*最后|然后.*接着|接着.*最后", 3),
    (r"同时.*(?:并且|同时|以及)", 2),
    # 英文序列模式
    (r"first.*then|first.*second|then.*finally", 3),
    # 多个独立动词模式（搜索+读取+检查 等）
    (r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询).*"
     r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询).*"
     r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询)", 3),
    (r"(?:search|read|find|open|run|create|write|check|fetch).*"
     r"(?:search|read|find|open|run|create|write|check|fetch).*"
     r"(?:search|read|find|open|run|create|write|check|fetch)", 3),
    # 编号列表
    (r"[1-9][\.\)、].*[2-9][\.\)、]", 3),
]


def estimate_tool_count(user_message: str) -> int:
    """估算用户消息可能需要的工具调用数。

    基于逗号、连接词和列表标记进行简单计数。
    """
    count = 1  # 至少 1 个工具

    # 逗号/顿号分隔的子任务
    separators = (
        user_message.count("，") + user_message.count(",") +
        user_message.count("、") + user_message.count("；")
    )
    if separators >= 2:
        count += separators

    # 连接词
    connectors = re.findall(
        r"(并且|同时|以及|还有|另外|然后|接着|此外|首先|然后|最后|first|also|additionally|furthermore|moreover)",
        user_message,
        re.IGNORECASE,
    )
    count += len(connectors)

    # 编号列表（中文/英文）
    numbered = re.findall(r"(?:^|\s)[1-9][\.\)、]", user_message)
    if len(numbered) >= 2:
        count += len(numbered)

    return min(count, 10)


def should_use_rewoo(user_message: str) -> bool:
    """判断是否应该使用 ReWOO 规划-执行模式。

    Args:
        user_message: 用户消息文本。

    Returns:
        True 表示应使用 ReWOO。
    """
    msg_lower = user_message.lower()

    # 检查复杂模式
    pattern_matches = 0
    for pattern, weight in _COMPLEX_PATTERNS:
        if re.search(pattern, msg_lower):
            pattern_matches += weight

    # 估算工具数
    tool_estimate = estimate_tool_count(user_message)

    # 决策规则：
    # 1. 明确的多步骤模式 + 工具估算 ≥ 2 → ReWOO
    if pattern_matches >= 1 and tool_estimate >= 2:
        return True

    # 2. 工具估算 ≥ 5 → 高复杂度，ReWOO
    if tool_estimate >= 5:
        return True

    # 3. 工具估算 ≥ 3 且有多分隔符 → 多独立子任务，ReWOO
    separators = (
        user_message.count("，") + user_message.count(",") +
        user_message.count("、") + user_message.count("；")
    )
    if tool_estimate >= 3 and separators >= 2:
        return True

    # 4. 其它 → ReAct
    return False

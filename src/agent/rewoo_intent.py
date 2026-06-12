"""ReWOO 意图判定：决定用户请求是否应使用规划-执行模式。

规则：
- 单工具任务 → 标准 ReAct
- 多独立子任务 + 工具数 ≥ 3 → ReWOO
- 包含 "first/second/third" 或中文"首先/然后/接着"等模式 → ReWOO
"""

from __future__ import annotations

import re

# ── Pattern weights ──
_WEIGHT_SEQUENCE = 3       # 序列模式（首先→然后）
_WEIGHT_PARALLEL = 2       # 并行模式（同时/并且）
_WEIGHT_MULTI_VERB = 3     # 多个独立动词
_WEIGHT_NUMBERED = 3       # 编号列表

# ── Decision thresholds ──
_TOOL_ESTIMATE_CAP = 10             # 工具估算上限
_PATTERN_MIN_MATCHES = 1            # 最小模式匹配数
_PATTERN_LOW_TOOL_ESTIMATE = 3      # 有模式时的低工具估算阈值
_PATTERN_HIGH_TOOL_ESTIMATE = 5     # 无模式时的高工具估算阈值
_SEPARATOR_MULTI_THRESHOLD = 2      # 多分隔符判定阈值
_TOOL_SEPARATOR_THRESHOLD = 4       # 工具估算+分隔符判断阈值
_SHORT_MSG_LENGTH = 20              # 短消息长度阈值

# 多步骤任务模式（只要命中任意模式即加分）
_COMPLEX_PATTERNS: list[tuple[str, int]] = [
    # 中文序列模式
    (r"首先.*然后|先.*再|再.*最后|然后.*接着|接着.*最后", _WEIGHT_SEQUENCE),
    (r"同时.*(?:并且|同时|以及)", _WEIGHT_PARALLEL),
    # 英文序列模式
    (r"first.*then|first.*second|then.*finally", _WEIGHT_SEQUENCE),
    # 多个独立动词模式（搜索+读取+检查 等）
    (r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询).*"
     r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询).*"
     r"(?:搜索|查找|读取|打开|运行|创建|写入|检查|查看|查询)", _WEIGHT_MULTI_VERB),
    (r"(?:search|read|find|open|run|create|write|check|fetch).*"
     r"(?:search|read|find|open|run|create|write|check|fetch).*"
     r"(?:search|read|find|open|run|create|write|check|fetch)", _WEIGHT_MULTI_VERB),
    # 编号列表
    (r"[1-9][\.\)、].*[2-9][\.\)、]", _WEIGHT_NUMBERED),
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

    return min(count, _TOOL_ESTIMATE_CAP)


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
    # 1. 明确的多步骤模式 + 工具估算 ≥ PATTERN_LOW_TOOL_ESTIMATE → ReWOO
    if pattern_matches >= _PATTERN_MIN_MATCHES and tool_estimate >= _PATTERN_LOW_TOOL_ESTIMATE:
        return True

    # 2. 工具估算 ≥ PATTERN_HIGH_TOOL_ESTIMATE → 高复杂度，ReWOO（即使没有明确模式）
    if tool_estimate >= _PATTERN_HIGH_TOOL_ESTIMATE:
        return True

    # 3. 简短查询不触发 ReWOO（如 "1. A 2. B" 列表，保留给 ReAct 处理）
    if len(user_message) < _SHORT_MSG_LENGTH:
        return False

    # 4. 工具估算 ≥ TOOL_SEPARATOR_THRESHOLD 且有多分隔符 → 多独立子任务，ReWOO
    separators = (
        user_message.count("，") + user_message.count(",") +
        user_message.count("、") + user_message.count("；")
    )
    if tool_estimate >= _TOOL_SEPARATOR_THRESHOLD and separators >= _SEPARATOR_MULTI_THRESHOLD:
        return True

    # 5. 其它 → ReAct
    return False

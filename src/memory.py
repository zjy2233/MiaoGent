"""记忆管理器：限制 messages 长度 / 轮数，超限则调用 LLM 增量摘要。

设计：
- 摘要存到 state 的独立字段 ``summary``，**不**和系统提示词拼接
- 压缩后保留最近 ``max_turns`` 轮原始消息，老的塞进 summary
- 增量式：每次压缩基于"上一份摘要 + 新增片段"合并，不重读全部历史
- LLM 摘要失败时降级为滑动窗口：直接丢老消息，summary 字段不动

注意：因为 state 的 ``messages`` 字段使用 ``add_messages`` reducer，
普通 ``update_state({"messages": [...]})`` 是"追加"语义而不是"替换"。
要真正丢掉老消息，必须用 ``RemoveMessage(id=...)`` 标记后由 reducer 移除。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.graph.message import RemoveMessage

from src.config import Settings
from src.soul import ProfileManager

SUMMARIZE_PROMPT = """请把"已有摘要"和"新增对话片段"合并成一份简洁的中文摘要（不超过 200 字）。

保留以下关键信息：
- 用户的偏好、身份、习惯
- 用户提过的事实、定义、计算结果
- 未完成的任务、待确认的选项
- 讨论过的话题关键词

不要添加对话中没有的细节。如果新增片段为空，直接返回已有摘要。

已有摘要：
{prev_summary}

新增对话片段：
{messages}

合并后的摘要："""

DISCOVER_PROMPT = """从以下对话中提取用户的事实信息（姓名、职业、兴趣、偏好等）。
只返回你有把握的信息，不要猜测。
以 JSON 格式返回，key 是字段名，value 是字段值，格式示例：
{"name": "张三", "occupation": "工程师"}
不要返回其他内容，只返回 JSON。"""


@dataclass
class MemoryStats:
    """一次会话的当前状态统计。"""

    messages: int = 0
    chars: int = 0
    summary_len: int = 0

    def __str__(self) -> str:
        return (
            f"messages={self.messages}, chars={self.chars}, "
            f"summary_len={self.summary_len}"
        )


class MemoryManager:
    """在 ``agent.invoke`` 之后调用 ``compress_if_needed(thread_id)``。"""

    def __init__(
        self,
        agent: Any,
        llm: BaseChatModel,
        settings: Settings,
        *,
        compression_llm: BaseChatModel | None = None,
    ) -> None:
        self.agent = agent
        self.llm = llm
        self.compression_llm = compression_llm or llm
        self.settings = settings

    # ── 公开 API ────────────────────────────────────────────

    def get_stats(self, thread_id: str) -> MemoryStats:
        """读出当前 thread 的 messages / chars / summary 长度。"""
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = self.agent.get_state(config)
            values = state.values
        except Exception:
            return MemoryStats()
        messages = values.get("messages", []) or []
        summary = values.get("summary", "") or ""
        return MemoryStats(
            messages=len(messages),
            chars=sum(len(_content_str(m)) for m in messages),
            summary_len=len(summary),
        )

    def compress_if_needed(self, thread_id: str) -> bool:
        """如果 state 超限就压缩。返回是否真的发生了压缩。"""
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = self.agent.get_state(config)
            values = state.values
        except Exception:
            return False
        messages: list[BaseMessage] = list(values.get("messages", []) or [])
        summary: str = values.get("summary", "") or ""

        if not self._needs_compress(messages):
            return False

        # 先丢孤儿消息（无配对的 tool 响应 / ai tool_calls），避免 LLM API 400
        messages = _drop_orphans(messages)

        # 按"完整 turn"切分——以 human 消息为分界，保证
        # 任何 ai(tool_calls) 与其 tool 响应永远落在同一侧
        to_compress, recent = _split_by_turns(messages, self.settings.max_turns)
        if not to_compress:
            return False

        try:
            new_summary = self._summarize_with_llm(to_compress, summary)
        except Exception:
            # 兜底：滑动窗口丢消息，summary 字段保持原样
            self._replace_messages(config, to_compress, recent)
            return True

        self._replace_messages(config, to_compress, recent)
        self.agent.update_state(
            config,
            values={"summary": new_summary},
        )
        try:
            facts = self._discover_profile_facts(to_compress)
            if facts is not None:
                ProfileManager().merge(facts)
        except Exception:
            pass
        return True

    def _replace_messages(
        self,
        config: dict,
        to_remove: list[BaseMessage],
        to_keep: list[BaseMessage],
    ) -> None:
        """用 ``RemoveMessage`` 标记删除 + 重新 append 保留的消息。

        之所以要这么做：``messages`` 字段的 reducer 是 ``add_messages``，
        直接 ``update_state({"messages": [...]})`` 是追加语义而不是替换。
        """
        removes = [RemoveMessage(id=m.id) for m in to_remove if getattr(m, "id", None)]
        self.agent.update_state(
            config,
            values={"messages": removes + to_keep},
        )

    # ── 内部 ────────────────────────────────────────────────

    def _needs_compress(self, messages: list[BaseMessage]) -> bool:
        max_turns = self.settings.max_turns
        max_chars = self.settings.max_message_chars
        # 轮数：以 human 消息数计
        if sum(1 for m in messages if m.type == "human") > max_turns:
            return True
        # 字符数
        if sum(len(_content_str(m)) for m in messages) > max_chars:
            return True
        return False

    def _summarize_with_llm(
        self,
        old_messages: list[BaseMessage],
        prev_summary: str,
    ) -> str:
        lines = [
            f"[{m.type}] {_content_str(m)}"
            for m in old_messages
        ]
        prompt = SUMMARIZE_PROMPT.format(
            prev_summary=prev_summary or "（无）",
            messages="\n".join(lines) if lines else "（无新增片段）",
        )
        result = self.compression_llm.invoke(prompt)
        content = result.content
        if isinstance(content, str):
            return content.strip()
        # content 可能是 list[ContentBlock]
        if isinstance(content, list):
            return "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            ).strip()
        return str(content).strip()

    def _discover_profile_facts(
        self, new_messages: list[BaseMessage]
    ) -> dict | None:
        """Extract user facts from messages using LLM and merge into profile.json."""
        formatted = _format_messages_for_discovery(new_messages)
        prompt = DISCOVER_PROMPT + "\n\n" + formatted
        try:
            result = self.compression_llm.invoke(prompt)
            content = result.content
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                ).strip()
            else:
                text = str(content).strip()

            import re

            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            import json

            return json.loads(match.group())
        except Exception:
            return None


def _content_str(msg: BaseMessage) -> str:
    """把 message.content 统一拍平成 str，方便算长度。"""
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(
            b.get("text", "") for b in c if isinstance(b, dict)
        )
    return str(c)


def _format_messages_for_discovery(messages: list[BaseMessage]) -> str:
    """Format messages for profile discovery prompt."""
    lines = [
        f"[{m.type}] {_content_str(m)}"
        for m in messages
    ]
    return "\n".join(lines) if lines else "（无）"


def _drop_orphans(messages: list[BaseMessage]) -> list[BaseMessage]:
    """删除没有配对的 tool_calls 消息，避免后续 LLM 调用报 400。

    LLM API 要求：``assistant(tool_calls=[...])`` 后必须紧跟每个 ``tool_call_id``
    对应的 ``tool`` 响应；少了或多了都会触发
    ``insufficient tool messages following tool_calls`` 错误。

    孤儿分两类：
    - **孤 ai(tool_calls)**：assistant 发起调用但没有 tool 响应（例如工具执行被中断）
    - **孤 tool 响应**：tool 响应了不存在的 tool_call_id

    本函数会删除这两类消息，保留所有"配对完整"的消息。
    """
    # 1) 收集所有出现过的 tool_call_id
    announced: set[str] = set()  # 由 assistant(tool_calls) 声明的
    responded: set[str] = set()  # 由 tool 消息响应的
    for m in messages:
        if m.type == "ai":
            for tc in getattr(m, "tool_calls", None) or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    announced.add(tc_id)
        elif m.type == "tool":
            tc_id = getattr(m, "tool_call_id", None)
            if tc_id:
                responded.add(tc_id)

    orphan_calls = announced - responded  # 助理发了但没人回
    orphan_resps = responded - announced  # 有响应但找不到原始 call

    # 2) 过滤
    cleaned: list[BaseMessage] = []
    for m in messages:
        if m.type == "tool":
            tc_id = getattr(m, "tool_call_id", None)
            if tc_id in orphan_resps:
                continue
            cleaned.append(m)
            continue
        if m.type == "ai" and getattr(m, "tool_calls", None):
            tcs = m.tool_calls
            # 整个 ai 消息所有 tool_calls 都是孤儿，丢掉整条
            if all(
                (tc.get("id") if isinstance(tc, dict) else None) in orphan_calls
                for tc in tcs
            ):
                continue
            cleaned.append(m)
            continue
        cleaned.append(m)
    return cleaned


def _split_by_turns(
    messages: list[BaseMessage], keep_turns: int
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """按"完整 turn"切分消息，保留最近 ``keep_turns`` 个 human 消息及之后内容。

    turn 边界 = ``human`` 消息。每个 turn 内的 ``ai(tool_calls)`` 与其
    ``tool`` 响应保证不会被切到两侧。

    Returns:
        ``(to_compress, to_keep)``。若无需压缩，返回 ``([], messages)``。
    """
    if not messages or keep_turns <= 0:
        return [], list(messages)

    human_indices = [i for i, m in enumerate(messages) if m.type == "human"]
    if len(human_indices) <= keep_turns:
        return [], list(messages)

    split_idx = human_indices[-keep_turns]
    return list(messages[:split_idx]), list(messages[split_idx:])

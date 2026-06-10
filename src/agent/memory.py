"""记忆管理器：限制 messages 长度 / 轮数，超限则调用 LLM 增量摘要。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.graph.message import RemoveMessage

from src.core.config import Settings
from src.store.soul import ProfileManager
from src.store.memory_store import MemoryStore

SUMMARIZE_PROMPT = """请把"已有摘要"和"新增对话片段"合并成一份简洁的中文摘要（不超过 200 字）。

保留以下关键信息：
- 用户的偏好、身份、习惯
- 用户提过的事实、定义、计算结果
- 未完成的任务、待确认的选项
- 讨论过的话题关键词
- 工具执行的错误、异常、失败信息（如搜索超时、文件未找到、权限不足等）

不要添加对话中没有的细节。如果新增片段为空，直接返回已有摘要。

已有摘要：
{prev_summary}

新增对话片段：
{messages}

合并后的摘要："""

DISCOVER_PROMPT = """从以下对话中提取用户的事实信息（姓名、职业、兴趣、偏好等）。
只返回你有把握的信息，不要猜测。
以 JSON 格式返回，key 是字段名，value 是字段值，格式示例：
{{"name": "张三", "occupation": "工程师"}}

已知用户画像（仅供参考，如有冲突以新信息为准）：
{existing_profile}

请结合已有画像和新对话，补充或更新用户信息。
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
    """记忆管理器：超限时触发增量摘要。"""

    def __init__(
        self,
        agent: Any,
        llm: BaseChatModel,
        settings: Settings,
        *,
        compression_llm: BaseChatModel | None = None,
        profile_middleware: Any = None,
        memory_middleware: Any = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.agent = agent
        self.llm = llm
        self.compression_llm = compression_llm or llm
        self.settings = settings
        self.profile_middleware = profile_middleware
        self.memory_middleware = memory_middleware
        self._profile_manager = ProfileManager()
        self._memory_store = memory_store or MemoryStore()
        self._lock = asyncio.Lock()

    async def discover_and_update_profile(self, thread_id: str) -> dict | None:
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self.agent.aget_state(config)
            messages = list(state.values.get("messages", []) or [])
        except Exception:
            return None

        if not messages:
            return None

        try:
            existing = None
            if self.profile_middleware:
                existing = self.profile_middleware.profile
            facts = await self._discover_profile_facts_async(messages, existing)
            if facts is not None:
                if self.profile_middleware:
                    self.profile_middleware.update_profile(facts)
                else:
                    self._profile_manager.merge(facts)
                return facts
        except Exception:
            pass
        return None

    async def get_stats(self, thread_id: str) -> MemoryStats:
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self.agent.aget_state(config)
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

    async def compress_if_needed(self, thread_id: str, force: bool = False) -> bool:
        """压缩会话消息，提取画像和记忆。

        Args:
            thread_id: 会话 ID。
            force: True 时跳过 _needs_compress 检查，始终提取画像和记忆（摘要仍依赖消息量）。
        """
        if self._lock.locked():
            return False

        async with self._lock:
            config = {"configurable": {"thread_id": thread_id}}
            try:
                state = await self.agent.aget_state(config)
                values = state.values
            except Exception as exc:
                logger.warning("compress_if_needed: aget_state failed for %s: %s", thread_id, exc)
                return False

            messages = list(values.get("messages", []) or [])
            summary = values.get("summary", "") or ""

            needs_compress = force or self._needs_compress(messages)

            to_compress, recent = _split_by_turns(messages, self.settings.max_turns)
            compressed = False

            # 1. LLM 增量摘要（只在有待压缩消息且需要压缩时执行）
            if needs_compress and to_compress:
                try:
                    new_summary = await self._summarize_with_llm_async(to_compress, summary)
                except Exception as exc:
                    logger.warning("compress_if_needed: LLM summary failed for %s: %s", thread_id, exc)
                    await self._replace_messages_async(config, to_compress, recent)
                    compressed = True
                else:
                    # 替换消息 + 更新摘要
                    await self._replace_messages_async(config, to_compress, recent)
                    try:
                        await self.agent.aupdate_state(config, values={"summary": new_summary})
                    except Exception as exc:
                        logger.warning("compress_if_needed: aupdate_state(summary) failed for %s: %s", thread_id, exc)
                    compressed = True

            # 2. 画像发现（使用最近对话，而非被压缩的老消息）
            try:
                existing = None
                if self.profile_middleware:
                    existing = self.profile_middleware.profile
                target_msgs = recent if recent else messages
                facts = await self._discover_profile_facts_async(target_msgs, existing)
                if facts is not None:
                    if self.profile_middleware:
                        self.profile_middleware.update_profile(facts)
                    else:
                        self._profile_manager.merge(facts)
                    logger.info("compress_if_needed: discovered profile facts for %s: %s", thread_id, list(facts.keys()))
            except Exception as exc:
                logger.warning("compress_if_needed: profile discovery failed for %s: %s", thread_id, exc)

            # 3. 记忆提取（使用最近对话，确保有足够的人类消息通过分类门控）
            try:
                target_msgs = recent if recent else messages
                result = await self._extract_memories_async(target_msgs, thread_id)
                if result and result.get("count", 0) > 0:
                    logger.info("compress_if_needed: extracted %d memories for %s", result["count"], thread_id)
            except Exception as exc:
                logger.warning("compress_if_needed: memory extraction failed for %s: %s", thread_id, exc)

            # 4. 知识归并触发（raw facts 超过阈值时自动归并）
            try:
                raw_count = self._memory_store.count_raw_facts()
                if raw_count > 30:
                    from src.store.knowledge import KnowledgeConsolidator
                    consolidator = KnowledgeConsolidator(self.compression_llm, self._memory_store)
                    ck_result = await consolidator.consolidate()
                    if ck_result.get("consolidated"):
                        logger.info(
                            "compress_if_needed: consolidated %d facts (round %d) for %s",
                            ck_result.get("count", 0), ck_result.get("round_id", 0), thread_id,
                        )
            except Exception as exc:
                logger.warning("compress_if_needed: knowledge consolidation failed for %s: %s", thread_id, exc)

            if compressed:
                logger.info("compress_if_needed: completed for %s (%d msgs → summary)", thread_id, len(to_compress))

            # 4. 通知中间件缓存失效
            if self.memory_middleware is not None:
                self.memory_middleware.invalidate_cache()

            return compressed or True

    async def _replace_messages_async(self, config: dict, to_remove: list[BaseMessage], to_keep: list[BaseMessage]) -> None:
        removes = [RemoveMessage(id=m.id) for m in to_remove if getattr(m, "id", None)]
        await self.agent.aupdate_state(
            config,
            values={"messages": removes + to_keep},
        )

    async def _extract_memories_async(self, messages: list[BaseMessage], thread_id: str) -> dict:
        """对一批消息执行结构化记忆提取。"""
        from src.agent.memory_extractor import MemoryExtractor

        extractor = MemoryExtractor(self.compression_llm, self._memory_store)
        return await extractor.extract_from_messages_async(messages, source_session=thread_id)

    def _needs_compress(self, messages: list[BaseMessage]) -> bool:
        max_turns = self.settings.max_turns
        max_chars = self.settings.max_message_chars
        if sum(1 for m in messages if m.type == "human") > max_turns:
            return True
        if sum(len(_content_str(m)) for m in messages) > max_chars:
            return True
        return False

    async def _summarize_with_llm_async(self, old_messages: list[BaseMessage], prev_summary: str) -> str:
        lines = [f"[{m.type}] {_content_str(m)}" for m in old_messages]
        prompt = SUMMARIZE_PROMPT.format(
            prev_summary=prev_summary or "（无）",
            messages="\n".join(lines) if lines else "（无新增片段）",
        )
        result = await self.compression_llm.ainvoke(prompt)
        content = result.content
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            ).strip()
        else:
            text = str(content).strip()
        # 防止摘要无限膨胀
        MAX_SUMMARY_CHARS = 500
        if len(text) > MAX_SUMMARY_CHARS:
            text = text[:MAX_SUMMARY_CHARS] + "..."
        return text

    async def _discover_profile_facts_async(
        self, new_messages: list[BaseMessage], existing_profile: dict | None = None
    ) -> dict | None:
        import re, json
        formatted = _format_messages_for_discovery(new_messages)
        if existing_profile:
            existing_lines = [
                f"{k}: {v}" for k, v in existing_profile.items()
                if k != "version" and not k.endswith("_source")
            ]
            existing_str = "\n".join(existing_lines) if existing_lines else "（无）"
        else:
            existing_str = "（无）"
        prompt = DISCOVER_PROMPT.format(existing_profile=existing_str) + "\n\n" + formatted
        try:
            result = await self.compression_llm.ainvoke(prompt)
            content = result.content
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                ).strip()
            else:
                text = str(content).strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            return json.loads(match.group())
        except Exception as exc:
            logger.warning("_discover_profile_facts_async: LLM parse failed: %s", exc)
            return None


def _content_str(msg: BaseMessage) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return str(c)


def _format_messages_for_discovery(messages: list[BaseMessage]) -> str:
    lines = [f"[{m.type}] {_content_str(m)}" for m in messages]
    return "\n".join(lines) if lines else "（无）"


def _drop_orphans(messages: list[BaseMessage]) -> list[BaseMessage]:
    """删除没有配对的 tool_calls 消息，避免后续 LLM 调用报 400。"""
    announced: set[str] = set()
    responded: set[str] = set()
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

    orphan_calls = announced - responded
    orphan_resps = responded - announced

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
    if not messages or keep_turns <= 0:
        return [], list(messages)
    human_indices = [i for i, m in enumerate(messages) if m.type == "human"]
    if len(human_indices) <= keep_turns:
        return [], list(messages)
    split_idx = human_indices[-keep_turns]
    return list(messages[:split_idx]), list(messages[split_idx:])

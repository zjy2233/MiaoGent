"""MemoryManager 单元测试：压缩触发、增量摘要、失败兜底滑动窗口。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import Field

from src.agent.builder import build_agent
from src.core.config import Settings
from src.agent.memory import MemoryManager, MemoryStats, _content_str, _drop_orphans, _split_by_turns


class _StubChatModel(BaseChatModel):
    """按顺序返回响应的桩 LLM。"""

    responses: list[str] = Field(default_factory=list)
    idx: int = 0
    raise_on_call: bool = False  # 设为 True 让 LLM 调用抛异常

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_StubChatModel":
        return self

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.raise_on_call:
            self.idx += 1
            raise RuntimeError("simulated LLM outage")
        content = self.responses[self.idx]
        self.idx += 1
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )


def _build_agent(responses: list[str], max_turns: int = 3, max_chars: int = 200):
    """拼一个 max_turns=3 / max_chars=200 的小窗口 agent。"""
    llm = _StubChatModel(responses=responses)
    checkpointer = MemorySaver()
    agent = build_agent(llm, checkpointer=checkpointer)
    settings = Settings(
        deepseek_api_key="dummy",
        deepseek_base_url="https://example",
        deepseek_model="stub",
        max_turns=max_turns,
        max_message_chars=max_chars,
    )
    manager = MemoryManager(agent, llm, settings)
    return agent, manager, settings


def _ask(agent: Any, prompt: str, thread_id: str) -> list:
    return agent.invoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={"configurable": {"thread_id": thread_id}},
    )["messages"]


class TestContentStr:
    def test_str_passthrough(self) -> None:
        assert _content_str(HumanMessage(content="hi")) == "hi"

    def test_list_content(self) -> None:
        msg = AIMessage(content=[{"type": "text", "text": "abc"}])
        assert _content_str(msg) == "abc"


class TestMemoryStats:
    def test_empty_thread(self) -> None:
        _, manager, _ = _build_agent(responses=["x"])
        stats = manager.get_stats("nonexistent")
        assert stats.messages == 0
        assert stats.chars == 0
        assert stats.summary_len == 0


class TestNoCompressWhenUnderLimit:
    def test_short_conversation_untouched(self) -> None:
        agent, manager, _ = _build_agent(responses=["hi", "hello"])
        thread = "t1"
        _ask(agent, "Q1", thread)
        _ask(agent, "Q2", thread)
        # 2 轮，没超 max_turns=3
        assert manager.compress_if_needed(thread) is False
        stats = manager.get_stats(thread)
        assert stats.messages == 4  # 2 human + 2 ai
        assert stats.summary_len == 0  # 没有摘要


class TestCompressTriggersOnTurns:
    def test_turns_over_limit_triggers_compress(self) -> None:
        # max_turns=3，需要 6 响应（每次 human 1 + ai 1）。6 轮之后第 7 轮触发
        responses = [f"答{i}" for i in range(20)]
        agent, manager, _ = _build_agent(responses=responses, max_turns=3)
        thread = "t1"
        for i in range(7):
            _ask(agent, f"问{i}", thread)
        # 最后一轮 invoke 后调一次 compress
        compressed = manager.compress_if_needed(thread)
        assert compressed is True
        stats = manager.get_stats(thread)
        # 保留最近 3 轮 = 6 条消息
        assert stats.messages == 6
        # 摘要字段被写入
        assert stats.summary_len > 0
        # 摘要里应该包含部分 LLM 摘要文本
        state = agent.get_state({"configurable": {"thread_id": thread}})
        assert state.values.get("summary")
        assert "答" in state.values["summary"]


class TestIncrementalSummary:
    def test_second_compress_uses_prev_summary(self) -> None:
        """第二次压缩的 prompt 应该传入"已有摘要"。"""
        # 通过 monkey-patching 观察 prompt 内容
        from src import memory

        seen_prompts: list[str] = []

        original = memory.MemoryManager._summarize_with_llm

        def spy(self, old_messages, prev_summary):
            prompt = memory.SUMMARIZE_PROMPT.format(
                prev_summary=prev_summary or "（无）",
                messages="\n".join(
                    f"[{m.type}] {_content_str(m)}" for m in old_messages
                ),
            )
            seen_prompts.append(prompt)
            return f"[已含{prev_summary[:10]}] + 新片段"

        memory.MemoryManager._summarize_with_llm = spy
        try:
            responses = [f"r{i}" for i in range(40)]
            agent, manager, _ = _build_agent(responses=responses, max_turns=2)
            thread = "t1"
            for i in range(10):
                _ask(agent, f"q{i}", thread)
            manager.compress_if_needed(thread)
            first_summary = manager.agent.get_state(
                {"configurable": {"thread_id": thread}}
            ).values["summary"]
            # 再多问几轮触发第二次压缩
            for i in range(10, 20):
                _ask(agent, f"q{i}", thread)
            manager.compress_if_needed(thread)
            second_summary = manager.agent.get_state(
                {"configurable": {"thread_id": thread}}
            ).values["summary"]
            # 第二次压缩的 prompt 里 prev_summary 不应为空
            assert len(seen_prompts) >= 2
            assert "（无）" not in seen_prompts[1]
            assert first_summary in seen_prompts[1] or first_summary[:10] in seen_prompts[1]
            # 第二次的 summary 应包含前一次的痕迹（证明增量）
            assert "[已含" in second_summary
        finally:
            memory.MemoryManager._summarize_with_llm = original


class TestSlidingWindowFallback:
    def test_llm_failure_falls_back_to_sliding_window(self) -> None:
        """LLM 摘要抛异常时，应退化为滑动窗口，summary 字段保持原值。"""
        responses = [f"答{i}" for i in range(40)]
        agent, manager, settings = _build_agent(responses=responses, max_turns=2)
        thread = "t1"
        for i in range(10):
            _ask(agent, f"问{i}", thread)

        # 把 compression LLM 换成一个会抛异常的桩
        failing_llm = _StubChatModel(responses=[], raise_on_call=True)
        manager.compression_llm = failing_llm

        # 先手工写一个 summary 进 state
        agent.update_state(
            {"configurable": {"thread_id": thread}},
            values={"summary": "之前已存在的摘要"},
        )

        compressed = manager.compress_if_needed(thread)
        assert compressed is True  # 仍然发生了"压缩"（滑动窗口也算）
        stats = manager.get_stats(thread)
        # messages 截到 max_turns * 2 = 4 条
        assert stats.messages == 4
        # summary 字段保留原值（不被清空）
        state = agent.get_state({"configurable": {"thread_id": thread}})
        assert state.values["summary"] == "之前已存在的摘要"


class TestSqliteSaverIntegration:
    @pytest.mark.asyncio
    async def test_compress_works_with_sqlite_checkpointer(self, tmp_path) -> None:
        """真实 SqliteSaver 路径下，压缩能正常写入 SQLite。"""
        db_path = str(tmp_path / "test.db")
        responses = [f"答{i}" for i in range(40)]
        llm = _StubChatModel(responses=responses)
        settings = Settings(
            deepseek_api_key="dummy",
            deepseek_base_url="https://example",
            deepseek_model="stub",
            max_turns=2,
        )
        async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
            agent = build_agent(llm, checkpointer=checkpointer)
            manager = MemoryManager(agent, llm, settings)
            thread = "t1"
            for i in range(8):
                _ask(agent, f"q{i}", thread)
            assert manager.compress_if_needed(thread) is True
            state = agent.get_state({"configurable": {"thread_id": thread}})
            assert state.values["summary"]
            assert len(state.values["messages"]) == 4


# ── 工具调用配对保护：覆盖"切分时把 ai(tool_calls) 和 tool 响应拆散"这个 400 bug ──


def _ai_with_tool_calls(call_id: str) -> AIMessage:
    """构造一条带 tool_calls 的 AI 消息。"""
    return AIMessage(
        content="",
        tool_calls=[{"name": "x", "args": {}, "id": call_id}],
    )


def _tool_response(call_id: str, content: str = "ok") -> ToolMessage:
    """构造一条 tool 响应消息。"""
    return ToolMessage(content=content, tool_call_id=call_id)


class TestDropOrphans:
    """验证孤儿消息过滤逻辑。"""

    def test_drops_orphan_ai_tool_calls(self) -> None:
        msgs = [
            HumanMessage(content="q1"),
            _ai_with_tool_calls("call_orphan"),  # 没人响应
            HumanMessage(content="q2"),
        ]
        cleaned = _drop_orphans(msgs)
        # 孤儿的 ai 消息被丢掉
        assert len(cleaned) == 2
        assert all(m.type in ("human",) for m in cleaned)

    def test_drops_orphan_tool_response(self) -> None:
        msgs = [
            HumanMessage(content="q1"),
            _tool_response("call_doesnt_exist"),  # 没有对应 call
        ]
        cleaned = _drop_orphans(msgs)
        assert len(cleaned) == 1
        assert cleaned[0].type == "human"

    def test_keeps_paired_messages(self) -> None:
        msgs = [
            HumanMessage(content="q1"),
            _ai_with_tool_calls("call_ok"),
            _tool_response("call_ok", "result"),
            HumanMessage(content="q2"),
        ]
        cleaned = _drop_orphans(msgs)
        assert len(cleaned) == 4
        assert cleaned[1].tool_calls[0]["id"] == "call_ok"
        assert cleaned[2].tool_call_id == "call_ok"

    def test_no_op_on_plain_conversation(self) -> None:
        msgs = [HumanMessage(content=f"q{i}") for i in range(3)]
        assert _drop_orphans(msgs) == msgs


class TestSplitByTurns:
    """验证按 turn 切分不破坏 tool_call 配对。"""

    def test_keeps_under_limit(self) -> None:
        msgs = [HumanMessage(content=f"q{i}") for i in range(3)]
        to_compress, recent = _split_by_turns(msgs, keep_turns=3)
        assert to_compress == []
        assert recent == msgs

    def test_splits_at_human_boundary(self) -> None:
        msgs = [HumanMessage(content=f"q{i}") for i in range(5)]
        to_compress, recent = _split_by_turns(msgs, keep_turns=2)
        # 保留最近 2 个 human 及之后内容 = q3, q4
        assert [m.content for m in to_compress] == ["q0", "q1", "q2"]
        assert [m.content for m in recent] == ["q3", "q4"]

    def test_keeps_tool_call_pair_intact(self) -> None:
        """关键回归测试：split 之后所有 ai(tool_calls) 的 tool 响应仍在同一侧。"""
        msgs = [
            HumanMessage(content="q0"),
            _ai_with_tool_calls("call_0"),
            _tool_response("call_0", "r0"),
            HumanMessage(content="q1"),
            _ai_with_tool_calls("call_1"),
            _tool_response("call_1", "r1"),
            HumanMessage(content="q2"),
            _ai_with_tool_calls("call_2"),
            _tool_response("call_2", "r2"),
        ]
        to_compress, recent = _split_by_turns(msgs, keep_turns=2)
        # recent 必须以 human 开始
        assert recent[0].type == "human"
        # 配对完整性：recent 中每个 ai(tool_calls) 的 tool 响应都在 recent 里
        for m in recent:
            if m.type == "ai" and getattr(m, "tool_calls", None):
                tc_id = m.tool_calls[0]["id"]
                tool_in_recent = any(
                    t.type == "tool" and t.tool_call_id == tc_id for t in recent
                )
                assert tool_in_recent, f"{tc_id} 的 tool 响应应该在 recent 里"

    def test_split_never_lands_in_middle_of_pair(self) -> None:
        """切分点绝不能落在 [ai(tool_calls), tool, tool] 中间。"""
        # 构造最易触发的场景：turn 数刚好让 keep 落在配对中间
        msgs = [
            HumanMessage(content="q0"),
            _ai_with_tool_calls("c0"),
            _tool_response("c0"),
            HumanMessage(content="q1"),
            _ai_with_tool_calls("c1"),
            _tool_response("c1"),
        ]
        # 原始的 len-keep 切分可能把 c1 的 tool 留在 recent
        # 而 _split_by_turns 应该保证 c1 整组都在 recent 里
        to_compress, recent = _split_by_turns(msgs, keep_turns=1)
        # 关键断言：to_compress 和 recent 之间没有"半截配对"
        for m in to_compress:
            if m.type == "ai" and getattr(m, "tool_calls", None):
                tc_id = m.tool_calls[0]["id"]
                tool_in_compress = any(
                    t.type == "tool" and t.tool_call_id == tc_id for t in to_compress
                )
                assert tool_in_compress
        for m in recent:
            if m.type == "ai" and getattr(m, "tool_calls", None):
                tc_id = m.tool_calls[0]["id"]
                tool_in_recent = any(
                    t.type == "tool" and t.tool_call_id == tc_id for t in recent
                )
                assert tool_in_recent


class TestCompressPreservesToolCallPairs:
    """端到端：compress_if_needed 切分后 state 里的 messages 必须满足 LLM API 校验。

    这覆盖了原来"再次提问报 400"的核心 bug。
    """

    def _is_well_formed_for_llm(self, messages: list) -> bool:
        """模拟 LLM API 校验：每个 ai(tool_calls) 的所有 tool_call_id
        都必须在后续的 tool 消息中出现过。"""
        for i, m in enumerate(messages):
            if m.type == "ai" and getattr(m, "tool_calls", None):
                announced = {tc["id"] for tc in m.tool_calls}
                responded = {
                    t.tool_call_id
                    for t in messages[i + 1 :]
                    if t.type == "tool"
                }
                if not announced.issubset(responded):
                    return False
            if m.type == "tool":
                # 检查这个 tool 响应有没有对应的 call
                preceded = messages[:i]
                announced = set()
                for p in preceded:
                    if p.type == "ai" and getattr(p, "tool_calls", None):
                        for tc in p.tool_calls:
                            announced.add(tc["id"])
                if m.tool_call_id not in announced:
                    return False
        return True

    def test_compressed_state_passes_llm_validation(self) -> None:
        """max_turns 触发压缩后，state 仍能通过 LLM API 校验。"""
        responses = [f"r{i}" for i in range(40)]
        agent, manager, _ = _build_agent(responses=responses, max_turns=2)
        thread = "t1"
        for i in range(8):
            _ask(agent, f"q{i}", thread)
        manager.compress_if_needed(thread)
        state = agent.get_state({"configurable": {"thread_id": thread}})
        messages = state.values["messages"]
        # 关键断言：压缩后消息序列对 LLM API 仍然合法
        assert self._is_well_formed_for_llm(messages), (
            "压缩后的 messages 包含孤 tool_calls，"
            "下次 invoke 会触发 400: insufficient tool messages"
        )

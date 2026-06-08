"""验证 InMemorySaver 接入后 agent 的会话记忆行为。

全部使用桩 LLM，避免任何外部 API 调用。
桩 LLM 每次被调用时按顺序返回 ``responses`` 列表里的下一条 AIMessage，
没有 tool_call，因此 ReAct 循环跑完一次 LLM 决策就终止。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from src.agent.builder import build_agent


class _StubChatModel(BaseChatModel):
    """最小可用的桩 LLM：按顺序吐响应，不带 tool_call。"""

    responses: list[str] = Field(default_factory=list)
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(  # noqa: D401 — 必须实现 create_agent 才会调它
        self,
        tools: Any,
        **kwargs: Any,
    ) -> "_StubChatModel":
        return self

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = self.responses[self.idx]
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def _build_fake_agent(responses: list[str]):
    """拼一个 LLM 行为可预测的 agent。"""
    llm = _StubChatModel(responses=responses)
    return build_agent(llm, checkpointer=MemorySaver()).agent


async def _ask(agent, prompt: str, thread_id: str) -> list:
    """ainvoke 一次并返回 state['messages'] 列表。"""
    state = {"messages": [HumanMessage(content=prompt)]}
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke(state, config=config)
    return result["messages"]


class TestMemoryAccumulation:
    """同一 thread_id 的多次 ainvoke 会把历史串起来。"""

    @pytest.mark.asyncio
    async def test_same_thread_accumulates_messages(self) -> None:
        agent = _build_fake_agent(responses=["答 1", "答 2"])
        thread = "thread-A"

        msgs_1 = await _ask(agent, "问 1", thread)
        msgs_2 = await _ask(agent, "问 2", thread)

        # 第一轮：1 条 human + 1 条 ai = 2 条
        assert len(msgs_1) == 2
        assert msgs_1[0].content == "问 1"
        assert msgs_1[1].content == "答 1"

        # 第二轮：上轮的 2 条 + 本轮的 1 human + 1 ai = 4 条
        assert len(msgs_2) == 4
        assert msgs_2[0].content == "问 1"
        assert msgs_2[1].content == "答 1"
        assert msgs_2[2].content == "问 2"
        assert msgs_2[3].content == "答 2"

    @pytest.mark.asyncio
    async def test_different_threads_are_isolated(self) -> None:
        agent = _build_fake_agent(responses=["答 A", "答 B", "答 C"])
        thread_a = "thread-A"
        thread_b = "thread-B"

        msgs_a1 = await _ask(agent, "问 A1", thread_a)
        msgs_b1 = await _ask(agent, "问 B1", thread_b)
        msgs_a2 = await _ask(agent, "问 A2", thread_a)

        assert len(msgs_a1) == 2
        assert len(msgs_b1) == 2
        assert len(msgs_a2) == 4
        assert msgs_a2[0].content == "问 A1"
        assert msgs_a2[2].content == "问 A2"
        contents_b1 = [m.content for m in msgs_b1]
        assert "问 A1" not in contents_b1
        assert "问 A2" not in contents_b1

    @pytest.mark.asyncio
    async def test_missing_thread_id_raises(self) -> None:
        agent = _build_fake_agent(responses=["答"])
        state = {"messages": [HumanMessage(content="问")]}
        with pytest.raises(Exception):
            await agent.ainvoke(state)


class TestBackwardCompatibility:
    """不传 checkpointer 时行为与改造前一致：每次 ainvoke 互不影响。"""

    @pytest.mark.asyncio
    async def test_no_checkpointer_means_fresh_state(self) -> None:
        llm = _StubChatModel(responses=["答 1", "答 2"])
        agent = build_agent(llm).agent  # 不传 checkpointer
        state = {"messages": [HumanMessage(content="问 1")]}
        result = await agent.ainvoke(state)
        assert len(result["messages"]) == 2

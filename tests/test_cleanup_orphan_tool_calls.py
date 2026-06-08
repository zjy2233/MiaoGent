"""验证清理残留 tool_calls 的逻辑。

当上一个流式请求因中断残留了不完整的 tool_calls 时，
新消息应该能成功发送，不会因 LLM API 400 而失败。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import RemoveMessage
from pydantic import Field

from src.agent.builder import build_agent


class _StubChatModel(BaseChatModel):
    """桩 LLM：按顺序吐响应，不带 tool_call。"""

    responses: list[str] = Field(default_factory=list)
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(
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


@pytest.fixture
def agent():
    llm = _StubChatModel(responses=["OK"])
    return build_agent(llm, checkpointer=MemorySaver()).agent


async def _get_msgs(agent, config) -> list:
    snap = await agent.aget_state(config)
    return list(snap.values.get("messages", []) or [])


class TestRemoveOrphanedToolCalls:
    """验证聊天前清理残留 tool_calls 的三种场景。"""

    @pytest.mark.asyncio
    async def test_no_orphan(self, agent):
        """场景 0：state 干净 → 不做任何操作。"""
        config = {"configurable": {"thread_id": "t0"}}
        await agent.aupdate_state(config, {
            "messages": [HumanMessage(content="hello")],
        })

        # 执行 cleanup 逻辑（同 chat_stream）
        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            break  # 不该走到这里

        # 仍然只有 1 条 HumanMessage
        assert len(await _get_msgs(agent, config)) == 1

    @pytest.mark.asyncio
    async def test_orphan_tool_calls_at_end(self, agent):
        """场景 1：最后一条是 AIMessage(tool_calls) 且无 ToolMessage 跟随。

        [HumanMessage, AIMessage(tool_calls=[c1])]
        → 移除 AIMessage → [HumanMessage]
        """
        config = {"configurable": {"thread_id": "t1"}}
        await agent.aupdate_state(config, {
            "messages": [
                HumanMessage(content="天气如何", id="h1"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_weather", "args": {}, "id": "c1", "type": "tool_call"}],
                    id="a1",
                ),
            ],
        }, as_node="__start__")

        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            following_ids = {getattr(msgs[j], "tool_call_id", None) for j in range(i + 1, len(msgs))}
            following_ids.discard(None)
            missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
            if missing:
                await agent.aupdate_state(config, {
                    "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                }, as_node="__start__")
            break

        msgs = await _get_msgs(agent, config)
        assert len(msgs) == 1
        assert msgs[0].content == "天气如何"

    @pytest.mark.asyncio
    async def test_orphan_with_human_in_between(self, agent):
        """场景 2（核心 bug）：state 被失败请求写入 HumanMessage 夹在中间。

        [HumanMessage, AIMessage(tool_calls=[c1]), HumanMessage(failed)]
        → 全部移除 → [HumanMessage]
        """
        config = {"configurable": {"thread_id": "t2"}}
        await agent.aupdate_state(config, {
            "messages": [
                HumanMessage(content="天气如何", id="h1"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_weather", "args": {}, "id": "c1", "type": "tool_call"}],
                    id="a1",
                ),
                HumanMessage(content="重试", id="h2"),
            ],
        }, as_node="__start__")

        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            following_ids = {getattr(msgs[j], "tool_call_id", None) for j in range(i + 1, len(msgs))}
            following_ids.discard(None)
            missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
            if missing:
                await agent.aupdate_state(config, {
                    "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                }, as_node="__start__")
            break

        msgs = await _get_msgs(agent, config)
        assert len(msgs) == 1
        assert msgs[0].content == "天气如何"

    @pytest.mark.asyncio
    async def test_orphan_partial_tool_results(self, agent):
        """场景 3：部分 tool 执行完，但还有 tool_call 缺 ToolMessage。

        [HumanMessage, AIMessage(tool_calls=[c1,c2]), ToolMessage(c1)]
        → 移除 AIMessage 及之后所有 → [HumanMessage]
        """
        config = {"configurable": {"thread_id": "t3"}}
        await agent.aupdate_state(config, {
            "messages": [
                HumanMessage(content="天气如何", id="h1"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_weather", "args": {}, "id": "c1", "type": "tool_call"},
                        {"name": "get_time", "args": {}, "id": "c2", "type": "tool_call"},
                    ],
                    id="a1",
                ),
                ToolMessage(content="晴", tool_call_id="c1", id="tm1"),
            ],
        }, as_node="__start__")

        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            following_ids = {getattr(msgs[j], "tool_call_id", None) for j in range(i + 1, len(msgs))}
            following_ids.discard(None)
            missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
            if missing:
                await agent.aupdate_state(config, {
                    "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                }, as_node="__start__")
            break

        msgs = await _get_msgs(agent, config)
        assert len(msgs) == 1
        assert msgs[0].content == "天气如何"

    @pytest.mark.asyncio
    async def test_orphan_interleaved_human_with_matching_tool(self, agent):
        """场景 4：AIMessage(tool_calls) → HumanMessage → HumanMessage → ToolMessage(matching)。

        ToolMessage 虽然存在，但 HumanMessage 被夹在 AIMessage 和 ToolMessage 中间，
        序列不合法。missing 检查会通过（因为有 ToolMessage），但 seq_broken 应触发。
        → 移除 AIMessage 及之后所有 → [HumanMessage]

        注：state 注入用 as_node="__start__" 而非 "model"，因为 build_agent 的
        model_to_tools_edge 条件函数在检测到所有 tool_calls 都有匹配 ToolMessage
        时会返回 "model"（规则 #6：人工注入 tool message），而 "__start__" 不是
        条件分支的终点，会触发 KeyError。
        """
        config = {"configurable": {"thread_id": "t5"}}
        await agent.aupdate_state(config, {
            "messages": [
                HumanMessage(content="帮我写个文件", id="h1"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "write_file", "args": {}, "id": "c1", "type": "tool_call"}],
                    id="a1",
                ),
                HumanMessage(content="如何了", id="h2"),
                HumanMessage(content="如何", id="h3"),
                ToolMessage(content="[上一个任务已被用户中断]", tool_call_id="c1", id="tm1"),
                HumanMessage(content="如何了", id="h4"),
                HumanMessage(content="如何了", id="h5"),
                HumanMessage(content="如何了", id="h6"),
            ],
        }, as_node="__start__")

        # 执行 cleanup（模拟 chat/chat_stream 中的逻辑）
        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            next_type = ""
            if i + 1 < len(msgs):
                next_type = getattr(msgs[i + 1], "type", "")
            following_ids = {getattr(msgs[j], "tool_call_id", None) for j in range(i + 1, len(msgs))}
            following_ids.discard(None)
            missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
            seq_broken = next_type not in ("", "tool")
            if missing or seq_broken:
                await agent.aupdate_state(config, {
                    "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                }, as_node="__start__")
            break

        msgs = await _get_msgs(agent, config)
        assert len(msgs) == 1
        assert msgs[0].content == "帮我写个文件"

    @pytest.mark.asyncio
    async def test_invoke_after_cleanup(self, agent):
        """清理后发送新消息应当成功（不会 400）。"""
        config = {"configurable": {"thread_id": "t4"}}
        # 注入损坏的 state
        await agent.aupdate_state(config, {
            "messages": [
                HumanMessage(content="天气如何", id="h1"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_weather", "args": {}, "id": "c1", "type": "tool_call"}],
                    id="a1",
                ),
                HumanMessage(content="重试", id="h2"),
            ],
        }, as_node="__start__")

        # 执行 cleanup
        msgs = await _get_msgs(agent, config)
        for i in range(len(msgs) - 1, -1, -1):
            msg = msgs[i]
            tc = getattr(msg, "tool_calls", None)
            if not tc:
                continue
            following_ids = {getattr(msgs[j], "tool_call_id", None) for j in range(i + 1, len(msgs))}
            following_ids.discard(None)
            missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
            if missing:
                await agent.aupdate_state(config, {
                    "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                }, as_node="__start__")
            break

        # 验证清理后发送新消息成功
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="新问题")]},
            config=config,
        )
        final_msgs = result.get("messages", [])
        assert len(final_msgs) >= 2, "应有至少 2 条消息（human + ai 回复）"
        # 最后一条是 AI 回复
        assert final_msgs[-1].type == "ai"

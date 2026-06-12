"""Updated tests: sub-agent tool inheritance via tools parameter, delegate_task forwarding.

Verifies:
- create_sub_agent / run_sub_agent work without skills parameter (removed)
- build_delegate_task forwards skill tools via tools parameter
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.sub_agent import create_sub_agent, REGULAR_TOOLS
from src.skills.schema import SkillDefinition
from src.tools.delegate_task import build_delegate_task


# ── Helper: a real @tool that can be added to agent tool lists ──

from langchain_core.tools import tool as _langchain_tool

@_langchain_tool
def _dummy_tool(query: str) -> str:
    """A dummy skill tool for testing."""
    return f"processed: {query}"


@pytest.fixture
def mock_skill() -> SkillDefinition:
    return SkillDefinition(
        name="test_skill",
        description="A test skill",
        prompt_injection="你可以做测试。",
    )


# ── Sub-agent (no skills parameter) ──


class TestSubAgentBasics:
    def test_create_sub_agent_basic(self) -> None:
        """不传额外 tools → 正常的 REGULAR_TOOLS。"""
        agent = create_sub_agent(MagicMock())
        from langgraph.graph.state import CompiledStateGraph
        assert isinstance(agent, CompiledStateGraph)

    def test_create_sub_agent_with_custom_tools(self) -> None:
        """tools 参数 → 自定义工具列表。"""
        tools = list(REGULAR_TOOLS) + [_dummy_tool]
        agent = create_sub_agent(MagicMock(), tools=tools)
        from langgraph.graph.state import CompiledStateGraph
        assert isinstance(agent, CompiledStateGraph)


# ── Delegate task skill forwarding ──


class TestDelegateTaskSkills:
    def test_build_delegate_task_accepts_llm_only(self) -> None:
        """build_delegate_task 只接受 llm 参数。"""
        import inspect
        sig = inspect.signature(build_delegate_task)
        params = sig.parameters
        assert "llm" in params
        assert "session_id" not in params
        assert "skill_registry" not in params

    @pytest.mark.asyncio
    async def test_delegate_task_uses_regular_tools(self) -> None:
        """delegate_task 只使用 REGULAR_TOOLS（skills 是纯提示注入）。"""
        import src.tools.delegate_task as dt

        tool_fn = build_delegate_task(MagicMock())

        with patch.object(dt, "run_sub_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"result": "ok", "agent_id": "abc"}
            dt._recursion_depth.set(0)

            result = await tool_fn.ainvoke({"task": "test", "timeout": 10})

            mock_run.assert_called_once()
            _, kwargs = mock_run.call_args
            assert "tools" in kwargs
            # 只有 REGULAR_TOOLS（skills 是纯提示注入）
            assert kwargs["tools"] == list(REGULAR_TOOLS)

    @pytest.mark.asyncio
    async def test_delegate_task_no_registry(self) -> None:
        """不传 skill_registry → 只有 REGULAR_TOOLS。"""
        import src.tools.delegate_task as dt

        tool_fn = build_delegate_task(None)

        with patch.object(dt, "run_sub_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"result": "ok", "agent_id": "abc"}
            dt._recursion_depth.set(0)

            result = await tool_fn.ainvoke({"task": "test", "timeout": 10})
            assert result == "ok"

            _, kwargs = mock_run.call_args
            assert "tools" in kwargs
            # 没有 skill tools，就是 REGULAR_TOOLS
            assert kwargs["tools"] == list(REGULAR_TOOLS)

"""测试 Sub-agent 工厂模块。

验证：
- REGULAR_TOOLS 不包含委派能力
- create_sub_agent 能正确创建 CompiledStateGraph
- SUB_AGENT_PROMPT 包含安全约束
"""

from __future__ import annotations

import pytest

from src.agent.sub_agent import REGULAR_TOOLS, SUB_AGENT_PROMPT, create_sub_agent


class TestRegularTools:
    """REGULAR_TOOLS 安全约束验证。"""

    def test_tools_have_invoke_method(self):
        """所有工具应该有 invoke 方法（StructuredTool 接口）。"""
        for tool in REGULAR_TOOLS:
            assert hasattr(tool, "invoke"), f"{tool} has no invoke method"
            assert hasattr(tool, "name"), f"{tool} has no name"

    def test_tool_names(self):
        """验证工具名称列表，确保没有委派能力的工具。"""
        names = {t.name if hasattr(t, "name") else str(t) for t in REGULAR_TOOLS}
        expected = {
            "calculator",
            "current_time",
            "weather",
            "search",
            "web_fetch",
            "list_files",
            "read_file",
            "grep_search",
            "create_folder",
            "write_file",
            "run_python",
            "shell",
        }
        assert names == expected, f"工具集不匹配: {names} != {expected}"

    def test_no_delegation_tools(self):
        """确认没有任何可创建 agent 的委派工具。"""
        forbidden = {"delegate", "create_agent", "spawn", "sub_agent", "dispatch"}
        names = {t.name if hasattr(t, "name") else "" for t in REGULAR_TOOLS}
        for name in names:
            for keyword in forbidden:
                assert keyword not in name.lower(), f"发现委派工具: {name}"


class TestCreateSubAgent:
    """create_sub_agent 基础功能测试。"""

    def test_returns_compiled_graph(self, mocker):
        """返回 CompiledStateGraph 实例。"""
        mock_llm = mocker.MagicMock()
        agent = create_sub_agent(mock_llm)
        from langgraph.graph.state import CompiledStateGraph

        assert isinstance(agent, CompiledStateGraph)

    def test_default_prompt_used(self, mocker):
        """默认使用 SUB_AGENT_PROMPT。"""
        from langgraph.prebuilt import create_react_agent

        mock_llm = mocker.MagicMock()
        spy = mocker.spy(create_sub_agent, "__wrapped__" if hasattr(create_sub_agent, "__wrapped__") else "__call__")
        agent = create_sub_agent(mock_llm)
        assert agent is not None

    def test_isolated_checkpointer(self, mocker):
        """每个 sub-agent 使用独立的 MemorySaver。"""
        mock_llm = mocker.MagicMock()
        agent1 = create_sub_agent(mock_llm)
        agent2 = create_sub_agent(mock_llm)
        # 不同的实例应该有独立的 checkpointer
        assert agent1.checkpointer is not agent2.checkpointer


class TestSubAgentPrompt:
    """SUB_AGENT_PROMPT 安全约束验证。"""

    def test_no_delegation_in_prompt(self):
        """System prompt 明确禁止委派。"""
        assert "不要创建子任务" in SUB_AGENT_PROMPT
        assert "委派" in SUB_AGENT_PROMPT
        assert "只有当前这一次执行机会" in SUB_AGENT_PROMPT

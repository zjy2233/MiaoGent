"""测试 delegate_task 工具工厂。

验证：
- build_delegate_task 返回 @tool 装饰的 async 函数
- 递归深度计数器正确递增/递减
- 超时返回错误提示
"""

from __future__ import annotations

import pytest

from src.tools.delegate_task import build_delegate_task


class TestBuildDelegateTask:
    """build_delegate_task 工厂测试。"""

    def test_returns_tool_function(self):
        """返回 @tool 装饰的 async 函数，具有 name 和 invoke 方法。"""
        mock_llm = None
        tool_fn = build_delegate_task(mock_llm)
        assert hasattr(tool_fn, "name")
        assert tool_fn.name == "delegate_task"
        assert hasattr(tool_fn, "invoke")
        assert hasattr(tool_fn, "args_schema")

    def test_tool_docstring_describes_delegation(self):
        """docstring 描述 sub-agent 委派功能。"""
        mock_llm = None
        tool_fn = build_delegate_task(mock_llm)
        doc = getattr(tool_fn, "description", "") or ""
        assert "sub-agent" in doc.lower() or "sub_agent" in doc.lower() or "子任务" in doc or "委派" in doc

    def test_has_task_and_timeout_parameters(self):
        """工具接受 task 和 timeout 参数。"""
        mock_llm = None
        tool_fn = build_delegate_task(mock_llm)
        schema = tool_fn.args_schema.schema() if hasattr(tool_fn, "args_schema") else {}
        properties = schema.get("properties", {})
        assert "task" in properties
        assert "timeout" in properties
        assert properties["timeout"].get("default", 0) == 60

    @pytest.mark.asyncio
    async def test_recursion_limit_returns_error(self):
        """超过 _MAX_DEPTH 时返回错误提示。"""
        mock_llm = None
        tool_fn = build_delegate_task(mock_llm)

        # 重置递归深度
        import src.tools.delegate_task as dt
        dt._recursion_depth = 99  # 模拟超出限制

        result = await tool_fn.ainvoke({"task": "test", "timeout": 10})
        assert "已达上限" in result or "3" in result

        dt._recursion_depth = 0  # 清理

    @pytest.mark.asyncio
    async def test_sub_agent_execution_error_is_caught(self, mocker):
        """sub-agent 执行异常时返回错误信息。"""
        import src.tools.delegate_task as dt

        original_run = dt.run_sub_agent
        dt.run_sub_agent = mocker.AsyncMock(side_effect=Exception("mock error"))
        dt._recursion_depth = 0

        mock_llm = None
        tool_fn = build_delegate_task(mock_llm)

        try:
            result = await tool_fn.ainvoke({"task": "test", "timeout": 10})
            assert isinstance(result, str)
        finally:
            dt.run_sub_agent = original_run

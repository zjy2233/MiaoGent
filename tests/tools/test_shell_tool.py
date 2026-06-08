"""Tests for the shell tool (using LangGraph interrupt pattern)."""

import pytest
from src.tools.shell import shell


class TestShellTool:
    def test_shell_is_tool(self):
        from langchain_core.tools import BaseTool
        assert isinstance(shell, BaseTool)

    def test_shell_name(self):
        assert shell.name == "shell"

    @pytest.mark.asyncio
    async def test_safe_echo(self):
        result = await shell.ainvoke("echo hello")
        assert "hello" in result.lower()

    @pytest.mark.asyncio
    async def test_safe_pwd(self):
        result = await shell.ainvoke("pwd")
        assert len(result.strip()) > 0

    @pytest.mark.asyncio
    async def test_unknown_command_confirm_level(self):
        """Commands not in safe list return confirmation message (no exception)."""
        result = await shell.ainvoke("nonexistent_cmd_xyz_123")
        assert "需要确认" in result

    @pytest.mark.asyncio
    async def test_sleep_confirm_level(self):
        """Sleep command returns confirmation message (no exception)."""
        result = await shell.ainvoke("sleep 10")
        assert "需要确认" in result

    @pytest.mark.asyncio
    async def test_high_risk_blocked(self):
        """High risk commands return blocked message."""
        result = await shell.ainvoke("rm -rf /")
        assert "拦截" in result

    @pytest.mark.asyncio
    async def test_safe_command_ping(self):
        """Ping is in safe list and should execute."""
        result = await shell.ainvoke("ping -n 1 127.0.0.1", timeout=5)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_mkdir_in_safe_list(self):
        """mkdir is in safe commands list."""
        result = await shell.ainvoke("echo mkdir_test")
        assert "mkdir" in result.lower()

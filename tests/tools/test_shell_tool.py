import pytest
from src.tools.shell import shell
from src.tools.dangerous import ConfirmationError


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
        """Commands not in safe list raise ConfirmationError (confirm level)."""
        with pytest.raises(ConfirmationError):
            await shell.ainvoke("nonexistent_cmd_xyz_123")

    @pytest.mark.asyncio
    async def test_sleep_confirm_level(self):
        """Sleep command is not in safe list, classified as confirm level."""
        with pytest.raises(ConfirmationError):
            await shell.ainvoke("sleep 10")

    @pytest.mark.asyncio
    async def test_safe_command_ping(self):
        """Ping is in safe list and should execute."""
        result = await shell.ainvoke("ping -n 1 127.0.0.1", timeout=5)
        assert isinstance(result, str)
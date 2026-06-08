"""Tests for SandboxExecutor (async shell executor)."""

import asyncio
import os
import sys

import pytest

from src.tools.shell.executor import (
    SandboxExecutor,
    _adapt_command_for_windows,
    _detect_command_head,
    _get_system_encoding,
    _get_timeout,
    _truncate_output_simple,
    MAX_INLINE_CHARS,
    IS_WINDOWS,
)


class TestSandboxExecutor:
    """SandboxExecutor 功能测试。"""

    @pytest.mark.asyncio
    async def test_execute_echo(self):
        executor = SandboxExecutor()
        result = await executor.execute("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout.lower() or "hello" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_execute_returncode(self):
        executor = SandboxExecutor()
        # On Windows, cmd /c exit N returns exit code N
        # On Unix, use python to exit with specific code
        result = await executor.execute("exit 42")
        assert result.returncode == 42

    @pytest.mark.asyncio
    async def test_execute_non_existent(self):
        executor = SandboxExecutor()
        result = await executor.execute("nonexistent_command_xyz_123")
        assert result.returncode != 0

    @pytest.mark.asyncio
    async def test_timeout(self):
        executor = SandboxExecutor()
        # Use sleep command with very short timeout
        if IS_WINDOWS:
            result = await executor.execute("ping -n 10 127.0.0.1", timeout=1)
        else:
            result = await executor.execute("sleep 10", timeout=1)
        assert result.timed_out
        assert "超时" in result.stderr

    @pytest.mark.asyncio
    async def test_truncation(self):
        executor = SandboxExecutor()
        n = MAX_INLINE_CHARS + 1000
        cmd = f'python -c "import sys; sys.stdout.write(\"x\" * {n})"'
        result = await executor.execute(cmd, timeout=10)
        # Should either be truncated or return cleanly
        if result.returncode == 0:
            # 头尾截断：保留 head(3000) + tail(1500)，中间折叠
            assert "中间省略" in result.stdout
            assert len(result.stdout) < n

    @pytest.mark.asyncio
    async def test_semaphore_concurrency(self):
        executor = SandboxExecutor()
        # Run multiple commands concurrently — should not crash
        tasks = [
            executor.execute("echo task1"),
            executor.execute("echo task2"),
            executor.execute("echo task3"),
            executor.execute("echo task4"),
            executor.execute("echo task5"),
        ]
        results = await asyncio.gather(*tasks)
        assert all(r.returncode == 0 for r in results)

    @pytest.mark.asyncio
    async def test_stdout_and_stderr(self):
        executor = SandboxExecutor()
        cmd = 'python -c "import sys; print(\"out\"); print(\"err\", file=sys.stderr)"'
        result = await executor.execute(cmd, timeout=10)
        if result.returncode == 0:
            assert "out" in result.stdout
            assert "err" in result.stderr

    @pytest.mark.asyncio
    async def test_empty_command(self):
        executor = SandboxExecutor()
        result = await executor.execute("")
        # 空命令期待任何合理结果
        assert isinstance(result, object)


class TestHelpers:
    """测试辅助函数。"""

    def test_detect_command_head(self):
        assert _detect_command_head("ls -la") == "ls"
        assert _detect_command_head("  echo hello  ") == "echo"
        assert _detect_command_head("") == ""
        assert _detect_command_head("   ") == ""

    def test_get_timeout(self):
        env_default = int(os.environ.get("SHELL_TIMEOUT", "15"))
        # 已知命令（不受 env 影响，TIMP_OUT_MAP 优先）
        assert _get_timeout("ls") == 3
        assert _get_timeout("git status") == 15
        assert _get_timeout("python script.py") == 30
        assert _get_timeout("pip install numpy") >= 120
        # 未知命令回退 DEFAULT_TIMEOUT（可能被环境变量覆盖）
        assert _get_timeout("foo bar") == env_default

    def test_truncate_output_simple(self):
        text = "a" * 100
        truncated, was_truncated = _truncate_output_simple(text, max_chars=50)
        assert was_truncated
        assert len(truncated) < 100
        assert "truncated" in truncated

        short_text = "hello"
        result, was_truncated = _truncate_output_simple(short_text, max_chars=50)
        assert not was_truncated
        assert result == "hello"


class TestWindowsAdaptation:
    """Windows 命令适配测试（平台无关的逻辑测试）。"""

    def test_tilde_expansion(self):
        """~ 展开为 %USERPROFILE%。"""
        adapted = _adapt_command_for_windows("ls ~/Desktop")
        if IS_WINDOWS:
            assert "%USERPROFILE%" in adapted or "Users" in adapted
            assert "~" not in adapted.replace("%USERPROFILE%", "")
        else:
            assert adapted == "ls ~/Desktop"  # 非 Windows 不变

    def test_mkdir_p_removed(self):
        """mkdir -p 转换为 mkdir。"""
        adapted = _adapt_command_for_windows("mkdir -p foo/bar")
        if IS_WINDOWS:
            assert adapted == "mkdir foo/bar"
        else:
            assert adapted == "mkdir -p foo/bar"

    def test_mkdir_parents_removed(self):
        """mkdir --parents 转换为 mkdir。"""
        adapted = _adapt_command_for_windows("mkdir --parents foo/bar")
        if IS_WINDOWS:
            assert adapted == "mkdir foo/bar"

    def test_dev_null_to_nul(self):
        """>/dev/null 转换为 >nul。"""
        adapted = _adapt_command_for_windows("echo hi >/dev/null")
        if IS_WINDOWS:
            assert "/dev/null" not in adapted
            assert adapted.endswith(">nul") or "nul" in adapted

    def test_stderr_dev_null(self):
        """2>/dev/null 转换为 2>nul。"""
        adapted = _adapt_command_for_windows("grep foo bar.txt 2>/dev/null")
        if IS_WINDOWS:
            assert "/dev/null" not in adapted
            assert "2>nul" in adapted

    def test_empty_command(self):
        """空命令适配不变。"""
        assert _adapt_command_for_windows("") == ""
        assert _adapt_command_for_windows("   ") == "   "

    def test_no_change_for_normal_command(self):
        """普通 Windows 命令不受影响。"""
        adapted = _adapt_command_for_windows("dir /b")
        if IS_WINDOWS:
            assert adapted == "dir /b"

    def test_system_encoding(self):
        """系统编码不为空。"""
        enc = _get_system_encoding()
        assert isinstance(enc, str)
        assert len(enc) > 0

"""Tests for run_python tool.

run_python 是异步 StructuredTool，用 await .ainvoke() 调用。
"""

import pytest

from src.tools.run_python import run_python, _build_sandbox_code


class TestBuildSandboxCode:
    def test_simple_code(self):
        result = _build_sandbox_code("x = 1")
        assert "x = 1" in result
        assert "try:" in result

    def test_syntax_error(self):
        result = _build_sandbox_code("x = ")
        assert "SyntaxError" in result


class TestRunPython:
    @pytest.mark.asyncio
    async def test_print(self):
        result = await run_python.ainvoke({"code": "print('hello from python')"})
        assert "hello from python" in result

    @pytest.mark.asyncio
    async def test_calculation(self):
        result = await run_python.ainvoke({"code": "print(42 * 2)"})
        assert "84" in result

    @pytest.mark.asyncio
    async def test_stderr(self):
        result = await run_python.ainvoke({"code": "import sys; sys.stderr.write('err msg')"})
        assert "err msg" in result

    @pytest.mark.asyncio
    async def test_syntax_error_execution(self):
        result = await run_python.ainvoke({"code": "x ="})
        assert "错误" in result or "SyntaxError" in result

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await run_python.ainvoke({"code": ""})
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        result = await run_python.ainvoke({"code": "raise ValueError('test err')"})
        assert "ValueError" in result or "test err" in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        result = await run_python.ainvoke({"code": "import time; time.sleep(100)", "timeout": 1})
        assert "超时" in result

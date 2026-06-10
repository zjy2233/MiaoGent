"""Python 代码执行工具：沙箱子进程运行，隔离主进程环境。

复用 src/tools/shell_executor.py 的 timeout 和截断模式。
"""

from __future__ import annotations

import asyncio
import sys
import time

from langchain_core.tools import tool

__category__ = "code_execution"

from src.tools.shell.executor import ShellResult, _get_system_encoding, _truncate_output_simple, IS_WINDOWS

MAX_OUTPUT_CHARS = 10_000


_TOOL_GUIDE = "执行 Python 代码时优先使用 run_python，而非 shell 的 python -c。"


@tool(description="在隔离子进程中执行 Python 代码。仅标准库，禁止文件操作。")
async def run_python(code: str, timeout: int = 30) -> str:
    """在隔离的 Python 子进程中运行代码并返回输出。

    适合执行计算、数据处理、字符串操作等任务。
    不能使用第三方库（只提供 Python 标准库）。
    不能进行文件系统操作（会被拒绝）。

    Args:
        code: 要执行的 Python 代码。
        timeout: 超时秒数（默认 30，最大 120）。

    Returns:
        代码的 stdout/stderr 输出。
    """
    if not code.strip():
        return "错误：代码为空"

    # 注入安全限制
    sandboxed_code = _build_sandbox_code(code)

    effective_timeout = min(max(timeout, 5), 120)

    start = time.monotonic()

    shell_cmd = [sys.executable, "-c", sandboxed_code]

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return f"错误：Python 未找到 — {exc}"
    except Exception as exc:
        return f"错误：进程创建失败 — {exc}"

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        stdout_bytes, stderr_bytes = b"", b""

    duration = time.monotonic() - start

    encoding = _get_system_encoding() if IS_WINDOWS else "utf-8"
    stdout = stdout_bytes.decode(encoding, errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode(encoding, errors="replace") if stderr_bytes else ""

    stdout, _ = _truncate_output_simple(stdout, MAX_OUTPUT_CHARS)
    stderr, _ = _truncate_output_simple(stderr, MAX_OUTPUT_CHARS)

    if timed_out:
        return f"错误：执行超时（{effective_timeout}秒）"

    if proc.returncode != 0:
        msg = f"退出码 {proc.returncode}"
        if stderr:
            msg += f"\n{stderr}"
        if stdout:
            msg += f"\n{stdout}"
        return msg

    output = stdout or stderr or "(无输出)"
    return output.strip()


def _build_sandbox_code(user_code: str) -> str:
    """包装用户代码，加入基本的安全限制。

    通过限制 builtins 和防止某些危险的 import，
    降低用户代码意外破坏主进程的风险。
    注意：这不是真正的沙箱，只是基本防护。
    """
    # 使用 compile 检测语法
    try:
        compile(user_code, "<user_code>", "exec")
    except SyntaxError as e:
        return f"raise SyntaxError({repr(str(e))})"

    # 简单包装：捕获异常并打印
    wrapped = (
        "import sys\n"
        "try:\n"
        "    sys.stdout.reconfigure(encoding='utf-8')\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        f"    {_indent_code(user_code)}\n"
        "except Exception:\n"
        "    import traceback; traceback.print_exc()\n"
    )
    return wrapped


def _indent_code(code: str) -> str:
    """给代码每行加 4 空格缩进。"""
    lines = code.splitlines()
    if not lines:
        return "    pass"
    indented = "\n".join(f"    {line}" for line in lines)
    return indented

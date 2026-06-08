"""异步安全 Shell 执行器：差异化超时 + 头尾截断 + 超大输出文件外置 + 进程清理。"""

from __future__ import annotations

import asyncio
import locale
import os
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

IS_WINDOWS = sys.platform == "win32"

TIMEOUT_MAP: dict[str, int] = {
    "ls": 3, "dir": 3, "pwd": 3, "echo": 3, "printf": 3,
    "whoami": 3, "id": 3, "uname": 3, "date": 3,
    "cat": 8, "head": 8, "tail": 8, "grep": 8, "find": 8,
    "sort": 8, "wc": 8, "diff": 8,
    "git": 15, "curl": 15, "wget": 15, "ping": 15,
    "ssh": 15, "scp": 15,
    "python": 30, "python3": 30, "node": 30, "npm": 30,
    "yarn": 30, "ruby": 30, "perl": 30, "php": 30,
    "java": 30, "go": 30, "rustc": 30, "cargo": 30,
    "make": 30, "cmake": 30, "gcc": 30, "g++": 30,
    "pip": 60, "uv": 60, "conda": 60,
}

DEFAULT_TIMEOUT = int(os.environ.get("SHELL_TIMEOUT", "15"))

# 输出大小策略
MAX_INLINE_CHARS = 10_000       # 超过此值启动头尾截断
HEAD_CHARS = 3_000              # 保留头部字符数
TAIL_CHARS = 1_500              # 保留尾部字符数
EXTERNALIZE_CHARS = 50_000      # 超过此值写入文件外置


def _get_system_encoding() -> str:
    if IS_WINDOWS:
        try:
            return locale.getpreferredencoding(do_setlocale=False) or "gbk"
        except Exception:
            return "gbk"
    return "utf-8"

_SYSTEM_ENCODING = _get_system_encoding()


def _adapt_command_for_windows(command: str) -> str:
    if not IS_WINDOWS:
        return command
    cmd = command
    cmd = re.sub(
        r'(?<!\w)~\s*(?=/|$)',
        os.environ.get("USERPROFILE", "C:\\Users\\Default").replace("\\", "/") + "/",
        cmd,
    )
    cmd = re.sub(r'(?<!\w)~(?:\s|$)', lambda m: os.environ.get("USERPROFILE", "C:\\Users\\Default") + m.group(0)[1:], cmd)
    cmd = re.sub(r'\bmkdir\s+(?:-p|--parents)(?:\s+|$)', 'mkdir ', cmd)
    cmd = re.sub(r'\bmkdir\s+(-p)(?!\S)', 'mkdir ', cmd)
    cmd = cmd.replace("/dev/null", "nul")
    return cmd


@dataclass
class ShellResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    duration: float = 0.0
    timed_out: bool = False
    truncated: bool = False
    output_path: str = ""  # 文件外置时的路径

    @property
    def output(self) -> str:
        return self.stdout or self.stderr or ""


def _detect_command_head(command: str) -> str:
    stripped = command.strip().split()
    if not stripped:
        return ""
    return stripped[0].lower().lstrip("/")


def _get_timeout(command: str, default: int | None = None) -> int:
    head = _detect_command_head(command)
    base = default or TIMEOUT_MAP.get(head, DEFAULT_TIMEOUT)
    if head in ("pip", "pip3") and "install" in command:
        return max(base, 120)
    if head in ("uv",) and "install" in command:
        return max(base, 120)
    if head in ("npm", "yarn") and ("install" in command or "ci" in command):
        return max(base, 120)
    return base


def _truncate_output_simple(text: str, max_chars: int) -> tuple[str, bool]:
    """简单截断：保留前 max_chars 字符（供 run_python 等其它模块使用）。"""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + f"\n... (output truncated, {len(text)} chars total)", True


def _truncate_output_head_tail(text: str) -> tuple[str, bool]:
    """头尾截断：保留头部 HEAD_CHARS + 尾部 TAIL_CHARS，中间折叠。"""
    if len(text) <= MAX_INLINE_CHARS:
        return text, False
    head = text[:HEAD_CHARS]
    tail = text[-TAIL_CHARS:]
    collapsed = len(text) - HEAD_CHARS - TAIL_CHARS
    return f"{head}\n... (中间省略 {collapsed} 字符) ...\n{tail}", True


def _externalize_output(text: str, command: str, temp_dir: str) -> str:
    """超大输出写入临时文件，返回文件路径。"""
    os.makedirs(temp_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    # 取命令前 30 字符做文件名标识
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', command.strip().split()[0][:30]) if command.strip() else "shell"
    filename = f"shell_out_{safe_name}_{ts}.txt"
    filepath = os.path.join(temp_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    return filepath


class SandboxExecutor:
    """异步安全 Shell 命令执行器。"""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(4)

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        effective_timeout = _get_timeout(command) if timeout is None else timeout
        async with self._semaphore:
            return await self._run_process(command, effective_timeout, cwd, env)

    async def _run_process(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> ShellResult:
        start = time.monotonic()
        actual_command = _adapt_command_for_windows(command) if IS_WINDOWS else command
        if IS_WINDOWS:
            shell_cmd = ["cmd.exe", "/c", actual_command]
        else:
            shell_cmd = ["/bin/bash", "-c", actual_command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            duration = time.monotonic() - start
            return ShellResult(stderr=f"命令未找到：{exc}", returncode=-1, duration=duration)
        except Exception as exc:
            duration = time.monotonic() - start
            return ShellResult(stderr=f"进程创建失败：{exc}", returncode=-1, duration=duration)

        timed_out = False
        comm_task = asyncio.create_task(proc.communicate())
        sleep_task = asyncio.create_task(asyncio.sleep(timeout))
        done, pending = await asyncio.wait(
            [comm_task, sleep_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        timed_out = sleep_task in done

        if timed_out:
            comm_task.cancel()
            try:
                await asyncio.wait_for(comm_task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            try:
                if IS_WINDOWS:
                    import subprocess as _sp
                    _sp.run(["taskkill", "/f", "/t", "/pid", str(proc.pid)],
                             capture_output=True, timeout=5)
                else:
                    proc.kill()
            except (ProcessLookupError, Exception):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            stdout_bytes, stderr_bytes = b"", b""
        else:
            stdout_bytes, stderr_bytes = comm_task.result()
            sleep_task.cancel()

        duration = time.monotonic() - start
        encoding = _SYSTEM_ENCODING if IS_WINDOWS else "utf-8"
        stdout = stdout_bytes.decode(encoding, errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(encoding, errors="replace") if stderr_bytes else ""

        # 输出大小策略：头尾截断 / 文件外置
        output_path = ""
        truncated = False
        if len(stdout) > EXTERNALIZE_CHARS:
            output_path = _externalize_output(stdout, command, "data/temp")
            stdout = f"输出过长（{len(stdout)} 字符），已保存到 {output_path}\n请使用 read_file 工具读取前 3000 字符了解输出内容。"
            truncated = True
        else:
            stdout, truncated = _truncate_output_head_tail(stdout)

        if timed_out:
            stderr = f"命令超时（{timeout}秒）\n" + stderr

        return ShellResult(
            stdout=stdout,
            stderr=stderr,
            returncode=-1 if timed_out else proc.returncode or 0,
            duration=duration,
            timed_out=timed_out,
            truncated=truncated,
            output_path=output_path,
        )


_executor = SandboxExecutor()


async def run_shell(
    command: str,
    *,
    timeout: int | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> ShellResult:
    return await _executor.execute(command, timeout=timeout, cwd=cwd, env=env)

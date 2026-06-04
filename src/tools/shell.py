"""Shell 命令执行工具：危险检测 + subprocess 执行。"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from langchain_core.tools import tool

from src.tools.dangerous import ConfirmationError, check_danger

IS_WINDOWS = sys.platform == "win32"
SHELL_ARGS = ["cmd.exe", "/c"] if IS_WINDOWS else ["/bin/bash", "-c"]


@tool
def shell(command: str, timeout: int = 30) -> str:
    """执行 shell 命令并返回 stdout/stderr 输出。

    Args:
        command: 要执行的 shell 命令。
        timeout: 超时秒数，默认 30 秒。

    Returns:
        命令的标准输出/错误输出。
        危险命令被拒绝时返回错误信息，Agent 应据此回复用户。
    """
    # 1. 危险检测
    danger = check_danger(command)
    if danger is not None:
        if danger.danger_level == "high_risk":
            msg = f"错误：高危命令已被系统拦截 — {danger.reason}"
            if danger.safer_alternatives:
                msg += f"\n   替代建议：{'；'.join(danger.safer_alternatives)}"
            return msg
        # confirm 级别抛异常，由 REPL 层处理确认
        raise danger

    # 2. 执行
    try:
        result = subprocess.run(
            SHELL_ARGS + [command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"错误：命令超时（{timeout}秒）"
    except Exception as exc:
        return f"错误：执行失败 — {exc}"

    # 3. 结果处理
    if result.returncode != 0:
        return f"错误（退出码 {result.returncode}）：{result.stderr or result.stdout}"
    return result.stdout or result.stderr or "(命令执行成功，无输出)"
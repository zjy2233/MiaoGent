"""Shell 命令执行工具：危险检测 → interrupt 确认 → async 沙箱执行。

流程:
  1. 危险检测 (shell_patterns 四层语义闸门)
  2. 高危 → 直接返回错误
  3. 需确认 → interrupt() 暂停 Graph 等待用户，恢复后执行
  4. 安全 → 使用 SandboxExecutor 异步沙箱执行 + AuditLogger 审计
"""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt

__category__ = "code_execution"

from src.tools.shell.danger import check_danger
from src.tools.shell.executor import SandboxExecutor, _get_timeout
from src.store.audit import AuditLogger

_executor = SandboxExecutor()
_logger = AuditLogger()


_TOOL_GUIDE = (
    "shell 是最后兜底手段，仅在专用工具无法完成任务时使用。\n"
    "shell 运行在 Windows cmd.exe 上：\n"
    "- 使用 Windows 路径语法（C:\\Users\\name），不要用 ~/（它不展开）\n"
    "- mkdir 直接用（无需 -p 参数）\n"
    "- 重定向到空设备用 nul 而非 /dev/null\n"
    "- 环境变量查看用 set 而非 env"
)


@tool(description="执行 shell 命令。Windows cmd.exe 环境。高危命令自动拦截，需确认命令暂停等待批准。")
async def shell(command: str, timeout: int | None = None) -> str:
    """执行 shell 命令并返回 stdout/stderr 输出。

    Args:
        command: 要执行的 shell 命令。
        timeout: 超时秒数，为 None 时根据命令类型自动选择。

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
        if danger.danger_level == "confirm":
            try:
                approved = interrupt({
                    "type": "shell_confirm",
                    "command": command,
                    "reason": danger.reason,
                    "alternatives": danger.safer_alternatives,
                })
            except (RuntimeError, KeyError):
                msg = f"此操作需要确认：{command}"
                if danger.reason:
                    msg += f"\n原因：{danger.reason}"
                if danger.safer_alternatives:
                    msg += f"\n替代建议：{'；'.join(danger.safer_alternatives)}"
                return msg
            if not approved:
                return f"操作已取消：{command}"

    # 2. 异步沙箱执行
    effective_timeout = _get_timeout(command) if timeout is None else timeout
    result = await _executor.execute(command, timeout=effective_timeout)

    # 3. 审计日志
    _logger.log_simple(
        command=command,
        returncode=result.returncode,
        duration=result.duration,
        stdout_size=len(result.stdout),
        approved=True,
    )

    # 4. 结果处理
    if result.timed_out:
        return (
            f"命令执行超时（{effective_timeout}秒），进程已强制终止。\n"
            f"请将此超时错误直接告知用户，不要重试此命令。"
        )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        parts = [f"命令执行失败（退出码 {result.returncode}）"]
        if stderr:
            parts.append(stderr)
        elif stdout:
            parts.append(stdout)
        parts.append("请将此错误直接告知用户，不要重试此命令。")
        return "\n".join(parts)
    output = result.output.strip()
    return output or "(命令执行成功，无输出)"

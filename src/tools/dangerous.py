"""危险检测入口 + ConfirmationError 异常定义。"""

from __future__ import annotations

import re

from src.tools.shell_patterns import CommandClassifier, DangerLevel

__all__ = ["ConfirmationError", "check_danger", "DangerLevel"]


class ConfirmationError(Exception):
    """危险操作需要用户确认时抛出。

    Attributes:
        command: 原始命令字符串
        reason: 危险原因描述
        danger_level: "confirm" 或 "high_risk"
    """

    def __init__(self, command: str, reason: str, danger_level: str):
        super().__init__(f"危险操作：{command} — {reason}（{danger_level}）")
        self.command = command
        self.reason = reason
        self.danger_level = danger_level


def check_danger(command: str) -> ConfirmationError | None:
    """检测命令危险等级。

    Returns:
        None — 安全命令，可直接执行
        ConfirmationError — 需确认或直接拒绝的命令
    """
    level = CommandClassifier().classify(command)

    if level == DangerLevel.SAFE:
        return None

    if level == DangerLevel.HIGH_RISK:
        reason = _get_high_risk_reason(command)
        return ConfirmationError(command, reason, "high_risk")

    # CONFIRM
    reason = _get_confirm_reason(command)
    return ConfirmationError(command, reason, "confirm")


def _get_high_risk_reason(command: str) -> str:
    if re.search(r"rm\s+-rf\s+/", command):
        return "递归删除根目录"
    if "dd " in command:
        return "直接磁盘操作"
    if re.search(r">\s*/dev/(sd|nul|null)", command):
        return "写入设备文件"
    if re.search(r"(curl|wget).*\|\s*(sh|bash)", command):
        return "远程代码执行"
    if re.search(r":\(\)\s*\{.*:\|", command):
        return "Fork 炸弹"
    if "shutdown" in command and "-h" in command:
        return "系统关机"
    if "reboot" in command:
        return "系统重启"
    if "mkfs" in command:
        return "格式化分区"
    return "高危操作"


def _get_confirm_reason(command: str) -> str:
    if re.search(r"\brm\b", command):
        if re.search(r"\brm\s+-r", command) or re.search(r"\brm\s+-rf", command):
            return "递归删除目录"
        return "删除文件"
    if re.search(r"\bmv\b", command):
        return "移动/重命名文件"
    if re.search(r"\bcp\b", command):
        return "复制文件"
    if re.search(r"(?<!>)>\s*\w", command):
        return "重定向覆盖文件"
    if re.search(r"\bchmod\b", command):
        return "变更文件权限"
    if re.search(r"\bchown\b", command):
        return "变更文件所有权"
    if re.search(r"\bkill\b", command):
        return "终止进程"
    if re.search(r"\bshutdown\b", command):
        return "系统关机命令"
    if re.search(r"\breboot\b", command):
        return "系统重启命令"
    return "危险操作"
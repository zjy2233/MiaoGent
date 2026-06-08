"""危险检测入口 + ConfirmationError 异常定义。"""

from __future__ import annotations

import re

from src.tools.shell.patterns import CommandClassifier, DangerLevel, _parse_tokens

__all__ = ["ConfirmationError", "check_danger", "DangerLevel"]


class ConfirmationError(Exception):
    """危险操作需要用户确认时抛出。"""

    def __init__(
        self,
        command: str,
        reason: str,
        danger_level: str,
        safer_alternatives: list[str] | None = None,
    ):
        super().__init__(f"危险操作：{command} — {reason}（{danger_level}）")
        self.command = command
        self.reason = reason
        self.danger_level = danger_level
        self.safer_alternatives = safer_alternatives or []


def check_danger(command: str) -> ConfirmationError | None:
    """检测命令危险等级。

    Returns:
        None — 安全命令，可直接执行
        ConfirmationError — 需确认或直接拒绝的命令
    """
    level, reason, alts = CommandClassifier().classify(command)

    if level == DangerLevel.SAFE:
        return None

    if level == DangerLevel.HIGH_RISK:
        return ConfirmationError(command, reason or "高危操作", "high_risk", alts)

    return ConfirmationError(command, reason or "危险操作", "confirm", alts)

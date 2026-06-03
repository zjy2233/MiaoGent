"""Shell 命令危险等级分类：安全 / 需确认 / 高危。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class DangerLevel(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"    # 需用户确认
    HIGH_RISK = "high_risk"  # 直接拒绝


# ── 高危命令模式（正则）─────────────────────────────────────────────────────

_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # 根目录递归删除
    re.compile(r"^\s*rm\s+-rf\s+/\s*$"),
    re.compile(r"^\s*rm\s+-rf?\s+/\s"),
    # 磁盘直接操作
    re.compile(r"\bdd\b.*(?:of=|if=).*(?:/dev/|sd[a-z])"),
    # 设备文件写入
    re.compile(r">\s*/dev/(sd[a-z]|nul|null)"),
    # 远程代码执行
    re.compile(r"(?:curl|wget).*\|\s*sh\b"),
    re.compile(r"\|\s*bash\b"),
    # 系统级危险命令
    re.compile(r"\bshutdown\b.*-h\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bmkfs\b"),
    # Fork 炸弹
    re.compile(r":\(\)\s*\{\s*:\|:\s*&", re.IGNORECASE),
]


# ── 需确认命令模式（正则）──────────────────────────────────────────────────

_CONFIRM_PATTERNS: list[re.Pattern[str]] = [
    # rm 删除文件/目录
    re.compile(r"\brm\b"),
    # mv 移动/重命名
    re.compile(r"\bmv\b"),
    # cp 复制
    re.compile(r"\bcp\b"),
    # 重定向覆盖（不是追加）
    re.compile(r"(?<!>)>\s*\w"),
    # chmod 权限变更
    re.compile(r"\bchmod\b"),
    # chown 所有权变更
    re.compile(r"\bchown\b"),
    # kill 终止进程
    re.compile(r"\bkill\b"),
    # shutdown/reboot（非高危的其他形式）
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    # 写入设备文件（非高危模式）
    re.compile(r">\s*/dev/"),
]


# ── 安全命令（明确允许，无需检测）─────────────────────────────────────────

_SAFE_COMMANDS: set[str] = {
    "ls", "dir", "pwd", "whoami", "uname", "id",
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep",
    "find", "which", "whereis", "type",
    "echo", "printf", "date", "cal",
    "df", "du", "free", "top", "ps", "env", "export", "history",
    "curl", "wget", "ping", "nc", "netstat", "ss", "ip", "ifconfig",
    "git", "svn", "hg",
    "python", "python3", "node", "ruby", "perl", "php", "java", "go", "rustc",
    "make", "cmake", "gcc", "g++", "clang", "cargo", "npm", "yarn", "pip", "uv",
    "docker", "kubectl", "terraform", "ansible", "vagrant",
    "tar", "zip", "unzip", "gzip", "gunzip", "bzip2", "xz",
    "sort", "uniq", "wc", "cut", "awk", "sed", "tr", "tee",
    "mkdir", "rmdir",
    "ln", "readlink",
    "mount", "umount",
    "crontab",
    "ssh", "scp", "rsync",
    "awk", "sed", "vi", "vim", "nano", "emacs",
}


class CommandClassifier:
    """根据命令内容分类危险等级。"""

    def __init__(
        self,
        allowed_patterns: Optional[list[str]] = None,
        blocked_patterns: Optional[list[str]] = None,
    ):
        self._allowed = [re.compile(p, re.IGNORECASE) for p in (allowed_patterns or [])]
        self._blocked = [re.compile(p, re.IGNORECASE) for p in (blocked_patterns or [])]

    def classify(self, command: str) -> DangerLevel:
        # 1. 自定义黑名单优先
        if any(p.search(command) for p in self._blocked):
            return DangerLevel.HIGH_RISK

        # 2. 自定义白名单（跳过危险检测）
        if any(p.search(command) for p in self._allowed):
            return DangerLevel.SAFE

        # 3. 高危模式匹配
        if any(p.search(command) for p in _HIGH_RISK_PATTERNS):
            return DangerLevel.HIGH_RISK

        # 4. 需确认模式匹配
        if any(p.search(command) for p in _CONFIRM_PATTERNS):
            return DangerLevel.CONFIRM

        # 5. 已知安全命令（基于命令头）
        cmd_head = command.strip().split()[0] if command.strip() else ""
        if cmd_head in _SAFE_COMMANDS:
            return DangerLevel.SAFE

        # 6. 默认：需确认（保守策略）
        return DangerLevel.CONFIRM
"""Shell 命令危险等级分类：安全 / 需确认 / 高危。

四层语义闸门架构：
  Layer 1: 命令解析层  — shlex 解析失败 → HIGH_RISK
  Layer 2: 高危短路层  — 密钥路径/远端执行/强制 push 等 → HIGH_RISK
  Layer 3: 子命令白名单 — git/kubectl/docker/curl 分族管控 + SAFE 命令头兜底
  Layer 4: ask 层      — 可疑但可能合理 → CONFIRM（含替代建议）
"""

from __future__ import annotations

import re
import shlex
from enum import Enum
from typing import Optional

# ── shlex 兼容性 ──────────────────────────────────────────────────────────────
# Windows cmd /c 需要 posix=False，避免引号解析失败
_POSIX = False


class DangerLevel(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"  # 需用户确认
    HIGH_RISK = "high_risk"  # 直接拒绝


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: 高危短路模式（直接拒绝）
# ─────────────────────────────────────────────────────────────────────────────

_SHORTCUT_DENY_PATTERNS: list[re.Pattern[str]] = [
    # 密钥 / 敏感路径
    re.compile(r"/\.ssh/|/\.git/config|~\./\.ssh|\.ssh/\.+|osascript.*-e"),
    # 远端代码执行
    re.compile(r"(?:curl|wget).*\|\s*(?:bash|sh)\b", re.IGNORECASE),
    re.compile(r"\|\s*sh\b"),
    # 磁盘直接操作
    re.compile(r"\bdd\b.*(?:of=|if=).*(?:/dev/|sd[a-z])"),
    # 强制远程写入
    re.compile(r"git\s+push\s+--force", re.IGNORECASE),
    re.compile(r"git\s+reset\s+--hard", re.IGNORECASE),
    # Docker 高危
    re.compile(r"docker\s+system\s+prune", re.IGNORECASE),
    re.compile(r"docker\s+kill\b"),
    re.compile(r"docker\s+rmi\b"),
    # kubectl 高危写操作
    re.compile(r"kubectl\s+delete", re.IGNORECASE),
    re.compile(r"kubectl\s+exec\b"),
    re.compile(r"kubectl\s+apply\s+-f", re.IGNORECASE),
    # nc 远控
    re.compile(r"\bnc\s+.*-e\b"),
    # 系统级危险
    re.compile(r"\bshutdown\b.*-h\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\binit\b"),
    # Fork 炸弹
    re.compile(r":\(\)\s*\{\s*:\|:\s*&", re.IGNORECASE),
    # rm -rf / 根目录
    re.compile(r"^\s*rm\s+-rf\s+/\s*$"),
    re.compile(r"^\s*rm\s+-rf?\s+/\s"),
]


# 已知安全命令（明确允许，无需检测）
_SAFE_COMMANDS: frozenset[str] = frozenset({
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
    "vi", "vim", "nano", "emacs",
    "ruff", "mypy", "go", "cargo",
})

# ─────────────────────────────────────────────────────────────────────────────
# Layer 3: 子命令白名单
# ─────────────────────────────────────────────────────────────────────────────

SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "status", "diff", "log", "show", "branch", "fetch", "pull", "remote", "rev-parse",
})
SAFE_KUBECTL_SUBCOMMANDS: frozenset[str] = frozenset({
    "get", "describe", "logs", "top", "explain", "cluster-info", "api-resources",
})
SAFE_DOCKER_SUBCOMMANDS: frozenset[str] = frozenset({
    "ps", "logs", "inspect", "images", "pull", "build", "run", "create", "start", "stop",
})
SAFE_CURL_METHODS: frozenset[str] = frozenset({"POST", "PUT", "DELETE", "PATCH", "OPTIONS"})

# 允许的 curl flag（只读类）
SAFE_CURL_FLAGS: frozenset[str] = frozenset({
    "-I", "--head", "-s", "-S", "-o", "-w", "--max-time", "-L", "--max-filesize",
})

# 需确认的命令模式（原有 CONFIRM 逻辑保留，移到 ask 层）
_ASK_PATTERNS: list[tuple[re.Pattern[str], str, tuple[str, ...]]] = [
    # (pattern, reason, safer_alternatives)
    (re.compile(r"\brm\b"), "删除文件/目录", ("谨慎确认目标路径",)),
    (re.compile(r"\bmv\b"), "移动/重命名文件", ("确认目标路径正确",)),
    (re.compile(r"\bcp\b"), "复制文件", ("确认源和目标路径正确",)),
    (re.compile(r"(?<![0-9&|/>])>(?!/)\s*[a-zA-Z]"), "重定向覆盖文件", ("echo 内容 >> file.txt 追加代替覆盖", "确认目标文件不需要保留")),
    (re.compile(r"\bchmod\b"), "变更文件权限", ("确认权限变更不影响系统安全",)),
    (re.compile(r"\bchown\b"), "变更文件所有权", ("确认所有权变更不影响系统安全",)),
    (re.compile(r"\bkill\b"), "终止进程", ("确认进程 ID 正确，避免杀错进程",)),
    (re.compile(r"\bshutdown\b"), "系统关机命令", ("确认无误",)),
    (re.compile(r"\breboot\b"), "系统重启命令", ("确认无误",)),
]


# ─────────────────────────────────────────────────────────────────────────────
# 高危原因映射
# ─────────────────────────────────────────────────────────────────────────────

def _shortcut_reason(command: str) -> str:
    if re.search(r"/\.ssh/|~\./\.ssh", command):
        return "访问密钥路径"
    if re.search(r"(?:curl|wget).*\|\s*(?:bash|sh)\b", command):
        return "远程代码执行"
    if re.search(r"\|\s*sh\b", command):
        return "管道执行 shell"
    if re.search(r"\bdd\b.*(?:of=|if=).*(?:/dev/|sd[a-z])", command):
        return "直接磁盘操作"
    if re.search(r"git\s+push\s+--force", command):
        return "强制推送远端"
    if re.search(r"git\s+reset\s+--hard", command):
        return "强制重置本地提交历史"
    if re.search(r"docker\s+system\s+prune", command):
        return "清理所有 Docker 资源"
    if re.search(r"docker\s+kill\b", command):
        return "强制终止容器"
    if re.search(r"kubectl\s+delete", command):
        return "删除 K8s 资源"
    if re.search(r"kubectl\s+exec\b", command):
        return "在容器执行命令"
    if re.search(r"\bnc\s+.*-e\b", command):
        return "网络远控"
    if re.search(r"shutdown.*-h", command):
        return "系统关机"
    if re.search(r"\breboot\b", command):
        return "系统重启"
    if re.search(r"\bmkfs\b", command):
        return "格式化分区"
    if re.search(r"\binit\b", command):
        return "初始化系统"
    if re.search(r":\(\)\s*\{\s*:\|:\s*&", command):
        return "Fork 炸弹"
    if re.search(r"rm\s+-rf\s+/\s*$", command):
        return "递归删除根目录"
    if re.search(r"rm\s+-rf?\s+/\s", command):
        return "递归删除根目录"
    return "高危操作"


# ─────────────────────────────────────────────────────────────────────────────
# curl 方法级管控
# ─────────────────────────────────────────────────────────────────────────────

def _classify_curl(tokens: list[str]) -> tuple[DangerLevel, str | None, list[str]]:
    """检查 curl 命令，只允许只读方法（HEAD/GET），拦 POST/PUT/DELETE。"""
    has_post = False
    has_body = False
    safe_flags = SAFE_CURL_FLAGS

    for token in tokens[1:]:
        upper = token.upper()
        # 检测 HTTP 方法
        if upper in SAFE_CURL_METHODS:
            has_post = True
        # 检测 -d/--data/--data-binary 等 body 标志
        if token in ("-d", "--data", "--data-binary", "--data-urlencode"):
            has_body = True
        if upper.startswith("--DATA") or upper.startswith("-D"):
            has_body = True

    # curl 单独使用（无参数）视为安全
    if len(tokens) == 1:
        return (DangerLevel.SAFE, None, [])

    # -I/--head 优先，即使有 POST 也放行
    if any(t in ("-I", "--head") for t in tokens[1:]):
        return (DangerLevel.SAFE, None, [])

    if has_post or has_body:
        return (
            DangerLevel.HIGH_RISK,
            "curl 写入操作（POST/PUT/DELETE 或带 body）",
            ["curl -I https://... 只读检查", "curl -X GET https://..."],
        )

    return (DangerLevel.SAFE, None, [])


# ─────────────────────────────────────────────────────────────────────────────
# 命令解析辅助
# ─────────────────────────────────────────────────────────────────────────────

def _parse_tokens(command: str) -> list[str] | None:
    """解析命令 token，解析失败返回 None。"""
    try:
        return shlex.split(command, posix=_POSIX)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 主分类器
# ─────────────────────────────────────────────────────────────────────────────

class CommandClassifier:
    """四层语义闸门命令分类器。"""

    def __init__(
        self,
        allowed_patterns: Optional[list[str]] = None,
        blocked_patterns: Optional[list[str]] = None,
    ):
        self._allowed = [re.compile(p, re.IGNORECASE) for p in (allowed_patterns or [])]
        self._blocked = [re.compile(p, re.IGNORECASE) for p in (blocked_patterns or [])]

    def classify(self, command: str) -> tuple[DangerLevel, str | None, list[str]]:
        """分类命令，返回 (level, reason, safer_alternatives)。

        - level: SAFE/CONFIRM/HIGH_RISK
        - reason: 危险原因（CONFIRM/HIGH_RISK 时有值）
        - safer_alternatives: 替代建议列表（ask 层 CONFIRM 时有值）
        """
        # ── Layer 1: 命令解析 ──
        tokens = _parse_tokens(command)
        if tokens is None:
            return (DangerLevel.HIGH_RISK, "命令解析失败（语法错误）", [])

        if not tokens:
            return (DangerLevel.CONFIRM, "空命令", [])

        # ── 自定义规则优先 ──
        if any(p.search(command) for p in self._blocked):
            return (DangerLevel.HIGH_RISK, "自定义黑名单规则", [])

        if any(p.search(command) for p in self._allowed):
            return (DangerLevel.SAFE, None, [])

        # ── Layer 2: 高危短路 ──
        for p in _SHORTCUT_DENY_PATTERNS:
            if p.search(command):
                return (DangerLevel.HIGH_RISK, _shortcut_reason(command), [])

        # ── Layer 3: 子命令白名单 ──
        cmd_head = tokens[0].lower()
        subcmd = tokens[1].lower() if len(tokens) > 1 else ""

        # git 只读子命令
        if cmd_head == "git":
            if subcmd in SAFE_GIT_SUBCOMMANDS:
                return (DangerLevel.SAFE, None, [])
            return (DangerLevel.CONFIRM, f"git {subcmd} 操作", [])

        # kubectl 只读子命令
        if cmd_head == "kubectl":
            if subcmd in SAFE_KUBECTL_SUBCOMMANDS:
                return (DangerLevel.SAFE, None, [])
            return (DangerLevel.CONFIRM, f"kubectl {subcmd} 操作", [])

        # docker 只读子命令
        if cmd_head == "docker":
            if subcmd in SAFE_DOCKER_SUBCOMMANDS:
                return (DangerLevel.SAFE, None, [])
            return (DangerLevel.CONFIRM, f"docker {subcmd} 操作", [])

        # curl 方法级管控
        if cmd_head == "curl":
            return _classify_curl(tokens)

        # npm/pip/yarn 管控
        if cmd_head in ("npm", "pnpm", "yarn"):
            has_g = "-g" in tokens or "--global" in tokens
            if has_g:
                return (DangerLevel.HIGH_RISK, "全局安装（污染环境）", ["npm install（项目内）", "npx 临时执行"])
            return (DangerLevel.SAFE, None, [])

        if cmd_head in ("pip", "pip3"):
            has_user = "--user" in tokens
            if has_user:
                return (DangerLevel.HIGH_RISK, "用户级安装", ["uv pip install（项目内）", "pip install -r requirements.txt"])
            return (DangerLevel.SAFE, None, [])

        # ── Layer 4: ask 模式（在 SAFE 头兜底之前，确保 rm/chmod 等被正确识别）──
        for pattern, reason, alts in _ASK_PATTERNS:
            if pattern.search(command):
                return (DangerLevel.CONFIRM, reason, list(alts))

        # ── 已知安全命令头兜底 ──
        if cmd_head in _SAFE_COMMANDS:
            return (DangerLevel.SAFE, None, [])

        # ── 默认：需确认（保守策略）──
        return (DangerLevel.CONFIRM, "未分类操作", [])


# ─────────────────────────────────────────────────────────────────────────────
# 兼容性别名（保留原有调用方式）
# ─────────────────────────────────────────────────────────────────────────────

def classify(command: str) -> tuple[DangerLevel, str | None, list[str]]:
    """ Convenience wrapper —等价于 CommandClassifier().classify() """
    return CommandClassifier().classify(command)
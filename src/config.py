"""集中加载配置，从 .env 文件与环境变量读取 LLM 凭据。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env（如果存在），不覆盖已有环境变量
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass(frozen=True)
class Settings:
    """应用配置，所有字段都从环境变量读取。"""

    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    request_timeout: float = 10.0
    # ── Shell 命令执行 ────────────────────────────────
    shell_auto_confirm: bool = False    # true = 安全命令免确认直接执行（默认行为）
    shell_high_risk_block: bool = True  # true = 高危命令直接拒绝
    shell_allowed_patterns: list[str] = field(default_factory=list)   # 自定义白名单（免检测命令）
    shell_blocked_patterns: list[str] = field(default_factory=list)   # 黑名单（强制高危）
    # ── 持久化与记忆管理（新增）──
    db_path: str = "history.db"          # SqliteSaver 用的 SQLite 文件
    max_turns: int = 10                  # 保留最近 N 轮（1 轮 = 1 human + 1 ai）
    max_message_chars: int = 12000       # 保留消息的累计字符数上限
    compression_model: str = ""          # 摘要用的 LLM；空字符串 = 复用主 LLM

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "未找到 DEEPSEEK_API_KEY，请在项目根目录的 .env 中配置，"
                "或参考 .env.example。"
            )
        return cls(
            deepseek_api_key=api_key,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "10")),
            shell_auto_confirm=os.getenv("SHELL_AUTO_CONFIRM", "false").lower() == "true",
            shell_high_risk_block=os.getenv("SHELL_HIGH_RISK_BLOCK", "true").lower() == "true",
            shell_allowed_patterns=_env_list("SHELL_ALLOWED_PATTERNS"),
            shell_blocked_patterns=_env_list("SHELL_BLOCKED_PATTERNS"),
            db_path=os.getenv("DB_PATH", "history.db"),
            max_turns=_env_int("MAX_TURNS", 10),
            max_message_chars=_env_int("MAX_MESSAGE_CHARS", 12000),
            compression_model=os.getenv("COMPRESSION_MODEL", "").strip(),
        )

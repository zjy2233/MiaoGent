"""集中加载配置，从 .env 文件与环境变量读取 LLM 凭据。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from src.core.miaogent_home import get_data_path

# 加载项目根目录下的 .env（如果存在），不覆盖已有环境变量
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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

    # LLM 泛化配置（provider: deepseek | openai | anthropic）
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    request_timeout: float = 10.0
    shell_timeout: int = 30
    shell_auto_confirm: bool = False
    shell_high_risk_block: bool = True
    shell_allowed_patterns: list[str] = field(default_factory=list)
    shell_blocked_patterns: list[str] = field(default_factory=list)
    db_path: str = ""
    max_turns: int = 10
    max_message_chars: int = 12000
    compression_model: str = ""
    rewoo_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
        # 先读 LLM_* 通用变量，fallback 到旧版 DEEPSEEK_* 变量
        api_key = (os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        base_url = (os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "").strip()
        model = (os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "").strip()

        if not api_key:
            raise RuntimeError(
                "未找到 LLM_API_KEY，请在项目根目录的 .env 中配置，"
                "或参考 .env.example。"
            )
        return cls(
            llm_provider=provider,
            llm_api_key=api_key,
            llm_base_url=base_url,
            llm_model=model,
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "10")),
            shell_timeout=_env_int("SHELL_TIMEOUT", 30),
            shell_auto_confirm=os.getenv("SHELL_AUTO_CONFIRM", "false").lower() == "true",
            shell_high_risk_block=os.getenv("SHELL_HIGH_RISK_BLOCK", "true").lower() == "true",
            shell_allowed_patterns=_env_list("SHELL_ALLOWED_PATTERNS"),
            shell_blocked_patterns=_env_list("SHELL_BLOCKED_PATTERNS"),
            db_path=os.getenv("DB_PATH", "") or str(get_data_path("history.db")),
            max_turns=_env_int("MAX_TURNS", 10),
            max_message_chars=_env_int("MAX_MESSAGE_CHARS", 12000),
            compression_model=os.getenv("COMPRESSION_MODEL", "").strip(),
            rewoo_enabled=os.getenv("REWOO_ENABLED", "false").lower() == "true",
        )

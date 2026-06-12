"""设置读写服务。"""

from __future__ import annotations

import os
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

from src.core.config import Settings


_SETTINGS_KEY_TO_ENV: dict[str, str] = {
    "debug_enabled": "DEBUG_ENABLED",
    # LLM 泛化配置（新）
    "llm_provider": "LLM_PROVIDER",
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    # 向后兼容（旧 deepseek 专用）
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "request_timeout": "REQUEST_TIMEOUT",
    "shell_timeout": "SHELL_TIMEOUT",
    "shell_auto_confirm": "SHELL_AUTO_CONFIRM",
    "shell_high_risk_block": "SHELL_HIGH_RISK_BLOCK",
    "shell_allowed_patterns": "SHELL_ALLOWED_PATTERNS",
    "shell_blocked_patterns": "SHELL_BLOCKED_PATTERNS",
    "db_path": "DB_PATH",
    "max_turns": "MAX_TURNS",
    "max_message_chars": "MAX_MESSAGE_CHARS",
    "compression_model": "COMPRESSION_MODEL",
}

_BOOL_KEYS: frozenset[str] = frozenset({"shell_auto_confirm", "shell_high_risk_block", "debug_enabled"})
_INT_KEYS: frozenset[str] = frozenset({"max_turns", "max_message_chars", "shell_timeout"})
_FLOAT_KEYS: frozenset[str] = frozenset({"request_timeout"})
_LIST_KEYS: frozenset[str] = frozenset({"shell_allowed_patterns", "shell_blocked_patterns"})

_DATACLASS_DEFAULTS: dict[str, Any] = {
    f.name: f.default
    for f in fields(Settings)
    if f.default is not MISSING
}
_EXTRA_DEFAULTS: dict[str, Any] = {
    # LLM 泛化配置
    "llm_api_key": "",
    "llm_base_url": "",
    "llm_model": "",
    "llm_provider": "deepseek",
    # 向后兼容
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "debug_enabled": False,
}


def _get_default(key: str) -> Any:
    if key in _EXTRA_DEFAULTS:
        return _EXTRA_DEFAULTS[key]
    return _DATACLASS_DEFAULTS.get(key)


class SettingsService:
    """设置读写：从 .env 文件和环境变量读取/写入 LLM 凭据与行为参数。"""

    def __init__(self, root_dir: Path) -> None:
        self._env_path = root_dir / ".env"

    def get_settings(self) -> dict[str, Any]:
        file_values = self._read_env_file()
        merged: dict[str, str] = dict(file_values)
        for env_name in _SETTINGS_KEY_TO_ENV.values():
            if env_name in os.environ:
                merged[env_name] = os.environ[env_name]
        result: dict[str, Any] = {}
        for key, env_name in _SETTINGS_KEY_TO_ENV.items():
            raw = merged.get(env_name, "")
            if key in _BOOL_KEYS:
                result[key] = raw.strip().lower() == "true" if raw.strip() else _get_default(key)
            elif key in _INT_KEYS:
                default = _get_default(key)
                result[key] = int(raw) if raw.strip() else default
            elif key in _FLOAT_KEYS:
                default = _get_default(key)
                result[key] = float(raw) if raw.strip() else default
            elif key in _LIST_KEYS:
                result[key] = [s.strip() for s in raw.split(",") if s.strip()]
            else:
                result[key] = raw if raw else _get_default(key)
        return result

    def get_settings_defaults(self) -> dict[str, Any]:
        """返回所有设置的默认值。"""
        result: dict[str, Any] = {}
        for key in _SETTINGS_KEY_TO_ENV:
            result[key] = _get_default(key)
        return result

    def save_settings(self, settings: dict[str, Any]) -> None:
        existing = self._read_env_file()
        for key, value in settings.items():
            env_name = _SETTINGS_KEY_TO_ENV.get(key, key.upper())
            if isinstance(value, bool):
                existing[env_name] = "true" if value else "false"
            elif isinstance(value, list):
                existing[env_name] = ",".join(str(v) for v in value)
            else:
                existing[env_name] = str(value)
            os.environ[env_name] = existing[env_name]
        self._write_env_file(existing)

    def _read_env_file(self) -> dict[str, str]:
        if not self._env_path.exists():
            return {}
        result: dict[str, str] = {}
        for line in self._env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            result[k.strip()] = v.strip()
        return result

    def _write_env_file(self, data: dict[str, str]) -> None:
        lines = [f"{k}={v}" for k, v in sorted(data.items())]
        self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

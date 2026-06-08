"""LLM 工厂 — 根据 provider 选择对应的 LangChain 模型实现。

支持：
- ``deepseek`` / ``openai`` → ``langchain_openai.ChatOpenAI``
- ``anthropic`` → ``langchain_anthropic.ChatAnthropic``

利用 LangChain 框架的 ``BaseChatModel`` 多态能力，下游代码无需关注具体实现。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from src.core.config import Settings


def build_llm(settings: Settings | None = None, *, temperature: float = 0.0) -> BaseChatModel:
    """根据 settings.llm_provider 构造对应的 LLM 实例。"""
    cfg = settings or Settings.from_env()
    provider = cfg.llm_provider

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=cfg.llm_model or "claude-sonnet-4-20250514",
            api_key=cfg.llm_api_key,
            temperature=temperature,
            timeout=cfg.request_timeout,
            max_retries=2,
            streaming=True,
        )

    # OpenAI 兼容分支（deepseek / openai / 其他）
    if provider == "openai":
        default_base_url = "https://api.openai.com/v1"
        default_model = "gpt-4o"
    else:  # deepseek 及其他
        default_base_url = "https://api.deepseek.com"
        default_model = "deepseek-chat"

    return ChatOpenAI(
        model=cfg.llm_model or default_model,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url or default_base_url,
        temperature=temperature,
        timeout=cfg.request_timeout,
        max_retries=2,
        streaming=True,
    )

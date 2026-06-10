"""LLM 工厂 — 根据 provider 选择对应的 LangChain 模型实现。

支持：
- ``deepseek`` / ``openai`` → ``langchain_openai.ChatOpenAI``
- ``anthropic`` → ``langchain_anthropic.ChatAnthropic``

利用 LangChain 框架的 ``BaseChatModel`` 多态能力，下游代码无需关注具体实现。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from src.core.config import Settings


class CacheAwareChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类，在 streaming 模式下保留原始 ``token_usage`` 到 ``response_metadata``。

    LangChain 默认的 ``_convert_chunk_to_generation_chunk`` 只将 raw usage 转换
    为 ``UsageMetadata`` 后设到 ``usage_metadata``，但不会把原始 ``token_usage``
    写入 ``response_metadata``。这导致 DeepSeek 特有的 ``prompt_cache_hit_tokens`` /
    ``prompt_cache_miss_tokens`` 字段在 streaming 中丢失。

    本子类仅做一件事：在有 ``token_usage`` 时将其原样保留到
    ``message_chunk.response_metadata["token_usage"]``，供下游 tracing 使用。
    """

    # 重写在 langchain_openai 中定义的缓存字段名常量（类属性）
    # 这些常量在模块层定义，通过实例属性覆盖不生效；改用 _convert_chunk_to_generation_chunk 注入
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        """与父类逻辑一致，但在有 token_usage 时将其也写入 response_metadata。"""
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None:
            return None
        token_usage = chunk.get("usage")
        if token_usage and isinstance(result.message, AIMessageChunk):
            result.message.response_metadata["token_usage"] = token_usage
        return result


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
            model_kwargs={"parallel_tool_calls": True},
            default_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )

    # OpenAI 兼容分支（deepseek / openai / 其他）
    if provider == "openai":
        default_base_url = "https://api.openai.com/v1"
        default_model = "gpt-4o"
    else:  # deepseek 及其他
        default_base_url = "https://api.deepseek.com"
        default_model = "deepseek-chat"

    return CacheAwareChatOpenAI(
        model=cfg.llm_model or default_model,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url or default_base_url,
        temperature=temperature,
        timeout=cfg.request_timeout,
        max_retries=2,
        streaming=True,
        model_kwargs={"parallel_tool_calls": True},
    )

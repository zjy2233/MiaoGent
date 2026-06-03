"""组装 agent：注册工具、设置 system_prompt、绑定 LLM。

LangChain 1.x 推荐用 :func:`langchain.agents.create_agent` 构建 agent，
它返回一个 LangGraph ``CompiledStateGraph``，可直接 ``invoke({"messages": [...]})``。
底层默认走 ReAct 风格的 tool-calling loop。

本模块还做两件事：
1. 把"历史摘要"做成 state 的独立字段 ``summary``，不与系统提示词混在一起
2. 通过 :class:`SummaryMiddleware` 在每次 LLM 调用前把 ``summary`` 注入为消息列表头部的
   独立 ``SystemMessage``——LLM 能感知到，但和主系统提示词是两条独立的内容
"""

from __future__ import annotations

from collections import namedtuple
from typing import Annotated, Required

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from src.tools import calculator, current_time, weather, web_search, shell
from src.soul import SoulManager, ProfileManager

SYSTEM_PROMPT = """你是一个有用的中文助手。可以调用工具来回答问题。

行为准则：
1. 拿到用户问题后先想清楚需不需要工具、需要哪个。
2. **所有数学计算必须调用 calculator 工具，不要心算。**
3. 如果用户问题模糊（例如"那个城市"），先追问再调用工具。
4. 工具返回错误时，**基于错误信息调整输入再试**，或明确告诉用户失败原因。
5. 给出最终答案时用简洁的中文。
6. 用户问"现在几点 / 当前时间"时，**必须调用 current_time 工具**，不要凭印象回答。"""


# Return type for build_agent
AgentBundle = namedtuple("AgentBundle", ["agent", "profile_middleware"])


def _get_soul_manager() -> "SoulManager":
    return SoulManager()


def _get_profile_manager() -> "ProfileManager":
    return ProfileManager()


class AgentState(TypedDict):
    """扩展默认 AgentState：增加 ``summary`` 字段记录历史摘要。"""

    messages: Required[Annotated[list, add_messages]]
    summary: NotRequired[str]  # 历史摘要；空字符串/缺省 = 还没有压缩过


class SummaryMiddleware(AgentMiddleware):
    """在 LLM 调用前把 state.summary 注入为消息列表头部的 SystemMessage。

    注入位置是"主 system prompt 之后、第一条 human 之前"。
    主 system prompt 由 :func:`create_agent` 内部管理，我们只追加。
    这样 LLM 看到的是 ``[主提示词, 历史摘要, ...recent]``，
    两个内容块独立、互不污染。
    """

    async def awrap_model_call(self, request, handler):
        summary = request.state.get("summary", "") or ""
        if summary:
            summary_msg = SystemMessage(content=f"[对话历史摘要]\n{summary}")
            request = request.override(
                messages=[summary_msg, *request.messages]
            )
        return await handler(request)


class ProfileMiddleware(AgentMiddleware):
    """在 LLM 调用前把用户画像注入为消息列表头部的 SystemMessage。

    注入位置是"主 system prompt 之后、第一条 human 之前"。
    只会注入有实际数据的画像（排除 version 和 _source 结尾的字段）。

    支持运行时更新画像：调用 ``update_profile()`` 会重新加载并更新内存。
    """

    def __init__(self, profile: dict):
        super().__init__()
        self.profile = profile or {}
        self._profile_manager = ProfileManager()

    async def awrap_model_call(self, request, handler):
        # Build profile content, excluding 'version' and fields ending with '_source'
        profile_lines = []
        for key, value in self.profile.items():
            if key == "version" or key.endswith("_source"):
                continue
            profile_lines.append(f"{key}: {value}")

        if not profile_lines:
            # No actual data to inject
            return await handler(request)

        profile_text = "[用户画像]\n" + "\n".join(profile_lines)
        profile_msg = SystemMessage(content=profile_text)
        request = request.override(
            messages=[profile_msg, *request.messages]
        )
        return await handler(request)

    def update_profile(self, new_facts: dict | None = None) -> None:
        """Update profile from file, optionally merging new facts first.

        Args:
            new_facts: Optional dict of facts to merge before reloading.
        """
        if new_facts:
            self._profile_manager.merge(new_facts)
        self.profile = self._profile_manager.load()


def build_agent(
    llm: BaseChatModel,
    *,
    checkpointer: MemorySaver | None = None,
    profile: dict | None = None,
) -> AgentBundle:
    """构造一个配置好工具的 agent graph。

    返回 AgentBundle(agent, profile_middleware)，调用方式：
    ``bundle.agent.invoke({"messages": [{"role": "user", "content": "..."}]})``

    当传入 ``checkpointer`` 时，agent 会把每轮状态持久化到该 checkpointer，
    调用方需在 ``config`` 里传 ``{"configurable": {"thread_id": <id>}}`` 来区分会话。
    不传 checkpointer 则保持"无状态"行为，向后兼容。

    Args:
        llm: Language model instance.
        checkpointer: Optional memory checkpointer for persistence.
        profile: Optional profile dict. If not provided, loads from ProfileManager.
    """
    # Load soul and prepend description to system prompt if non-empty
    soul = _get_soul_manager().load()
    soul_description = soul.get("description", "")
    if soul_description:
        system_prompt = f"你是一个{soul_description}的助手。\n\n{SYSTEM_PROMPT}"
    else:
        system_prompt = SYSTEM_PROMPT

    # Load profile if not provided
    if profile is None:
        profile = _get_profile_manager().load()

    profile_middleware = ProfileMiddleware(profile=profile)

    agent = create_agent(
        model=llm,
        tools=[calculator, current_time, weather, web_search, shell],
        system_prompt=system_prompt,
        state_schema=AgentState,
        middleware=[SummaryMiddleware(), profile_middleware],
        name="single-agent",
        checkpointer=checkpointer,
    )

    return AgentBundle(agent=agent, profile_middleware=profile_middleware)

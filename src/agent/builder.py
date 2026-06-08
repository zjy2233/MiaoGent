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
from datetime import datetime
from typing import Annotated, Required

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from src.tools import (
    calculator, current_time, weather, search, web_fetch,
    list_files, read_file, grep_search, create_folder, write_file, run_python,
    shell,
    install_skill, uninstall_skill, list_registry,
)
from src.store.soul import SoulManager, ProfileManager
from src.store.memory_store import MemoryStore
from src.tools.delegate_task import build_delegate_task
from src.tools.list_skills import build_list_skills_tool

# Skill 系统（可选加载）
try:
    from src.skills.registry import SkillRegistry
    from src.skills.middleware import SkillContextMiddleware
    _SKILL_AVAILABLE = True
except ImportError:
    SkillRegistry = None  # type: ignore[assignment,misc]
    SkillContextMiddleware = None  # type: ignore[assignment,misc]
    _SKILL_AVAILABLE = False

from langchain_core.tools import tool as _skill_tool

SYSTEM_PROMPT = """你是一个有用的中文助手。可以调用工具来回答问题。

行为准则：
1. 拿到用户问题后先想清楚需不需要工具、需要哪个。
2. 如果用户问题模糊（例如"那个城市"），先追问再调用工具。
3. 工具返回错误时，基于错误信息调整输入再试一次；如果同一个工具连续失败2次，立即停止重试，改用其他工具或直接告诉用户失败原因。
4. shell 工具返回错误（超时、命令不存在、非零退出码）时，不要重试，直接告诉用户错误原因。
5. 给出最终答案时用简洁的中文。

临时文件规范：
5. 需要创建临时脚本（.py/.sh 等）时，必须写到 `data/temp/` 目录下，不得写在项目根目录或其他地方。
6. 临时文件用完后必须立即删除清理（使用 shell 的 rm/del 命令），不要残留。
7. 简单的 Python 代码优先用 run_python 工具（内联执行，不写文件），只有较长（超过 20 行）或需要分步调试的脚本才写成文件。
{tool_guide}"""


def _build_tool_guide(tools: list) -> str:
    """从各工具模块自动收集 _TOOL_GUIDE 生成工具使用指南。

    通过 ``src.tools._TOOL_GUIDE_MODULES`` 将工具名映射到其定义模块，
    然后读取模块的 ``_TOOL_GUIDE`` 字符串常量。
    新增工具只需在模块中定义该常量并更新映射，无需修改本函数。
    """
    # 延迟导入避免循环依赖
    from src.tools import _TOOL_GUIDE_MODULES

    module_map: dict[str, dict] = {}

    for t in tools:
        name = t.name if hasattr(t, "name") else getattr(t, "__name__", "")
        if not name:
            continue

        mod_name = _TOOL_GUIDE_MODULES.get(name)
        if not mod_name:
            continue

        if mod_name not in module_map:
            try:
                mod = __import__(mod_name, fromlist=["_TOOL_GUIDE"])
            except ImportError:
                continue
            guide = getattr(mod, "_TOOL_GUIDE", "")
            module_map[mod_name] = {"names": [], "guide": guide}

        module_map[mod_name]["names"].append(name)

    if not module_map:
        return ""

    lines = ["\n## 工具使用指南"]
    for entry in module_map.values():
        if not entry["guide"]:
            continue
        name_str = " / ".join(entry["names"])
        lines.append(f"- **{name_str}**：{entry['guide']}")

    return "\n".join(lines)


# Return type for build_agent
AgentBundle = namedtuple("AgentBundle", [
    "agent", "profile_middleware", "memory_middleware", "memory_store",
    "skill_middleware", "skill_registry", "tools",
])
AgentBundle.__new__.__defaults__ = (None, None, None)  # skill_middleware, skill_registry, tools

# Return type for build_supervisor_agent
SupervisorBundle = namedtuple("SupervisorBundle", [
    "agent", "profile_middleware", "memory_middleware", "memory_store",
    "skill_middleware", "skill_registry", "tools",
])
SupervisorBundle.__new__.__defaults__ = (None, None, None)


def _get_soul_manager() -> "SoulManager":
    return SoulManager()


def _get_profile_manager() -> "ProfileManager":
    return ProfileManager()


class AgentState(TypedDict):
    """扩展默认 AgentState：增加 ``summary`` 字段记录历史摘要。"""

    messages: Required[Annotated[list, add_messages]]
    summary: NotRequired[str]  # 历史摘要；空字符串/缺省 = 还没有压缩过


class SummaryMiddleware(AgentMiddleware):
    """在 LLM 调用前把 state.summary 注入为消息列表头部的 SystemMessage。"""

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

    如果传入了 ``profile_manager``，每次 LLM 调用前从磁盘重新加载，
    确保用户通过设置面板手动修改的 ``profile.json`` 实时生效；
    否则使用构造函数传入的快照（测试场景）。
    """

    def __init__(self, profile: dict, profile_manager: ProfileManager | None = None):
        super().__init__()
        self._profile_manager = profile_manager
        self._init_profile = profile or {}
        self.profile = profile or {}

    async def awrap_model_call(self, request, handler):
        if self._profile_manager:
            self.profile = self._profile_manager.load()
        profile_lines = []
        for key, value in self.profile.items():
            if key == "version" or key.endswith("_source"):
                continue
            profile_lines.append(f"{key}: {value}")

        if not profile_lines:
            return await handler(request)

        profile_text = "[用户画像]\n" + "\n".join(profile_lines)
        profile_msg = SystemMessage(content=profile_text)
        request = request.override(
            messages=[profile_msg, *request.messages]
        )
        return await handler(request)

    def update_profile(self, new_facts: dict | None = None) -> None:
        if not self._profile_manager:
            if new_facts:
                self.profile.update(new_facts)
            return
        if new_facts:
            self._profile_manager.merge(new_facts)
        self.profile = self._profile_manager.load()


class MemoryMiddleware(AgentMiddleware):
    """在 LLM 调用前把用户画像 + 结构化记忆合并注入为一条 SystemMessage。

    画像（手工设定）标记为【用户设定】，记忆（自动提取）标记为【自动学习】，
    合并为 ``[关于用户]`` 一个块，避免两条 SystemMessage 内容重复。

    使用缓存避免同一轮 ReAct 循环中重复读取磁盘：
    - ``_cached_text`` 在首次构建后缓存
    - ``_cache_version`` 递增后触发刷新
    - MemoryManager 在 ``compress_if_needed`` 完成后调用 ``invalidate_cache()``
    """

    def __init__(self, store: MemoryStore, profile_manager: ProfileManager | None = None):
        super().__init__()
        self.store = store
        self._profile_manager = profile_manager
        self._cached_text: str | None = None
        self._cache_version: int = 0
        self._last_build_version: int = -1

    def invalidate_cache(self) -> None:
        self._cache_version += 1

    def _build_combined_text(self) -> str:
        """合并画像 + 记忆为一段文本。"""
        parts: list[str] = []

        # 1. 用户画像（手工设定）
        if self._profile_manager:
            profile = self._profile_manager.load()
            profile_lines: list[str] = []
            for key, value in profile.items():
                if key == "version" or key.endswith("_source"):
                    continue
                profile_lines.append(f"{key}: {value}")
            if profile_lines:
                parts.append("【用户设定】\n" + "\n".join(profile_lines))

        # 2. 结构化记忆（自动提取）
        memory_text = self.store.get_all_formatted()
        if memory_text:
            parts.append("【自动学习】\n" + memory_text)

        return "\n\n".join(parts)

    async def awrap_model_call(self, request, handler):
        if self._last_build_version < self._cache_version or self._cached_text is None:
            self._cached_text = self._build_combined_text()
            self._last_build_version = self._cache_version

        combined = self._cached_text
        if not combined:
            return await handler(request)
        memory_msg = SystemMessage(content=f"[关于用户]\n{combined}")
        request = request.override(
            messages=[memory_msg, *request.messages]
        )
        return await handler(request)


class TimeMiddleware(AgentMiddleware):
    """在每次 LLM 调用前注入当前日期时间。"""

    async def awrap_model_call(self, request, handler):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_msg = SystemMessage(content=f"[当前时间]\n{now}")
        request = request.override(
            messages=[time_msg, *request.messages]
        )
        return await handler(request)


def build_agent(
    llm: BaseChatModel,
    *,
    checkpointer: MemorySaver | None = None,
    profile: dict | None = None,
    memory_store: MemoryStore | None = None,
    session_id: str | None = None,
    skill_registry: Any | None = None,
) -> AgentBundle:
    """构造一个配置好工具的 agent graph。

    Args:
        llm: LLM 实例。
        checkpointer: 持久化 checkpointer。
        profile: 用户画像字典，None 则自动从磁盘加载。
        memory_store: 外部 MemoryStore 实例，None 则自动创建。
        session_id: 会话 ID。提供后 Skill 系统将被激活。
        skill_registry: SkillRegistry 实例。不传但传入 session_id 时自动创建。

    Returns:
        AgentBundle(agent, profile_middleware, memory_middleware, memory_store, skill_middleware, skill_registry)

    .. versionchanged:: 1.0
       新增 ``session_id`` 和 ``skill_registry`` 参数。
    """
    soul = _get_soul_manager().load()
    soul_description = soul.get("description", "")

    if profile is None:
        profile = _get_profile_manager().load()

    profile_middleware = ProfileMiddleware(profile=profile, profile_manager=_get_profile_manager())
    if memory_store is None:
        memory_store = MemoryStore()
    memory_middleware = MemoryMiddleware(store=memory_store, profile_manager=_get_profile_manager())

    # ── Skill 系统初始化（load_skill 工具） ──
    skill_middleware = None
    resolved_registry = None

    if _SKILL_AVAILABLE:
        resolved_registry = skill_registry or SkillRegistry()
        resolved_registry.discover()

        # load_skill 工具（闭包捕获 resolved_registry）
        @_skill_tool
        def load_skill(skill_name: str) -> str:
            """激活指定的 Skill，使其指令在后续对话中可用。先用 list_skills 查看所有 Skill。"""
            skill = resolved_registry.get(skill_name)
            if not skill:
                available = ", ".join(resolved_registry.names())
                return f"Skill '{skill_name}' 不存在。可用: {available}"
            return f"✅ 已激活 Skill '{skill_name}'——{skill.description}"
    else:
        @_skill_tool
        def load_skill(skill_name: str) -> str:  # type: ignore[misc]
            return "Skill 系统未启用"

    delegate_tool = build_delegate_task(
        llm,
        session_id=session_id,
        skill_registry=resolved_registry,
    )

    # ── 工具列表 ──
    tools = [
        calculator, current_time, weather, search, web_fetch,
        list_files, read_file, grep_search, create_folder, write_file, run_python,
        shell, delegate_tool,
        install_skill, uninstall_skill, list_registry,
    ]

    # list_skills 无条件加入
    tools.append(
        build_list_skills_tool(
            skill_registry=resolved_registry,
        )
    )

    # load_skill 工具
    tools.append(load_skill)

    # ── 系统提示词（基础规则 + 各工具自声明的使用指南） ──
    tool_guide = _build_tool_guide(tools)
    system_prompt = SYSTEM_PROMPT.format(tool_guide=tool_guide)
    if soul_description:
        system_prompt = f"你是一个{soul_description}的助手。\n\n{system_prompt}"

    # ── 中间件列表 ──
    middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)

    # ── 创建 Agent ──
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        state_schema=AgentState,
        middleware=middleware,
        name="single-agent",
        checkpointer=checkpointer,
    )

    return AgentBundle(
        agent=agent,
        profile_middleware=profile_middleware,
        memory_middleware=memory_middleware,
        memory_store=memory_store,
        skill_middleware=skill_middleware,
        skill_registry=resolved_registry,
        tools=tools,
    )


def build_supervisor_agent(
    llm: BaseChatModel,
    *,
    checkpointer: MemorySaver | None = None,
    profile: dict | None = None,
    memory_store: MemoryStore | None = None,
    session_id: str | None = None,
    skill_registry: Any | None = None,
) -> SupervisorBundle:
    """构造一个带 sub-agent 委派能力的 agent。

    主 agent 拥有全部工具（包括 ``delegate_task``），遇到复杂任务时
    自主决定调用 ``delegate_task`` 工具创建隔离 sub-agent 执行子任务。

    Sub-agent 只拥有 ``REGULAR_TOOLS``（不含 ``delegate_task``），
    从根本上防止无限递归。

    Args:
        llm: LLM 实例。
        checkpointer: 持久化 checkpointer。
        profile: 用户画像字典，None 则自动从磁盘加载。
        memory_store: 外部 MemoryStore 实例，None 则自动创建。
        session_id: 会话 ID。提供后 Skill 系统将被激活。
        skill_registry: SkillRegistry 实例。不传但传入 session_id 时自动创建。

    Returns:
        SupervisorBundle(...)

    .. versionchanged:: 1.0
       新增 ``session_id`` 和 ``skill_registry`` 参数。
    """
    bundle = build_agent(
        llm,
        checkpointer=checkpointer,
        profile=profile,
        memory_store=memory_store,
        session_id=session_id,
        skill_registry=skill_registry,
    )
    return SupervisorBundle(
        agent=bundle.agent,
        profile_middleware=bundle.profile_middleware,
        memory_middleware=bundle.memory_middleware,
        memory_store=bundle.memory_store,
        skill_middleware=bundle.skill_middleware,
        skill_registry=bundle.skill_registry,
        tools=bundle.tools,
    )

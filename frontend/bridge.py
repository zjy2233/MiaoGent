"""HTTP API 桥接层：暴露 Api 类给前端 JS，通过 HTTP 端点访问。

模块布局说明：
- Api 类现在是**薄门面**，实际逻辑委托给 ``frontend/services/`` 下的服务类
- ``TracingStreamHandler`` → ``src/tracing/stream_handler.py``
- ``_serialize_llm_input`` / ``_serialize_llm_output`` / ``_short_repr`` → ``src/core/serialize.py``
- ``_parse_tool_files`` / ``_is_tool_decorator`` → ``frontend/services/tool.py``
- settings 常量 → ``frontend/services/settings.py``

向后兼容导出的符号（供测试使用）：
- ``_parse_tool_files``
- ``_SETTINGS_KEY_TO_ENV``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from src.store.memory_store import MemoryStore

from frontend.services import (
    SessionService,
    SettingsService,
    SoulProfileService,
    ToolService,
    SkillService,
    ChatService,
    TracingService,
)
# 向后兼容：供 tests/test_agent_shell.py 导入
from frontend.services.settings import _SETTINGS_KEY_TO_ENV
from frontend.services.tool import _parse_tool_files


def _project_root() -> Path:
    """推断项目根目录：``frontend/bridge.py`` 的父级的父级。"""
    return Path(__file__).resolve().parent.parent


class Api:
    """HTTP API 桥接层：薄门面，委托给对应的 Service 类。

    可传入 ``agent`` / ``settings`` 等依赖来启用聊天功能；
    缺少时聊天相关方法不可用（返回错误提示）。

    所有 public 方法签名保持不变，``http_server.py`` 无需修改。
    """

    def __init__(
        self,
        root_dir: Path | str | None = None,
        agent: Any = None,
        memory_manager: Any = None,
        settings: Any = None,
        memory_store: MemoryStore | None = None,
        tools: list[Any] | None = None,
        tracing_api: Any = None,
    ) -> None:
        self.root_dir = Path(root_dir) if root_dir else _project_root()

        # 测试隔离：root_dir 非项目根时，数据文件写入 root_dir/data/ 而非 ~/.miaogent/
        _is_test = self.root_dir.resolve() != _project_root().resolve()
        if _is_test:
            _data_home = self.root_dir / "data"
            _data_home.mkdir(parents=True, exist_ok=True)
            self._sessions_path = _data_home / ".sessions.json"
            self._soul_path = _data_home / "soul.json"
            self._profile_path = _data_home / "profile.json"
        else:
            from src.core.miaogent_home import get_data_path
            self._sessions_path = get_data_path(".sessions.json")
            self._soul_path = get_data_path("soul.json")
            self._profile_path = get_data_path("profile.json")
        self._tools_dir = self.root_dir / "src" / "tools"

        # 保持引用以供外部访问
        self._agent = agent
        self._tracing_api = tracing_api
        self._memory_manager = memory_manager
        self._tools = tools or []
        self._memory_store = memory_store or MemoryStore()

        # ── 初始化服务层 ──
        self.sessions = SessionService(self._sessions_path, agent=agent)
        self.settings_svc = SettingsService(self.root_dir)
        self.soul_profile = SoulProfileService(self._soul_path, self._profile_path)
        self.tool_service = ToolService(self._tools_dir)
        self.skill_service = SkillService()

        settings_getter = lambda: self.settings_svc.get_settings() if self.settings_svc else {}
        self.chat_service = ChatService(
            agent=agent,
            memory_manager=memory_manager,
            memory_store=self._memory_store,
            tools=self._tools,
            sessions_path=self._sessions_path,
            tracing_api=tracing_api,
            session_service=self.sessions,
            settings_getter=settings_getter,
        )
        self.tracing_service = TracingService(tracing_api)

        self._active_thread_id: str = ""

    # ── 会话管理 ──

    async def get_sessions(self) -> list[dict]:
        return await self.sessions.get_sessions()

    def delete_session(self, thread_id: str) -> bool:
        return self.sessions.delete_session(thread_id)

    def delete_sessions_batch(self, thread_ids: list[str]) -> dict:
        return self.sessions.delete_sessions_batch(thread_ids)

    async def create_session(self) -> dict[str, str]:
        return await self.sessions.create_session()

    async def get_messages(
        self, thread_id: str, *,
        include_tool_calls: bool = True,
        limit: int = 50,
        before_id: str | None = None,
    ) -> dict:
        return await self.sessions.get_messages(
            thread_id, include_tool_calls=include_tool_calls, limit=limit, before_id=before_id,
        )

    # ── 设置读写 ──

    def get_settings(self) -> dict[str, Any]:
        return self.settings_svc.get_settings()

    def get_settings_defaults(self) -> dict[str, Any]:
        return self.settings_svc.get_settings_defaults()

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.settings_svc.save_settings(settings)

    # ── Soul / Profile ──

    def get_soul(self) -> dict:
        return self.soul_profile.get_soul()

    def save_soul(self, soul: dict) -> None:
        self.soul_profile.save_soul(soul)

    def get_profile(self) -> dict:
        return self.soul_profile.get_profile()

    def save_profile(self, profile: dict) -> None:
        self.soul_profile.save_profile(profile)

    # ── 工具枚举 ──

    def get_tools(self) -> list[dict[str, str]]:
        return self.tool_service.get_tools()

    # ── Skill 查询 ──

    def get_skills(self) -> list[dict]:
        return self.skill_service.get_skills()

    def get_skill_detail(self, skill_name: str) -> dict | None:
        return self.skill_service.get_skill_detail(skill_name)

    # ── 聊天功能 ──

    async def edit_message(self, thread_id: str, message_id: str, new_content: str) -> dict:
        self._active_thread_id = thread_id
        return await self.chat_service.edit_message(thread_id, message_id, new_content)

    async def compress_session(self, thread_id: str) -> dict:
        return await self.chat_service.compress_session(thread_id)

    async def trigger_consolidation(self) -> dict:
        return await self.chat_service.trigger_consolidation()

    async def close(self) -> dict:
        return await self.chat_service.close()

    async def chat(self, thread_id: str, message: str) -> dict:
        self._active_thread_id = thread_id
        return await self.chat_service.chat(thread_id, message)

    async def resume_chat(self, thread_id: str, approved: bool) -> dict:
        self._active_thread_id = thread_id
        return await self.chat_service.resume_chat(thread_id, approved)

    async def checkpoint_session(self, thread_id: str) -> None:
        await self.chat_service.checkpoint_session(thread_id)

    async def chat_stream(self, thread_id: str, message: str, resume: bool | None = None):
        self._active_thread_id = thread_id
        async for event in self.chat_service.chat_stream(thread_id, message, resume=resume):
            yield event

    # ── Tracing API ──

    def get_traces(self, q: str = "", status: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        return self.tracing_service.get_traces(q=q, status=status, limit=limit, offset=offset)

    def get_trace_detail(self, trace_id: str) -> dict:
        return self.tracing_service.get_trace_detail(trace_id)

    def get_trace_spans(self, trace_id: str) -> list[dict]:
        return self.tracing_service.get_trace_spans(trace_id)

    def get_trace_stats(self) -> dict:
        return self.tracing_service.get_trace_stats()

    def get_trace_cache_stats(self) -> dict:
        return self.tracing_service.get_trace_cache_stats()

    def get_trace_daily_stats(self) -> list[dict]:
        return self.tracing_service.get_trace_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict]:
        return self.tracing_service.get_traces_by_session(session_id)

    def get_trace_count(self, q: str = "", status: str = "") -> int:
        return self.tracing_service.get_trace_count(q=q, status=status)

    def get_token_top_traces(self, days: int = 3, limit: int = 10) -> list[dict]:
        return self.tracing_service.get_token_top_traces(days=days, limit=limit)

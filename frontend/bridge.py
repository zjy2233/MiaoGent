"""HTTP API 桥接层：暴露 Api 类给前端 JS，通过 HTTP 端点访问。

模块布局说明：
- 业务方法（``get_sessions`` / ``save_settings`` 等）都是无状态、无副作用 I/O 包装，
  不缓存磁盘数据，每次调用都重新读取最新值，方便前端实时刷新。
- ``get_tools`` 用 :mod:`ast` 解析 ``src/tools/*.py`` 的源代码，识别 ``@tool``
  装饰器，提取 ``name`` 和 docstring；不依赖 ``import`` 工具模块（避免副作用）。
- 路径均基于 ``root_dir``（默认项目根），便于测试用 ``tmp_path`` 注入。
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.store.sessions import SessionRegistry
from src.store.soul import ProfileManager, SoulManager
from src.store.memory_store import MemoryStore


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


def _project_root() -> Path:
    """推断项目根目录：``frontend/bridge.py`` 的父级的父级。"""
    return Path(__file__).resolve().parent.parent


def _is_tool_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Name) and decorator.id == "tool":
        return True
    if isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
        return True
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Name) and func.id == "tool":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            return True
    return False


def _parse_tool_files(tools_dir: Path) -> list[dict[str, str]]:
    if not tools_dir.is_dir():
        return []
    results: list[dict[str, str]] = []
    for py_file in sorted(tools_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_tool_decorator(d) for d in node.decorator_list):
                continue
            doc = ast.get_docstring(node) or ""
            results.append({"name": node.name, "description": doc.strip(), "file": str(py_file)})
    return results


class Api:
    """HTTP API 桥接层：提供会话管理、设置读写、Soul/Profile、工具枚举、聊天等功能。

    可传入 ``agent`` / ``settings`` 等依赖来启用聊天功能；
    缺少时聊天相关方法不可用（返回错误提示）。
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
        self._env_path = self.root_dir / ".env"
        self._sessions_path = self.root_dir / "data" / ".sessions.json"
        self._soul_path = self.root_dir / "data" / "soul.json"
        self._profile_path = self.root_dir / "data" / "profile.json"
        self._tools_dir = self.root_dir / "src" / "tools"
        self._agent = agent
        self._memory_manager = memory_manager
        self._settings = settings
        self._memory_store = memory_store or MemoryStore()
        self._skill_registry = None
        self._tools = tools or []  # 工具列表，用于 debug 上下文显示
        self._tracing_api = tracing_api

    # ── 会话管理 ──

    def get_sessions(self) -> list[dict]:
        return SessionRegistry(self._sessions_path).list()

    def delete_session(self, thread_id: str) -> bool:
        return SessionRegistry(self._sessions_path).remove(thread_id)

    # ── 设置读写 ──

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

    # ── Soul / Profile ──

    def get_soul(self) -> dict:
        return SoulManager(self._soul_path).load()

    def save_soul(self, soul: dict) -> None:
        SoulManager(self._soul_path).save(soul)

    def get_profile(self) -> dict:
        return ProfileManager(self._profile_path).load()

    def save_profile(self, profile: dict) -> None:
        ProfileManager(self._profile_path).save(profile)

    # ── 工具枚举 ──

    def get_tools(self) -> list[dict[str, str]]:
        return _parse_tool_files(self._tools_dir)

    # ── Skill 查询（只读，enable/disable 已移除，按需加载由 agent 通过 load_skill 工具完成） ──

    def _lazy_skill_registry(self):
        if self._skill_registry is None:
            from src.skills.registry import SkillRegistry
            reg = SkillRegistry()
            reg.discover()
            self._skill_registry = reg
        return self._skill_registry

    def get_skills(self) -> list[dict]:
        """返回所有可用 Skill 的元信息。"""
        reg = self._lazy_skill_registry()
        return [s.to_dict() for s in reg.list_all()]

    def get_skill_detail(self, skill_name: str) -> dict | None:
        """返回单个 Skill 的详细信息。"""
        reg = self._lazy_skill_registry()
        skill = reg.get(skill_name)
        if skill is None:
            return None
        return skill.to_dict()

    # ── 记忆管理 ──

    async def compress_session(self, thread_id: str) -> dict:
        """触发指定会话的记忆压缩与提取（退出时调用），强制提取画像和记忆。"""
        if self._memory_manager is None:
            return {"compressed": False, "reason": "MemoryManager 未就绪"}
        try:
            ok = await self._memory_manager.compress_if_needed(thread_id, force=True)
            return {"compressed": ok}
        except Exception as exc:
            return {"compressed": False, "reason": str(exc)}

    # ── 聊天功能（HTTP 服务器模式） ──

    async def create_session(self) -> dict[str, str]:
        registry = SessionRegistry(self._sessions_path)
        tid = registry.new_thread_id()
        registry.add(tid)
        return {"thread_id": tid}

    async def get_messages(self, thread_id: str) -> list[dict[str, str]]:
        if self._agent is None:
            return []
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self._agent.aget_state(config)
        except Exception:
            return []
        messages = list(state.values.get("messages", []) or [])
        result: list[dict[str, str]] = []
        for m in messages:
            role = "human" if m.type == "human" else "ai" if m.type == "ai" else m.type
            parts = []
            content = m.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "text":
                            parts.append(block.get("text", ""))
                        elif block_type == "tool_use":
                            name = block.get("name", "")
                            inp = block.get("input", {})
                            parts.append(f"[工具: {name}({json.dumps(inp, ensure_ascii=False)})]")
                        else:
                            parts.append(str(block.get("text", "")))
            elif content:
                parts.append(str(content))
            # 如果消息有 tool_calls 属性（非 content list 方式），也添加
            tc = getattr(m, "tool_calls", None)
            if tc:
                for call in tc:
                    tname = call.get("name", "") if isinstance(call, dict) else ""
                    targs = call.get("args", {}) if isinstance(call, dict) else {}
                    if tname:
                        parts.append(f"[工具调用: {tname}({json.dumps(targs, ensure_ascii=False)})]")
            text = "".join(parts)
            result.append({"role": role, "content": text} if text else {"role": role})
        return result

    async def chat(self, thread_id: str, message: str) -> dict:
        if self._agent is None:
            return {"response": "", "error": "Agent 未就绪（请检查 LLM 配置）"}
        from langchain_core.messages import HumanMessage
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        # 清理可能残留的孤立 tool_calls（详见 chat_stream 的注释）
        try:
            state_snap = await self._agent.aget_state(config)
            msgs = list(state_snap.values.get("messages", []) or [])
            for i in range(len(msgs) - 1, -1, -1):
                msg = msgs[i]
                tc = getattr(msg, "tool_calls", None)
                if not tc:
                    continue
                # 检查紧跟在 AI 消息后面的是否是 ToolMessage
                next_type = ""
                if i + 1 < len(msgs):
                    next_type = getattr(msgs[i + 1], "type", "")

                following_ids: set[str] = set()
                for j in range(i + 1, len(msgs)):
                    tcid = getattr(msgs[j], "tool_call_id", None)
                    if tcid:
                        following_ids.add(tcid)
                missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
                seq_broken = next_type not in ("", "tool")

                if missing or seq_broken:
                    from langgraph.graph.message import RemoveMessage
                    await self._agent.aupdate_state(config, {
                        "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                    }, as_node="__start__")
                break
        except Exception:
            pass
        try:
            result = await self._agent.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            )
        except Exception as exc:
            return {"response": "", "error": str(exc)}
        await self._update_turn_count(thread_id)
        messages = result.get("messages", [])
        if not messages:
            return {"response": "(agent 没有返回消息)"}
        content = messages[-1].content
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        # 自动压缩与记忆提取
        if self._memory_manager:
            await self._memory_manager.compress_if_needed(thread_id)
        return {"response": str(content) if content else "(空回答)"}

    async def resume_chat(self, thread_id: str, approved: bool) -> dict:
        if self._agent is None:
            return {"response": "", "error": "Agent 未就绪"}
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        try:
            result = await self._agent.ainvoke(
                Command(resume=approved),
                config=config,
            )
        except Exception as exc:
            return {"response": "", "error": str(exc)}
        messages = result.get("messages", [])
        if not messages:
            return {"response": "(agent 没有返回消息)"}
        content = messages[-1].content
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        # 自动压缩与记忆提取
        if self._memory_manager:
            await self._memory_manager.compress_if_needed(thread_id)
        return {"response": str(content) if content else "(空回答)"}

    async def checkpoint_session(self, thread_id: str) -> None:
        if self._agent is None:
            return
        config = {"configurable": {"thread_id": thread_id}}
        try:
            await self._agent.aupdate_state(config, {"messages": []})
        except Exception:
            pass

    async def _update_turn_count(self, thread_id: str) -> None:
        """从 checkpoint 统计 human 消息轮数并更新 SessionRegistry。"""
        if self._agent is None:
            return
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self._agent.aget_state(config)
            messages = list(state.values.get("messages", []) or [])
            turns = sum(1 for m in messages if getattr(m, "type", "") == "human")
        except Exception:
            return
        registry = SessionRegistry(self._sessions_path)
        registry.update(thread_id, turn_count=turns)

    async def chat_stream(self, thread_id: str, message: str, resume: bool | None = None):
        """流式聊天 async generator。

        Args:
            thread_id: 会话 ID。
            message: 用户消息（resume 模式时传空字符串）。
            resume: None=正常发送消息, True=恢复(批准中断), False=拒绝中断。
        """
        if self._agent is None:
            yield {"event": "error", "data": {"error": "Agent 未就绪（请检查 LLM 配置）"}}
            return

        from langchain_core.messages import HumanMessage
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        if resume is not None:
            # ── Resume 模式：执行 Command(resume=approved) ──
            # ── Tracing: 直接从事件流构建 spans ──
            tracer = None
            run_id_to_span_id: dict[str, str] = {}
            if self._tracing_api is not None:
                from src.tracing.tracer import Tracer
                tracer = Tracer(session_id=thread_id, session_turn=0)
                tracer.start_span("session_turn", user_message="(resume)")
            try:
                async for event in self._agent.astream_events(
                    Command(resume=resume),
                    config=config,
                    version="v2",
                ):
                    kind = event.get("event", "")
                    run_id = event.get("run_id")
                    # ── Tracing spans ──
                    if tracer is not None:
                        if kind == "on_chat_model_start":
                            sid = tracer.start_span("llm_call", model=event.get("name", "") or "chat_model")
                            run_id_to_span_id[run_id] = sid
                        elif kind == "on_chat_model_end":
                            sid = run_id_to_span_id.pop(run_id, None)
                            if sid:
                                tracer.end_span(sid)
                        elif kind == "on_chat_model_error":
                            sid = run_id_to_span_id.pop(run_id, None)
                            if sid:
                                tracer.end_span(sid, status="error", error_message=str(event.get("data", {}).get("error", "")))
                        elif kind == "on_tool_start":
                            name = event.get("name", "?")
                            inp = event["data"].get("input", "")
                            sid = tracer.start_span("tool_call", tool_name=name, tool_input=_short_repr(inp, 500))
                            run_id_to_span_id[run_id] = sid
                        elif kind == "on_tool_end":
                            sid = run_id_to_span_id.pop(run_id, None)
                            if sid:
                                tracer.end_span(sid)
                        elif kind == "on_tool_error":
                            sid = run_id_to_span_id.pop(run_id, None)
                            if sid:
                                tracer.end_span(sid, status="error", error_message=str(event.get("data", {}).get("error", "")))
                    if kind == "on_chat_model_stream":
                        chunk = event["data"].get("chunk")
                        if chunk is None:
                            continue
                        text = self._extract_chunk_text(chunk)
                        if text:
                            yield {"event": "token", "data": {"text": text}}
                    elif kind == "on_tool_start":
                        name = event.get("name", "?")
                        inp = event["data"].get("input", {})
                        yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp)}}
                    elif kind == "on_tool_end":
                        name = event.get("name", "?")
                        out = event["data"].get("output")
                        yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out)}}
                    elif kind == "on_tool_error":
                        err = event["data"].get("error", "")
                        yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err)}}
            except Exception as exc:
                yield {"event": "error", "data": {"error": str(exc)}}
                return
            finally:
                # ── Tracing: 结束根 span 并写入 store ──
                if tracer is not None and self._tracing_api is not None:
                    for sid in list(tracer._span_stack):
                        tracer.end_span(sid)
                    finished = list(tracer._spans.values())
                    if finished:
                        self._tracing_api.store.write_spans(finished)
            yield {"event": "done", "data": {}}
            await self._update_turn_count(thread_id)
            if self._memory_manager:
                await self._memory_manager.compress_if_needed(thread_id)
            return

        # ── 正常模式：发送消息 ──
        # 检查是否有未处理的中断（如用户在上一次 shell 确认时发送了新消息）
        # 如果有，先静默取消中断（resume=False），清除孤立的 tool_calls 状态
        try:
            state_snap = await self._agent.aget_state(config)
            if state_snap.tasks and state_snap.tasks[0].interrupts:
                await self._agent.ainvoke(
                    Command(resume=False),
                    config=config,
                )
        except Exception:
            pass

        # 清理上次中断残留的 tool_calls：找到最后一个有 tool_calls 但缺少对应
        # ToolMessage 的 AI 消息，将其及其后所有消息一并移除。
        #
        # 不能简单地追加 ToolMessage 占位，因为中间可能已夹有来自失败请求的
        # HumanMessage（tool_calls → 失败 checkpoint 写入 HumanMessage → 触发重试），
        # 此时 ToolMessage 会追加在 HumanMessage 之后，序列仍然不合法。
        try:
            state_snap = await self._agent.aget_state(config)
            msgs = list(state_snap.values.get("messages", []) or [])
            for i in range(len(msgs) - 1, -1, -1):
                msg = msgs[i]
                tc = getattr(msg, "tool_calls", None)
                if not tc:
                    continue

                # 检查紧跟在 AI 消息后面的是否是 ToolMessage
                # 如果是 HumanMessage 或其他非 tool 消息说明序列已错乱
                next_type = ""
                if i + 1 < len(msgs):
                    next_type = getattr(msgs[i + 1], "type", "")

                # 收集该 AI 消息之后所有 ToolMessage 的 tool_call_id
                following_ids: set[str] = set()
                for j in range(i + 1, len(msgs)):
                    tcid = getattr(msgs[j], "tool_call_id", None)
                    if tcid:
                        following_ids.add(tcid)

                missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
                seq_broken = next_type not in ("", "tool")  # 空=没有下一条, tool=正常

                if missing or seq_broken:
                    from langgraph.graph.message import RemoveMessage
                    await self._agent.aupdate_state(config, {
                        "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                    }, as_node="__start__")
                break  # 只需检查最新的一个有 tool_calls 的消息
        except Exception:
            pass

        # ── Tracing: 直接从事件流构建 spans（比 callback handler 更可靠）──
        tracer = None
        run_id_to_span_id: dict[str, str] = {}
        if self._tracing_api is not None:
            from src.tracing.tracer import Tracer
            session_turn = 0
            try:
                state = await self._agent.aget_state(config)
                msgs = list(state.values.get("messages", []) or [])
                session_turn = sum(1 for m in msgs if getattr(m, "type", "") == "human")
            except Exception:
                pass
            tracer = Tracer(session_id=thread_id, session_turn=session_turn)
            tracer.start_span("session_turn", user_message=message)

        # ── Debug 上下文（在 on_chat_model_start 事件中捕获，确保包含中间件注入的消息）──
        debug_enabled = self.get_settings().get("debug_enabled", False)
        debug_emitted = False

        try:
            async for event in self._agent.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event.get("event", "")
                run_id = event.get("run_id")

                # ── Tracing spans ──
                if tracer is not None:
                    if kind == "on_chain_start" and not tracer.current_span_id:
                        # only create root span if not already set
                        tracer.start_span("session_turn", user_message=message)
                    elif kind == "on_chat_model_start":
                        name = event.get("name", "") or "chat_model"
                        sid = tracer.start_span("llm_call", model=name)
                        run_id_to_span_id[run_id] = sid
                    elif kind == "on_chat_model_end":
                        sid = run_id_to_span_id.pop(run_id, None)
                        if sid:
                            resp = event.get("data", {}).get("output", {})
                            usage = getattr(resp, "usage_metadata", None)
                            if isinstance(usage, dict):
                                span = tracer._spans.get(sid)
                                if span:
                                    span.input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                                    span.output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                            tracer.end_span(sid)
                    elif kind == "on_chat_model_error":
                        sid = run_id_to_span_id.pop(run_id, None)
                        if sid:
                            tracer.end_span(sid, status="error",
                                            error_message=str(event.get("data", {}).get("error", "")))
                    elif kind == "on_tool_start":
                        name = event.get("name", "?")
                        inp = event["data"].get("input", "")
                        sid = tracer.start_span("tool_call", tool_name=name,
                                                tool_input=_short_repr(inp, 500))
                        run_id_to_span_id[run_id] = sid
                    elif kind == "on_tool_end":
                        sid = run_id_to_span_id.pop(run_id, None)
                        if sid:
                            tracer.end_span(sid)
                    elif kind == "on_tool_error":
                        sid = run_id_to_span_id.pop(run_id, None)
                        if sid:
                            tracer.end_span(sid, status="error",
                                            error_message=str(event.get("data", {}).get("error", "")))

                # ── 从 on_chat_model_start 捕获 LLM 实际接收到的完整输入 ──
                if debug_enabled and not debug_emitted and kind == "on_chat_model_start":
                    try:
                        input_data = event.get("data", {}).get("input", {})
                        if isinstance(input_data, dict):
                            msgs = input_data.get("messages", [])
                        elif isinstance(input_data, list):
                            msgs = input_data
                        else:
                            msgs = []
                        if msgs and isinstance(msgs[0], list):
                            msgs = msgs[0]

                        debug_parts = []
                        for m in msgs:
                            msg_type = getattr(m, "type", "unknown")
                            content = getattr(m, "content", "")
                            if isinstance(content, list):
                                content = "".join(
                                    b.get("text", "") for b in content if isinstance(b, dict)
                                )
                            if content:
                                label = msg_type.upper()
                                debug_parts.append(f"【{label}】\n{content}")

                        # ── 已绑定的工具定义 ──
                        if self._tools:
                            tool_lines = []
                            for t in self._tools:
                                tname = getattr(t, "name", "?")
                                tdesc = getattr(t, "description", "")
                                targs = getattr(t, "args", {})
                                if not targs:
                                    try:
                                        schema = getattr(t, "args_schema", None)
                                        targs = schema.schema() if schema else {}
                                    except Exception:
                                        targs = {}
                                param_str = json.dumps(targs, ensure_ascii=False) if targs else "{}"
                                if len(param_str) > 300:
                                    param_str = param_str[:300] + "..."
                                tool_lines.append(f"  📦 {tname}\n     描述: {tdesc}\n     参数: {param_str}")
                            debug_parts.append(f"【已绑定的工具 ({len(self._tools)} 个)】\n" + "\n\n".join(tool_lines))

                        if debug_parts:
                            yield {"event": "context", "data": {
                                "text": "\n\n═══════════════════\n\n".join(debug_parts)
                            }}
                    except Exception as exc:
                        yield {"event": "context", "data": {
                            "text": f"(获取上下文失败: {exc})"
                        }}
                    finally:
                        debug_emitted = True

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk is None:
                        continue
                    text = self._extract_chunk_text(chunk)
                    if text:
                        yield {"event": "token", "data": {"text": text}}
                elif kind == "on_tool_start":
                    name = event.get("name", "?")
                    inp = event["data"].get("input", {})
                    yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp)}}
                elif kind == "on_tool_end":
                    name = event.get("name", "?")
                    out = event["data"].get("output")
                    yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out)}}
                elif kind == "on_tool_error":
                    err = event["data"].get("error", "")
                    yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err)}}
                elif kind == "on_chain_end":
                    pass
        except Exception as exc:
            yield {"event": "error", "data": {"error": str(exc)}}
            return
        finally:
            # ── Tracing: 结束根 span 并写入 store ──
            if tracer is not None and self._tracing_api is not None:
                for sid in list(tracer._span_stack):
                    tracer.end_span(sid)
                finished = list(tracer._spans.values())
                if finished:
                    self._tracing_api.store.write_spans(finished)

        # ── 检查是否有 interrupt（如 shell 确认）──
        try:
            state_snap = await self._agent.aget_state(config)
            pending_interrupt = None
            if state_snap.tasks and state_snap.tasks[0].interrupts:
                pending_interrupt = state_snap.tasks[0].interrupts[0].value
        except Exception:
            pending_interrupt = None

        if pending_interrupt and isinstance(pending_interrupt, dict):
            yield {
                "event": "interrupt",
                "data": {
                    "type": pending_interrupt.get("type", ""),
                    "command": pending_interrupt.get("command") or pending_interrupt.get("path", ""),
                    "reason": pending_interrupt.get("reason", ""),
                    "alternatives": pending_interrupt.get("alternatives", []),
                },
            }
            # 不 yield "done" — 等待前端通过 resume 端点继续
            return

        yield {"event": "done", "data": {}}
        await self._update_turn_count(thread_id)
        if self._memory_manager:
            await self._memory_manager.compress_if_needed(thread_id)

    @staticmethod
    def _extract_chunk_text(chunk: Any) -> str:
        if chunk is None:
            return ""
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            c = chunk.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return "".join(b.get("text", "") for b in c if isinstance(b, dict))
            return ""
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return ""


    # ── Tracing API ──

    def get_traces(self, q: str = "", status: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_trace_list(q=q, status=status, limit=limit, offset=offset)

    def get_trace_detail(self, trace_id: str) -> dict:
        if self._tracing_api is None:
            return {"error": "Tracing 未就绪"}
        return self._tracing_api.get_trace_tree(trace_id)

    def get_trace_spans(self, trace_id: str) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.store.get_trace_spans(trace_id)

    def get_trace_stats(self) -> dict:
        if self._tracing_api is None:
            return {"total_traces": 0, "total_tokens": 0, "avg_duration_ms": 0, "error_rate": 0}
        return self._tracing_api.get_stats()

    def get_trace_daily_stats(self) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_traces_by_session(session_id)


def _short_repr(x: Any, limit: int = 200) -> str:
    if isinstance(x, (dict, list)):
        s = json.dumps(x, ensure_ascii=False).replace("\n", " ").strip()
    elif isinstance(x, bytes):
        s = x.decode("utf-8", errors="replace").replace("\n", " ").strip()
    else:
        s = str(x).replace("\n", " ").strip()
    if len(s) > limit:
        s = s[:limit] + "..."
    return s

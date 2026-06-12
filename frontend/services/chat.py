"""聊天服务 — 会话消息编辑、流式聊天、记忆管理、知识归并。"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.serialize import _short_repr
from src.tracing.stream_handler import TracingStreamHandler

logger = logging.getLogger(__name__)


class ChatService:
    """聊天功能：消息编辑、发送、流式回复、记忆压缩、知识归并。

    内部创建 TracingStreamHandler 来采集流式事件中的跨度数据。
    """

    def __init__(
        self,
        agent: Any = None,
        memory_manager: Any = None,
        memory_store: Any = None,
        tools: list[Any] | None = None,
        sessions_path: Any = None,
        tracing_api: Any = None,
        session_service: Any = None,
        settings_getter: Any = None,
    ) -> None:
        self._agent = agent
        self._memory_manager = memory_manager
        self._memory_store = memory_store
        self._tools = tools or []
        self._sessions_path = sessions_path
        self._tracing_api = tracing_api
        self._session_service = session_service
        self._settings_getter = settings_getter or (lambda: {})
        self._active_thread_id: str = ""

    # ── 消息编辑 ──

    async def edit_message(self, thread_id: str, message_id: str, new_content: str) -> dict:
        """编辑已发送消息并删除后续消息。"""
        if self._agent is None:
            return {"success": False, "error": "Agent 未就绪"}
        from langgraph.graph.message import RemoveMessage
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self._agent.aget_state(config)
            messages = list(state.values.get("messages", []) or [])
            cut_idx = None
            for i, m in enumerate(messages):
                if getattr(m, "id", "") == message_id:
                    cut_idx = i
                    break
            if cut_idx is None:
                return {"success": False, "error": f"消息 {message_id} 不存在"}
            to_remove = [RemoveMessage(id=m.id) for m in messages[cut_idx:] if getattr(m, "id", None)]
            if to_remove:
                await self._agent.aupdate_state(config, {"messages": to_remove}, as_node="__start__")
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ── 记忆管理 ──

    async def compress_session(self, thread_id: str) -> dict:
        """触发指定会话的记忆压缩与提取。"""
        if self._memory_manager is None:
            return {"compressed": False, "reason": "MemoryManager 未就绪"}
        try:
            ok = await self._memory_manager.compress_if_needed(thread_id, force=True)
            return {"compressed": ok}
        except Exception as exc:
            return {"compressed": False, "reason": str(exc)}

    async def trigger_consolidation(self) -> dict:
        """触发知识归并（事件驱动，非阻塞式）。"""
        if self._memory_manager is None:
            return {"consolidated": False, "reason": "MemoryManager 未就绪"}
        try:
            llm = getattr(self._memory_manager, "compression_llm", None)
            if llm is None:
                return {"consolidated": False, "reason": "LLM 未就绪"}
            from src.store.knowledge import KnowledgeConsolidator
            consolidator = KnowledgeConsolidator(llm, self._memory_store)
            result = await consolidator.consolidate()
            return {
                "consolidated": result.get("consolidated", False),
                "count": result.get("count", 0),
                "skip_reason": result.get("skip_reason"),
            }
        except Exception as exc:
            return {"consolidated": False, "error": str(exc)}

    async def close(self) -> dict:
        """应用退出时调用：触发知识归并 + 活跃会话的 profile 发现和记忆提取。"""
        result: dict[str, Any] = {"consolidated": False, "profile_discovered": False}

        try:
            if self._memory_manager is not None:
                llm = getattr(self._memory_manager, "compression_llm", None)
                if llm is not None:
                    from src.store.knowledge import KnowledgeConsolidator
                    consolidator = KnowledgeConsolidator(llm, self._memory_store)
                    ck = await consolidator.consolidate()
                    result["consolidated"] = ck.get("consolidated", False)
                    result["consolidate_count"] = ck.get("count", 0)
        except Exception as exc:
            logger.warning("close: consolidation failed: %s", exc)

        try:
            if self._memory_manager is not None and self._active_thread_id:
                ok = await self._memory_manager.compress_if_needed(
                    self._active_thread_id, force=True
                )
                result["profile_discovered"] = ok
        except Exception as exc:
            logger.warning("close: profile discovery failed: %s", exc)

        return result

    # ── 聊天功能 ──

    async def chat(self, thread_id: str, message: str) -> dict:
        self._active_thread_id = thread_id
        if self._agent is None:
            return {"response": "", "error": "Agent 未就绪（请检查 LLM 配置）"}
        from langchain_core.messages import HumanMessage
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        if self._session_service:
            await self._session_service._cleanup_orphan_tool_calls(config)
        try:
            result = await self._agent.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            )
        except Exception as exc:
            return {"response": "", "error": str(exc)}
        if self._session_service:
            await self._session_service._update_turn_count(thread_id)
        messages = result.get("messages", [])
        if not messages:
            return {"response": "(agent 没有返回消息)"}
        content = messages[-1].content
        if isinstance(content, list):
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        if self._memory_manager:
            await self._memory_manager.compress_if_needed(thread_id)
        return {"response": str(content) if content else "(空回答)"}

    async def resume_chat(self, thread_id: str, approved: bool) -> dict:
        self._active_thread_id = thread_id
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

    async def chat_stream(self, thread_id: str, message: str, resume: bool | None = None):
        """流式聊天 async generator。

        Args:
            thread_id: 会话 ID。
            message: 用户消息（resume 模式时传空字符串）。
            resume: None=正常发送消息, True=恢复(批准中断), False=拒绝中断。
        """
        self._active_thread_id = thread_id
        if self._agent is None:
            yield {"event": "error", "data": {"error": "Agent 未就绪（请检查 LLM 配置）"}}
            return

        from langchain_core.messages import HumanMessage
        from langgraph.types import Command
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        if resume is not None:
            # ── Resume 模式：执行 Command(resume=approved) ──
            handler = None
            if self._tracing_api is not None:
                from src.tracing.tracer import Tracer
                tracer = Tracer(session_id=thread_id, session_turn=0)
                tracer.start_span("session_turn", user_message="(resume)")
                handler = TracingStreamHandler(tracer, detect_delegate=False)
            try:
                async for event in self._agent.astream_events(
                    Command(resume=resume),
                    config=config,
                    version="v2",
                ):
                    kind = event.get("event", "")
                    if handler is not None:
                        handler.handle_event(event)
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
                        yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp), "run_id": event.get("run_id", "")}}
                    elif kind == "on_tool_end":
                        name = event.get("name", "?")
                        out = event["data"].get("output")
                        yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out), "run_id": event.get("run_id", "")}}
                    elif kind == "on_tool_error":
                        err = event["data"].get("error", "")
                        yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err), "run_id": event.get("run_id", "")}}
            except Exception as exc:
                yield {"event": "error", "data": {"error": str(exc)}}
                return
            finally:
                if handler is not None and self._tracing_api is not None:
                    handler.write_to_store(self._tracing_api.store)
            yield {"event": "done", "data": {}}
            if self._session_service:
                await self._session_service._update_turn_count(thread_id)
            if self._memory_manager:
                await self._memory_manager.compress_if_needed(thread_id)
            return

        # ── 正常模式：发送消息 ──
        try:
            state_snap = await self._agent.aget_state(config)
            if state_snap.tasks and state_snap.tasks[0].interrupts:
                await self._agent.ainvoke(
                    Command(resume=False),
                    config=config,
                )
        except Exception:
            pass

        if self._session_service:
            await self._session_service._cleanup_orphan_tool_calls(config)

        # ── Tracing ──
        handler = None
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
            handler = TracingStreamHandler(tracer, detect_delegate=True)

        # ── Debug 上下文 ──
        debug_enabled = self._settings_getter().get("debug_enabled", False)
        debug_emitted = False

        try:
            async for event in self._agent.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event.get("event", "")
                if handler is not None:
                    handler.handle_event(event)

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
                                debug_parts.append(f"\u3010{label}\u3011\n{content}")

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
                                tool_lines.append(f"  \U0001f4e6 {tname}\n     \u63cf\u8ff0: {tdesc}\n     \u53c2\u6570: {param_str}")
                            debug_parts.append(f"\u3010\u5df2\u7ed1\u5b9a\u7684\u5de5\u5177 ({len(self._tools)} \u4e2a)\u3011\n" + "\n\n".join(tool_lines))

                        if debug_parts:
                            yield {"event": "context", "data": {
                                "text": "\n\n\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\n\n".join(debug_parts)
                            }}
                    except Exception as exc:
                        yield {"event": "context", "data": {
                            "text": f"(\u83b7\u53d6\u4e0a\u4e0b\u6587\u5931\u8d25: {exc})"
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
                    yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp), "run_id": event.get("run_id", "")}}
                elif kind == "on_tool_end":
                    name = event.get("name", "?")
                    out = event["data"].get("output")
                    yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out), "run_id": event.get("run_id", "")}}
                elif kind == "on_tool_error":
                    err = event["data"].get("error", "")
                    yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err), "run_id": event.get("run_id", "")}}
                elif kind == "on_chain_end":
                    pass
        except Exception as exc:
            yield {"event": "error", "data": {"error": str(exc)}}
            return
        finally:
            if handler is not None and self._tracing_api is not None:
                handler.write_to_store(self._tracing_api.store)

        # ── 检查是否有 interrupt ──
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
            return

        yield {"event": "done", "data": {}}
        if self._session_service:
            await self._session_service._update_turn_count(thread_id)
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

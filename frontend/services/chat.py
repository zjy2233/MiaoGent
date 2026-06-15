"""聊天服务 — 会话消息编辑、流式聊天、记忆管理、知识归并。"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.errors import GraphInterrupt
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
        # 记录被 interrupt 暂停的工具信息（thread_id → {name, run_id}），
        # 用于 resume 后补发 tool_end 事件，避免前端卡片一直"执行中"
        self._pending_interrupted_tools: dict[str, dict] = {}

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
        from langgraph.errors import GraphInterrupt
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

        # 逐层恢复嵌套中断（避免 map 形式同时恢复多层导致状态不一致）
        result = None
        try:
            for _ in range(5):
                try:
                    result = await self._agent.ainvoke(Command(resume=approved), config=config)
                    break
                except GraphInterrupt:
                    # 嵌套中断：继续循环逐层消费
                    continue
            else:
                # 循环耗尽仍未完成
                state_snap = await self._agent.aget_state(config)
                pending = _get_pending_interrupt(state_snap)
                if pending:
                    return {"response": "", "interrupt": pending}
                return {"response": "", "error": "恢复执行时再次遇到需要确认的操作，请在前端重新确认"}
        except Exception as exc:
            return {"response": "", "error": str(exc)}

        # 检查 ainvoke 返回的 __interrupt__（二次 interrupt 可能出现在这里）
        if isinstance(result, dict) and "__interrupt__" in result:
            try:
                state_snap = await self._agent.aget_state(config)
                pending = _get_pending_interrupt(state_snap)
                if pending:
                    return {"response": "", "interrupt": pending}
            except Exception:
                pass

        messages = result.get("messages", []) if isinstance(result, dict) else []
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
            # ── Resume 模式：astream_events + tracing ──
            # 切换为 astream_events 以正确发射 on_tool_start/on_tool_end 事件，
            # 同时启用 TracingStreamHandler 采集 resume 期间的 span。
            resume_cmd = await self._build_resume_cmd(config, resume)
            logger.info("[RESUME] cmd=%s type=%s", resume_cmd, type(resume_cmd).__name__)

            # ── human_decision span + SSE 事件 ──
            pending_info = self._pending_interrupted_tools.pop(thread_id, None)
            _resumed_tool_ended = False  # 标记是否已给恢复的工具发过 tool_end（run_id 替换只做一次）

            if pending_info and self._tracing_api is not None:
                from src.tracing.models import SpanData
                decision = "approved" if resume else "denied"
                decision_span = SpanData(
                    span_type="human_decision",
                    parent_span_id=pending_info.get("tool_span_id", ""),
                    trace_id=pending_info.get("trace_id", ""),
                    session_id=thread_id,
                    tool_name=pending_info.get("name", ""),
                    tool_input=(pending_info.get("command", "") or "")[:500],
                    status="ok",
                    error_message=decision,
                )
                decision_span.end()
                self._tracing_api.store.write_span(decision_span)

            if pending_info:
                yield {
                    "event": "human_decision",
                    "data": {
                        "tool_name": pending_info["name"],
                        "run_id": pending_info["run_id"],
                        "result": "approved" if resume else "denied",
                    },
                }

            # ── Tracing ──
            handler = None
            if self._tracing_api is not None:
                from src.tracing.tracer import Tracer
                session_turn = 0
                try:
                    state = await self._agent.aget_state(config)
                    msgs = list(state.values.get("messages", []) or [])
                    session_turn = sum(1 for m in msgs if getattr(m, "type", "") == "human")
                    last_human_msg = ""
                    for m in reversed(msgs):
                        if getattr(m, "type", "") == "human":
                            content = getattr(m, "content", "")
                            if isinstance(content, str):
                                last_human_msg = content[:200]
                            elif isinstance(content, list):
                                last_human_msg = "".join(
                                    b.get("text", "") for b in content if isinstance(b, dict)
                                )[:200]
                            break
                except Exception:
                    pass
                tracer = Tracer(session_id=thread_id, session_turn=session_turn)
                if pending_info and pending_info.get("trace_id"):
                    tracer.start_span("session_turn", trace_id=pending_info["trace_id"],
                                      user_message=last_human_msg)
                else:
                    tracer.start_span("session_turn", user_message=last_human_msg)
                handler = TracingStreamHandler(tracer, detect_delegate=True)

            _last_tool_name = ""
            _last_tool_run_id = ""

            try:
                async for event in self._agent.astream_events(
                    resume_cmd, config=config, version="v2",
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
                        _last_tool_name = name
                        _last_tool_run_id = event.get("run_id", "")
                        # 被中断后恢复的工具：跳过 tool_start（前端已有卡片），
                        # 后续 tool_end 会复用原始 run_id 匹配原有卡片
                        if pending_info and pending_info.get("name") == name:
                            continue
                        yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp), "run_id": _last_tool_run_id}}
                    elif kind == "on_tool_end":
                        name = event.get("name", "?")
                        out = event["data"].get("output")
                        effective_run_id = event.get("run_id", "")
                        # 被中断后恢复的工具：用原始 run_id 匹配前端已有卡片（仅第一次）
                        if pending_info and not _resumed_tool_ended and pending_info.get("name") == name and pending_info.get("run_id"):
                            effective_run_id = pending_info["run_id"]
                            _resumed_tool_ended = True
                        yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out), "run_id": effective_run_id}}
                    elif kind == "on_tool_error":
                        err = event["data"].get("error", "")
                        if isinstance(err, GraphInterrupt):
                            continue
                        yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err), "run_id": event.get("run_id", "")}}
            except GraphInterrupt:
                logger.info("[RESUME] GraphInterrupt caught in astream_events")
                pass
            except Exception as exc:
                yield {"event": "error", "data": {"error": str(exc)}}
                return
            finally:
                if handler is not None and self._tracing_api is not None:
                    handler.write_to_store(self._tracing_api.store)

            # ── 检查是否有新的/残余 interrupt ──
            # 嵌套子图可能产生多层中断；同一工具的嵌套中断自动消费，不同工具则提示用户
            _gave_up = False
            try:
                for _retry in range(5):
                    state_snap = await self._agent.aget_state(config)
                    pending = None
                    if state_snap.tasks and state_snap.tasks[0].interrupts:
                        pending = state_snap.tasks[0].interrupts[0].value

                    if not (pending and isinstance(pending, dict)):
                        break

                    # 判断是否与刚恢复的工具相同（嵌套中断而非新工具调用）
                    same_tool = bool(
                        pending_info
                        and pending.get("command") == pending_info.get("command")
                    )

                    if same_tool:
                        # 同一工具的嵌套中断：自动消费，不重复询问用户
                        logger.info("[RESUME] auto-consuming nested interrupt for same tool")
                        try:
                            await self._agent.ainvoke(Command(resume=resume), config=config)
                        except Exception:
                            pass
                        continue
                    else:
                        # 新工具的中断：需要用户确认
                        if _last_tool_name and _last_tool_run_id:
                            tool_span_id = ""
                            trace_id = ""
                            if handler is not None:
                                tool_span_id = handler._run_id_to_span_id.get(_last_tool_run_id, "")
                                span = handler._tracer._spans.get(tool_span_id)
                                trace_id = span.trace_id if span else ""
                            self._pending_interrupted_tools[thread_id] = {
                                "name": _last_tool_name,
                                "run_id": _last_tool_run_id,
                                "trace_id": trace_id,
                                "tool_span_id": tool_span_id,
                                "command": pending.get("command", ""),
                            }
                        yield {
                            "event": "interrupt",
                            "data": {
                                "type": pending.get("type", ""),
                                "command": pending.get("command") or pending.get("path", ""),
                                "reason": pending.get("reason", ""),
                                "alternatives": pending.get("alternatives", []),
                            },
                        }
                        return
                else:
                    # 循环耗尽：仍有中断未消费
                    _gave_up = True
            except Exception as exc:
                logger.warning("[RESUME] nested interrupt check failed: %s", exc)

            if _gave_up:
                # 最终检查：如果有未能自动消费的中断，交给前端处理
                try:
                    state_snap = await self._agent.aget_state(config)
                    if state_snap.tasks and state_snap.tasks[0].interrupts:
                        pending = state_snap.tasks[0].interrupts[0].value
                        if pending and isinstance(pending, dict):
                            yield {
                                "event": "interrupt",
                                "data": {
                                    "type": pending.get("type", ""),
                                    "command": pending.get("command") or pending.get("path", ""),
                                    "reason": pending.get("reason", ""),
                                    "alternatives": pending.get("alternatives", []),
                                },
                            }
                            return
                except Exception:
                    pass

            yield {"event": "done", "data": {}}
            if self._session_service:
                await self._session_service._update_turn_count(thread_id)
            if self._memory_manager:
                await self._memory_manager.compress_if_needed(thread_id)
            return

        # ── 正常模式：发送消息前先清理残留中断 ──
        # 循环逐层恢复，避免一次 map 形式同时恢复多层中断导致状态不一致
        try:
            for _ in range(5):  # 最多 5 层嵌套（安全上限）
                state_snap = await self._agent.aget_state(config)
                if not (state_snap.tasks and state_snap.tasks[0].interrupts):
                    break
                logger.info("[CHAT] auto-reject pending interrupt")
                await self._agent.ainvoke(Command(resume=False), config=config)
        except Exception as exc:
            logger.warning("[CHAT] auto-reject failed: %s", exc)

        # 清理残留的孤立 tool_calls（确保消息序列合法）
        if self._session_service:
            await self._session_service._cleanup_orphan_tool_calls(config)

        # 二次验证：确保中断已清除，否则再尝试一次子图级恢复
        try:
            state_snap = await self._agent.aget_state(config, subgraphs=True)
            all_ids: list[str] = []
            for task in state_snap.tasks or []:
                for intr in task.interrupts or []:
                    all_ids.append(intr.id)
            if all_ids:
                logger.warning("[CHAT] residual interrupts after cleanup: %s", all_ids)
                # 使用 map 形式做兜底清理
                try:
                    await self._agent.ainvoke(Command(resume={id: False for id in all_ids}), config=config)
                except Exception as exc2:
                    logger.warning("[CHAT] residual cleanup failed: %s", exc2)
                # 再次清理孤儿 tool_calls
                if self._session_service:
                    await self._session_service._cleanup_orphan_tool_calls(config)
        except Exception as exc:
            logger.warning("[CHAT] residual check failed: %s", exc)

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
        _last_tool_name = ""
        _last_tool_run_id = ""

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
                    _last_tool_name = name
                    _last_tool_run_id = event.get("run_id", "")
                    yield {"event": "tool_start", "data": {"name": name, "input": _short_repr(inp), "run_id": _last_tool_run_id}}
                elif kind == "on_tool_end":
                    name = event.get("name", "?")
                    out = event["data"].get("output")
                    yield {"event": "tool_end", "data": {"name": name, "output": _short_repr(out), "run_id": event.get("run_id", "")}}
                elif kind == "on_tool_error":
                    err = event["data"].get("error", "")
                    # interrupt() 触发的 GraphInterrupt 会包装为 on_tool_error，
                    # 不应作为错误发送给前端（后续 aget_state 会检测并 yield interrupt 事件）
                    if isinstance(err, GraphInterrupt):
                        continue
                    yield {"event": "tool_error", "data": {"name": event.get("name", "?"), "error": str(err), "run_id": event.get("run_id", "")}}
                elif kind == "on_chain_end":
                    pass
        except GraphInterrupt:
            logger.info("[CHAT] GraphInterrupt caught in astream_events (normal flow)")
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
            # 记录被中断的工具信息（含 trace 上下文），resume 时用于 human_decision span
            if _last_tool_name and _last_tool_run_id:
                tool_span_id = ""
                trace_id = ""
                if handler is not None:
                    tool_span_id = handler._run_id_to_span_id.get(_last_tool_run_id, "")
                    span = handler._tracer._spans.get(tool_span_id)
                    trace_id = span.trace_id if span else ""
                self._pending_interrupted_tools[thread_id] = {
                    "name": _last_tool_name,
                    "run_id": _last_tool_run_id,
                    "trace_id": trace_id,
                    "tool_span_id": tool_span_id,
                    "command": pending_interrupt.get("command", ""),
                }
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

    async def _build_resume_cmd(self, config: dict, resume_value: bool) -> Command:
        """构建 Command(resume=...)。

        始终优先使用单值 Command(resume=value) 形式。create_agent 子图嵌套产生的
        多层中断是链接关系——恢复最外层后子图层级自动解除，无需 map 形式。
        map 形式 Command(resume={id: val, ...}) 同时恢复多层链接中断会导致
        ToolMessage 未正确写入父图状态，引发 LLM API 报 insufficient tool messages。
        """
        from langgraph.types import Command
        return Command(resume=resume_value)

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


def _get_pending_interrupt(state_snap) -> dict | None:
    """从 aget_state 返回值中提取 pending interrupt 的 data dict。"""
    try:
        if state_snap.tasks and state_snap.tasks[0].interrupts:
            pending = state_snap.tasks[0].interrupts[0].value
            if pending and isinstance(pending, dict):
                return {
                    "type": pending.get("type", ""),
                    "command": pending.get("command") or pending.get("path", ""),
                    "reason": pending.get("reason", ""),
                    "alternatives": pending.get("alternatives", []),
                }
    except Exception:
        pass
    return None

"""TraceCallbackHandler — LangChain 事件采集，零侵入。"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.tracing.tracer import Tracer
from src.tracing.store import TraceStore


class TraceCallbackHandler(BaseCallbackHandler):
    """LangChain BaseCallbackHandler 实现，通过回调采集 trace 数据。

    注意：本 handler 的 start/end 回调中会直接读写 Tracer，
    Tracer 维护了 '当前 span' 栈，确保嵌套关系正确。

    支持两种模式：
    - 独立模式：创建自己的 Tracer 实例（用于主 agent）
    - 共享模式：传入外部 Tracer（用于 sub-agent，共享父 agent 的 span 栈）
    """

    def __init__(
        self,
        store: TraceStore,
        session_id: str = "",
        session_turn: int = 0,
        tracer: Tracer | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.session_id = session_id
        self.session_turn = session_turn
        self._tracer: Tracer | None = tracer  # 外部传入的 tracer（共享模式）
        self._trace_id: str = ""
        self._run_id_to_span_id: dict[uuid.UUID, str] = {}
        self._has_root = False
        self._is_shared = tracer is not None  # 共享模式下不创建 root span
        self._supervisor_llm_id: str | None = None  # 最近的 supervisor LLM（ReAct 串行，单值足够）

    def _ensure_tracer(self) -> Tracer:
        if self._tracer is None:
            self._tracer = Tracer(session_id=self.session_id, session_turn=self.session_turn)
        return self._tracer

    def _extract_tokens(self, llm_output: dict | None) -> dict[str, int]:
        if not llm_output:
            return {"input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(usage, dict):
            return {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "cache_hit": usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0),
                "cache_miss": usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0),
            }
        return {"input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0}

    # ── Chain (用于 session_turn + agent_step) ──

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any],
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        # 共享模式下不创建 chain span（sub-agent 的 span 直接挂在父 tracer 的当前栈顶下）
        if self._is_shared:
            return
        tracer = self._ensure_tracer()
        user_message = ""
        if not self._has_root:
            self._has_root = True
            # root chain → session_turn
            messages = inputs.get("messages", [])
            if messages:
                last = messages[-1] if isinstance(messages, list) else messages
                content = getattr(last, "content", "") if not isinstance(last, str) else last
                if isinstance(content, str):
                    user_message = content[:200]
            span_id = tracer.start_span(
                "session_turn", user_message=user_message,
            )
            self._trace_id = tracer._spans[span_id].trace_id
            self._run_id_to_span_id[run_id] = span_id
        # agent_step spans intentionally removed — redundant with llm_call/tool_call

    def on_chain_end(
        self, outputs: dict[str, Any],
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id)
            span = tracer._spans.get(span_id)
            if span:
                if not self._is_shared:
                    self.store.write_span(span)

    def on_chain_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                if not self._is_shared:
                    self.store.write_span(span)

    # ── LLM ──

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str],
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        name = serialized.get("name", "") if isinstance(serialized, dict) else ""
        tracer = self._ensure_tracer()
        # Detect llm_role: 'sub' if inside delegate_task, else 'supervisor'
        llm_role = "supervisor"
        for sid in tracer._span_stack:
            s = tracer._spans.get(sid)
            if s and s.span_type == "delegate_task":
                llm_role = "sub"
                break
        span_id = tracer.start_span("llm_call", model=name or "unknown", llm_role=llm_role)
        if llm_role == "supervisor":
            self._supervisor_llm_id = span_id  # ReAct 串行，新 LLM 覆盖旧值即可
        self._run_id_to_span_id[run_id] = span_id
        if not self._trace_id:
            self._trace_id = tracer._spans[span_id].trace_id

    def on_llm_end(
        self, response: Any,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if not span_id:
            return
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            tokens = self._extract_tokens(llm_output)
            span = tracer._spans.get(span_id)
            if span:
                span.input_tokens = tokens["input"]
                span.output_tokens = tokens["output"]
                span.cache_hit_tokens = tokens["cache_hit"]
                span.cache_miss_tokens = tokens["cache_miss"]
        tracer.end_span(span_id)
        span = tracer._spans.get(span_id)
        if span:
            self.store.write_span(span)

    def on_llm_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                if not self._is_shared:
                    self.store.write_span(span)

    # ── Tool ──

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str,
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        tracer = self._ensure_tracer()
        name = (serialized.get("name", "") if isinstance(serialized, dict)
                else getattr(serialized, "name", ""))
        is_delegate = name == "delegate_task"
        # supervisor 级 tool → 挂到发起它的 LLM（不从 tracer 栈取，因 LLM 已在 on_llm_end 出栈）
        inside_delegate = any(
            tracer._spans.get(sid) and tracer._spans[sid].span_type == "delegate_task"
            for sid in tracer._span_stack
        )
        parent_override = None
        if not inside_delegate and self._supervisor_llm_id:
            parent_override = self._supervisor_llm_id
        span_type = "delegate_task" if is_delegate else "tool_call"
        span_id = tracer.start_span(
            span_type, parent_span_id=parent_override,
            tool_name=name or "unknown", tool_input=input_str[:500],
        )
        # 为 delegate_task 设置 trace context，使 sub-agent 共享 tracer
        if is_delegate:
            from src.tracing.context import set_trace_context
            set_trace_context(tracer, span_id)
        self._run_id_to_span_id[run_id] = span_id
        if not self._trace_id:
            self._trace_id = tracer._spans[span_id].trace_id

    def on_tool_end(
        self, output: Any,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            span = tracer._spans.get(span_id)
            if span and span.span_type == "delegate_task":
                from src.tracing.context import clear_trace_context
                clear_trace_context()
            tracer.end_span(span_id)
            span = tracer._spans.get(span_id)
            if span:
                if not self._is_shared:
                    self.store.write_span(span)

    def on_tool_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            span = tracer._spans.get(span_id)
            if span and span.span_type == "delegate_task":
                from src.tracing.context import clear_trace_context
                clear_trace_context()
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                if not self._is_shared:
                    self.store.write_span(span)

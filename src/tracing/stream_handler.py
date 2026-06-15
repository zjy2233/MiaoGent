"""Handle tracing span start/end/error events from astream_events v2.

Encapsulates the span collection logic used by chat_stream's resume and normal modes.
"""

from __future__ import annotations

from typing import Any

from src.core.serialize import _serialize_llm_input, _serialize_llm_output, _short_repr
from src.tracing.tracer import Tracer


class TracingStreamHandler:
    """Handle tracing span start/end/error events from astream_events v2.

    Args:
        tracer: Tracer instance.
        detect_delegate: If True, detect delegate_task tool calls and
            set/clear trace context. Set False for resume mode.
    """

    def __init__(self, tracer: Tracer, *, detect_delegate: bool = True) -> None:
        self._tracer = tracer
        self._run_id_to_span_id: dict[str, str] = {}
        self._detect_delegate = detect_delegate
        self._supervisor_llm_id: str | None = None

    def current_span_id(self) -> str | None:
        return self._tracer.current_span_id

    def handle_event(self, event: dict) -> None:
        """Dispatch astream_events v2 event to the appropriate span handler."""
        kind = event.get("event", "")
        run_id = event.get("run_id")

        if kind == "on_chat_model_start":
            self._on_chat_model_start(run_id, event)
        elif kind == "on_chat_model_end":
            self._on_chat_model_end(run_id, event)
        elif kind == "on_chat_model_error":
            self._on_chat_model_error(run_id, event)
        elif kind == "on_tool_start":
            self._on_tool_start(run_id, event)
        elif kind == "on_tool_end":
            self._on_tool_end(run_id, event)
        elif kind == "on_tool_error":
            self._on_tool_error(run_id, event)

    def flush(self) -> None:
        """End any remaining open spans."""
        for sid in list(self._tracer._span_stack):
            self._tracer.end_span(sid)

    def write_to_store(self, store: Any) -> None:
        """Flush and write collected spans to the trace store."""
        self.flush()
        finished = list(self._tracer._spans.values())
        if finished:
            store.write_spans(finished)

    # ── Private: llm_role detection ──

    def _detect_llm_role(self) -> str:
        """Return 'sub' if inside a delegate_task, else 'supervisor'."""
        for sid in self._tracer._span_stack:
            span = self._tracer._spans.get(sid)
            if span and span.span_type == "delegate_task":
                return "sub"
        return "supervisor"

    # ── Private handlers ──

    def _on_chat_model_start(self, run_id: str, event: dict) -> None:
        name = event.get("name", "") or "chat_model"
        role = self._detect_llm_role()
        sid = self._tracer.start_span(
            "llm_call",
            model=name,
            llm_role=role,
            llm_input=_serialize_llm_input(event["data"]["input"]),
        )
        if role == "supervisor":
            self._supervisor_llm_id = sid
        self._run_id_to_span_id[run_id] = sid

    def _on_chat_model_end(self, run_id: str, event: dict) -> None:
        sid = self._run_id_to_span_id.pop(run_id, None)
        if not sid:
            return
        resp = event.get("data", {}).get("output", {})
        if isinstance(resp, dict):
            usage = resp.get("usage_metadata") or {}
            resp_meta = resp.get("response_metadata") or {}
        else:
            usage = getattr(resp, "usage_metadata", {})
            resp_meta = getattr(resp, "response_metadata", {})
        span = self._tracer._spans.get(sid)
        if span:
            span.llm_output = _serialize_llm_output(resp)
            if isinstance(usage, dict) and usage:
                span.input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                span.output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            token_usage = resp_meta.get("token_usage") or {}
            details = token_usage.get("prompt_tokens_details") or {}
            span.cache_hit_tokens = (
                usage.get("prompt_cache_hit_tokens", 0)
                or usage.get("cache_read_input_tokens", 0)
                or token_usage.get("prompt_cache_hit_tokens", 0)
                or details.get("cached_tokens", 0)
            )
            span.cache_miss_tokens = (
                usage.get("prompt_cache_miss_tokens", 0)
                or usage.get("cache_creation_input_tokens", 0)
                or token_usage.get("prompt_cache_miss_tokens", 0)
            )
        self._tracer.end_span(sid)

    def _on_chat_model_error(self, run_id: str, event: dict) -> None:
        sid = self._run_id_to_span_id.pop(run_id, None)
        if sid:
            self._tracer.end_span(
                sid, status="error",
                error_message=str(event.get("data", {}).get("error", "")),
            )

    def _on_tool_start(self, run_id: str, event: dict) -> None:
        name = event.get("name", "?")
        inp = event["data"].get("input", "")
        is_delegate = self._detect_delegate and name == "delegate_task"
        span_type = "delegate_task" if is_delegate else "tool_call"
        # 工具默认从栈顶取 parent（LLM span 可能已在 on_chat_model_end 出栈），
        # 对于 supervisor 级工具，显式挂到发起它的 LLM span 下
        inside_delegate = any(
            self._tracer._spans.get(sid) and self._tracer._spans[sid].span_type == "delegate_task"
            for sid in self._tracer._span_stack
        )
        parent_override = None
        if not inside_delegate and not is_delegate and self._supervisor_llm_id:
            parent_override = self._supervisor_llm_id
        sid = self._tracer.start_span(
            span_type, parent_span_id=parent_override,
            tool_name=name, tool_input=_short_repr(inp, 500),
        )
        self._run_id_to_span_id[run_id] = sid
        if is_delegate:
            from src.tracing.context import set_trace_context
            set_trace_context(self._tracer, sid)

    def _on_tool_end(self, run_id: str, event: dict) -> None:
        sid = self._run_id_to_span_id.pop(run_id, None)
        if sid:
            span = self._tracer._spans.get(sid)
            if span:
                span.tool_output = _short_repr(event["data"].get("output"), 4096)
            is_delegate = bool(span and span.span_type == "delegate_task")
            self._tracer.end_span(sid)
            if is_delegate:
                from src.tracing.context import clear_trace_context
                clear_trace_context()

    def _on_tool_error(self, run_id: str, event: dict) -> None:
        sid = self._run_id_to_span_id.pop(run_id, None)
        if sid:
            span = self._tracer._spans.get(sid)
            is_delegate = bool(span and span.span_type == "delegate_task")
            self._tracer.end_span(
                sid, status="error",
                error_message=str(event.get("data", {}).get("error", "")),
            )
            if is_delegate:
                from src.tracing.context import clear_trace_context
                clear_trace_context()

"""Tracer -- span 生命周期管理。"""

from __future__ import annotations

from typing import Any

from src.tracing.models import SpanData


class Tracer:
    def __init__(self, session_id: str = "", session_turn: int = 0):
        self._spans: dict[str, SpanData] = {}
        self._span_stack: list[str] = []
        self._session_id = session_id
        self._session_turn = session_turn

    def start_span(
        self,
        span_type: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        user_message: str = "",
        **kwargs: Any,
    ) -> str:
        if parent_span_id is None:
            parent_span_id = self._span_stack[-1] if self._span_stack else None
        span_kwargs = dict(
            span_type=span_type,
            parent_span_id=parent_span_id,
            session_id=self._session_id,
            session_turn=self._session_turn,
            user_message=user_message,
        )
        if trace_id:
            span_kwargs["trace_id"] = trace_id
        elif self._span_stack:
            # inherit trace_id from root span
            span_kwargs["trace_id"] = self._spans[self._span_stack[0]].trace_id
        span = SpanData(**span_kwargs, **kwargs)
        self._spans[span.span_id] = span
        self._span_stack.append(span.span_id)
        return span.span_id

    def end_span(self, span_id: str, status: str = "ok", error_message: str = "") -> None:
        span = self._spans.get(span_id)
        if span:
            span.end(status=status, error_message=error_message)
        # 始终从栈中移除（非栈顶时也会因并行工具执行而残留）
        try:
            self._span_stack.remove(span_id)
        except ValueError:
            pass  # 已被移除（flush 后的重复调用）

    @property
    def finished_spans(self) -> list[SpanData]:
        return [s for s in self._spans.values() if s.ended_at]

    @property
    def current_span_id(self) -> str | None:
        return self._span_stack[-1] if self._span_stack else None

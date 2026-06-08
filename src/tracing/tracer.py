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
        user_message: str = "",
        **kwargs: Any,
    ) -> str:
        parent_span_id = self._span_stack[-1] if self._span_stack else None
        span = SpanData(
            span_type=span_type,
            trace_id=trace_id or "",
            parent_span_id=parent_span_id,
            session_id=self._session_id,
            session_turn=self._session_turn,
            user_message=user_message,
            **kwargs,
        )
        # inherit trace_id from root span if not set
        if not span.trace_id and self._span_stack:
            span.trace_id = self._spans[self._span_stack[0]].trace_id
        self._spans[span.span_id] = span
        self._span_stack.append(span.span_id)
        return span.span_id

    def end_span(self, span_id: str, status: str = "ok", error_message: str = "") -> None:
        span = self._spans.get(span_id)
        if span:
            span.end(status=status, error_message=error_message)
        if self._span_stack and self._span_stack[-1] == span_id:
            self._span_stack.pop()

    @property
    def finished_spans(self) -> list[SpanData]:
        return [s for s in self._spans.values() if s.ended_at]

    @property
    def current_span_id(self) -> str | None:
        return self._span_stack[-1] if self._span_stack else None

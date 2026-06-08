"""Trace span 数据模型。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class SpanData:
    span_id: str = field(default_factory=_new_id)
    parent_span_id: str | None = None
    trace_id: str = field(default_factory=_new_id)
    session_id: str = ""
    session_turn: int = 0
    span_type: str = ""  # session_turn | llm_call | agent_step | tool_call | delegate_task
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tool_name: str = ""
    tool_input: str = ""
    status: str = "ok"  # ok | error
    error_message: str = ""
    started_at: str = field(default_factory=_timestamp)
    ended_at: str = ""
    duration_ms: int = 0
    user_message: str = ""

    def end(self, status: str = "ok", error_message: str = "") -> None:
        now = datetime.now(timezone.utc)
        self.ended_at = now.isoformat()
        self.status = status
        self.error_message = error_message
        start = datetime.fromisoformat(self.started_at)
        self.duration_ms = int((now - start).total_seconds() * 1000)

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "session_turn": self.session_turn,
            "span_type": self.span_type,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "status": self.status,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "user_message": self.user_message,
        }

"""Tracing 上下文变量 — 跨越 async 边界传递 Tracer 实例。

用于 delegate_task → run_sub_agent 的调用链中共享 Tracer，
使 sub-agent 内部的 LLM/Tool 调用能正确挂载到 delegate_task span 下。
"""

from __future__ import annotations

import contextvars

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tracing.tracer import Tracer

_current_tracer: contextvars.ContextVar["Tracer | None"] = contextvars.ContextVar(
    "current_tracer", default=None
)
_current_parent_span_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_parent_span_id", default=""
)


def set_trace_context(tracer: "Tracer", parent_span_id: str) -> None:
    """在 delegate_task 执行前设置上下文，供 sub-agent 感知。"""
    _current_tracer.set(tracer)
    _current_parent_span_id.set(parent_span_id)


def get_trace_context() -> tuple["Tracer | None", str]:
    """返回 (tracer, parent_span_id)；未设置时均为 falsy。"""
    return _current_tracer.get(), _current_parent_span_id.get()


def clear_trace_context() -> None:
    """delegate_task 执行完成后清除上下文。"""
    _current_tracer.set(None)
    _current_parent_span_id.set("")

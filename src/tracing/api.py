"""TracingAPI — 供 bridge.py 调用的查询接口。"""

from __future__ import annotations

from typing import Any

from src.tracing.store import TraceStore


class TracingAPI:
    def __init__(self, store: TraceStore) -> None:
        self.store = store

    def get_trace_list(
        self, q: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self.store.get_trace_list(q=q, status=status, limit=limit, offset=offset)

    def get_trace_tree(self, trace_id: str) -> dict[str, Any]:
        spans = self.store.get_trace_spans(trace_id)
        if not spans:
            return {"trace_id": trace_id, "spans": [], "total_duration_ms": 0}

        # 后处理：将 tool_call/delegate_task 重新关联到其前置 supervisor LLM
        spans = self._reparent_tools_to_llm(spans)

        root = next((s for s in spans if s["span_type"] == "session_turn"), spans[0])
        # Build tree structure
        children: list[dict] = []
        span_map: dict[str, dict] = {}
        for s in spans:
            d = dict(s)
            d["children"] = []
            span_map[d["span_id"]] = d
        root = span_map[root["span_id"]]
        for s in spans:
            pid = s.get("parent_span_id")
            if pid and pid in span_map:
                span_map[pid]["children"].append(span_map[s["span_id"]])
            elif s["span_id"] != root["span_id"]:
                children.append(span_map[s["span_id"]])
        root["children"].extend(children)
        return {
            "trace_id": trace_id,
            "spans": spans,
            "tree": root,
            "total_duration_ms": root.get("duration_ms", 0),
            "total_tokens": sum(s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in spans),
            "user_message": root.get("user_message", ""),
        }

    @staticmethod
    def _reparent_tools_to_llm(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """兼容层：修复旧 trace 中 tool_call 的错误 parent_span_id。

        旧 trace 已知问题：
        1. on_llm_end 早于 on_tool_start 触发，tool 得不到 LLM 作 parent
        2. 并行工具间 toolA→toolB 错误嵌套

        新 trace 已被 stream_handler.py 和 handler.py 正确修复，此方法仅保留作为兼容。
        """
        if not spans:
            return spans

        sorted_spans = sorted(spans, key=lambda s: s["started_at"])

        modified: dict[str, dict[str, Any]] = {}
        for s in spans:
            modified[s["span_id"]] = {**s}

        session_turn_span_id: str | None = next(
            (s["span_id"] for s in spans if s["span_type"] == "session_turn"),
            None,
        )
        last_supervisor_llm_id: str | None = None

        for s in sorted_spans:
            sid = s["span_id"]
            span = modified[sid]
            stype = span["span_type"]
            role = span.get("llm_role", "")

            if stype == "llm_call" and role != "sub":
                last_supervisor_llm_id = sid
                continue

            if stype not in ("tool_call", "delegate_task"):
                continue

            if not last_supervisor_llm_id:
                continue

            parent_id = span.get("parent_span_id")
            # parent_span_id 为 None（DB 中 NULL）或为 session_turn / 其他 tool → 重新挂到 supervisor LLM
            if parent_id is None or parent_id == session_turn_span_id:
                span["parent_span_id"] = last_supervisor_llm_id
            else:
                parent = modified.get(parent_id)
                if parent and parent["span_type"] == "tool_call":
                    span["parent_span_id"] = last_supervisor_llm_id

        return [modified[s["span_id"]] for s in spans]

    def get_stats(self) -> dict[str, Any]:
        return self.store.get_stats()

    def get_daily_stats(self) -> list[dict[str, Any]]:
        return self.store.get_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.get_traces_by_session(session_id)

    def get_trace_count(self, q: str = "", status: str = "") -> int:
        return self.store.get_trace_count(q=q, status=status)

    def get_token_top_traces(self, days: int = 3, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.get_token_top_traces(days=days, limit=limit)

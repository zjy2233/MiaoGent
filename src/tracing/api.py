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

    def get_stats(self) -> dict[str, Any]:
        return self.store.get_stats()

    def get_daily_stats(self) -> list[dict[str, Any]]:
        return self.store.get_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.get_traces_by_session(session_id)

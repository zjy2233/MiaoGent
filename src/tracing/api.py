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
        """将 tool_call/delegate_task 重新关联到发起它们的 supervisor LLM。

        当前 Tracer 基于调用栈确定父子关系，但 LangChain ReAct 的回调顺序是:
          LLM start → LLM end → Tool start → Tool end
        导致所有工具都成为 session_turn 的直接子级（平级关系）。
        此方法通过时间序分析，将工具正确挂到发起它们的 LLM 下。

        同时解决并行工具执行时 toolA → toolB 的错误嵌套（当 toolB 在
        toolA 的 on_tool_end 之前触发 on_tool_start 时发生）。
        """
        if not spans:
            return spans

        # 找到 session_turn span_id
        session_id = None
        for s in spans:
            if s["span_type"] == "session_turn":
                session_id = s["span_id"]
                break
        if not session_id:
            return spans

        # 按 started_at 排序
        sorted_spans = sorted(spans, key=lambda s: s["started_at"])

        # 跟踪最近的 supervisor LLM span_id（llm_role != "sub"）
        last_supervisor_llm_id: str | None = None

        # 用 dict 保存修改后的 spans（引用原数据，原地修改）
        modified: dict[str, dict[str, Any]] = {}
        for s in spans:
            span_id = s["span_id"]
            # 创建可变副本（避免影响原始数据）
            modified[span_id] = {**s}

        for s in sorted_spans:
            sid = s["span_id"]
            span = modified[sid]
            stype = span["span_type"]
            role = span.get("llm_role", "")

            if stype == "llm_call" and role != "sub":
                # supervisor LLM — 记录为当前活跃 LLM
                last_supervisor_llm_id = sid

            elif stype in ("tool_call", "delegate_task"):
                # 工具或委派任务 — 如果它的 parent 是 session_turn
                # 则重新关联到最近的活跃 supervisor LLM
                if span.get("parent_span_id") == session_id and last_supervisor_llm_id:
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

"""Tracing 查询服务 — 封装对 TracingAPI 的调用。"""

from __future__ import annotations

from typing import Any


class TracingService:
    """Tracing 查询：trace 列表、详情、统计、按会话查询等。"""

    def __init__(self, tracing_api: Any = None) -> None:
        self._tracing_api = tracing_api

    def get_traces(self, q: str = "", status: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_trace_list(q=q, status=status, limit=limit, offset=offset)

    def get_trace_detail(self, trace_id: str) -> dict:
        if self._tracing_api is None:
            return {"error": "Tracing 未就绪"}
        return self._tracing_api.get_trace_tree(trace_id)

    def get_trace_spans(self, trace_id: str) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.store.get_trace_spans(trace_id)

    def get_trace_stats(self) -> dict:
        if self._tracing_api is None:
            return {"total_traces": 0, "total_tokens": 0, "avg_duration_ms": 0, "error_rate": 0}
        return self._tracing_api.get_stats()

    def get_trace_cache_stats(self) -> dict:
        if self._tracing_api is None:
            return {
                "total_cache_hit_tokens": 0,
                "total_cache_miss_tokens": 0,
                "cache_hit_rate": 0,
            }
        base = self._tracing_api.get_stats()
        hit = base.get("all_time_cache_hit_tokens", 0) or base.get("total_cache_hit_tokens", 0)
        miss = base.get("all_time_cache_miss_tokens", 0) or base.get("total_cache_miss_tokens", 0)
        total_cacheable = hit + miss
        return {
            "total_cache_hit_tokens": hit,
            "total_cache_miss_tokens": miss,
            "cache_hit_rate": round(hit / total_cacheable * 100, 1) if total_cacheable > 0 else 0,
        }

    def get_trace_daily_stats(self) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_traces_by_session(session_id)

    def get_trace_count(self, q: str = "", status: str = "") -> int:
        if self._tracing_api is None:
            return 0
        return self._tracing_api.get_trace_count(q=q, status=status)

    def get_token_top_traces(self, days: int = 3, limit: int = 10) -> list[dict]:
        if self._tracing_api is None:
            return []
        return self._tracing_api.get_token_top_traces(days=days, limit=limit)

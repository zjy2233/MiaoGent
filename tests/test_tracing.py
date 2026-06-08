"""Tracing 模块测试。"""

from src.tracing.models import SpanData


class TestSpanData:
    def test_create_span(self):
        span = SpanData(span_type="llm_call")
        assert span.span_id
        assert span.trace_id
        assert span.span_type == "llm_call"
        assert span.status == "ok"
        assert span.started_at
        assert not span.ended_at

    def test_end_span_sets_duration(self):
        span = SpanData(span_type="llm_call")
        import time
        time.sleep(0.01)
        span.end(status="ok")
        assert span.ended_at
        assert span.duration_ms > 0
        assert span.status == "ok"

    def test_end_span_with_error(self):
        span = SpanData(span_type="tool_call")
        span.end(status="error", error_message="command not found")
        assert span.status == "error"
        assert "command not found" in span.error_message

    def test_to_dict(self):
        span = SpanData(span_type="llm_call", model="deepseek-chat", input_tokens=100, output_tokens=50)
        d = span.to_dict()
        assert d["span_type"] == "llm_call"
        assert d["model"] == "deepseek-chat"
        assert d["input_tokens"] == 100


import pytest
from pathlib import Path
from src.tracing.models import SpanData
from src.tracing.store import TraceStore


class TestTraceStore:
    def test_write_and_count(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        span = SpanData(span_type="llm_call", trace_id="t1", session_id="s1")
        store.write_span(span)
        assert store.count() == 1

    def test_get_trace_spans(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        parent = SpanData(span_type="session_turn", trace_id="t1", session_id="s1")
        store.write_span(parent)
        child = SpanData(
            span_type="llm_call", trace_id="t1", session_id="s1",
            parent_span_id=parent.span_id,
        )
        store.write_span(child)
        spans = store.get_trace_spans("t1")
        assert len(spans) == 2
        # parent first
        assert spans[0]["span_type"] == "session_turn"

    def test_get_trace_list(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        s1 = SpanData(span_type="session_turn", trace_id="t1", session_id="s1", user_message="hello")
        s1.end()
        store.write_span(s1)
        s2 = SpanData(span_type="session_turn", trace_id="t2", session_id="s1", user_message="world")
        s2.end()
        store.write_span(s2)
        traces = store.get_trace_list()
        assert len(traces) == 2

    def test_search_by_user_message(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        s = SpanData(span_type="session_turn", trace_id="t1", session_id="s1", user_message="最新AI新闻")
        s.end()
        store.write_span(s)
        results = store.get_trace_list(q="AI")
        assert len(results) == 1

    def test_get_stats(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        s = SpanData(span_type="session_turn", trace_id="t1", session_id="s1")
        s.end()
        s.duration_ms = 1500
        s.input_tokens = 100
        s.output_tokens = 50
        store.write_span(s)
        s2 = SpanData(span_type="session_turn", trace_id="t2", session_id="s2")
        s2.end()
        s2.duration_ms = 500
        s2.input_tokens = 200
        s2.output_tokens = 100
        s2.status = "error"
        store.write_span(s2)
        stats = store.get_stats()
        assert stats["total_traces"] == 2
        assert stats["total_input_tokens"] == 300
        assert stats["total_output_tokens"] == 150
        assert stats["error_count"] == 1

    def test_cleanup_old(self, tmp_path: Path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        from datetime import datetime, timedelta, timezone
        old = SpanData(span_type="session_turn", trace_id="old")
        old.started_at = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        old.end()
        store.write_span(old)
        new = SpanData(span_type="session_turn", trace_id="new")
        new.end()
        store.write_span(new)
        store.cleanup(retention_days=30)
        assert store.count() == 1
        assert store.get_trace_spans("new")


from src.tracing.tracer import Tracer


class TestTracer:
    def test_start_and_end_span(self):
        t = Tracer(session_id="s1", session_turn=1)
        span_id = t.start_span(span_type="llm_call")
        assert span_id
        span = t._spans[span_id]
        assert span.span_type == "llm_call"
        t.end_span(span_id)
        assert t._spans[span_id].ended_at
        assert t._spans[span_id].duration_ms >= 0

    def test_parent_child(self):
        t = Tracer(session_id="s1", session_turn=1)
        parent_id = t.start_span(span_type="session_turn", user_message="hello")
        child_id = t.start_span(span_type="llm_call")
        t.end_span(child_id)
        t.end_span(parent_id)
        assert t._spans[child_id].parent_span_id == parent_id

    def test_finished_spans(self):
        t = Tracer(session_id="s1", session_turn=1)
        sid = t.start_span(span_type="llm_call")
        t.end_span(sid)
        finished = t.finished_spans
        assert len(finished) == 1
        assert finished[0].span_id == sid

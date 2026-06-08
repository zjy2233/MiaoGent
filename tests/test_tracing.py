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

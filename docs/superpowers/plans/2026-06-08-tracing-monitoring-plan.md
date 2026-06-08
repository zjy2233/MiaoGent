# 链路追踪与 Token 监控 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现完整的链路追踪（Tracing）和 Token 消耗监控，覆盖 LLM 调用、工具调用、Agent 步骤的完整调用链，并在前端提供监控面板。

**Architecture:** 纯后端 `src/tracing/` 模块通过 LangChain `BaseCallbackHandler` 零侵入采集事件，SQLite 持久化存储 Span，通过 REST API 暴露给前端监控面板。

**Tech Stack:** Python 3.11+, LangChain BaseCallbackHandler, SQLite, aiohttp SSE, Vanilla JS

---

## 文件映射

| 操作 | 文件 | 职责 |
|------|------|------|
| 创建 | `src/tracing/__init__.py` | 模块导出 |
| 创建 | `src/tracing/models.py` | SpanData 数据类 |
| 创建 | `src/tracing/store.py` | TraceStore — SQLite 持久化 + CRUD + 聚合查询 |
| 创建 | `src/tracing/tracer.py` | Tracer — span 生命周期管理 |
| 创建 | `src/tracing/handler.py` | TraceCallbackHandler — LangChain 事件采集 |
| 创建 | `src/tracing/api.py` | 查询接口（供 bridge.py 调用） |
| 修改 | `frontend/bridge.py` | Api 类新增 tracing 相关方法 |
| 修改 | `frontend/http_server.py` | 新增 /api/traces/* 路由 + 创建 TraceStore 和 handler |
| 修改 | `frontend/browser-api.js` | window.api 新增 tracing 方法 |
| 修改 | `frontend/electron/preload.js` | contextBridge 新增 tracing 方法 |
| 修改 | `frontend/index.html` | 新增 monitoring-panel 面板 |
| 修改 | `frontend/styles.css` | 新增监控面板样式 |
| 修改 | `frontend/app.js` | 新增 setupMonitoringPanel() |
| 创建 | `tests/test_tracing.py` | Tracing 模块测试 |

---

### Task 1: models.py — SpanData 数据类

**Files:**
- Create: `src/tracing/models.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write SpanData dataclass**

```python
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
```

- [ ] **Step 2: Write tests for SpanData**

```python
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
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd D:/learn/agent-learn/single-agent && .venv/Scripts/python -m pytest tests/test_tracing.py::TestSpanData -v`
Expected: 4 passed

- [ ] **Step 4: Commit**

```bash
git add src/tracing/models.py tests/test_tracing.py
git commit -m "feat(tracing): add SpanData dataclass model"
```

---

### Task 2: store.py — TraceStore SQLite 持久化

**Files:**
- Create: `src/tracing/store.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write tests for TraceStore**

Append to `tests/test_tracing.py`:

```python
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
```

- [ ] **Step 2: Implement TraceStore**

```python
"""TraceStore — SQLite 持久化层。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.tracing.models import SpanData
from src.core.miaogent_home import get_data_path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    parent_span_id TEXT,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    session_turn INTEGER NOT NULL DEFAULT 0,
    span_type TEXT NOT NULL,
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    tool_name TEXT DEFAULT '',
    tool_input TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    error_message TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    user_message TEXT DEFAULT ''
);
"""
INDEX_TRACE_SQL = "CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);"
INDEX_SESSION_SQL = "CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id);"
INDEX_STARTED_SQL = "CREATE INDEX IF NOT EXISTS idx_spans_started ON spans(started_at);"


class TraceStore:
    def __init__(self, db_path: str | None = None):
        resolved = Path(db_path).resolve() if db_path else get_data_path("traces.db")
        self._db_path = str(resolved)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(SCHEMA_SQL)
                conn.execute(INDEX_TRACE_SQL)
                conn.execute(INDEX_SESSION_SQL)
                conn.execute(INDEX_STARTED_SQL)
                conn.commit()
            finally:
                conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def write_span(self, span: SpanData) -> None:
        cols = [
            "span_id", "parent_span_id", "trace_id", "session_id", "session_turn",
            "span_type", "model", "input_tokens", "output_tokens", "tool_name",
            "tool_input", "status", "error_message", "started_at", "ended_at",
            "duration_ms", "user_message",
        ]
        placeholders = ", ".join("?" for _ in cols)
        names = ", ".join(cols)
        values = [getattr(span, c) for c in cols]
        sql = f"INSERT OR REPLACE INTO spans ({names}) VALUES ({placeholders})"
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(sql, values)
                conn.commit()
            finally:
                conn.close()

    def write_spans(self, spans: list[SpanData]) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cols = [
                    "span_id", "parent_span_id", "trace_id", "session_id", "session_turn",
                    "span_type", "model", "input_tokens", "output_tokens", "tool_name",
                    "tool_input", "status", "error_message", "started_at", "ended_at",
                    "duration_ms", "user_message",
                ]
                placeholders = ", ".join("?" for _ in cols)
                names = ", ".join(cols)
                sql = f"INSERT OR REPLACE INTO spans ({names}) VALUES ({placeholders})"
                rows = [[getattr(s, c) for c in cols] for s in spans]
                conn.executemany(sql, rows)
                conn.commit()
            finally:
                conn.close()

    def get_trace_spans(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at ASC",
                    (trace_id,),
                )
                return [self._row_to_dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def get_trace_list(
        self, q: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                conditions = ["span_type = 'session_turn'"]
                params: list[Any] = []
                if q:
                    conditions.append("(user_message LIKE ? OR trace_id LIKE ?)")
                    params.extend([f"%{q}%", f"%{q}%"])
                if status:
                    conditions.append("status = ?")
                    params.append(status)
                where = " AND ".join(conditions)
                sql = (
                    f"SELECT * FROM spans WHERE {where} "
                    f"ORDER BY started_at DESC LIMIT ? OFFSET ?"
                )
                params.extend([limit, offset])
                cursor = conn.execute(sql, params)
                return [self._row_to_dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def get_traces_by_session(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM spans WHERE span_type = 'session_turn' AND session_id = ? "
                    "ORDER BY started_at DESC",
                    (session_id,),
                )
                return [self._row_to_dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row = conn.execute(
                    "SELECT COUNT(*) as total_traces, "
                    "COALESCE(SUM(input_tokens), 0) as total_input_tokens, "
                    "COALESCE(SUM(output_tokens), 0) as total_output_tokens, "
                    "COALESCE(AVG(duration_ms), 0) as avg_duration_ms, "
                    "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_count "
                    "FROM spans WHERE span_type='session_turn' AND started_at >= ?",
                    (today,),
                ).fetchone()
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                row_y = conn.execute(
                    "SELECT COUNT(*) as total_traces, "
                    "COALESCE(SUM(input_tokens+output_tokens), 0) as total_tokens "
                    "FROM spans WHERE span_type='session_turn' AND started_at >= ? AND started_at < ?",
                    (yesterday, today),
                ).fetchone()
                return {
                    "total_traces": row[0],
                    "total_input_tokens": row[1],
                    "total_output_tokens": row[2],
                    "total_tokens": row[1] + row[2],
                    "avg_duration_ms": round(row[3], 1),
                    "error_count": row[4],
                    "error_rate": round(row[4] / row[0] * 100, 1) if row[0] > 0 else 0,
                    "yesterday_tokens": int(row_y[1]) if row_y[1] else 0,
                }
            finally:
                conn.close()

    def get_daily_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT DATE(started_at) as day, "
                    "COUNT(*) as count, "
                    "COALESCE(SUM(input_tokens+output_tokens), 0) as total_tokens "
                    "FROM spans WHERE span_type='session_turn' "
                    "GROUP BY DATE(started_at) ORDER BY day DESC LIMIT 14"
                )
                return [self._row_to_dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def count(self) -> int:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute("SELECT COUNT(*) FROM spans").fetchone()
                return row[0] if row else 0
            finally:
                conn.close()

    def cleanup(self, retention_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    "DELETE FROM spans WHERE started_at < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                conn.commit()
                return deleted
            finally:
                conn.close()
```

- [ ] **Step 3: Run tests**

Run: `cd D:/learn/agent-learn/single-agent && .venv/Scripts/python -m pytest tests/test_tracing.py::TestTraceStore -v`
Expected: 7 passed

- [ ] **Step 4: Commit**

```bash
git add src/tracing/store.py tests/test_tracing.py
git commit -m "feat(tracing): add TraceStore SQLite persistence"
```

---

### Task 3: tracer.py — Tracer Span 生命周期管理

**Files:**
- Create: `src/tracing/tracer.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write Tracer tests**

Append to `tests/test_tracing.py`:

```python
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
```

- [ ] **Step 2: Implement Tracer**

```python
"""Tracer — span 生命周期管理。"""

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
            trace_id=trace_id or (self._span_stack[0] if self._span_stack else ""),
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
```

- [ ] **Step 3: Run tests**

Run: `cd D:/learn/agent-learn/single-agent && .venv/Scripts/python -m pytest tests/test_tracing.py::TestTracer -v`
Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add src/tracing/tracer.py tests/test_tracing.py
git commit -m "feat(tracing): add Tracer span lifecycle manager"
```

---

### Task 4: handler.py — TraceCallbackHandler

**Files:**
- Create: `src/tracing/handler.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write TraceCallbackHandler tests**

```python
from src.tracing.handler import TraceCallbackHandler
from src.tracing.store import TraceStore


class TestTraceCallbackHandler:
    def test_handler_initialization(self, tmp_path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        handler = TraceCallbackHandler(store, session_id="s1")
        assert handler.session_id == "s1"

    def test_on_llm_start_end(self, tmp_path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        handler = TraceCallbackHandler(store, session_id="s1", session_turn=1)
        import uuid
        run_id = uuid.uuid4()
        handler.on_llm_start(
            {"name": "test"}, ["prompt"],
            run_id=run_id, parent_run_id=None,
        )
        assert run_id in handler._run_id_to_span_id
        handler.on_llm_end(
            type("LLMResult", (), {
                "generations": [[]],
                "llm_output": {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            })(),
            run_id=run_id,
        )
        spans = store.get_trace_spans(handler._trace_id)
        llm_spans = [s for s in spans if s["span_type"] == "llm_call"]
        assert len(llm_spans) == 1
        assert llm_spans[0]["input_tokens"] == 100
        assert llm_spans[0]["output_tokens"] == 50

    def test_on_tool_start_end(self, tmp_path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        handler = TraceCallbackHandler(store, session_id="s1", session_turn=1)
        import uuid
        run_id = uuid.uuid4()
        handler.on_tool_start(
            {"name": "search"}, "input json",
            run_id=run_id, parent_run_id=None,
        )
        handler.on_tool_end("output result", run_id=run_id)
        spans = store.get_trace_spans(handler._trace_id)
        tool_spans = [s for s in spans if s["span_type"] == "tool_call"]
        assert len(tool_spans) == 1
        assert tool_spans[0]["tool_name"] == "search"
```

- [ ] **Step 2: Implement TraceCallbackHandler**

```python
"""TraceCallbackHandler — LangChain 事件采集，零侵入。"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.tracing.tracer import Tracer
from src.tracing.store import TraceStore


class TraceCallbackHandler(BaseCallbackHandler):
    """LangChain BaseCallbackHandler 实现，通过回调采集 trace 数据。

    注意：本 handler 的 start/end 回调中会直接读写 Tracer，
    Tracer 维护了 '当前 span' 栈，确保嵌套关系正确。
    """

    def __init__(
        self,
        store: TraceStore,
        session_id: str = "",
        session_turn: int = 0,
    ) -> None:
        super().__init__()
        self.store = store
        self.session_id = session_id
        self.session_turn = session_turn
        self._tracer: Tracer | None = None
        self._trace_id: str = ""
        self._run_id_to_span_id: dict[uuid.UUID, str] = {}
        self._has_root = False

    def _ensure_tracer(self) -> Tracer:
        if self._tracer is None:
            self._tracer = Tracer(session_id=self.session_id, session_turn=self.session_turn)
        return self._tracer

    def _extract_tokens(self, llm_output: dict | None) -> tuple[int, int]:
        if not llm_output:
            return 0, 0
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(usage, dict):
            return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        return 0, 0

    # ── Chain (用于 session_turn + agent_step) ──

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: dict[str, Any],
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        tracer = self._ensure_tracer()
        user_message = ""
        if not self._has_root:
            self._has_root = True
            # root chain → session_turn
            messages = inputs.get("messages", [])
            if messages:
                last = messages[-1] if isinstance(messages, list) else messages
                content = getattr(last, "content", "") if not isinstance(last, str) else last
                if isinstance(content, str):
                    user_message = content[:200]
            span_id = tracer.start_span(
                "session_turn", user_message=user_message,
            )
            self._trace_id = tracer._spans[span_id].trace_id
            self._run_id_to_span_id[run_id] = span_id
        else:
            span_id = tracer.start_span("agent_step")
            self._run_id_to_span_id[run_id] = span_id

    def on_chain_end(
        self, outputs: dict[str, Any],
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id)
            span = tracer._spans.get(span_id)
            if span:
                self.store.write_span(span)

    def on_chain_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                self.store.write_span(span)

    # ── LLM ──

    def on_llm_start(
        self, serialized: dict[str, Any], prompts: list[str],
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        name = serialized.get("name", "") if isinstance(serialized, dict) else ""
        tracer = self._tracer
        if not tracer:
            return
        span_id = tracer.start_span("llm_call", model=name or "unknown")
        self._run_id_to_span_id[run_id] = span_id

    def on_llm_end(
        self, response: Any,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if not span_id:
            return
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            input_t, output_t = self._extract_tokens(llm_output)
            span = tracer._spans.get(span_id)
            if span:
                span.input_tokens = input_t
                span.output_tokens = output_t
        tracer.end_span(span_id)
        span = tracer._spans.get(span_id)
        if span:
            self.store.write_span(span)

    def on_llm_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                self.store.write_span(span)

    # ── Tool ──

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str,
        *, run_id: uuid.UUID, parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        name = (serialized.get("name", "") if isinstance(serialized, dict)
                else getattr(serialized, "name", ""))
        span = tracer.start_span("tool_call", tool_name=name or "unknown", tool_input=input_str[:500])
        self._run_id_to_span_id[run_id] = span

    def on_tool_end(
        self, output: Any,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id)
            span = tracer._spans.get(span_id)
            if span:
                self.store.write_span(span)

    def on_tool_error(
        self, error: Exception,
        *, run_id: uuid.UUID, **kwargs: Any,
    ) -> Any:
        tracer = self._tracer
        if not tracer:
            return
        span_id = self._run_id_to_span_id.pop(run_id, None)
        if span_id:
            tracer.end_span(span_id, status="error", error_message=str(error))
            span = tracer._spans.get(span_id)
            if span:
                self.store.write_span(span)
```

- [ ] **Step 3: Run tests**

Run: `cd D:/learn/agent-learn/single-agent && .venv/Scripts/python -m pytest tests/test_tracing.py::TestTraceCallbackHandler -v`
Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add src/tracing/handler.py tests/test_tracing.py
git commit -m "feat(tracing): add TraceCallbackHandler for LangChain event collection"
```

---

### Task 5: api.py — 查询接口层

**Files:**
- Create: `src/tracing/api.py`
- Modify: `tests/test_tracing.py`

- [ ] **Step 1: Write api tests**

```python
from src.tracing.api import TracingAPI
from src.tracing.store import TraceStore
from src.tracing.models import SpanData


class TestTracingAPI:
    def test_get_trace_tree(self, tmp_path):
        store = TraceStore(db_path=str(tmp_path / "traces.db"))
        api = TracingAPI(store)
        parent = SpanData(span_type="session_turn", trace_id="t1", session_id="s1", user_message="hi")
        parent.end()
        parent.duration_ms = 1000
        store.write_span(parent)
        child = SpanData(
            span_type="llm_call", trace_id="t1", session_id="s1",
            parent_span_id=parent.span_id, model="deepseek-chat",
        )
        child.end()
        child.duration_ms = 500
        store.write_span(child)
        tree = api.get_trace_tree("t1")
        assert tree["trace_id"] == "t1"
        assert len(tree["spans"]) == 2
        assert tree["total_duration_ms"] == 1000
```

- [ ] **Step 2: Implement TracingAPI**

```python
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
            "total_tokens": root.get("input_tokens", 0) + root.get("output_tokens", 0),
            "user_message": root.get("user_message", ""),
        }

    def get_stats(self) -> dict[str, Any]:
        return self.store.get_stats()

    def get_daily_stats(self) -> list[dict[str, Any]]:
        return self.store.get_daily_stats()

    def get_traces_by_session(self, session_id: str) -> list[dict[str, Any]]:
        return self.store.get_traces_by_session(session_id)
```

- [ ] **Step 3: Run tests**

Run: `cd D:/learn/agent-learn/single-agent && .venv/Scripts/python -m pytest tests/test_tracing.py::TestTracingAPI -v`
Expected: 1 passed

- [ ] **Step 4: Commit**

```bash
git add src/tracing/api.py src/tracing/__init__.py tests/test_tracing.py
git commit -m "feat(tracing): add TracingAPI query interface"
```

---

### Task 6: 后端集成 — bridge.py + http_server.py

**Files:**
- Modify: `frontend/bridge.py` — Api 类新增 tracing 方法
- Modify: `frontend/http_server.py` — 新增路由 + 创建 TraceStore

- [ ] **Step 1: 修改 bridge.py 新增 tracing 方法**

在 `Api.__init__` 中新增 `tracing_api` 参数：

```python
# 在 __init__ 参数列表末尾添加
tracing_api: Any = None,
# 在 __init__ 方法体中添加
self._tracing_api = tracing_api
```

新增以下方法：

```python
# ── Tracing API ──

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

def get_trace_daily_stats(self) -> list[dict]:
    if self._tracing_api is None:
        return []
    return self._tracing_api.get_daily_stats()

def get_traces_by_session(self, session_id: str) -> list[dict]:
    if self._tracing_api is None:
        return []
    return self._tracing_api.get_traces_by_session(session_id)
```

- [ ] **Step 2: 修改 http_server.py — 创建 TraceStore + TraceCallbackHandler + 路由**

在 `init_agent` 中初始化 tracing：

```python
# 在 init_agent 中，创建 agent 之后添加：
from src.tracing.store import TraceStore
from src.tracing.api import TracingAPI

trace_store = TraceStore()
trace_store.cleanup()  # 启动时清理旧数据
tracing_api = TracingAPI(trace_store)
```

将 tracing_api 传入 Api 构造函数：

```python
_api = Api(
    root_dir=root_dir,
    agent=bundle.agent,
    memory_manager=memory_manager,
    settings=settings,
    memory_store=bundle.memory_store,
    tools=bundle.tools,
    tracing_api=tracing_api,  # 新增
)
```

为简化集成（避免修改所有聊天路径），采用以下策略注入 TraceCallbackHandler 到 chat 请求的 callbacks 中：

在 `post_chat_stream` 和 `post_chat` 中创建 handler 并传入 config：
（注意：这需要 thread_id 和 session_turn 从 state 获取）

```python
# 在 post_chat_stream 和 post_chat 中，获取 lock 后，创建 handler：
from src.tracing.handler import TraceCallbackHandler

# 获取当前 turn 数
trace_handler = None
try:
    state_snap = await _api.agent.aget_state(
        {"configurable": {"thread_id": thread_id}}
    )
    messages = list(state_snap.values.get("messages", []) or [])
    turn_count = sum(1 for m in messages if getattr(m, "type", "") == "human") + 1
    trace_handler = TraceCallbackHandler(
        trace_store, session_id=thread_id, session_turn=turn_count,
    )
except Exception:
    pass
```

然后在 config 中附加 callbacks。对于流式路径（post_chat_stream），config 需要包含 callbacks。

注意：由于 `agent.ainvoke` / `agent.astream_events` 的 config 参数在 `create_agent` 构造的 agent 中可能不支持 `callbacks` 直接传入，简化的方案是在 Api.chat 和 Api.chat_stream 方法内部处理。更好的方式是：让 Api 持有 trace_store，在 chat 方法中创建 handler。

实际实现方案：修改 `bridge.py` 的 `chat_stream` 和 `chat` 方法，传入 callbacks。

在 `bridge.py` 中：

```python
async def chat(self, thread_id: str, message: str) -> dict:
    # ... 现有代码 ...
    # 在调用 agent.ainvoke 前创建 handler
    callbacks = self._build_trace_callbacks(thread_id)
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100,
        "callbacks": callbacks,
    }
    # ...

async def chat_stream(self, thread_id: str, message: str, resume: bool | None = None):
    # ... 类似地添加 callbacks ...

def _build_trace_callbacks(self, thread_id: str) -> list:
    if self._tracing_api is None:
        return []
    from src.tracing.handler import TraceCallbackHandler
    try:
        import asyncio
        # 统计 turn
        messages = []
        try:
            state = asyncio.run(self._agent.aget_state(
                {"configurable": {"thread_id": thread_id}}
            ))
            messages = list(state.values.get("messages", []) or [])
        except Exception:
            pass
        turn = sum(1 for m in messages if getattr(m, "type", "") == "human") + 1
        store = self._tracing_api.store
        return [TraceCallbackHandler(store, session_id=thread_id, session_turn=turn)]
    except Exception:
        return []
```

注意 `_build_trace_callbacks` 用 `asyncio.run` 同步获取 state — 这需要在 chat 这个同步方法中工作。对于 `chat_stream` 异步方法，可以直接 await。

这里有个问题 — `chat` 是同步方法，但 `chat_stream` 是 async generator。让我在 `chat_stream` 中直接 await，在 `chat` 中使用 `asyncio.run`。

添加路由：

```python
# ── Tracing 路由 ──

async def get_traces(request: Request) -> Response:
    q = request.query.get("q", "")
    status = request.query.get("status", "")
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    return json_response(get_api().get_traces(q=q, status=status, limit=limit, offset=offset))

async def get_trace_detail(request: Request) -> Response:
    trace_id = request.match_info["trace_id"]
    return json_response(get_api().get_trace_detail(trace_id))

async def get_trace_spans(request: Request) -> Response:
    trace_id = request.match_info["trace_id"]
    return json_response(get_api().get_trace_spans(trace_id))

async def get_trace_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_stats())

async def get_trace_daily_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_daily_stats())

async def get_traces_by_session(request: Request) -> Response:
    session_id = request.match_info["session_id"]
    return json_response(get_api().get_traces_by_session(session_id))
```

注册路由：

```python
def setup_routes(app: web.Application) -> None:
    # ... 现有路由 ...
    app.router.add_route("GET", "/api/traces", get_traces)
    app.router.add_route("GET", "/api/traces/stats", get_trace_stats)
    app.router.add_route("GET", "/api/traces/stats/daily", get_trace_daily_stats)
    app.router.add_route("GET", "/api/traces/{trace_id}", get_trace_detail)
    app.router.add_route("GET", "/api/traces/{trace_id}/spans", get_trace_spans)
    app.router.add_route("GET", "/api/traces/sessions/{session_id}", get_traces_by_session)
```

- [ ] **Step 3: Commit**

```bash
git add frontend/bridge.py frontend/http_server.py
git commit -m "feat(tracing): integrate tracing API and routes into backend"
```

---

### Task 7: 前端 JS 集成 — browser-api.js + preload.js

**Files:**
- Modify: `frontend/browser-api.js`
- Modify: `frontend/electron/preload.js`

- [ ] **Step 1: 修改 browser-api.js 新增 tracing API 方法**

在 `window.api` 对象中添加：

```javascript
getTraces: (q, status, limit, offset) => {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  if (limit) params.set('limit', limit);
  if (offset) params.set('offset', offset);
  const qs = params.toString();
  return fetchJSON(`${BASE_URL}/api/traces${qs ? '?' + qs : ''}`);
},
getTraceDetail: (traceId) => fetchJSON(`${BASE_URL}/api/traces/${traceId}`),
getTraceSpans: (traceId) => fetchJSON(`${BASE_URL}/api/traces/${traceId}/spans`),
getTraceStats: () => fetchJSON(`${BASE_URL}/api/traces/stats`),
getTraceDailyStats: () => fetchJSON(`${BASE_URL}/api/traces/stats/daily`),
getTracesBySession: (sessionId) => fetchJSON(`${BASE_URL}/api/traces/sessions/${sessionId}`),
```

- [ ] **Step 2: 修改 preload.js 添加相同方法**

在 `api` 对象中添加相同的方法（函数体与 browser-api.js 一致，用 fetch + then）。

- [ ] **Step 3: Commit**

```bash
git add frontend/browser-api.js frontend/electron/preload.js
git commit -m "feat(tracing): add tracing API methods to frontend JS bridge"
```

---

### Task 8: 前端面板 — HTML + CSS + JS

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/styles.css`
- Modify: `frontend/app.js`

- [ ] **Step 1: 修改 index.html — 添加监控面板和菜单按钮**

在 ball mode hover menu 中添加监控按钮：

```html
<button class="menu-btn" data-panel="monitoring">&#128200; 监控</button>
```

在 panel mode 中添加 monitoring-panel（放在 skills-panel 之后）：

```html
<div id="monitoring-panel" class="panel hidden">
  <div class="panel-header">
    <h3>监控</h3>
    <span class="panel-subtitle">链路追踪与 Token 消耗</span>
    <button class="close-btn" data-close-panel title="关闭">&times;</button>
  </div>
  <div class="panel-body" id="monitoring-body">
    <!-- 由 js 动态渲染 -->
  </div>
</div>
```

同时更新 panel-mode 中的 allPanels 判断，加上 monitoring-panel。

- [ ] **Step 2: 添加监控面板样式到 styles.css**

```css
/* ── Monitoring Panel ────────────────────────── */
#monitoring-body {
  font-size: 13px;
}

.monitoring-tab-bar {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
}

.monitoring-tab {
  padding: 5px 16px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  background: #2a2a42;
  color: #8888bb;
  border: none;
  font-family: inherit;
  transition: all 120ms;
}

.monitoring-tab.active {
  background: #6c5ce7;
  color: #fff;
  font-weight: 600;
}

.monitoring-card {
  background: #242438;
  border: 1px solid #3a3a52;
  border-radius: 10px;
  padding: 14px;
  margin-bottom: 12px;
}

.monitoring-stat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 16px;
}

.monitoring-stat-box {
  background: #1e1e34;
  border: 1px solid #2e2e48;
  border-radius: 8px;
  padding: 10px 14px;
}

.monitoring-stat-label {
  font-size: 10px;
  color: #8888aa;
  letter-spacing: 0.3px;
  margin-bottom: 2px;
}

.monitoring-stat-value {
  font-size: 20px;
  font-weight: 700;
  color: #fff;
}

.monitoring-trace-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #1e1e34;
  border: 1px solid #2e2e48;
  cursor: pointer;
  transition: background 120ms;
}

.monitoring-trace-row:hover {
  background: #2a2a42;
}

.monitoring-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 4px;
}

.monitoring-badge.success {
  background: #1a3a2a;
  color: #4ade80;
  border: 1px solid #2a5a3a;
}

.monitoring-badge.error {
  background: #3a1a1a;
  color: #f87171;
  border: 1px solid #5a2a2a;
}

.monitoring-link {
  color: #a29bfe;
  font-size: 12px;
  cursor: pointer;
  font-weight: 600;
}

.monitoring-link:hover {
  color: #c4b8ff;
}

.monitoring-view {
  display: none;
}

.monitoring-view.active {
  display: block;
}

.monitoring-span-tree {
  margin-bottom: 4px;
}

.monitoring-span-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-top: 6px;
}

.monitoring-span-item.session {
  background: #2a1a3a;
  border: 1px solid #4a2a6a;
}

.monitoring-span-item.llm {
  background: #0a1a3a;
  border: 1px solid #1a3a6a;
}

.monitoring-span-item.step {
  background: #0a2a2a;
  border: 1px solid #1a4a4a;
}

.monitoring-span-item.tool {
  background: #2a2a1a;
  border: 1px solid #4a4a1a;
}

.monitoring-legend {
  display: flex;
  gap: 14px;
  margin-bottom: 12px;
  font-size: 11px;
  color: #888;
}

.monitoring-legend-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  vertical-align: middle;
  margin-right: 4px;
}

.monitoring-search-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #3a3a52;
  border-radius: 8px;
  background: #1a1a2e;
  color: #ccc;
  font-size: 12px;
  outline: none;
  font-family: inherit;
}

.monitoring-search-input:focus {
  border-color: #6c5ce7;
}

.monitoring-select {
  padding: 8px 12px;
  border: 1px solid #3a3a52;
  border-radius: 8px;
  background: #1a1a2e;
  color: #ccc;
  font-size: 12px;
  outline: none;
  font-family: inherit;
}

.monitoring-progress-bar {
  background: #2a2a42;
  border-radius: 4px;
  height: 8px;
}

.monitoring-progress-fill {
  background: linear-gradient(90deg, #a29bfe, #74b9ff);
  border-radius: 4px;
  height: 8px;
}
```

- [ ] **Step 3: 修改 app.js — 实现 setupMonitoringPanel**

在 `setupChatPanel` 函数后添加 `setupMonitoringPanel` 函数，同时在 `initPanelMode` 的 switch 中添加 monitoring case。

核心逻辑概述：

```javascript
async function setupMonitoringPanel() {
  // 1. 渲染监控面板 HTML 结构（选项卡 + 各视图容器）
  // 2. 加载总览数据（getTraceStats）
  // 3. 绑定选项卡切换事件
  // 4. 渲染近期 Trace 列表（getTraces）
}

async function loadTraces() {
  // 调用 window.api.getTraces(q, status) 渲染列表
}

async function showTraceDetail(traceId) {
  // 调用 window.api.getTraceDetail(traceId)
  // 渲染调用链路树
}

async function loadTokenStats() {
  // 调用 window.api.getTraceDailyStats()
  // 渲染 Token 分布
}

async function loadLatencyStats() {
  // 调用 window.api.getTraces() 聚合计算
  // 渲染延迟分布
}
```

具体实现代码在 app.js 中 `setupMonitoringPanel` 函数内动态渲染所有监控视图。完整实现参考 spec 中的 mockup 布局。

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/styles.css frontend/app.js
git commit -m "feat(tracing): add monitoring panel UI"
```

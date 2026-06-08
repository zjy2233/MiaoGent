"""TraceStore — SQLite 持久化层。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.tracing.models import SpanData


def _get_data_path(name: str) -> Path:
    """Get path in ~/.miaogent/ directory."""
    from pathlib import Path as _Path
    home = _Path.home() / ".miaogent"
    home.mkdir(parents=True, exist_ok=True)
    return home / name


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
        resolved = Path(db_path).resolve() if db_path else _get_data_path("traces.db")
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

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
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

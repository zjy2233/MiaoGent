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
    cache_hit_tokens INTEGER DEFAULT 0,
    cache_miss_tokens INTEGER DEFAULT 0,
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
                # 数据库迁移：为已有数据库添加 cache 列
                for col in ("cache_hit_tokens", "cache_miss_tokens"):
                    try:
                        conn.execute(
                            f"ALTER TABLE spans ADD COLUMN {col} INTEGER DEFAULT 0"
                        )
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise
                conn.commit()
                self._migrate_schema(conn)
                conn.commit()
            finally:
                conn.close()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Add new columns if they don't exist (idempotent)."""
        migrations = [
            "ALTER TABLE spans ADD COLUMN llm_input TEXT DEFAULT ''",
            "ALTER TABLE spans ADD COLUMN llm_output TEXT DEFAULT ''",
            "ALTER TABLE spans ADD COLUMN tool_output TEXT DEFAULT ''",
        ]
        existing = {row[1] for row in conn.execute("PRAGMA table_info(spans)")}
        for sql in migrations:
            col_name = sql.split("ADD COLUMN ")[1].split(" ")[0]
            if col_name not in existing:
                conn.execute(sql)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def write_span(self, span: SpanData) -> None:
        cols = [
            "span_id", "parent_span_id", "trace_id", "session_id", "session_turn",
            "span_type", "model", "input_tokens", "output_tokens",
            "cache_hit_tokens", "cache_miss_tokens", "tool_name",
            "tool_input", "status", "error_message", "started_at", "ended_at",
            "duration_ms", "user_message",
            "llm_input", "llm_output", "tool_output",
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
                    "span_type", "model", "input_tokens", "output_tokens",
                    "cache_hit_tokens", "cache_miss_tokens", "tool_name",
                    "tool_input", "status", "error_message", "started_at", "ended_at",
                    "duration_ms", "user_message",
                    "llm_input", "llm_output", "tool_output",
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
                rows = [self._row_to_dict(row) for row in cursor.fetchall()]
                # 从所有 span（llm_call 等）汇总每个 trace 的 token 数
                if rows:
                    trace_ids = [r["trace_id"] for r in rows]
                    placeholders = ", ".join("?" for _ in trace_ids)
                    token_rows = conn.execute(
                        f"SELECT trace_id, "
                        f"COALESCE(SUM(input_tokens), 0) as total_input, "
                        f"COALESCE(SUM(output_tokens), 0) as total_output "
                        f"FROM spans WHERE trace_id IN ({placeholders}) "
                        f"GROUP BY trace_id",
                        trace_ids,
                    ).fetchall()
                    token_map = {r[0]: (r[1], r[2]) for r in token_rows}
                    for r in rows:
                        inp, out = token_map.get(r["trace_id"], (0, 0))
                        r["input_tokens"] = inp
                        r["output_tokens"] = out
                return rows
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
                rows = [self._row_to_dict(row) for row in cursor.fetchall()]
                if rows:
                    trace_ids = [r["trace_id"] for r in rows]
                    placeholders = ", ".join("?" for _ in trace_ids)
                    token_rows = conn.execute(
                        f"SELECT trace_id, "
                        f"COALESCE(SUM(input_tokens), 0) as total_input, "
                        f"COALESCE(SUM(output_tokens), 0) as total_output "
                        f"FROM spans WHERE trace_id IN ({placeholders}) "
                        f"GROUP BY trace_id",
                        trace_ids,
                    ).fetchall()
                    token_map = {r[0]: (r[1], r[2]) for r in token_rows}
                    for r in rows:
                        inp, out = token_map.get(r["trace_id"], (0, 0))
                        r["input_tokens"] = inp
                        r["output_tokens"] = out
                return rows
            finally:
                conn.close()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                # session_turn count, avg duration, error count
                row = conn.execute(
                    "SELECT COUNT(*) as total_traces, "
                    "COALESCE(AVG(duration_ms), 0) as avg_duration_ms, "
                    "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_count "
                    "FROM spans WHERE span_type='session_turn' AND started_at >= ?",
                    (today,),
                ).fetchone()
                # token sums from ALL spans today (token data lives on llm_call spans)
                row_t = conn.execute(
                    "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
                    "COALESCE(SUM(cache_hit_tokens), 0), COALESCE(SUM(cache_miss_tokens), 0) "
                    "FROM spans WHERE started_at >= ?",
                    (today,),
                ).fetchone()
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                row_y = conn.execute(
                    "SELECT COALESCE(SUM(input_tokens+output_tokens), 0) "
                    "FROM spans WHERE started_at >= ? AND started_at < ?",
                    (yesterday, today),
                ).fetchone()
                return {
                    "total_traces": row[0],
                    "total_input_tokens": row_t[0],
                    "total_output_tokens": row_t[1],
                    "total_cache_hit_tokens": row_t[2],
                    "total_cache_miss_tokens": row_t[3],
                    "total_tokens": row_t[0] + row_t[1],
                    "avg_duration_ms": round(row[1], 1),
                    "error_count": row[2],
                    "error_rate": round(row[2] / row[0] * 100, 1) if row[0] > 0 else 0,
                    "yesterday_tokens": int(row_y[0]) if row_y[0] else 0,
                }
            finally:
                conn.close()

    def get_daily_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT d.day, d.count, "
                    "COALESCE(t.token_sum, 0) as total_tokens "
                    "FROM ("
                    "  SELECT DATE(started_at) as day, COUNT(*) as count "
                    "  FROM spans WHERE span_type='session_turn' "
                    "  GROUP BY DATE(started_at)"
                    ") d "
                    "LEFT JOIN ("
                    "  SELECT DATE(started_at) as day, "
                    "  COALESCE(SUM(input_tokens+output_tokens), 0) as token_sum "
                    "  FROM spans GROUP BY DATE(started_at)"
                    ") t ON d.day = t.day "
                    "ORDER BY d.day DESC LIMIT 14"
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

    def get_trace_count(self, q: str = "", status: str = "") -> int:
        """Return total count of session_turn traces matching filters."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conditions = ["span_type = 'session_turn'"]
                params: list[Any] = []
                if q:
                    conditions.append("(user_message LIKE ? OR trace_id LIKE ?)")
                    params.extend([f"%{q}%", f"%{q}%"])
                if status:
                    conditions.append("status = ?")
                    params.append(status)
                where = " AND ".join(conditions)
                row = conn.execute(
                    f"SELECT COUNT(*) FROM spans WHERE {where}", params
                ).fetchone()
                return row[0] if row else 0
            finally:
                conn.close()

    def get_token_top_traces(self, days: int = 3, limit: int = 10) -> list[dict[str, Any]]:
        """Return top N traces by total tokens in last N days, sorted by tokens DESC."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
                total_expr = (
                    "COALESCE(SUM(t.input_tokens), 0) + COALESCE(SUM(t.output_tokens), 0)"
                )
                rows = conn.execute(
                    "SELECT s.trace_id, s.session_id, s.user_message, s.started_at, s.status, "
                    "  s.duration_ms, "
                    "  COALESCE(SUM(t.input_tokens), 0) as input_tokens, "
                    "  COALESCE(SUM(t.output_tokens), 0) as output_tokens "
                    "FROM spans s "
                    "JOIN spans t ON t.trace_id = s.trace_id "
                    "WHERE s.span_type = 'session_turn' AND s.started_at >= ? "
                    "GROUP BY s.trace_id "
                    f"ORDER BY {total_expr} DESC "
                    "LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
                return [self._row_to_dict(row) for row in rows]
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

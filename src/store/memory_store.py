"""MemoryStore：结构化跨会话记忆系统。

三层记忆架构：
1. 核心记忆 (Core Memory) — 始终注入上下文，存储在 ``data/memory.json``
   - identity / environment / preferences / projects / facts
2. 工作记忆 (Working Memory) — 自动提取写入 SQLite，跨会话持久化
3. 存档记忆 (Archival Memory) — 已有 checkpointer 全量历史

设计参考：
- ChatGPT Memory 的显式事实表 + 预计算注入（零检索延迟）
- Mem0 的 confidence 分层（explicit > discovered > inferred）
- thane-ai-agent 的 upsert 去重 + source 追踪
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── 数据模型 ──────────────────────────────────────────────────────────────

CONFIDENCE_ORDER = {"explicit": 3, "discovered": 2, "inferred": 1}

CORE_CATEGORIES = ("identity", "environment", "preferences", "projects", "facts")

DEFAULT_CORE_MEMORY: dict[str, dict[str, dict]] = {
    cat: {} for cat in CORE_CATEGORIES
}


@dataclass
class MemoryEntry:
    """工作记忆条目。"""

    id: str = ""
    category: str = ""
    key: str = ""
    value: str = ""
    confidence: str = "discovered"
    source_session: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "source_session": self.source_session,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── MemoryStore ────────────────────────────────────────────────────────────


class MemoryStore:
    """结构化记忆存储。

    - 核心记忆：JSON 文件，人类可编辑
    - 工作记忆：SQLite 表，自动去重 + 冲突解决
    """

    def __init__(
        self,
        memory_path: str | Path = "data/memory.json",
        db_path: str | Path = "data/memory.db",
    ) -> None:
        self._memory_path = Path(memory_path)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._async_lock = asyncio.Lock()
        self._ensure_db()

    # ── 数据库初始化 ────────────────────────────────────────────────────

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS working_memories (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence TEXT NOT NULL
                        CHECK(confidence IN ('explicit','discovered','inferred')),
                    source_session TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category, key)
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ══════════════════════════════════════════════════════════════════
    #  核心记忆 (Core Memory)
    # ══════════════════════════════════════════════════════════════════

    def load_core_memory(self) -> dict[str, dict]:
        """加载完整核心记忆。"""
        if not self._memory_path.exists():
            return dict(DEFAULT_CORE_MEMORY)
        try:
            data = json.loads(self._memory_path.read_text(encoding="utf-8"))
            core = data.get("core", {})
            # 确保所有 category 都存在
            for cat in CORE_CATEGORIES:
                core.setdefault(cat, {})
            return core
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_CORE_MEMORY)

    def save_core_memory(self, core: dict[str, dict]) -> None:
        """原子写入核心记忆（先写临时文件再 rename，防止写入崩溃导致文件损坏）。"""
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 2,
            "updated_at": _now_iso(),
            "core": core,
        }
        tmp_path = self._memory_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # 原子替换（Windows 上 os.replace 是 near-atomic）
        os.replace(str(tmp_path), str(self._memory_path))

    async def update_core_category(
        self, category: str, updates: dict[str, dict], source: str = "discovered"
    ) -> None:
        """更新核心记忆的某个分类（合并模式，带并发保护）。

        ``updates`` 格式: ``{"key": {"value": "...", "source": "..."}}``
        """
        async with self._async_lock:
            if category not in CORE_CATEGORIES:
                return
            core = self.load_core_memory()
            existing = core.setdefault(category, {})
            for key, entry in updates.items():
                if not isinstance(entry, dict):
                    entry = {"value": entry, "source": source}
                entry.setdefault("source", source)
                existing_entry = existing.get(key, {})
                # 冲突解决：explicit > discovered > inferred
                if (
                    CONFIDENCE_ORDER.get(existing_entry.get("source", ""), 0)
                    > CONFIDENCE_ORDER.get(entry.get("source", ""), 0)
                ):
                    continue
                existing[key] = entry
            self.save_core_memory(core)

    def get_formatted_core_memory(self) -> str:
        """格式化为 LLM 可读的文本。"""
        core = self.load_core_memory()
        lines: list[str] = []
        category_labels = {
            "identity": "身份信息",
            "environment": "环境信息",
            "preferences": "用户偏好",
            "projects": "项目上下文",
            "facts": "已知事实",
        }
        for cat in CORE_CATEGORIES:
            entries = core.get(cat, {})
            if not entries:
                continue
            lines.append(f"\n[{category_labels.get(cat, cat)}]")
            for key, entry in entries.items():
                value = entry.get("value", "") if isinstance(entry, dict) else entry
                source = entry.get("source", "") if isinstance(entry, dict) else ""
                source_tag = f" ({source})" if source else ""
                lines.append(f"  - {key}: {value}{source_tag}")
        return "\n".join(lines).strip()

    # ══════════════════════════════════════════════════════════════════
    #  工作记忆 (Working Memory)
    # ══════════════════════════════════════════════════════════════════

    def add_working_memory(
        self,
        category: str,
        key: str,
        value: str,
        confidence: str = "discovered",
        source_session: str = "",
    ) -> bool:
        """添加一条工作记忆。已存在同 category+key 时按 confidence 决定是否覆盖。

        Returns:
            True=写入/更新成功, False=被更高 confidence 的已有记录拒绝
        """
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id, confidence FROM working_memories WHERE category=? AND key=?",
                (category, key),
            ).fetchone()

            now = _now_iso()
            if existing:
                # 冲突解决：新记录 confidence 必须 >= 已有
                if CONFIDENCE_ORDER.get(confidence, 0) < CONFIDENCE_ORDER.get(
                    existing["confidence"], 0
                ):
                    return False
                conn.execute(
                    """UPDATE working_memories
                       SET value=?, confidence=?, source_session=?, updated_at=?
                       WHERE category=? AND key=?""",
                    (value, confidence, source_session, now, category, key),
                )
            else:
                conn.execute(
                    """INSERT INTO working_memories (id, category, key, value, confidence, source_session, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (_new_id(), category, key, value, confidence, source_session, now, now),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def merge_working_memory(
        self,
        updates: list[dict[str, str]],
        source_session: str = "",
    ) -> int:
        """批量合并工作记忆。

        Args:
            updates: ``[{"category":..., "key":..., "value":..., "confidence":...}, ...]``
            source_session: 来源会话 ID

        Returns:
            成功写入/更新的记录数
        """
        count = 0
        for entry in updates:
            ok = self.add_working_memory(
                category=entry.get("category", "facts"),
                key=entry["key"],
                value=entry["value"],
                confidence=entry.get("confidence", "discovered"),
                source_session=source_session,
            )
            if ok:
                count += 1
        return count

    def get_working_memories(
        self, category: str | None = None, limit: int = 50
    ) -> list[dict[str, str]]:
        """获取工作记忆，按更新时间倒序。"""
        conn = self._conn()
        try:
            if category:
                rows = conn.execute(
                    "SELECT * FROM working_memories WHERE category=? ORDER BY updated_at DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM working_memories ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_working_memory(self, memory_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM working_memories WHERE id=?", (memory_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_all_formatted(self) -> str:
        """获取格式化的完整记忆（核心 + 工作），用于注入 LLM 上下文。"""
        parts = []
        core_text = self.get_formatted_core_memory()
        if core_text:
            parts.append(core_text)

        wm = self.get_working_memories()
        if wm:
            wm_lines = ["\n[从对话中学到的信息]"]
            seen_keys: set[str] = set()
            for entry in wm:
                key = entry.get("key", "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                cat_label = entry.get("category", "?")
                val = entry.get("value", "")
                conf = entry.get("confidence", "")
                conf_tag = f" ({conf})" if conf else ""
                wm_lines.append(f"  - [{cat_label}] {key}: {val}{conf_tag}")
            parts.append("\n".join(wm_lines))

        return "\n".join(parts).strip()

    # ── 统计 ──────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        core = self.load_core_memory()
        core_count = sum(len(v) for v in core.values())
        conn = self._conn()
        try:
            wm_count = conn.execute("SELECT COUNT(*) AS c FROM working_memories").fetchone()["c"]
            by_category = {
                r["category"]: r["c"]
                for r in conn.execute(
                    "SELECT category, COUNT(*) AS c FROM working_memories GROUP BY category"
                ).fetchall()
            }
        finally:
            conn.close()
        return {
            "core_memory_entries": core_count,
            "working_memories_total": wm_count,
            "working_memories_by_category": by_category,
        }

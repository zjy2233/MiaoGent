"""KnowledgeConsolidator：将零散 facts 定期归并为结构化知识。

管线流程：
  收集 raw facts → 单次 LLM 聚类+总结 → 写回 MemoryStore

核心约束：
  - 零额外依赖，全部复用 MemoryStore
  - 单次 LLM 调用完成聚类和总结（统一 prompt）
  - consolidation_at + consolidation_round 双字段幂等保护
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.store.memory_store import MemoryStore

logger = logging.getLogger(__name__)

# ── 预算控制 ──────────────────────────────────────────────────────────────

MAX_FACTS_PER_ROUND = 50          # 单次最多归并 50 条 facts
TRIGGER_THRESHOLD = 30            # raw facts > 30 触发归并
MAX_ROUNDS_PER_DAY = 5            # 每日最多 5 轮
MIN_FACTS_PER_ROUND = 5           # 低于 5 条不归并（性价比低）
MAX_CK_ENTRIES = 100              # consolidated_knowledge 总条目上限
LOW_CONFIDENCE_THRESHOLD = 0.3    # 低于此值不生成知识，直接归档 facts

# Token 预算（基于字符的粗略估算，~3 chars/token 中英混合）
MAX_INPUT_CHARS = 7500            # ≈ 2500 tokens
MAX_OUTPUT_CHARS = 2400           # ≈ 800 tokens

# 冲突检测：主题词重叠比例阈值
TOPIC_OVERLAP_THRESHOLD = 0.4     # 超过此比例认为新旧知识冲突，旧条目标记 superseded

# ── LLM Prompt ────────────────────────────────────────────────────────────

CONSOLIDATION_PROMPT = """你是一个知识归纳助手。请将以下 facts 按主题分组，每组生成一条结构化总结。
与已有的已归纳知识重复的主题可以跳过（不要重复生成）。

要求：
1. 每个分组必须包含至少 2 条 facts（单个孤立 fact 不归纳，留待后续）
2. 总结应精炼且信息完整，保留具体细节
3. 如果所有 facts 都与已有知识重复，返回空数组 []
4. 置信度 (confidence) 评分规则：0.9=完全确定, 0.7=合理推断, 0.5=推测

已有已归纳知识：
{existing_knowledge}

待归并 facts（每条格式：索引. 内容）：
{facts_text}

以 JSON 格式返回（严格格式，不要 markdown 代码块，不要额外文字）：
[{{"topic": "主题名", "summary": "总结内容", "source_indices": [0, 1], "confidence": 0.9}}]
"""


class KnowledgeConsolidator:
    """归并 raw facts 为结构化知识。"""

    def __init__(
        self,
        llm: BaseChatModel,
        memory_store: MemoryStore,
    ) -> None:
        self._llm = llm
        self._store = memory_store
        self._round_id: int = 0

    async def consolidate(self) -> dict[str, Any]:
        """执行一轮知识归并。

        Returns:
            {"consolidated": bool, "round_id": int, "count": int,
             "archived_count": int, "skip_reason": str|None}
        """
        result: dict[str, Any] = {
            "consolidated": False,
            "round_id": 0,
            "count": 0,
            "archived_count": 0,
            "skip_reason": None,
        }

        # Step 1: 收集 raw facts（初始读取，用于后续 LLM 调用）
        initial_facts = self._store.get_raw_facts(limit=MAX_FACTS_PER_ROUND)
        if not initial_facts:
            result["skip_reason"] = "no_raw_facts"
            return result

        if len(initial_facts) < MIN_FACTS_PER_ROUND:
            result["skip_reason"] = f"too_few_facts ({len(initial_facts)} < {MIN_FACTS_PER_ROUND})"
            return result

        # 频率控制：每日归并次数上限（初始检查）
        today_count = self._store.count_today_consolidations()
        if today_count >= MAX_ROUNDS_PER_DAY:
            result["skip_reason"] = f"daily_limit_reached ({today_count}/{MAX_ROUNDS_PER_DAY})"
            return result

        # Step 2: LLM 聚类 + 总结（受 token 预算约束）
        try:
            grouped = await self._llm_consolidate(initial_facts)
        except Exception as exc:
            logger.warning("consolidate: LLM call failed: %s", exc)
            result["skip_reason"] = f"llm_error: {exc}"
            return result

        if not grouped:
            result["skip_reason"] = "llm_no_output"
            return result

        # Step 3: 写入 + 状态更新（单连接+单事务，原子提交）
        async with self._store._async_lock:
            # 在锁内重读 facts，检测是否已被其他并发归并处理
            fresh_facts = self._store.get_raw_facts(limit=MAX_FACTS_PER_ROUND)
            original_ids = {f["id"] for f in initial_facts}
            fresh_ids = {f["id"] for f in fresh_facts}
            remaining = original_ids & fresh_ids
            if not remaining:
                result["skip_reason"] = "already_consolidated_by_another_round"
                return result

            # 重读后的频率检查（防止并发消耗配额）
            if self._store.count_today_consolidations() >= MAX_ROUNDS_PER_DAY:
                result["skip_reason"] = "daily_limit_reached_under_lock"
                return result

            self._round_id = int(time.time())
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")

            # 使用单连接执行所有写操作
            conn = self._store._conn()
            try:
                # 3a. 低置信度条目归档
                low_conf_ids: list[str] = []
                keep_groups: list[dict] = []
                for g in grouped:
                    src_indices = g.get("source_indices", [])
                    conf = g.get("confidence", 0.0)
                    if conf < LOW_CONFIDENCE_THRESHOLD:
                        for idx in src_indices:
                            if idx < len(initial_facts) and initial_facts[idx]["id"] in remaining:
                                low_conf_ids.append(initial_facts[idx]["id"])
                        continue
                    keep_groups.append(g)

                if low_conf_ids:
                    _mark_facts_state_on_conn(conn, low_conf_ids, state="archived")
                    result["archived_count"] = len(low_conf_ids)

                # 3b. 检查 consolidated_knowledge 总条目上限
                _enforce_entry_limit_on_conn(conn)

                # 3c. 冲突解决：新知识覆盖旧知识
                superseded_count = 0
                ck_entries: list[dict] = []
                all_fact_ids: set[str] = set()

                for g in keep_groups:
                    src_indices = g.get("source_indices", [])
                    fact_ids = [initial_facts[i]["id"] for i in src_indices
                                if i < len(initial_facts) and initial_facts[i]["id"] in remaining]
                    all_fact_ids.update(fact_ids)

                    # 检查是否与已有知识冲突（基于当前 conn 读）
                    sup_ids = _resolve_conflicts_on_conn(conn, g["topic"])
                    superseded_count += len(sup_ids)

                    ck_entries.append({
                        "id": f"know_{_new_id()}",
                        "topic": g["topic"],
                        "content": g["summary"],
                        "source_ids": fact_ids,
                        "confidence": g.get("confidence", 0.7),
                        "status": "active",
                        "created_at": now,
                    })

                if ck_entries:
                    _save_ck_on_conn(conn, ck_entries)
                    result["count"] = len(ck_entries)
                    result["superseded_count"] = superseded_count

                    # 3d. 标记已归并 facts
                    if all_fact_ids:
                        _mark_facts_state_on_conn(conn, list(all_fact_ids), state="consolidated", round_id=self._round_id)

                conn.commit()
                result["consolidated"] = True
                result["round_id"] = self._round_id
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            return result

    async def _llm_consolidate(self, facts: list[dict]) -> list[dict] | None:
        """调用 LLM 进行聚类和总结（受 token 预算约束）。

        Args:
            facts: get_raw_facts() 返回的 fact 列表

        Returns:
            [{"topic": "...", "summary": "...", "source_indices": [...], "confidence": 0.x}, ...]
        """
        # 已有知识（用于去重参考）
        existing = self._store.get_consolidated_knowledge(status="active")
        existing_text = "\n".join(
            f"- {e['topic']}: {e['content']}" for e in existing
        ) if existing else "（无）"

        # 预算控制：截断已有知识到 ~2000 chars（≈ 700 tokens）
        if len(existing_text) > 2000:
            existing_text = existing_text[:2000] + "..."

        # 格式化 facts
        facts_lines = [
            f"{i}. [{f.get('category', '?')}] {f.get('key', '')}: {f.get('value', '')}"
            for i, f in enumerate(facts)
        ]
        facts_text = "\n".join(facts_lines)

        # 预算控制：若 facts_text 超过 MAX_INPUT_CHARS 则截断尾部
        if len(facts_text) > MAX_INPUT_CHARS:
            truncated = facts_lines[:20]  # 至少保留 20 条
            facts_text = "\n".join(truncated)
            if len(facts_text) > MAX_INPUT_CHARS:
                facts_text = facts_text[:MAX_INPUT_CHARS] + "..."

        prompt = CONSOLIDATION_PROMPT.format(
            existing_knowledge=existing_text,
            facts_text=facts_text,
        )

        result = await self._llm.ainvoke(prompt)
        text = _extract_text(result.content)

        # 输出预算控制：截断过长输出
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS]

        return _parse_json_output(text)

    # _enforce_entry_limit 和 _resolve_conflicts 已改为模块级函数
    # `consolidate()` 使用 `_enforce_entry_limit_on_conn` / `_resolve_conflicts_on_conn`


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """生成短随机 ID。"""
    import uuid
    return uuid.uuid4().hex[:8]


def _tokenize_topic(text: str) -> set[str]:
    """将文本切分为词集合用于重叠检测。

    中文按单字切分（单个字已经包含足够语义），英文按空格切分为单词。
    """
    import re
    # 提取所有中文单字
    chars = set(re.findall(r'[\u4e00-\u9fff]', text))
    # 提取英文单词（长度 >= 2，过滤单字母停用词）
    words = {w.lower() for w in re.findall(r'[a-zA-Z]+', text) if len(w) >= 2}
    return chars | words


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict)).strip()
    return str(content).strip()


# ── 基于现有连接的数据库操作（供 consolidate() 单事务使用） ──────────


def _enforce_entry_limit_on_conn(conn: sqlite3.Connection) -> None:
    """保证 consolidated_knowledge 总条目不超过上限，超限则淘汰最旧（使用已有连接）。"""
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM consolidated_knowledge WHERE status='active'"
    ).fetchone()["c"]
    if total > MAX_CK_ENTRIES:
        excess = total - MAX_CK_ENTRIES
        conn.execute(
            """UPDATE consolidated_knowledge SET status='archived'
               WHERE id IN (
                   SELECT id FROM consolidated_knowledge
                   WHERE status='active'
                   ORDER BY updated_at ASC LIMIT ?
               )""",
            (excess,),
        )


def _resolve_conflicts_on_conn(conn: sqlite3.Connection, new_topic: str) -> list[str]:
    """检测新知识与已有知识是否冲突，使用已有连接。

    Returns:
        被标记为 superseded 的条目 ID 列表。
    """
    rows = conn.execute(
        "SELECT id, topic FROM consolidated_knowledge WHERE status='active'"
    ).fetchall()

    new_words = _tokenize_topic(new_topic)
    if not new_words:
        return []

    superseded: list[str] = []
    for row in rows:
        old_words = _tokenize_topic(row["topic"])
        if not old_words:
            continue
        overlap = len(new_words & old_words)
        base = min(len(new_words), len(old_words))
        if base == 0:
            continue
        ratio = overlap / base
        if ratio >= TOPIC_OVERLAP_THRESHOLD:
            superseded.append(row["id"])
            conn.execute(
                "UPDATE consolidated_knowledge SET status='superseded', updated_at=? WHERE id=?",
                (_now_iso(), row["id"]),
            )
    return superseded


def _save_ck_on_conn(conn: sqlite3.Connection, entries: list[dict]) -> None:
    """批量写入归并知识条目（使用已有连接）。"""
    now = _now_iso()
    for entry in entries:
        eid = entry.get("id", _new_id())
        conn.execute(
            """INSERT OR REPLACE INTO consolidated_knowledge
               (id, topic, content, source_ids, confidence, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid,
                entry["topic"],
                entry["content"],
                json.dumps(entry.get("source_ids", []), ensure_ascii=False),
                entry.get("confidence", 0.0),
                entry.get("status", "active"),
                entry.get("created_at", now),
                now,
            ),
        )


def _mark_facts_state_on_conn(
    conn: sqlite3.Connection,
    fact_ids: list[str],
    state: str = "consolidated",
    round_id: int | None = None,
) -> None:
    """批量标记 facts 状态（使用已有连接）。"""
    if not fact_ids:
        return
    placeholders = ",".join("?" for _ in fact_ids)
    now = _now_iso()
    if state == "archived":
        conn.execute(
            f"UPDATE working_memories SET status='archived', updated_at=? WHERE id IN ({placeholders})",
            (now, *fact_ids),
        )
    else:
        round_val = round_id or int(datetime.now(timezone.utc).timestamp())
        conn.execute(
            f"""UPDATE working_memories
                SET status=?, consolidated_at=?, consolidation_round=?, updated_at=?
                WHERE id IN ({placeholders})""",
            (state, now, round_val, now, *fact_ids),
        )


def _parse_json_output(text: str) -> list[dict] | None:
    """从 LLM 输出中解析 JSON 数组。"""
    import re

    if not text:
        return None

    # 去掉 markdown 代码块标记
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

    # 找第一个 [ 和最后一个 ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list):
        return None

    # 验证每条记录
    validated = []
    for item in data:
        if isinstance(item, dict) and item.get("topic") and item.get("summary"):
            validated.append(item)
    return validated if validated else None

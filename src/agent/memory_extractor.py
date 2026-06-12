"""MemoryExtractor：从对话中自动提取结构化记忆。

管线流程：
  消息片段 → 分类门控(heuristic) → LLM 提取 → JSON 解析 → 归并入 MemoryStore

设计参考：
- thane-ai-agent 的分类门控：跳过 50-70% 的低价值对话，控制成本
- Mem0 的 ADD-only 提取 + 结构化输出
- ChatGPT Memory 的显式事实分类
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from src.core.utils import content_str
from src.store.memory_store import MemoryStore

# ── 提取提示词 ──────────────────────────────────────────────────────────

EXTRACT_MEMORY_PROMPT = """从以下对话片段中提取关于用户的 **确定性事实**。不要猜测，不要编造。

## 提取分类

| 分类 | 提取内容 | 示例 |
|------|---------|------|
| identity | 身份信息（姓名、职业、城市、公司等） | {{"name": "张三", "occupation": "工程师"}} |
| environment | 环境信息（操作系统、桌面路径、编辑器、工具等） | {{"os": "Windows 11", "desktop_path": "C:\\Users\\xxx\\Desktop"}} |
| preferences | 偏好（语气、格式、习惯等） | {{"response_style": "简洁中文"}} |
| projects | 项目与任务 | {{"current_project": "记忆系统重构"}} |
| facts | 其他有用事实 | [{{"key": "pet", "value": "养了一只猫叫咪咪"}}] |

## 规则

1. **只提取用户明确说出的或可以合理推断的事实**
2. 忽略：问候语、普通问答；**不要忽略工具执行结果和错误消息**——工具失败、搜索超时、权限错误等也值得提取（例如 "web_search 超时" 可提取为 facts）
3. 如果某分类无新信息，返回空对象 `{{}}` 或空数组 `[]`
4. **不要返回不在上述分类中的内容**

已有记忆（避免重复提取）：
{existing_summary}

对话内容：
{messages}

以 JSON 格式返回，严格按此结构（不要加 markdown 代码块标记，不要加额外文字）：
{{"identity": {{}}, "environment": {{}}, "preferences": {{}}, "projects": {{}}, "facts": []}}
"""


# ── 分类门控 ─────────────────────────────────────────────────────────────

_SKIP_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*(你好|hi|hello|早上好|晚上好|下午好|再见|bye)\s*$",
        r"现在几点",
        r"今天天气",
    ]
]

_MIN_HUMAN_MSGS = 2
_MIN_TOTAL_CHARS = 80


def _classify_gate(messages: list[BaseMessage]) -> bool:
    """启发式分类门控：判断这段对话是否值得提取。

    Returns:
        True = 值得提取, False = 跳过
    """
    human_msgs = [m for m in messages if m.type == "human"]
    if len(human_msgs) < _MIN_HUMAN_MSGS:
        return False

    total_chars = sum(len(content_str(m.content)) for m in messages)
    if total_chars < _MIN_TOTAL_CHARS:
        return False

    # 检查是否只有简单问候
    text = " ".join(content_str(m.content).strip() for m in human_msgs)
    if any(p.search(text) for p in _SKIP_PATTERNS) and total_chars < 150:
        return False

    return True


# ── 提取器 ────────────────────────────────────────────────────────────────


class MemoryExtractor:
    """从对话中提取结构化记忆。

    用法::
        extractor = MemoryExtractor(llm, store)
        extractor.extract_from_messages(messages, source_session="...")
    """

    def __init__(
        self,
        llm: BaseChatModel,
        store: MemoryStore,
        max_msgs: int = 20,
    ) -> None:
        self._llm = llm
        self._store = store
        self._max_msgs = max_msgs

    async def extract_from_messages_async(
        self,
        messages: list[BaseMessage],
        source_session: str = "",
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """从消息中提取记忆并归入 MemoryStore（异步版本）。

        Args:
            messages: 对话消息列表
            source_session: 来源会话 ID
            force: 为 True 时跳过分类门控

        Returns:
            {"extracted": bool, "categories": [...], "count": int, "error": str|None}
        """
        if not messages:
            return {"extracted": False, "categories": [], "count": 0}

        # 1. 分类门控
        if not force and not _classify_gate(messages):
            return {"extracted": False, "categories": [], "count": 0}

        # 2. LLM 提取
        try:
            facts = await self._llm_extract_async(messages)
        except Exception as exc:
            return {"extracted": False, "categories": [], "count": 0, "error": str(exc)}

        if not facts:
            return {"extracted": False, "categories": [], "count": 0}

        # 3. 归并入核心记忆
        updated_categories: list[str] = []
        for cat in ("identity", "environment", "preferences", "projects"):
            updates = facts.get(cat)
            if updates and isinstance(updates, dict):
                await self._store.update_core_category(cat, updates, source="discovered")
                updated_categories.append(cat)

        # 4. 归并入工作记忆（facts 数组）
        wm_updates: list[dict[str, str]] = []
        for item in facts.get("facts", []):
            if isinstance(item, dict) and item.get("key") and item.get("value"):
                wm_updates.append({
                    "category": "facts",
                    "key": item["key"],
                    "value": item["value"],
                    "confidence": "discovered",
                })
        total = self._store.merge_working_memory(wm_updates, source_session=source_session)

        return {
            "extracted": True,
            "categories": updated_categories,
            "count": total + len(updated_categories),
        }

    async def _llm_extract_async(self, messages: list[BaseMessage]) -> dict | None:
        """调用 LLM 从消息中提取结构化事实（异步）。"""
        existing = self._store.get_formatted_core_memory()
        formatted = _format_messages(messages, max_msgs=self._max_msgs)

        prompt = EXTRACT_MEMORY_PROMPT.format(
            existing_summary=existing or "（无）",
            messages=formatted,
        )

        result = await self._llm.ainvoke(prompt)
        text = content_str(result.content)
        if not text:
            return None

        # 尝试从文本中提取 JSON
        json_str = _extract_json(text)
        if not json_str:
            return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        # 清理：去掉空分类
        cleaned: dict[str, Any] = {}
        for cat in ("identity", "environment", "preferences", "projects", "facts"):
            val = data.get(cat)
            if val and (isinstance(val, dict) and val) or (isinstance(val, list) and val):
                cleaned[cat] = val
        return cleaned if cleaned else None


# ── 辅助函数 ──────────────────────────────────────────────────────────────


def _format_messages(messages: list[BaseMessage], max_msgs: int = 20) -> str:
    """将消息格式化为 LLM 可读的文本（截断尾部 N 条）。"""
    recent = messages[-max_msgs:] if len(messages) > max_msgs else messages
    lines = [f"[{m.type}] {content_str(m.content)}" for m in recent]
    return "\n".join(lines)


def _extract_json(text: str) -> str | None:
    """从 LLM 输出中提取第一个 JSON 对象。"""
    # 去掉 markdown 代码块标记
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]

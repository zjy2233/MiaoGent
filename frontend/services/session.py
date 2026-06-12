"""会话管理服务。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.store.sessions import SessionRegistry


class SessionService:
    """会话管理：列表、创建、删除、消息查询、孤儿 tool_call 清理、轮次统计。"""

    def __init__(self, sessions_path, agent=None):
        self._sessions_path = sessions_path
        self._agent = agent

    async def get_sessions(self) -> list[dict]:
        registry = SessionRegistry(self._sessions_path)
        sessions = registry.list()
        # 回填已有轮次但无消息摘要的旧会话
        if self._agent is not None:
            need = [s for s in sessions if s.get("turn_count", 0) > 0 and not s.get("last_message")]
            if need:
                async def _fetch_last(thread_id: str) -> str:
                    try:
                        state = await self._agent.aget_state({"configurable": {"thread_id": thread_id}})
                        for m in reversed(list(state.values.get("messages", []) or [])):
                            if getattr(m, "type", "") != "human":
                                continue
                            content = m.content
                            if isinstance(content, list):
                                chunks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                                content = "".join(chunks)
                            text = str(content).strip()[:60]
                            if text:
                                return text
                    except Exception:
                        pass
                    return ""
                results = await asyncio.gather(*[_fetch_last(s["thread_id"]) for s in need])
                for s, msg in zip(need, results):
                    if msg:
                        s["last_message"] = msg
                        registry.update(s["thread_id"], last_message=msg)
        return sessions

    def delete_session(self, thread_id: str) -> bool:
        return SessionRegistry(self._sessions_path).remove(thread_id)

    def delete_sessions_batch(self, thread_ids: list[str]) -> dict:
        removed = SessionRegistry(self._sessions_path).remove_many(thread_ids)
        return {"deleted_count": removed}

    async def create_session(self) -> dict[str, str]:
        registry = SessionRegistry(self._sessions_path)
        tid = registry.new_thread_id()
        registry.add(tid)
        return {"thread_id": tid}

    async def get_messages(
        self, thread_id: str, *,
        include_tool_calls: bool = True,
        limit: int = 50,
        before_id: str | None = None,
    ) -> dict:
        """获取会话消息，支持分页。"""
        if self._agent is None:
            return {"messages": [], "has_more": False}
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self._agent.aget_state(config)
        except Exception:
            return {"messages": [], "has_more": False}
        messages = list(state.values.get("messages", []) or [])

        cursor_idx = len(messages)
        if before_id:
            for i, m in enumerate(messages):
                if getattr(m, "id", "") == before_id:
                    cursor_idx = i
                    break

        start_idx = max(0, cursor_idx - limit)
        page = messages[start_idx:cursor_idx]
        has_more = start_idx > 0

        result: list[dict] = []
        for m in page:
            msg_id = getattr(m, "id", "")
            role = "human" if m.type == "human" else "ai" if m.type == "ai" else m.type
            parts = []
            content = m.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "text":
                            parts.append(block.get("text", ""))
                        elif block_type == "tool_use":
                            if include_tool_calls:
                                name = block.get("name", "")
                                inp = block.get("input", {})
                                parts.append(f"[工具: {name}({json.dumps(inp, ensure_ascii=False)})]")
                        elif block_type == "tool_result":
                            if include_tool_calls:
                                parts.append("[工具结果]")
                        else:
                            parts.append(str(block.get("text", "")))
            elif content:
                parts.append(str(content))
            if include_tool_calls:
                tc = getattr(m, "tool_calls", None)
                if tc:
                    for call in tc:
                        tname = call.get("name", "") if isinstance(call, dict) else ""
                        targs = call.get("args", {}) if isinstance(call, dict) else {}
                        if tname:
                            parts.append(f"[工具调用: {tname}({json.dumps(targs, ensure_ascii=False)})]")
            text = "".join(parts)
            if not include_tool_calls and role in ("tool",):
                continue
            entry: dict = {"id": msg_id, "role": role}
            if text:
                entry["content"] = text
            result.append(entry)
        return {"messages": result, "has_more": has_more}

    async def _cleanup_orphan_tool_calls(self, config: dict) -> None:
        """清理残留的孤立 tool_calls，确保消息序列合法。"""
        if self._agent is None:
            return
        try:
            state_snap = await self._agent.aget_state(config)
            msgs = list(state_snap.values.get("messages", []) or [])
            for i in range(len(msgs) - 1, -1, -1):
                msg = msgs[i]
                tc = getattr(msg, "tool_calls", None)
                if not tc:
                    continue
                next_type = ""
                if i + 1 < len(msgs):
                    next_type = getattr(msgs[i + 1], "type", "")
                following_ids: set[str] = set()
                for j in range(i + 1, len(msgs)):
                    tcid = getattr(msgs[j], "tool_call_id", None)
                    if tcid:
                        following_ids.add(tcid)
                missing = [t for t in tc if t.get("id") and t["id"] not in following_ids]
                seq_broken = next_type not in ("", "tool")
                if missing or seq_broken:
                    from langgraph.graph.message import RemoveMessage
                    await self._agent.aupdate_state(config, {
                        "messages": [RemoveMessage(id=m.id) for m in msgs[i:]],
                    }, as_node="__start__")
                break
        except Exception:
            pass

    async def _update_turn_count(self, thread_id: str) -> None:
        """从 checkpoint 统计 human 消息轮数并更新 SessionRegistry。"""
        if self._agent is None:
            return
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = await self._agent.aget_state(config)
            messages = list(state.values.get("messages", []) or [])
            turns = sum(1 for m in messages if getattr(m, "type", "") == "human")
            last_msg = ""
            for m in reversed(messages):
                if getattr(m, "type", "") != "human":
                    continue
                content = m.content
                if isinstance(content, list):
                    chunks = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunks.append(block.get("text", ""))
                    content = "".join(chunks)
                last_msg = str(content).strip()[:60]
                if last_msg:
                    break
        except Exception:
            return
        registry = SessionRegistry(self._sessions_path)
        registry.update(thread_id, turn_count=turns, last_message=last_msg)

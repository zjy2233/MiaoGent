"""命令行入口：交互式 REPL + 单次问答。

用法：
    python -m src.main                          # 启动交互式 REPL
    python -m src.main "北京今天天气怎么样？再算下 25*4+10"   # 一问一答

REPL 内置命令：
    :quit / :q / :exit / exit / quit  退出
    :reset / :clear                   换一个新的 thread_id（旧的留在 registry）
    :sessions                         重新显示历史会话列表
    :switch <编号>                     切到指定历史会话
    :delete <编号>                     删除指定会话（同时清 SQLite 中的 checkpoint）
    :stats                            显示当前会话的 messages/chars/summary 长度

持久化策略：
- REPL：``history.db``（SqliteSaver）+ ``.sessions.json``（注册表），跨进程保留
- CLI 单次问答：临时 SQLite，进程结束清理；不写注册表
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.agent import build_agent
from src.config import Settings
from src.llm import build_llm
from src.memory import MemoryManager
from src.sessions import SessionRegistry
from src.soul import ProfileManager, SoulManager

# 流式输出时单行显示的字符上限（避免刷屏）
MAX_PREVIEW_CHARS = 200

EXIT_COMMANDS = {":quit", ":q", ":exit", "exit", "quit"}
RESET_COMMANDS = {":reset", ":clear"}


# ── 工具函数 ───────────────────────────────────────────────────────────────


def _extract_final_answer(state: dict) -> str:
    """从 agent 返回的 state 中拿最后一条 AI 消息。"""
    messages = state.get("messages", [])
    if not messages:
        return "(agent 没有返回消息)"
    content = messages[-1].content
    if isinstance(content, str):
        return content or "(空回答)"
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        ) or "(空回答)"
    return str(content)


async def _invoke_once(agent: Any, prompt: str, config: dict) -> dict:
    """invoke 一次并返回完整 state。"""
    return await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]}, config=config
    )


# ── 流式输出 ────────────────────────────────────────────────────────────────


def _chunk_to_text(chunk: Any) -> str:
    """从 AIMessageChunk / dict / str 中提取纯文本片段。"""
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, dict):
        c = chunk.get("content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(b.get("text", "") for b in c if isinstance(b, dict))
        return ""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _short(x: Any, limit: int = MAX_PREVIEW_CHARS) -> str:
    """把任意对象压成单行字符串，超长截断。"""
    s = str(x).replace("\n", " ").strip()
    if len(s) > limit:
        s = s[:limit] + "..."
    return s


async def _invoke_stream(agent: Any, user_input: str, config: dict) -> None:
    """流式调用 agent：实时打印 LLM 思考片段 + 工具调用状态。

    使用 LangGraph 的 ``astream_events(version="v2")`` 拿到完整事件流：
    - ``on_chat_model_stream`` → LLM 输出片段（边写边打）
    - ``on_tool_start`` / ``on_tool_end`` → 工具调用边界
    """
    print()
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "")

        if kind == "on_chat_model_stream":
            text = _chunk_to_text(event["data"].get("chunk"))
            if text:
                print(text, end="", flush=True)

        elif kind == "on_tool_start":
            name = event.get("name", "?")
            inp = event["data"].get("input", {}) if isinstance(event["data"], dict) else {}
            print(f"\n🔧 调用工具: {name}({_short(inp)})", flush=True)

        elif kind == "on_tool_end":
            name = event.get("name", "?")
            out = event["data"].get("output") if isinstance(event["data"], dict) else None
            print(f"✅ {name} → {_short(out)}", flush=True)

    print()  # 最后换行收尾


# ── 启动会话选择器 ─────────────────────────────────────────────────────────


def _pick_session(registry: SessionRegistry) -> str:
    """REPL 启动时让用户在历史会话中选择 / 新建 / 删除。"""
    sessions = registry.list()
    if not sessions:
        tid = registry.new_thread_id()
        registry.add(tid)
        return tid

    print("=" * 60)
    print(f"检测到 {len(sessions)} 个历史会话：")
    for i, s in enumerate(sessions, 1):
        short = s["thread_id"][:8]
        turns = s.get("turn_count", 0)
        last = s.get("last_active", "?")
        print(f"  [{i}] {short}... | {turns:>3} 轮 | 最后活跃 {last}")
    print("=" * 60)
    raw = input("选择编号继续 / n 新建 / d <编号> 删除 [n]: ").strip()

    if raw.lower().startswith("d "):
        try:
            idx = int(raw[2:].strip()) - 1
            if 0 <= idx < len(sessions):
                removed = sessions[idx]["thread_id"]
                registry.remove(removed)
                print(f"已删除会话 {removed[:8]}...")
        except ValueError:
            print("无效编号")
        return _pick_session(registry)  # 重新选

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(sessions):
            return sessions[idx]["thread_id"]
        print("无效编号，新建会话")

    # n / 空 / 其它都视为新建
    tid = registry.new_thread_id()
    registry.add(tid)
    return tid


def _format_session_picker(registry: SessionRegistry) -> str:
    sessions = registry.list()
    if not sessions:
        return "(暂无历史会话)"
    lines = ["当前历史会话："]
    for i, s in enumerate(sessions, 1):
        short = s["thread_id"][:8]
        turns = s.get("turn_count", 0)
        last = s.get("last_active", "?")
        lines.append(f"  [{i}] {short}... | {turns:>3} 轮 | 最后活跃 {last}")
    return "\n".join(lines)


# ── 命令解析 ───────────────────────────────────────────────────────────────


def _parse_soul_command(argv: list[str]) -> tuple[str, str | None]:
    """Parse :soul command arguments.

    Args:
        argv: Split command arguments (e.g. [":soul", "文本"] or [":soul"])

    Returns:
        Tuple of (action, value) where action is "view" or "set".
    """
    if len(argv) == 1:
        return ("view", None)
    return ("set", argv[1])


def _parse_profile_command(argv: list[str]) -> tuple[str, str | None, str | None]:
    """Parse :profile command arguments.

    Args:
        argv: Split command arguments.

    Returns:
        Tuple of (action, key, value) where action is "view", "get", "set", or "unset".
    """
    if len(argv) == 1:
        return ("view", None, None)
    if len(argv) == 3 and argv[1] == "get":
        return ("get", argv[2], None)
    if len(argv) == 4 and argv[1] == "set":
        return ("set", argv[2], argv[3])
    if len(argv) == 3 and argv[1] == "unset":
        return ("unset", argv[2], None)
    return ("view", None, None)  # fallback


# ── REPL 主体 ─────────────────────────────────────────────────────────────


def _print_stats(manager: MemoryManager, thread_id: str) -> None:
    stats = manager.get_stats(thread_id)
    print(f"  当前 thread_id: {thread_id[:8]}... | {stats}")


def _repl_loop(
    agent: Any,
    memory_manager: MemoryManager,
    registry: SessionRegistry,
    thread_id: str,
    config: dict,
) -> None:
    """同步入口：内部走 asyncio.run 启动异步 REPL 循环。"""
    asyncio.run(
        _repl_loop_async(agent, memory_manager, registry, thread_id, config)
    )


async def _repl_loop_async(
    agent: Any,
    memory_manager: MemoryManager,
    registry: SessionRegistry,
    thread_id: str,
    config: dict,
) -> None:
    def switch_thread(new_tid: str) -> None:
        nonlocal thread_id
        # 切走前压缩当前会话
        if thread_id and thread_id != new_tid:
            memory_manager.compress_if_needed(thread_id)
        thread_id = new_tid
        config["configurable"]["thread_id"] = new_tid
        if registry.get(new_tid) is None:
            registry.add(new_tid)
        stats = memory_manager.get_stats(new_tid)
        print(f"(已切到会话 {new_tid[:8]}... | {stats})")

    print("=" * 60)
    print("单 Agent 已就绪。可用工具：calculator, current_time, weather, web_search")
    print("命令：:quit 退出 · :reset 新建 · :sessions 列表 · :switch n 切换 · :delete n 删除 · :stats 状态")
    _print_stats(memory_manager, thread_id)
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            # 退出前对当前 thread 做一次压缩
            memory_manager.compress_if_needed(thread_id)
            registry.update(thread_id)
            print("\nbye.")
            return
        if not user_input:
            continue
        low = user_input.lower()

        if low in EXIT_COMMANDS:
            memory_manager.compress_if_needed(thread_id)
            registry.update(thread_id)
            print("bye.")
            return
        if low in RESET_COMMANDS:
            # 旧的留在 registry，新开一个
            new_tid = registry.new_thread_id()
            registry.add(new_tid)
            switch_thread(new_tid)
            continue
        if low == ":sessions":
            print(_format_session_picker(registry))
            continue
        if low.startswith(":switch "):
            try:
                idx = int(low.split()[1]) - 1
                sessions = registry.list()
                if 0 <= idx < len(sessions):
                    switch_thread(sessions[idx]["thread_id"])
                else:
                    print("无效编号")
            except (ValueError, IndexError):
                print("用法：:switch <编号>")
            continue
        if low.startswith(":delete "):
            try:
                idx = int(low.split()[1]) - 1
                sessions = registry.list()
                if 0 <= idx < len(sessions):
                    target = sessions[idx]["thread_id"]
                    registry.remove(target)
                    if target == thread_id:
                        switch_thread(registry.new_thread_id())
                        registry.add(thread_id)
                    print(f"已删除会话 {target[:8]}...")
                else:
                    print("无效编号")
            except (ValueError, IndexError):
                print("用法：:delete <编号>")
            continue
        if low == ":stats":
            _print_stats(memory_manager, thread_id)
            continue
        if low.startswith(":soul"):
            action, value = _parse_soul_command(low.split())
            manager_soul = SoulManager()
            if action == "view":
                soul = manager_soul.load()
                print(f"当前风格：{soul.get('description', '')}")
            elif action == "set" and value:
                manager_soul.save({"version": 1, "description": value})
                print(f"风格已更新：{value}")
            continue
        if low.startswith(":profile"):
            action, key, value = _parse_profile_command(low.split())
            manager_profile = ProfileManager()
            if action == "view":
                profile = manager_profile.load()
                print("当前画像：")
                for k, v in profile.items():
                    if k != "version":
                        print(f"  {k}: {v}")
            elif action == "get" and key:
                profile = manager_profile.load()
                print(f"{key}: {profile.get(key, '(未设置)')}")
            elif action == "set" and key and value:
                manager_profile.set(key, value, source="explicit")
                print(f"{key} 已设置为 {value}。")
            elif action == "unset" and key:
                manager_profile.unset(key)
                print(f"{key} 已删除。")
            continue

        try:
            await _invoke_stream(agent, user_input, config)
        except Exception as exc:  # noqa: BLE001 — REPL 要吞所有错才能继续
            print(f"\n!!! 运行出错：{type(exc).__name__}: {exc}\n")
            continue

        # 正常一轮结束后：压缩检查 + 登记活跃时间 / 轮数
        compressed = memory_manager.compress_if_needed(thread_id)
        stats = memory_manager.get_stats(thread_id)
        # 轮数 = human 消息数；state 里有但 stats 字段没暴露
        registry.update(thread_id, turn_count=stats.messages // 2)
        if compressed:
            print(f"  (已触发记忆压缩：summary={stats.summary_len} 字符)")


# ── CLI 单次问答 ──────────────────────────────────────────────────────────


async def _run_cli_once(llm: Any, settings: Settings, prompt: str) -> int:
    """CLI 单次问答：临时 SQLite，进程退出清理，不写 registry。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        async with AsyncSqliteSaver.from_conn_string(tmp_path) as checkpointer:
            agent = build_agent(llm, checkpointer=checkpointer)
            memory_manager = MemoryManager(agent, llm, settings)
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
            result = await _invoke_once(agent, prompt, config)
            print(f"\n>>> {_extract_final_answer(result)}\n")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return 0


# ── 入口 ──────────────────────────────────────────────────────────────────


async def run_repl() -> int:
    settings = Settings.from_env()
    llm = build_llm(settings)
    registry = SessionRegistry()
    async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
        agent = build_agent(llm, checkpointer=checkpointer)
        memory_manager = MemoryManager(agent, llm, settings)
        thread_id = _pick_session(registry)
        config: dict = {"configurable": {"thread_id": thread_id}}
        await _repl_loop_async(agent, memory_manager, registry, thread_id, config)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    settings = Settings.from_env()
    llm = build_llm(settings)

    if argv:
        return asyncio.run(_run_cli_once(llm, settings, " ".join(argv)))

    return asyncio.run(run_repl())


if __name__ == "__main__":
    sys.exit(main())

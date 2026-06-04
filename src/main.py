"""命令行入口：交互式 REPL + 单次问答。

用法：
    python -m src.main                          # 启动交互式 REPL
    python -m src.main "北京今天天气怎么样？再算下 25*4+10"   # 一问一答

REPL 内置命令：
    /quit / /q / /exit / exit / quit  退出
    /reset / /clear                   换一个新的 thread_id（旧的留在 registry）
    /sessions                         重新显示历史会话列表
    /switch <编号>                     切到指定历史会话
    /delete <编号>                     删除指定会话（同时清 SQLite 中的 checkpoint）
    /stats                            显示当前会话的 messages/chars/summary 长度
    /soul [文本]                      查看/设置 agent 风格
    /profile [key [value]]            查看/设置用户画像

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
from src.tools.dangerous import ConfirmationError

# 流式输出时单行显示的字符上限（避免刷屏）
MAX_PREVIEW_CHARS = 200


# ── 工具函数 ───────────────────────────────────────────────────────────────


def _extract_final_answer(state: dict) -> str:
    """从 agent 返回的 state 中拿最后一条 AI 消息。"""
    messages = state.get("messages", [])
    if not messages:
        return "(agent 没有返回消息)"
    content = messages[-1].content
    if isinstance(content, str):
        return _clean_output(content) or "(空回答)"
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        ) or "(空回答)"
    return str(content)


def _clean_output(text: str) -> str:
    """移除输出中的非法 Unicode 代理字符。"""
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


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
        return _clean_output(chunk)
    if isinstance(chunk, dict):
        c = chunk.get("content", "")
        if isinstance(c, str):
            return _clean_output(c)
        if isinstance(c, list):
            return "".join(b.get("text", "") for b in c if isinstance(b, dict))
        return ""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return _clean_output(content)
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
    print('命令：输入编号继续 · 回车新建 · d 编号删除')
    raw = input('> ').strip()

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
    """Parse /soul command arguments.

    Args:
        argv: Split command arguments (e.g. ["/soul", "文本"] or ["/soul"])

    Returns:
        Tuple of (action, value) where action is "view" or "set".
    """
    if len(argv) == 1:
        return ("view", None)
    return ("set", argv[1])


def _parse_profile_command(argv: list[str]) -> tuple[str, str | None, str | None]:
    """Parse /profile command arguments.

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


# 命令调度表：prefix -> (exact_match, handler_fn)
# handler_fn 接收 (argv, context_dict) 返回 True 表示已处理（继续下一轮）
_builtin_commands: list[tuple[str, bool, callable]] = []


def _register_command(
    prefix: str, exact: bool, handler: callable
) -> None:
    _builtin_commands.append((prefix, exact, handler))


async def _dispatch(low: str, argv: list[str], ctx: dict) -> bool:
    """Dispatch user input to matching command handler.

    Returns True if command was handled (caller should continue loop).
    """
    for prefix, exact, handler in _builtin_commands:
        if exact:
            if low == prefix:
                return await handler(argv, ctx)
        else:
            if low.startswith(prefix):
                return await handler(argv, ctx)
    return False


# ── REPL 内置命令处理器 ─────────────────────────────────────────────────────


async def _cmd_quit(argv: list[str], ctx: dict) -> bool:
    facts = await ctx["memory_manager"].discover_and_update_profile(ctx["thread_id"])
    if facts:
        print(f"已更新画像：{facts}")
    await ctx["memory_manager"].compress_if_needed(ctx["thread_id"])
    ctx["registry"].update(ctx["thread_id"])
    print("bye.")
    ctx["running"] = False
    return True


async def _cmd_reset(argv: list[str], ctx: dict) -> bool:
    new_tid = ctx["registry"].new_thread_id()
    ctx["registry"].add(new_tid)
    ctx["switch_thread"](new_tid)
    return True


async def _cmd_sessions(argv: list[str], ctx: dict) -> bool:
    print(_format_session_picker(ctx["registry"]))
    return True


async def _cmd_switch(argv: list[str], ctx: dict) -> bool:
    if len(argv) < 2:
        print("用法：/switch <编号>")
        return True
    try:
        idx = int(argv[1]) - 1
        sessions = ctx["registry"].list()
        if 0 <= idx < len(sessions):
            ctx["switch_thread"](sessions[idx]["thread_id"])
        else:
            print("无效编号")
    except (ValueError, IndexError):
        print("用法：/switch <编号>")
    return True


async def _cmd_delete(argv: list[str], ctx: dict) -> bool:
    if len(argv) < 2:
        print("用法：/delete <编号>")
        return True
    try:
        idx = int(argv[1]) - 1
        sessions = ctx["registry"].list()
        if 0 <= idx < len(sessions):
            target = sessions[idx]["thread_id"]
            ctx["registry"].remove(target)
            if target == ctx["thread_id"]:
                ctx["switch_thread"](ctx["registry"].new_thread_id())
                ctx["registry"].add(ctx["thread_id"])
            print(f"已删除会话 {target[:8]}...")
        else:
            print("无效编号")
    except (ValueError, IndexError):
        print("用法：/delete <编号>")
    return True


async def _cmd_stats(argv: list[str], ctx: dict) -> bool:
    _print_stats(ctx["memory_manager"], ctx["thread_id"])
    return True


async def _cmd_soul(argv: list[str], ctx: dict) -> bool:
    action, value = _parse_soul_command(argv)
    manager_soul = SoulManager()
    if action == "view":
        soul = manager_soul.load()
        print(f"当前风格：{soul.get('description', '')}")
    elif action == "set" and value:
        manager_soul.save({"version": 1, "description": value})
        print(f"风格已更新：{value}")
    return True


async def _cmd_profile(argv: list[str], ctx: dict) -> bool:
    action, key, value = _parse_profile_command(argv)
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
    return True


# 注册所有内置命令
_register_command("/quit", True, _cmd_quit)
_register_command("/q", True, _cmd_quit)
_register_command("/exit", True, _cmd_quit)
_register_command("/reset", True, _cmd_reset)
_register_command("/clear", True, _cmd_reset)
_register_command("/sessions", True, _cmd_sessions)
_register_command("/switch", False, _cmd_switch)
_register_command("/delete", False, _cmd_delete)
_register_command("/stats", True, _cmd_stats)
_register_command("/soul", False, _cmd_soul)
_register_command("/profile", False, _cmd_profile)


# ── REPL 主体 ─────────────────────────────────────────────────────────────


async def _print_stats(manager: MemoryManager, thread_id: str) -> None:
    stats = await manager.get_stats(thread_id)
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
    async def switch_thread(new_tid: str) -> None:
        nonlocal thread_id
        # 切走前压缩当前会话
        if thread_id and thread_id != new_tid:
            await memory_manager.compress_if_needed(thread_id)
        thread_id = new_tid
        config["configurable"]["thread_id"] = new_tid
        if registry.get(new_tid) is None:
            registry.add(new_tid)
        stats = await memory_manager.get_stats(new_tid)
        print(f"(已切到会话 {new_tid[:8]}... | {stats})")

    print("=" * 60)
    print("单 Agent 已就绪。可用工具：calculator, current_time, weather, web_search, shell")
    print("命令：/quit 退出 · /reset 新建 · /sessions 列表 · /switch n 切换 · /delete n 删除 · /stats 状态 · /soul [文本] · /profile [key [value]]")
    await _print_stats(memory_manager, thread_id)
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            # 退出前对当前 thread 做一次压缩
            await memory_manager.compress_if_needed(thread_id)
            registry.update(thread_id)
            print("\nbye.")
            return
        if not user_input:
            continue

        argv = user_input.lower().split()
        low = argv[0]

        # 构建命令上下文
        ctx = {
            "memory_manager": memory_manager,
            "registry": registry,
            "thread_id": thread_id,
            "switch_thread": switch_thread,
            "running": True,
        }

        # 分发命令
        if await _dispatch(low, argv, ctx):
            if not ctx.get("running", True):
                return
            continue

        try:
            await _invoke_stream(agent, user_input, config)
        except ConfirmationError as exc:
            if exc.danger_level == "high_risk":
                print(f"\n!!! 高危命令已被拦截：{exc.command}\n   原因：{exc.reason}\n")
                continue
            # confirm 级别：打印确认提示
            print(f"\n⚠️  此操作需要确认：{exc.command}")
            print(f"   原因：{exc.reason}")
            raw = input("   确认执行？[y/N] ").strip()
            if raw.lower() == "y":
                # 重新执行（裸执行，不走 astream_events，否则再次抛异常）
                try:
                    result = await agent.ainvoke(
                        {"messages": [{"role": "user", "content": user_input}]},
                        config=config,
                    )
                    answer = _extract_final_answer(result)
                    print(f"\n>>> {answer}\n")
                except Exception as inner_exc:
                    print(f"\n!!! 执行出错：{type(inner_exc).__name__}: {inner_exc}\n")
            else:
                print("已取消。")
            continue
        except Exception as exc:  # noqa: BLE001 — REPL 要吞所有错才能继续
            print(f"\n!!! 运行出错：{type(exc).__name__}: {exc}\n")
            # astream_events 失败时尝试 ainvoke 保底（消息仍会持久化）
            try:
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                )
                answer = _extract_final_answer(result)
                print(f"\n>>> {answer}\n")
            except Exception as inner_exc:
                # 打印失败不代表消息没持久化，检查 state
                print(f"\n!!! 备选执行也失败：{type(inner_exc).__name__}: {inner_exc}\n")
            # 即使打印失败，消息仍可能已持久化，继续处理
            stats = await memory_manager.get_stats(thread_id)
            registry.update(thread_id, turn_count=stats.messages // 2)
            continue

        # 正常一轮结束后：压缩检查 + 登记活跃时间 / 轮数
        compressed = await memory_manager.compress_if_needed(thread_id)
        stats = await memory_manager.get_stats(thread_id)
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
            bundle = build_agent(llm, checkpointer=checkpointer)
            memory_manager = MemoryManager(
                bundle.agent, llm, settings,
                profile_middleware=bundle.profile_middleware,
            )
            thread_id = str(uuid.uuid4())
            config = {"configurable": {"thread_id": thread_id}}
            result = await _invoke_once(bundle.agent, prompt, config)
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
        bundle = build_agent(llm, checkpointer=checkpointer)
        memory_manager = MemoryManager(
            bundle.agent, llm, settings,
            profile_middleware=bundle.profile_middleware,
        )
        thread_id = _pick_session(registry)
        config: dict = {"configurable": {"thread_id": thread_id}}
        await _repl_loop_async(bundle.agent, memory_manager, registry, thread_id, config)
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

# Agent Shell 优化方案 — LangGraph 深度集成版

> v2.0 — 以 LangGraph 框架为核心，放弃自定义基础设施，用框架原生能力解决所有问题。

---

## 核心理念

**不要造 LangGraph 已经有的轮子。**

| 当前做法 | 问题 | LangGraph 原生方案 |
|----------|------|-------------------|
| `ConfirmationError` 异常 + 新 thread_id 重试 | 丢失上下文，中断不精确 | `interrupt()` 暂停 + `Command(resume)` 精确恢复 |
| `EventBridge` 自定义事件总线 | 不必要的抽象 | LangGraph checkpoint 直接存储中断状态 |
| `SummaryMiddleware` 自定义 middleware | 框架已有等价能力 | `create_react_agent` 的 `pre_model_hook` / prompt callable |
| `ProfileMiddleware` 自定义 middleware | 同上 | state 中维护 profile，节点函数注入 |
| `_drop_orphans` 手动清理消息 | 脆弱 | LangGraph checkpoint 确保消息完整性 |
| `subprocess.run` 同步执行 | 阻塞 event loop | `asyncio.create_subprocess_shell` + LangGraph 异步节点 |
| 自定义 REPL 循环 | 重复劳动 | LangGraph 的 `astream_events` 已提供完整事件流 |

---

## 现状：当前 Agent 架构

```python
# 当前：create_agent (langchain.agents) — 有状态但无中断能力
agent = create_agent(
    model=llm,
    tools=[...],
    system_prompt=...,
    state_schema=AgentState,
    middleware=[SummaryMiddleware(), profile_middleware],
    checkpointer=checkpointer,
)
```

当前 graph 结构（create_agent 构造的隐式图）：

```
START → [__start__] → [agent] → [tools] → [agent] → ... → END
```

所有节点内部由 LangGraph 框架管理，我们无法插入自定义逻辑（如确认节点）。

---

## 目标：基于 StateGraph 的显式图

```python
# 目标：StateGraph 自定义图 — 完全可控，嵌入确认节点
builder = StateGraph(AgentState)

builder.add_node("agent", call_model_node)      # LLM 调用
builder.add_node("tools", tool_executor_node)    # 工具执行
builder.add_node("human_confirm", confirm_node)  # 人工确认

builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue, {
    "tools": "tools",
    END: END,
})
builder.add_conditional_edges("tools", check_confirm, {
    "human_confirm": "human_confirm",
    "agent": "agent",
})
builder.add_conditional_edges("human_confirm", process_confirm, {
    "agent": "agent",
    END: END,
})
```

---

## 关键改动

### 1. `interrupt()` 替代 ConfirmationError

```python
# 之前（v1）：抛异常 → REPL 捕获 → 新 thread_id 重试
raise ConfirmationError(command, reason, "confirm")
# 缺陷：丢弃了当前推理上下文

# 之后（v2）：interrupt() 暂停 → 用户确认 → Command(resume) 恢复
from langgraph.types import interrupt

@tool
async def shell(command: str, timeout: int | None = None) -> str:
    level, reason, alts = CommandClassifier().classify(command)

    if level == DangerLevel.HIGH_RISK:
        return f"错误：高危命令已被拦截 — {reason}"

    if level == DangerLevel.CONFIRM:
        # ── 暂停 Graph，等待用户确认 ──
        approved = interrupt({
            "type": "shell_confirm",
            "command": command,
            "reason": reason,
            "alternatives": alts,
        })
        if not approved:
            return f"操作已取消：{command}"
        # 用户确认了，继续执行

    # 执行命令
    return await _executor.execute(command, timeout=timeout)
```

**`interrupt()` 行为**：
1. 首次调用 → 抛出 `GraphInterrupt` → Graph 暂停，状态写入 checkpoint
2. 恢复时 → 节点从头重执行 → 再次调用 `interrupt()` → **返回 resume 值**（而非抛出）
3. 节点继续执行后续逻辑

### 2. Command(resume) 恢复执行

```python
# 前端 / REPL 收到确认信号后：
# 方案 A：直接 resume（推荐）
thread = await checkpointer.aget_tuple(config)
# thread.tasks[0].interrupts[0].value == {"type": "shell_confirm", ...}

await agent.ainvoke(
    None,  # 不传新输入，只 resume
    Command(resume=True),  # 传给 interrupt() 的返回值
    config,
)

# 方案 B：通过 astream_events resume
async for event in agent.astream_events(
    None,
    Command(resume=True),
    config,
    version="v2",
):
    ...
```

### 3. 移出自定义 Middleware

```python
# 之前：自定义 Middleware 类（SummaryMiddleware, ProfileMiddleware）

# 之后：用 graph 节点 inject 上下文
def build_agent(llm, tools, checkpointer=None, profile=None):
    system_prompt = _build_system_prompt(profile)

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,  # ✅ 直接传 string / callable
        state_schema=AgentState,
        checkpointer=checkpointer,
    )
    return agent
```

对于历史摘要，直接在 `call_model` 节点中注入：

```python
def call_model_node(state: AgentState, config: RunnableConfig) -> dict:
    """自定义模型调用节点，注入 summary 和 profile。"""
    messages = list(state["messages"])
    summary = state.get("summary", "")
    profile = state.get("profile", {})

    # 注入 summary
    if summary:
        messages.insert(0, SystemMessage(content=f"[对话历史摘要]\n{summary}"))

    # 注入 profile
    if profile:
        profile_lines = [f"{k}: {v}" for k, v in profile.items()
                         if k != "version" and not k.endswith("_source")]
        if profile_lines:
            messages.insert(0, SystemMessage(content="[用户画像]\n" + "\n".join(profile_lines)))

    # 调用 LLM
    response = llm.invoke(messages)
    return {"messages": [response]}
```

### 4. ToolNode + interrupt_before

```python
# 最简洁的方案：用 create_react_agent + interrupt_before
# 在调用任何工具前暂停，用户可以 review 工具调用

agent = create_react_agent(
    model=llm,
    tools=[calculator, weather, web_search, shell],
    prompt=system_prompt,
    state_schema=AgentState,
    checkpointer=checkpointer,
    interrupt_before=["tools"],  # 每次调用工具前暂停 🎯
)
```

配合 WebSocket/frontend：

```python
async def handle_interrupt(thread: ThreadSnapshot, ws: WebSocket):
    """处理 interrupt：将待确认信息发送到前端。"""
    for task in thread.tasks:
        if task.interrupts:
            for interrupt_info in task.interrupts:
                value = interrupt_info.value
                if isinstance(value, dict) and value.get("type") == "shell_confirm":
                    # 发送到前端确认对话框
                    await ws.send_json({
                        "type": "tool_confirm",
                        "payload": value,
                    })
                    # 等待前端回复
                    response = await ws.receive_json()
                    approved = response.get("approved", False)
                    # 用 Command 恢复
                    return Command(resume=approved)
```

---

## 分层设计

```
┌──────────────────────────────────────────────────────────┐
│                    用户界面层                              │
│  ┌───────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ REPL 终端  │  │ Agent Shell  │  │ Web 管理界面(可选) │  │
│  │ (现有+增强) │  │ pywebview+WS │  │                   │  │
│  └─────┬─────┘  └──────┬───────┘  └────────┬──────────┘  │
└────────┼───────────────┼────────────────────┼─────────────┘
         │               │                    │
         ▼               ▼                    ▼
┌──────────────────────────────────────────────────────────┐
│                 通信层（极简）                              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  aiohttp WebSocket (仅用于事件推送)                  │  │
│  │  + LangGraph checkpoint (状态持久化与恢复)           │  │
│  └────────────────────────────────────────────────────┘  │
└──────────┬───────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│              LangGraph Agent Graph                        │
│                                                           │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐      │
│  │ agent    │→→→│ tools    │→→→│ human_confirm    │      │
│  │ node     │   │ ToolNode │   │ (interrupt node) │      │
│  └──────────┘   └──────────┘   └──────────────────┘      │
│       ↑              │                    │               │
│       └──────────────┴────────────────────┘               │
│                                                           │
│  ┌──────────────────────────────────────────────────┐     │
│  │ MemoryManager (独立于 Graph，在 Graph 外运行)       │     │
│  └──────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│                    基础设施层                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │ Sandbox  │  │  Audit   │  │ Config   │  │ Session │  │
│  │ Executor │  │  Logger  │  │ Center   │  │ Registry│  │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 具体实现方案

### 3.1 Agent Graph 重构

```python
# src/graph.py — 新文件，替代 src/agent.py 的大部分逻辑

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import interrupt, Command
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    summary: str  # 历史摘要
    profile: dict  # 用户画像


def call_model(state: AgentState, config: RunnableConfig) -> dict:
    """模型调用节点：注入 system prompt + summary + profile → 调用 LLM。"""
    messages = list(state["messages"])
    summary = state.get("summary", "")
    profile = state.get("profile", {})

    # 构建前缀消息
    prefix: list[SystemMessage] = []

    # 1. 用户画像
    if profile:
        profile_lines = [
            f"{k}: {v}" for k, v in profile.items()
            if k != "version" and not k.endswith("_source")
        ]
        if profile_lines:
            prefix.append(SystemMessage(
                content="[用户画像]\n" + "\n".join(profile_lines)
            ))

    # 2. 历史摘要
    if summary:
        prefix.append(SystemMessage(
            content=f"[对话历史摘要]\n{summary}"
        ))

    # 调用 LLM（从 config 中获取）
    llm: BaseChatModel = config["configurable"]["llm"]
    response = llm.invoke(prefix + messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> Literal["tools", END]:
    """判断下一步：工具调用 → tools，否则 → END。"""
    messages = state["messages"]
    if messages and messages[-1].tool_calls:
        return "tools"
    return END


def check_confirm(state: AgentState) -> Literal["human_confirm", "agent"]:
    """检查工具执行结果中是否有需要确认的请求。"""
    # 这个函数在工具节点执行后调用
    # 如果工具内部已通过 interrupt() 暂停，根本不会执行到这里
    # 所以这里实际上不需要特殊逻辑，直接返回 "agent" 即可
    # 真正的确认逻辑在 tool 函数内部的 interrupt() 中
    return "agent"


def build_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    checkpointer=None,
):
    """基于 StateGraph 构建带确认能力的 Agent。"""
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))

    # 构建图
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        END: END,
    })
    builder.add_edge("tools", "agent")  # 工具执行完回 agent

    return builder.compile(checkpointer=checkpointer)
```

> **注意**：以上是最简版本。`interrupt()` 机制可以直接在 tool 函数内部工作，无需显式的 `human_confirm` 节点。ToolNode 执行 tool 时，如果 tool 内部调用 `interrupt()`，Graph 自动暂停。这是 LangGraph v1.2+ 的关键特性。

### 3.2 Shell 工具中使用 interrupt()

```python
# src/tools/shell.py — 重构后

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import interrupt

from src.tools.shell_executor import SandboxExecutor, ShellResult
from src.tools.shell_patterns import CommandClassifier, DangerLevel

_executor = SandboxExecutor()


@tool
async def shell(command: str, timeout: int | None = None) -> str:
    """执行 shell 命令并返回输出。"""
    level, reason, alts = CommandClassifier().classify(command)

    if level == DangerLevel.HIGH_RISK:
        return f"错误：高危命令已被拦截 — {reason}"

    if level == DangerLevel.CONFIRM:
        # ── LangGraph 原生中断 ──
        approved = interrupt({
            "type": "shell_confirm",
            "command": command,
            "reason": reason,
            "alternatives": alts,
        })
        # 恢复后继续执行
        if not approved:
            return f"操作已取消：{command}"
        # 用户已确认，继续执行

    result: ShellResult = await _executor.execute(command, timeout=timeout)
    return _format_result(result)
```

### 3.3 REPL 中使用 Command(resume)

```python
# src/main.py — 改造后的 REPL 循环

async def _repl_loop_async(agent, ...):
    while True:
        user_input = input("\nYou> ").strip()
        # ... 命令处理 ...

        # 正常对话
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=user_input)]},
            config=config,
            version="v2",
        ):
            _handle_event(event)

        # 检查是否有中断（需要用户确认）
        thread = await checkpointer.aget_tuple(config)
        if thread and thread.tasks:
            for task in thread.tasks:
                if task.interrupts:
                    for info in task.interrupts:
                        value = info.value
                        if isinstance(value, dict) and value.get("type") == "shell_confirm":
                            # 展示确认提示
                            print(f"\n⚠️  需要确认：{value.get('command')}")
                            print(f"   原因：{value.get('reason')}")
                            raw = input("   确认执行？[y/N] ").strip()
                            approved = raw.lower() == "y"

                            # ✅ 用 Command(resume) 精确恢复
                            async for event in agent.astream_events(
                                None,
                                Command(resume=approved),
                                config,
                                version="v2",
                            ):
                                _handle_event(event)
```

### 3.4 Agent Shell WebSocket 集成

```python
# src/agent_shell/ws_handler.py

@web.middleware
async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    session_id = request.query.get("session_id")

    async for msg in ws:
        data = json.loads(msg.data)
        event_type = data.get("type")

        if event_type == "user_message":
            text = data["payload"]["text"]
            # 调用 agent
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=text)]},
                config={"configurable": {"thread_id": session_id}},
                version="v2",
            ):
                await _send_to_ws(ws, event)

            # 检查中断
            thread = await checkpointer.aget_tuple(
                {"configurable": {"thread_id": session_id}}
            )
            if thread and thread.tasks:
                for task in thread.tasks:
                    if task.interrupts:
                        for info in task.interrupts:
                            await ws.send_json({
                                "type": "tool_confirm",
                                "payload": info.value,
                            })

        elif event_type == "confirm_yes":
            # 用户确认 → Command(resume=True)
            async for event in agent.astream_events(
                None,
                Command(resume=True),
                {"configurable": {"thread_id": session_id}},
                version="v2",
            ):
                await _send_to_ws(ws, event)

        elif event_type == "confirm_no":
            async for event in agent.astream_events(
                None,
                Command(resume=False),
                {"configurable": {"thread_id": session_id}},
                version="v2",
            ):
                await _send_to_ws(ws, event)
```

### 3.5 Memory Manager 简化

LangGraph checkpoint 已经保证了消息完整性，`_drop_orphans` 变得不再必要：

```python
# src/memory.py — 简化版

class MemoryManager:
    """记忆管理器：超限时触发增量摘要。

    相比 v1 的改进：
    - 不再需要 _drop_orphans（LangGraph checkpoint 保证完整性）
    - 使用 asyncio.Lock 防止并发压缩
    - 使用 Checkpointer 的 aget_tuple 替代 aget_state
    """

    def __init__(self, llm, settings, *, agent=None):
        self.llm = llm
        self.settings = settings
        self.agent = agent
        self._lock = asyncio.Lock()

    async def compress_if_needed(self, thread_id: str) -> bool:
        async with self._lock:
            config = {"configurable": {"thread_id": thread_id}}
            state = await self.agent.aget_state(config)
            messages = state.values.get("messages", [])
            summary = state.values.get("summary", "")

            if not self._needs_compress(messages):
                return False

            to_compress, recent = _split_by_turns(messages, self.settings.max_turns)
            if not to_compress:
                return False

            new_summary = self._summarize(to_compress, summary)
            self._replace_messages(config, to_compress, recent, new_summary)
            return True
```

---

## 与业界方案的对比

| 能力 | Claude Code | Open Interpreter | **本方案（LangGraph 深度集成）** |
|------|------------|-----------------|-------------------------------|
| 中断恢复 | 容器 + checkpoint | 无 | **LangGraph interrupt() + checkpoint** |
| 命令分类 | LLM + 规则 | 规则 + 白名单 | **规则 + LLM 辅助 + interrupt()** |
| 沙箱 | Docker | Docker / venv | **临时目录 + async subprocess** |
| 审计 | SQLite | 无 | **SQLite audit logger** |
| 流式 UI | ANSI 富文本 | 逐行文本 | **WebSocket + markdown 渲染** |

---

## 实施路线图

### Phase 1（2 天）：Agent Graph 重构

| 任务 | 产出 |
|------|------|
| 用 `StateGraph` + `create_react_agent` 替代 `create_agent` | `src/graph.py` |
| 用 `interrupt()` 替代 `ConfirmationError` | `src/tools/shell.py` 修改 |
| 用 `Command(resume)` 替代新 thread_id 重试 | `src/main.py` 修改 |
| 移出自定义 Middleware，改用 prompt 注入 | `src/agent.py` 简化 |

**验证**：`python -m src.main` → 输入 `rm test.txt` → 暂停等待确认 → `y` → 恢复执行

### Phase 2（1 天）：Shell 执行器现代化

| 任务 | 产出 |
|------|------|
| 实现 `SandboxExecutor`（async + 差异化超时 + 输出截断） | `src/tools/shell_executor.py` |
| 实现 `AuditLogger`（SQLite 持久化） | `src/audit.py` |
| LLM 辅助分类（Layer 5） | `src/tools/shell_patterns.py` 增强 |

**验证**：`pytest tests/test_shell_executor.py -v` 通过

### Phase 3（2 天）：WebSocket + Agent Shell UI

| 任务 | 产出 |
|------|------|
| 后端 WebSocket 端点（集成 LangGraph checkpoint 读取） | `src/agent_shell/ws_server.py` |
| 前端 WebSocket 客户端 + 聊天面板 | `src/agent_shell/index.html` + `app.js` 改写 |
| 确认对话框 + 中断处理 | 前端 `confirm_yes`/`confirm_no` 交互 |

**验证**：Agent Shell 输入"删除 temp 目录" → 弹出确认框 → 确认后执行

### Phase 4（1 天）：记忆管理 + 打磨

| 任务 | 产出 |
|------|------|
| MemoryManager 简化 + 并发锁 | `src/memory.py` |
| 流式渲染引擎 | `src/renderer.py` |
| 所有测试通过 | `pytest -v` |

---

## 文件变更总结

```
新建:
  src/graph.py                    ← StateGraph 定义（核心）
  src/tools/shell_executor.py     ← 异步沙箱执行器
  src/audit.py                    ← 审计日志
  src/renderer.py                 ← 流式渲染引擎
  src/agent_shell/ws_server.py    ← WebSocket 端点

改写:
  src/tools/shell.py              ← 使用 interrupt() + SandboxExecutor
  src/tools/shell_patterns.py     ← +LLM 辅助分类
  src/agent.py                    ← 简化为 build_agent 入口（委托 graph.py）
  src/main.py                     ← REPL 使用 Command(resume)
  src/memory.py                   ← 简化，去掉 _drop_orphans
  src/agent_shell/index.html      ← +聊天面板
  src/agent_shell/app.js          ← +WebSocket 客户端
  src/agent_shell/styles.css      ← +聊天 UI 样式

删除:
  src/tools/dangerous.py          ← ConfirmationError 不再需要
  src/event_bridge.py             ← 不需要了，用 checkpoint 替代
  src/human_loop.py               ← 不需要了，interrupt() 内联在 tool 中
```

---

## 附录：关键 LangGraph API 参考

| API | 路径 | 用途 |
|-----|------|------|
| `interrupt(value)` | `langgraph.types` | 在节点内暂停，等待外部输入 |
| `Command(resume=..., goto=...)` | `langgraph.types` | 恢复 Graph 执行 |
| `create_react_agent(...)` | `langgraph.prebuilt` | 快速构建 ReAct agent |
| `ToolNode(tools)` | `langgraph.prebuilt` | 标准工具执行节点 |
| `tools_condition` | `langgraph.prebuilt` | "是否有工具调用"条件边 |
| `add_messages` | `langgraph.graph.message` | 消息合并 reducer |
| `AsyncSqliteSaver` | `langgraph.checkpoint.sqlite.aio` | 异步 SQLite 持久化 |

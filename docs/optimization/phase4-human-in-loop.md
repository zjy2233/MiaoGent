# Phase 4: Human-in-the-Loop 中断恢复

> 预估工时：1 天
> 依赖：Phase 1（EventBridge）、Phase 3（确认对话框）
> 目标：Agent 遇到需要用户确认的操作时暂停，等待前端响应后恢复

---

## 问题分析

当前流程：

```
用户输入 → Agent 调用 shell("rm file.txt")
                               ↓
                    shell_patterns → CONFIRM
                               ↓
                    ConfirmationError 抛出
                               ↓
                    REPL 捕获 → input("确认执行？[y/N]")
                               ↓
                    用户输入 y → 新 thread_id 重新执行
                               ↓
                    旧 thread 状态被丢弃！
```

**三个问题**：
1. 中断时**创建新的 thread_id**，丢弃了原来的推理上下文
2. 只能在 REPL 终端确认，Agent Shell UI 无法处理
3. 中断后无法精确恢复到中断点

---

## 方案：LangGraph NodeInterrupt

利用 LangGraph 的 `NodeInterrupt` + `Command(resume)` 实现原生暂停/恢复。

### 流程

```
用户输入 → Agent 进入工具选择节点
                               ↓
                    shell 工具被调用
                               ↓
                    CommandClassifier → CONFIRM
                               ↓
                    ┌──────────────┐
                    │  中断节点     │──→ NodeInterrupt("需要确认删除文件")
                    │  (pause_here) │     Agent 暂停，状态完整保留
                    └──────┬───────┘
                           │
                    EventBridge.send(confirm)
                           │
                    ┌──────▼───────┐
                    │  前端弹出确认框 │
                    │  用户点击 Y/N  │
                    └──────┬───────┘
                           │
                    EventBridge.resolve_confirm()
                           │
                    ┌──────▼───────┐
                    │  Command(resume=True/False)
                    │  Agent 精确恢复
                    └──────────────┘
```

### 实现

```python
# src/human_loop.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import Command, NodeInterrupt

from src.event_bridge import EventBridge, AgentEvent, EventType


@dataclass
class ConfirmRequest:
    """确认请求数据结构"""
    command: str
    reason: str
    safer_alternatives: list[str]
    tool_call_id: str  # 用于构造恢复后的 ToolMessage


class HumanInTheLoop:
    """Human-in-the-Loop 管理器。

    在 Agent Graph 中插入一个确认节点：
    - 当 shell 工具返回 CONFIRM 时，Agent 流程在此暂停
    - 等待 EventBridge 收到前端的确认信号
    - 通过 Command(resume) 精确恢复 Agent 状态
    """

    def __init__(self, bridge: EventBridge):
        self.bridge = bridge

    async def confirm_node(self, state: dict, session_id: str) -> dict:
        """Agent Graph 中的确认节点。

        此节点在 shell 工具抛出 ConfirmationError 后被调用。
        """
        pending: ConfirmRequest | None = state.get("_pending_confirm")
        if pending is None:
            return state  # 无需确认，正常通过

        # 通过 WebSocket 发送确认请求到前端
        await self.bridge.send(
            session_id,
            AgentEvent(
                type=EventType.TOOL_CONFIRM,
                payload={
                    "command": pending.command,
                    "reason": pending.reason,
                    "alternatives": pending.safer_alternatives,
                },
            ),
        )

        # ── 抛出 NodeInterrupt，Agent 在此暂停 ──
        raise NodeInterrupt(
            f"需要用户确认：{pending.reason}\n"
            f"  命令：{pending.command}"
        )

        # 注意：NodeInterrupt 后的代码不会执行。
        # 恢复时会重新进入此节点，resume_value 通过 Command(resume) 传入。

    async def resume_node(self, state: dict, session_id: str) -> dict:
        """Agent 恢复节点。

        当用户通过前端确认/拒绝后，此节点根据结果构造返回。
        """
        pending: ConfirmRequest | None = state.get("_pending_confirm")
        if pending is None:
            return state

        # 获取确认结果（通过 Command(resume) 传入）
        approved = state.get("_resume_value", False)

        # 清除待处理确认
        updates = {"_pending_confirm": None}

        if not approved:
            # 用户拒绝：构造拒绝消息作为 tool 响应
            from langchain_core.messages import ToolMessage

            updates["messages"] = [
                ToolMessage(
                    content=f"操作已取消：{pending.command}（{pending.reason}）",
                    tool_call_id=pending.tool_call_id,
                )
            ]

        return updates
```

---

## Agent Graph 集成

在 `src/agent.py` 中修改：

```python
# src/agent.py

from langgraph.graph import StateGraph, START, END
from src.human_loop import HumanInTheLoop

# 在 build_agent 中加入确认节点
def build_agent(llm, *, checkpointer=None, profile=None, bridge=None):
    # ... 原有逻辑 ...

    if bridge:
        hilt = HumanInTheLoop(bridge)

        # 在工具节点后插入确认节点
        graph = agent.get_graph()
        graph.add_node("confirm", hilt.confirm_node)
        graph.add_node("resume", hilt.resume_node)
        graph.add_edge("tools", "confirm")
        graph.add_conditional_edges(
            "confirm",
            lambda s: "resume" if s.get("_pending_confirm") else "next",
        )

    # ... 返回 agent ...
```

---

## 与 EventBridge 集成

当 Agent 暂停时，EventBridge 的 `wait_for_confirm` 被调用：

```python
# event_bridge.py

async def wait_for_confirm(
    self, session_id: str, timeout: int = 120
) -> bool | None:
    """等待用户确认。

    与 LangGraph 的 NodeInterrupt 配合使用。
    用户在 Web UI 上点击确认/拒绝后，此方法返回。
    """
    future: asyncio.Future[bool] = asyncio.Future()
    async with self._lock:
        self._pending_confirms[session_id] = future

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return None  # 超时视为拒绝
    finally:
        async with self._lock:
            self._pending_confirms.pop(session_id, None)
```

---

## 优势 vs 当前方案

| 方面 | 当前方案（新 thread_id） | Human-in-the-Loop |
|------|------------------------|-------------------|
| 上下文保留 | 丢失 | **完整保留** |
| 恢复精度 | 重新执行整轮 | **精确到中断点** |
| 前端兼容 | 仅 REPL | **Agent Shell + REPL** |
| 实现复杂度 | 低 | 中 |
| 用户体验 | 差（重新执行需等待） | **好（即时恢复）** |

---

## 兜底策略

1. **WebSocket 不可达**：降级为 REPL `input()` 模式
2. **前端超时不响应**：120s 后自动拒绝
3. **NodeInterrupt 不支持**：回退到当前异常捕获模式

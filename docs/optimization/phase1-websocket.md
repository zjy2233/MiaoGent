# Phase 1: WebSocket 双向通道

> 预估工时：2-3 天
> 依赖：无
> 目标：打通 Agent Shell 前端 ↔ Agent 引擎的实时双向通信

---

## 任务清单

### Task 1.1: EventBridge 事件总线

**文件**：`src/event_bridge.py`（新建）

实现核心事件路由：

```
EventBridge
├── send(session_id, event)     # 发送事件到前端
├── receive(session_id)         # 异步接收前端事件
├── wait_for_confirm()          # 等待用户确认（Future-based）
├── resolve_confirm()           # 解析确认
├── connect(session_id)         # 注册新连接
└── disconnect(session_id)      # 断开连接
```

**验收标准**：
- 单连接场景：send → receive 延迟 < 10ms
- 确认场景：wait_for_confirm 在被 resolve_confirm 调用时 1ms 内返回
- 多 session 隔离：session A 的事件不会到达 session B
- 超时：wait_for_confirm 120s 无响应返回 None

### Task 1.2: WebSocket 服务器集成

**文件**：`src/agent_shell/ws_server.py`（新建）

在现有 aiohttp 服务器上增加 WS 端点：

```
GET /ws?session_id={uuid} → WebSocket 升级
```

**事件协议**：

```
Agent → Frontend:
  {"type": "text_stream", "payload": {"text": "北京"}}
  {"type": "tool_start",  "payload": {"name": "weather", "input": {...}}}
  {"type": "tool_end",    "payload": {"name": "weather", "output": "..."}}
  {"type": "tool_confirm","payload": {"command": "rm -rf", "reason": "删除文件"}}
  {"type": "error",       "payload": {"message": "..."}}
  {"type": "done",        "payload": {}}

Frontend → Agent:
  {"type": "user_message", "payload": {"text": "北京天气"}}
  {"type": "confirm_yes",  "payload": {}}
  {"type": "confirm_no",   "payload": {}}
```

**验收标准**：
- `curl ws://127.0.0.1:18792/ws?session_id=test` 返回 101 Switching Protocols
- 发送 `user_message` 后收到事件流
- 断线重连：5 次自动重试，指数退避

### Task 1.3: 前端 WebSocket 客户端

**文件**：`src/agent_shell/app.js`（新增）

```javascript
class ChatClient {
  connect(sessionId)
  sendMessage(text)
  confirmTool(approved)
  onText(text)          // callback
  onToolConfirm(payload) // callback
  onDone()              // callback
  disconnect()
}
```

**验收标准**：
- 连接建立后 `console.log("[ChatClient] connected")`
- 收到事件后正确触发 callback
- 断线后自动重连（最多 5 次）

### Task 1.4: 现有 REST API 保持

不修改现有 `http_server.py` 的 REST 端点，只增加 WS 路由。

---

## 实现要点

### 1.4.1 并发安全

```python
# EventBridge 使用 asyncio.Queue 而非普通 Queue
# 使用 asyncio.Future 而非 threading.Event 做确认同步
import asyncio

class EventBridge:
    def __init__(self):
        self._connections: dict[str, asyncio.Queue] = {}
        self._pending_confirms: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str) -> None:
        async with self._lock:
            self._connections[session_id] = asyncio.Queue(maxsize=1000)
```

### 1.4.2 事件序列化

```python
@dataclass
class AgentEvent:
    type: str
    payload: dict = field(default_factory=dict)
    session_id: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type,
            "payload": self.payload,
        }, ensure_ascii=False)
```

### 1.4.3 错误处理

```python
async def safe_send(self, session_id: str, event: AgentEvent) -> bool:
    try:
        await self.send(session_id, event)
        return True
    except Exception:
        logger.warning("Failed to send event to %s", session_id)
        return False
```

---

## 测试

```python
# tests/test_event_bridge.py

import pytest
from src.event_bridge import EventBridge, AgentEvent

@pytest.mark.asyncio
async def test_send_receive():
    bridge = EventBridge()
    await bridge.connect("test-session")
    await bridge.send("test-session", AgentEvent(type="text_stream", payload={"text": "hello"}))
    event = await bridge.receive("test-session")
    assert event.type == "text_stream"
    assert event.payload["text"] == "hello"
```

---

## 回退方案

如果 WebSocket 在 pywebview 中不可用（已知某些旧版本 webview 不支持 WS）：
1. 降级为 HTTP 长轮询（`GET /poll?session_id=xxx`）
2. 自动检测 WS 支持，不可用时透明降级

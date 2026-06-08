# Agent Shell 工程级优化方案

> 基于对当前架构的全面分析，结合 Electron + Python 桌面应用业界实践，提出一套可落地的分阶段优化方案。

## 一、当前架构问题诊断

### 1.1 核心缺陷：Agent Shell 无法与 Agent 交互

**致命问题：Shell 没有 Chat API**

```
当前架构：

┌─ Terminal REPL ─────────────────┐   ┌─ Agent Shell (Electron) ────────┐
│  main.py                        │   │  HTTP Server → 只有 CRUD 管理 API│
│  - 拥有完整 Agent Runtime       │   │  - GET /api/sessions            │
│  - 可调用 LLM、流式输出        │   │  - GET/POST /api/settings       │
│  - 有 MemoryManager 做压缩     │   │  - GET/POST /api/soul/profile   │
│  - 完整的会话管理               │   │  - GET /api/tools               │
│  - /soul /profile 命令          │   │  没有 /api/chat！无法对话！     │
└─────────────────────────────────┘   └──────────────────────────────────┘
```

Shell 前端虽然有 "Chat" 面板，但 **只能看会话列表，不能发消息也不能收回复**。用户只能在终端里用 REPL，而 Shell 只是一个配置管理器。

### 1.2 架构问题清单

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| 1 | **两个独立的 Agent 入口** — REPL 和 Shell 各自运行、各自为政 | P0 | 用户在 Shell 里改配置，REPL 感知不到；用户只能选一个用 |
| 2 | **HTTP Server 没有 Agent Runtime** — 只有 CRUD 管理 API | P0 | Shell 的 Chat 面板是假的，无法对话 |
| 3 | **前端的错误处理为 0** — 所有 API 调用的 `.catch` 只 `console.error` | P1 | 服务器挂了前端静默失败，用户无反馈 |
| 4 | **没有流式传输能力** — 所有 API 都是 request-response | P1 | 不能实现流式打字效果 |
| 5 | **子进程管理脆弱** — 没有优雅关闭、没有自动重启 | P1 | `taskkill /f` 可能损坏 SQLite；crash 后不会自愈 |
| 6 | **配置分散、不一致** — 同一字段在 `config.py`、`agent_shell.py`、`.env` 中有三套默认值 | P2 | 改了一处忘了另一处，行为不一致 |
| 7 | **位置持久化无边界检查** — 窗口可能被拖出屏幕 | P2 | 窗口 "消失" 用户无法恢复 |
| 8 | **CORS 中间件有 Bug** — 异常处理不当 | P2 | 特定场景下 handler 异常被吞掉 |
| 9 | **没有 /api/chat 测试覆盖** | P2 | 无法验证核心功能 |

### 1.3 根因分析

当前的 "REPL + Shell 双进程" 架构起源于 **"前端 UI 只管配置，LLM 交互在终端"** 的设计假设。然而在实际使用中，用户期望的是 **一个统一的桌面 Agent 客户端** 而不是两个割裂的工具。

---

## 二、优化目标

1. **可工作** — Shell 能发消息、收回复，具备完整的 Agent 对话能力
2. **健壮** — 进程崩溃能自愈、网络错误有反馈、状态异常可恢复
3. **一致** — 配置单一来源、修改即时生效
4. **可观测** — 错误可见、状态可知、调用链可追踪
5. **可扩展** — 容易添加新工具、新面板、新能力

---

## 三、总体架构

### 3.1 新架构：Monolithic Agent Server

```
┌── Electron ──────────────────────────────────────────────┐
│  Main Process                                            │
│  ├─ BrowserWindow (80×80 mascot + panels)               │
│  └─ Python Subprocess (agent server)                    │
│       spawn → wait ready → show window                  │
│       quit → SIGINT → 3s timeout → SIGKILL             │
├──────────────────────────────────────────────────────────┤
│  Renderer Process (app.js)                               │
│  ├─ Mascot + Hover Menu                                 │
│  ├─ Settings Panel (config CRUD)                        │
│  ├─ Chat Panel (messages + input + streaming)            │
│  └─ Tools Panel (tool list)                             │
│     window.api → HTTP fetch + EventSource (SSE)         │
└──────────────────────────────────────────────────────────┘
                            │ HTTP REST + SSE (127.0.0.1:18792)
                            ▼
┌── Python Agent Server ───────────────────────────────────┐
│  aiohttp App                                              │
│  ├─ /health                                              │
│  ├─ /api/chat [POST → SSE stream] ← 新增！               │
│  ├─ /api/sessions {GET,POST,GET/:id/messages,DELETE/:id} │
│  ├─ /api/settings {GET,POST}                             │
│  ├─ /api/soul {GET,POST}                                 │
│  ├─ /api/profile {GET,POST}                              │
│  └─ /api/tools {GET}                                     │
│                                                           │
│  Agent Runtime ← 从 main.py 搬到这里                     │
│  ├─ LLM instance (ChatOpenAI → DeepSeek)                 │
│  ├─ Agent Graph (CompiledStateGraph)                     │
│  ├─ MemoryManager (compress + profile discover)          │
│  ├─ SoulManager / ProfileManager                         │
│  ├─ SqliteSaver (checkpointer)                           │
│  └─ SessionRegistry (.sessions.json)                     │
└──────────────────────────────────────────────────────────┘
```

### 3.2 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 通信协议 | HTTP REST + SSE (不是 WebSocket) | SSE 天然支持流式、自动重连、一行代码接入 EventSource；Python `aiohttp` 原生支持 SSE |
| Agent 位置 | 同一个 Python 进程，和 HTTP Server 共享事件循环 | 避免 RPC 序列化开销、零拷贝共享 state、复用已有的 asyncio 基础设施 |
| 配置传播 | HTTP Server 是唯一入口，修改即生效 | 消除双入口配置不一致 |
| 流式方案 | SSE `text/event-stream` | Chat API 返回 SSE stream，前端 EventSource 消费 |
| 子进程管理 | spawn → health check → auto restart (up to 3次) | 容错而不丢失会话状态 |
| 优雅关闭 | SIGINT → 3s grace → taskkill /f (仅 Windows) | 避免 SQLite WAL 损坏 |

### 3.3 SSE 协议设计

```
POST /api/chat
Content-Type: application/json

{"message": "北京今天天气怎么样？", "session_id": "uuid-xxx"}

Response: text/event-stream

event: token
data: {"text": "好的"}

event: token
data: {"text": "，我来查"}

event: tool_start
data: {"name": "weather", "input": {"city": "北京"}}

event: tool_end
data: {"name": "weather", "output": "北京今天20°C，晴"}

event: token
data: {"text": "北京今天气温20°C，天气晴朗。"}

event: done
data: {"session_id": "uuid-xxx"}
```

---

## 四、分阶段实施计划

### Phase 1（P0）— 核心链路打通

目标：Shell 能发消息、能看回复。

#### 1.1 将 Agent Runtime 移入 HTTP Server

**改动文件：** `src/agent_shell/http_server.py`（重写）

```python
class AgentRuntime:
    """统一的 Agent 运行时，和 HTTP Server 共享事件循环。"""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = build_llm(settings)
        self.checkpointer = AsyncSqliteSaver(...)
        self.bundle = build_agent(self.llm, checkpointer=self.checkpointer)
        self.memory_manager = MemoryManager(
            self.bundle.agent, self.llm, settings,
            profile_middleware=self.bundle.profile_middleware,
        )
        self.registry = SessionRegistry()
    
    async def chat(self, message: str, session_id: str | None = None) -> AsyncIterator[dict]:
        """SSE 流式对话。"""
        ...
    
    async def get_state(self, session_id: str) -> dict:
        """获取会话完整消息历史。"""
        ...
```

要点：
- `AgentRuntime` 在 `main()` 中初始化，和 `web.Application` 共享 `asyncio` 事件循环
- `web.run_app(app, ...)` 和 `asyncio.get_event_loop()` 是同一个 loop
- `build_agent()` 的 `checkpointer` 用 `AsyncSqliteSaver`（和原来 REPL 一样）

#### 1.2 实现 Chat SSE Endpoint

**新增文件：** `src/agent_shell/chat_handler.py`

```python
async def handle_chat(request: Request) -> web.StreamResponse:
    """POST /api/chat → SSE stream"""
    body = await request.json()
    message = body["message"]
    session_id = body.get("session_id")
    
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)
    
    runtime = get_runtime()  # 全局单例
    async for event in runtime.chat(message, session_id):
        payload = json.dumps(event, ensure_ascii=False)
        await response.write(f"event: {event['type']}\ndata: {payload}\n\n".encode())
    
    return response
```

#### 1.3 Chat 前端实现

**改动文件：** `src/agent_shell/app.js`

新增聊天功能：
- 消息列表（MessageList）—— 显示 AI/Human/System 消息
- 输入框（InputArea）—— 文本输入 + 发送按钮
- SSE 客户端 —— `EventSource` 消费流式响应
- 工具调用可视化 —— 在消息流中展示 `tool_start` / `tool_end`

```javascript
// SSE 客户端核心逻辑
async function sendMessage(text, sessionId) {
  const resp = await fetch('http://127.0.0.1:18792/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, session_id: sessionId }),
  });
  
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 解析 SSE events → 更新 UI
    processSSEBuffer(buffer);
  }
}
```

#### 1.4 Preload.js 新增 Chat API

**改动文件：** `src/agent_shell/electron/preload.js`

```javascript
const api = {
  // ... 原有 API
  
  /** POST /api/chat → ReadableStream (SSE) */
  sendMessage: (message, sessionId) => {
    return fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
  },
  
  /** GET /api/sessions/:id/messages */
  getMessages: (sessionId) =>
    fetch(`${BASE_URL}/api/sessions/${sessionId}/messages`).then(r => r.json()),
};
```

#### 1.5 Phase 1 改动清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agent_shell/http_server.py` | 重写 | 加入 AgentRuntime + Chat handler |
| `src/agent_shell/chat_handler.py` | 新增 | SSE 流式对话 handler |
| `src/agent_shell/electron/preload.js` | 修改 | 新增 `sendMessage`、`getMessages` |
| `src/agent_shell/app.js` | 修改 | Chat 面板：消息列表 + 输入框 + SSE |
| `src/agent_shell/index.html` | 修改 | Chat 面板加入消息容器和输入框 |
| `src/agent_shell/styles.css` | 修改 | 消息气泡、输入框、工具调用样式 |
| `src/main.py` | 可选 | 保留 REPL 作为辅助入口（读取同一 history.db） |

---

### Phase 2（P1）— 健壮性提升

目标：崩溃不失联、错误有反馈、状态可恢复。

#### 2.1 子进程生命周期管理

**改动文件：** `src/agent_shell/electron/main.js`

关键改进：

```javascript
// 优雅关闭：先 SIGINT，等 3s，不行再 force kill
async function stopPythonServer() {
  if (!pythonServer) return;
  const pid = pythonServer.pid;
  
  if (process.platform === 'win32') {
    // 1. 发 CTRL_C_EVENT
    execSync(`taskkill /pid ${pid} /f`, { windowsHide: true });
    // 注意：Windows 上没有真正的 SIGINT。但我们可以先发 CTRL_C_EVENT
    // 实际上 taskkill /f 是唯一可靠的方式
  } else {
    pythonServer.kill('SIGINT');
    // 2. 等 3s
    await new Promise(r => setTimeout(r, 3000));
    if (pythonServer) {
      pythonServer.kill('SIGKILL');
    }
  }
}

// 自动重启（至多 3 次）
let restartCount = 0;
const MAX_RESTARTS = 3;

pythonServer.on('exit', async (code) => {
  if (code !== 0 && restartCount < MAX_RESTARTS) {
    restartCount++;
    await startPythonServer(httpPort);
  }
});
```

**增加窗口显示/隐藏逻辑：**
```javascript
// 失焦时隐藏窗口（可选），托盘图标常驻
win.on('blur', () => { win.hide(); });
// 托盘恢复
tray.on('click', () => { win.show(); });
```

#### 2.2 前端全局错误处理

**改动文件：** `src/agent_shell/app.js`

```javascript
// 全局错误状态显示层
const errorToast = document.getElementById('error-toast');

function showError(message) {
  errorToast.textContent = message;
  errorToast.classList.add('visible');
  setTimeout(() => errorToast.classList.remove('visible'), 5000);
}

// 通用 fetch 包装
async function apiCall(fn, fallback) {
  try {
    return await fn();
  } catch (e) {
    showError(`请求失败: ${e.message}`);
    return fallback;
  }
}

// 替代所有裸 try/catch
const sessions = await apiCall(() => window.api.getSessions(), []);
```

#### 2.3 加载状态管理

所有面板添加三个状态：
- **Loading** — spinner / skeleton
- **Loaded** — 正常内容
- **Error** — 错误提示 + 重试按钮

```javascript
async function loadSessionsData() {
  const list = document.getElementById('session-list');
  list.innerHTML = '<div class="loading">加载中...</div>';
  
  const sessions = await apiCall(() => window.api.getSessions(), null);
  if (sessions === null) {
    list.innerHTML = '<div class="error">加载失败 <button onclick="loadSessionsData()">重试</button></div>';
    return;
  }
  if (sessions.length === 0) {
    list.innerHTML = '<div class="empty-state">暂无会话</div>';
    return;
  }
  renderSessionList(sessions);
}
```

**规范化的数据流：**
```
User Action → show Loading → fetch API → hide Loading
                                  ├─ success → render data
                                  └─ failure → show Error + Retry
```

#### 2.4 CORS 中间件修复

**改动文件：** `src/agent_shell/http_server.py`

```python
@web.middleware
async def cors_middleware(request: Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp
    
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unhandled error in %s %s", request.method, request.path)
        return web.json_response(
            {"error": str(exc)}, status=500,
        )
```

原问题：`asyncio.create_task(handler(request))` 创建了一个 task 但没有 await，异常无法传播到中间件。改用 `await handler(request)`。

#### 2.5 子进程健康检查

**改动文件：** `src/agent_shell/electron/main.js`

```javascript
// 定期健康检查
const healthInterval = setInterval(async () => {
  try {
    const resp = await fetch(`http://127.0.0.1:${httpPort}/health`);
    if (!resp.ok) throw new Error('health check failed');
  } catch {
    // 3s 后重试
    setTimeout(async () => {
      try {
        await fetch(`http://127.0.0.1:${httpPort}/health`);
      } catch {
        restartPythonServer();
      }
    }, 3000);
  }
}, 30000); // 每 30 秒检查一次
```

#### 2.6 Phase 2 改动清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/agent_shell/electron/main.js` | 修改 | 优雅关闭、自动重启、健康检查 |
| `src/agent_shell/app.js` | 修改 | 全局 error toast、加载状态、三态渲染 |
| `src/agent_shell/index.html` | 修改 | 新增 error-toast 元素 |
| `src/agent_shell/styles.css` | 修改 | error toast、loading spinner、skeleton 样式 |
| `src/agent_shell/http_server.py` | 修改 | CORS 中间件修复、异常统一处理 |

---

### Phase 3（P2）— 一致性与可观测性

目标：配置单一来源、状态可追踪。

#### 3.1 配置统一

**改动文件：** `src/config.py` + `src/agent_shell.py`

- `Settings` dataclass 作为 **唯一默认值来源**
- `agent_shell.py` 中的 `_DATACLASS_DEFAULTS` 从 `Settings` 反射获取
- 消除 `_EXTRA_DEFAULTS` 中的硬编码重复

```python
# agent_shell.py
def _get_default(key: str) -> Any:
    field = next((f for f in fields(Settings) if f.name == key), None)
    if field and field.default is not MISSING:
        return field.default
    return None
```

#### 3.2 日志统一

- HTTP Server 日志统一输出到 `agent-shell.log`
- 日志格式：`[timestamp] [module] [level] message`
- 错误日志包含 traceback（通过 `logger.exception`）
- Electron 日志通过 `console.log` + `main.js` 中捕获

#### 3.3 窗口边界检查

```javascript
function restorePosition() {
  const saved = localStorage.getItem('agent-shell-pos');
  if (!saved) return;
  try {
    const { x, y } = JSON.parse(saved);
    const screenW = window.screen.availWidth;
    const screenH = window.screen.availHeight;
    const clampedX = Math.max(0, Math.min(x, screenW - 80));
    const clampedY = Math.max(0, Math.min(y, screenH - 80));
    mascotContainer.style.left = clampedX + 'px';
    mascotContainer.style.top = clampedY + 'px';
  } catch (e) { /* ignore */ }
}
```

#### 3.4 Phase 3 改动清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/config.py` | 修改 | 确保所有字段有 defaults |
| `src/agent_shell.py` | 修改 | 从 Settings 反射获取默认值 |
| `src/agent_shell/app.js` | 修改 | 窗口边界检查 |

---

### Phase 4（P3）— 高级特性

目标：完整的桌面 Agent 体验。

#### 4.1 多会话管理

- 在 Chat 面板顶部新增会话选择器（dropdown）
- 点击 "+" 新建会话（`POST /api/sessions`）
- 会话切换时，自动加载历史消息（`GET /api/sessions/:id/messages`）

#### 4.2 消息历史可视化

- 新消息气泡样式：Human 消息右对齐（蓝色）、AI 消息左对齐（灰色）
- 工具调用显示为内联 card（可折叠），展示输入/输出
- Streaming 中的消息显示为 "正在输入..." 动画

#### 4.3 快捷键

- `Ctrl+Enter` / `Cmd+Enter` 发送消息
- `↑` 召回上一条输入
- `Escape` 关闭当前面板
- `Ctrl+Shift+I` 打开 DevTools（调试用）

#### 4.4 会话导出

- 导出 JSON：包含全部消息、工具调用、metadata
- 导出 Markdown：格式化的对话记录

---

## 五、实现优先级矩阵

| 模块 | Phase | 工作量 | 影响面 | 建议 |
|------|-------|--------|--------|------|
| Chat API + SSE | P1 | 3-4 天 | 最核心功能 | 立刻做 |
| 前端 Chat UI | P1 | 2-3 天 | 用户直接感知 | 立刻做 |
| Agent Runtime 迁移 | P1 | 1-2 天 | 架构变更 | 做 |
| 错误处理 + 加载状态 | P2 | 1 天 | 用户体验 | 紧随 P1 |
| 子进程管理 | P2 | 1 天 | 稳定性 | 紧随 P1 |
| 健康检查 | P2 | 0.5 天 | 稳定性 | 做 |
| CORS Bug 修复 | P2 | 0.2 天 | 稳定性 | 顺手修 |
| 配置统一 | P3 | 0.5 天 | 可维护性 | 做 |
| 窗口边界检查 | P3 | 0.2 天 | 用户体验 | 顺手修 |
| 多会话管理 | P4 | 1 天 | 高级特性 | 有余力做 |
| 快捷键 | P4 | 0.5 天 | 高级特性 | 有余力做 |
| 会话导出 | P4 | 0.5 天 | 高级特性 | 有余力做 |

**总计核心工作量：** ~8-11 人天

---

## 六、关键技术细节

### 6.1 SSE 在 aiohttp 中的实现

```python
from aiohttp import web
import json

async def chat_stream(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )
    await resp.prepare(request)
    
    async for event in agent_runtime.chat(message, session_id):
        data = json.dumps(event, ensure_ascii=False)
        await resp.write(
            f"event: {event['type']}\ndata: {data}\n\n".encode("utf-8")
        )
    
    return resp
```

### 6.2 Agent Runtime 在同一 Event Loop 中运行

关键原则：**不要跨线程**。`aiohttp` 的 event loop 和 `agent.ainvoke()` 的 event loop 是同一个。

```python
def main():
    loop = asyncio.get_event_loop()
    
    # 1. 初始化 Agent Runtime（同步，因为里面只有 I/O 创建）
    settings = Settings.from_env()
    runtime = AgentRuntime(settings)
    
    # 2. 挂载到 app
    app = web.Application()
    app["runtime"] = runtime
    
    # 3. 启动（同一 loop）
    web.run_app(app, host="127.0.0.1", port=port, loop=loop)
```

**注意：** `web.run_app` 在 aiohttp 3.9+ 中已弃用 `loop` 参数。正确做法：

```python
async def run():
    settings = Settings.from_env()
    runtime = AgentRuntime(settings)
    
    app = web.Application()
    app["runtime"] = runtime
    setup_routes(app)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    
    # 保持进程运行
    await asyncio.Event().wait()

asyncio.run(run())
```

### 6.3 保留 REPL 作为辅助入口

REPL 和 Agent Server 共用同一个 `history.db`：

```
同一 SQLite 文件：
  agent_server (asyncio) ──┐
                           ├── history.db (AsyncSqliteSaver)
  REPL (asyncio.run) ──────┘
```

唯一约束：两者不要同时写同一个 thread。实际使用场景是"要么用 REPL，要么用 Shell"，不会同时用。

### 6.4 自动重启策略

```
Python 进程 crash → Electron 检测到 exit(code ≠ 0)
  ├─ restartCount < 3 → 等待 2s → respawn → health check
  └─ restartCount ≥ 3 → 放弃 → tray 显示 "Agent 服务异常"
  
每次重启成功 → restartCount = 0（连续 2 次检查通过）
```

---

## 七、测试策略

| 类型 | 覆盖内容 | 工具 |
|------|----------|------|
| 单元测试 | Chat handler 逻辑、SSE 事件格式 | `pytest` + `aiohttp.test_utils` |
| 前端测试 | 消息渲染、SSE 解析、错误状态 | Playwright (webapp-testing skill) |
| 集成测试 | Agent Runtime + HTTP Server | `pytest-asyncio` |
| E2E 测试 | Electron 全链路 | `electron-mocha` 或手动 |

### Chat Handler 单元测试示例

```python
@pytest.mark.asyncio
async def test_chat_sse_stream():
    """验证 POST /api/chat 返回合法的 SSE stream。"""
    client = await get_test_client()
    resp = await client.post("/api/chat", json={
        "message": "你好",
        "session_id": str(uuid.uuid4()),
    })
    assert resp.status == 200
    assert resp.content_type == "text/event-stream"
    
    events = []
    async for line in resp.content:
        ...  # 解析 SSE，验证 event 结构
```

---

## 八、回滚方案

每个 Phase 独立可交付，如果 Phase 1 出现问题，可以回退到：

1. **保留原有 `main.py` REPL** — `python -m src.main` 继续工作（读同一 history.db）
2. **保留原有 `http_server.py` 的 CRUD API** — 不删旧 API，只加新 API
3. **Electron 启动失败时回退到 pywebview** — Electron 检测到缺少运行时，可以 fallback

---

## 九、部署与分发

### Electron 打包

使用 `electron-builder`（已在 plan 中提及）：
```json
{
  "scripts": {
    "package": "electron-builder --win portable"
  },
  "build": {
    "appId": "com.agent-shell.app",
    "win": {
      "target": "portable"
    },
    "extraResources": [
      { "from": "src/agent_shell/", "to": "agent_shell/" }
    ]
  }
}
```

打包后为一个单文件 `agent-shell.exe`（portable 模式，无需安装）。

### Python 分发

方案 A：嵌入 Python 运行时到 electron-builder
- 用 `embedded-python` 将 Python 3.11 + 依赖打包进 NSIS 安装包

方案 B：要求用户本地安装 Python（当前做法）
- 用 `python` 命令启动 server

**推荐方案 B** 作为初始方案（零额外工程成本），后续需要时再升级到方案 A。

---

## 十、附录：问题复现与验证

### 当前 Shell 使用中的典型故障场景

| 场景 | 预期行为 | 当前实际行为 | 原因 |
|------|----------|-------------|------|
| 打开 Chat 面板 | 显示消息列表和输入框 | 只显示会话列表，无输入框 | 没有 Chat UI |
| 点击会话 | 查看对话详情 | 什么也不发生 | 没有 `getMessages` API |
| 服务器未启动时打开面板 | 显示错误提示 | 面板空白 | 没有错误处理 |
| 窗口拖出屏幕边缘 | 自动修正位置 | 窗口消失 | 没有边界检查 |
| Python 进程 crash | 自动重启 | 窗口变黑，需手动重启 | 没有 auto restart |
| 通过 Shell 改设置 | REPL 立即生效 | REPL 感知不到 | 双入口配置隔离 |

### 优化后的验证清单

- [ ] `POST /api/chat` 返回 SSE stream
- [ ] SSE 包含 `token`、`tool_start`、`tool_end`、`done` 四种事件
- [ ] 前端接收 SSE 并实时渲染 Markdown 文本
- [ ] 工具调用以 card 形式展示输入/输出
- [ ] API 请求失败时显示 error toast
- [ ] 面板展示 Loading → Data / Empty / Error 三态
- [ ] 窗口不会拖出屏幕可视区域
- [ ] Python 进程 crash 后 5s 内自动重启
- [ ] Electron 退出时 Python 进程优雅关闭
- [ ] 通过 Shell 修改设置，下次对话生效
- [ ] 历史会话可查看完整消息记录
- [ ] 消息自动滚动到底部

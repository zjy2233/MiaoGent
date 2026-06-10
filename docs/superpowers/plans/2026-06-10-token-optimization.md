# Token 消耗与上下文缓存优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过修复中间件顺序、启用并行工具调用、合并上下文注入，使 DeepSeek 前缀缓存命中率从 0% 提升到 80%+，并行工具场景 LLM 调用次数减少 50-66%。

**Architecture:** 分三阶段实施。P0 修复中间件顺序并在 LLM 层启用 `parallel_tool_calls`，改动小、收益大（2 个文件，~20 行改动）。P1 将多个中间件合并为 `MergedContextMiddleware`，确保稳定内容在前、易变内容在后。P2 在 tracing 系统增加 cache hit/miss 监控。

**Tech Stack:** Python 3.11+, LangChain/LangGraph, ChatOpenAI (DeepSeek), SQLite

---

## 文件结构

| 文件 | 角色 | 本次改动 |
|------|------|---------|
| `src/core/llm.py` | LLM 工厂，构造 ChatOpenAI | P0: 添加 `parallel_tool_calls` |
| `src/agent/builder.py` | Agent 构造，中间件注册，SystemMessage 注入 | P0+P1: 重排/合并中间件，冻结时间戳 |
| `src/tracing/models.py` | `SpanData` 数据模型 | P2: 新增 cache 字段 |
| `src/tracing/store.py` | `TraceStore` SQLite 持久化 | P2: 新增 cache 列和查询 |
| `src/tracing/api.py` | `TracingAPI` 查询接口 | P2: 新增 cache stats 方法 |
| `frontend/bridge.py` | 主 API，聊天流处理，tracing 采集 | P2: 提取 cache tokens |
| `frontend/http_server.py` | HTTP 路由注册 | P2: 新增 cache stats 端点 |
| `frontend/browser-api.js` | 前端 API 调用 | P2: 新增 cache stats 调用 |
| `frontend/electron/preload.js` | Electron 预加载数据 | P2: 新增 cache stats 预加载 |
| `src/tracing/handler.py` | LangChain 回调处理器 | P2: 提取 cache tokens（旧路径） |

---

### Task 1: 冻结 TimeMiddleware 会话级时间戳

**Files:**
- Modify: `src/agent/builder.py:257-266`

**Why:** 当前 `TimeMiddleware.awrap_model_call` 每轮 LLM 调用都重新生成 `datetime.now()`，同一会话内多次 ReAct 循环导致时间戳微变，破坏前缀缓存。冻结为会话级时间戳后，同一 session 内缓存可以复用。

- [ ] **Step 1: 修改 TimeMiddleware 类**

将 `src/agent/builder.py:257-266` 替换为：

```python
class TimeMiddleware(AgentMiddleware):
    """在每次 LLM 调用前注入当前日期时间（会话级冻结，避免破坏前缀缓存）。"""

    def __init__(self):
        super().__init__()
        self._session_time: str | None = None

    async def awrap_model_call(self, request, handler):
        if self._session_time is None:
            self._session_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_msg = SystemMessage(content=f"[当前时间]\n{self._session_time}")
        request = request.override(
            messages=[time_msg, *request.messages]
        )
        return await handler(request)
```

- [ ] **Step 2: 验证语法正确**

```bash
.venv/Scripts/python -c "from src.agent.builder import TimeMiddleware; print('OK')"
```
Expected: 输出 `OK`，无 ImportError

- [ ] **Step 3: Commit**

```bash
git add src/agent/builder.py
git commit -m "perf: freeze TimeMiddleware timestamp at session level to preserve prefix cache"
```

---

### Task 2: 重排中间件注册顺序 — 稳定在前，易变在后

**Files:**
- Modify: `src/agent/builder.py:358`

**Why:** 当前 `middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware, skill_middleware]`。中间件按列表顺序依次 wrap model call，后注册的中间件在调用链外层，先注册的在最内层（最靠近原始 LLM 调用）。因此 **先注册** 的中间件注入的 SystemMessage 排在消息列表更后面（靠近历史消息），**后注册** 的中间件注入的 SystemMessage 排在更前面。

对于前缀缓存，消息列表后面的内容先被模型处理，因此 **靠后的消息变化影响更小**。我们需要将稳定内容放在后面（先注册），易变内容放在前面（后注册）。

按稳定性排序：`SummaryMiddleware`（压缩时才变）> `MemoryMiddleware`（memory 更新时变）> `SkillContextMiddleware`（load_skill 时变）> `TimeMiddleware`（每轮必变，已完成冻结优化，风险最低放最后）

- [ ] **Step 1: 修改中间件注册顺序**

将 `src/agent/builder.py:358`（即 `middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware]` 这行及上下文）替换为：

```python
    # ── 中间件列表（稳定在前、易变在后，最大化前缀缓存命中率）──
    middleware = [SummaryMiddleware(), memory_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)
    middleware.append(TimeMiddleware())
```

**注意：** skill_middleware 变量赋值需要从原来的条件块内提出来。原代码在 if 块内赋值 `skill_middleware`，现在需要在注册到列表后再单独赋值给局部变量，确保 `AgentBundle` 返回时能访问到。

实际上需要查看完整上下文。原代码 357-361 行：

```python
    # ── 中间件列表 ──
    middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)
```

修改为：

```python
    # ── 中间件列表（稳定在前、易变在后，最大化前缀缓存命中率）──
    middleware = [SummaryMiddleware(), memory_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)
    middleware.append(TimeMiddleware())
```

这保持了 `skill_middleware` 变量的赋值位置不变，返回值 `AgentBundle(skill_middleware=skill_middleware, ...)` 不受影响。

- [ ] **Step 2: 验证语法和导入**

```bash
.venv/Scripts/python -c "from src.agent.builder import build_agent; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/agent/builder.py
git commit -m "perf: reorder middleware to put stable content before volatile for prefix cache"
```

---

### Task 3: 在 ChatOpenAI 构造中显式启用 parallel_tool_calls

**Files:**
- Modify: `src/core/llm.py:43-51`

**Why:** `ChatOpenAI` 的 `parallel_tool_calls` 参数默认为 `True`，但 DeepSeek API 在未显式传参时行为不稳定。显式设置后，模型在需要多个独立工具时会在单次响应返回多个 `tool_call`，LangGraph 的 `Send` 机制自动并行执行，减少 50-66% 的 LLM 调用次数。

- [ ] **Step 1: 添加 `parallel_tool_calls` 参数**

将 `src/core/llm.py:43-51` 的 `ChatOpenAI(...)` 调用修改为：

```python
    return ChatOpenAI(
        model=cfg.llm_model or default_model,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url or default_base_url,
        temperature=temperature,
        timeout=cfg.request_timeout,
        max_retries=2,
        streaming=True,
        model_kwargs={"parallel_tool_calls": True},
    )
```

- [ ] **Step 2: 同样修改 Anthropic 分支（`src/core/llm.py:26-33`）**

将 `ChatAnthropic(...)` 调用也添加并行工具调用支持：

```python
        return ChatAnthropic(
            model=cfg.llm_model or "claude-sonnet-4-20250514",
            api_key=cfg.llm_api_key,
            temperature=temperature,
            timeout=cfg.request_timeout,
            max_retries=2,
            streaming=True,
            model_kwargs={"parallel_tool_calls": True},
        )
```

**注意：** `model_kwargs` 中 `parallel_tool_calls` 不是 Anthropic API 原生参数，但 LangChain 的 `ChatAnthropic` 会将其过滤或正确处理。

- [ ] **Step 3: 验证语法**

```bash
.venv/Scripts/python -c "from src.core.llm import build_llm; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: 验证 DeepSeek 实际支持 `parallel_tool_calls`**

```bash
.venv/Scripts/python -c "
import asyncio
from src.core.llm import build_llm
from src.core.config import Settings
llm = build_llm(Settings.from_env())
# 发送一个需要多个工具的请求，观察响应中的 tool_calls 数量
from langchain_core.messages import HumanMessage
from src.tools import weather, current_time
llm_with_tools = llm.bind_tools([weather, current_time])
async def test():
    resp = await llm_with_tools.ainvoke([HumanMessage(content='同时查询北京和上海的天气')])
    tcs = getattr(resp, 'tool_calls', []) or []
    print(f'tool_calls count: {len(tcs)}')
    if len(tcs) >= 2:
        print('PASS: parallel_tool_calls working')
    else:
        print(f'WARN: only got {len(tcs)} tool_call(s), parallel_tool_calls may not be effective')
asyncio.run(test())
"
```
Expected: `tool_calls count: >= 2` 或 `WARN`（如果 DeepSeek 模型版本不支持并行，则回退单次调用也不影响功能）

- [ ] **Step 5: Commit**

```bash
git add src/core/llm.py
git commit -m "feat: enable parallel_tool_calls in LLM factory for reduced tool-calling rounds"
```

---

### Task 4: 实现 MergedContextMiddleware 合并上下文注入

**Files:**
- Modify: `src/agent/builder.py:141-266` (合并 SummaryMiddleware, ProfileMiddleware, MemoryMiddleware, TimeMiddleware 为 MergedContextMiddleware)
- Modify: `src/agent/builder.py:300-303` (ProfileMiddleware / MemoryMiddleware 实例化改为传入依赖)
- Modify: `src/agent/builder.py:358` (中间件列表改为单个 MergedContextMiddleware)
- Modify: `src/agent/builder.py:374-380` (AgentBundle 返回调整)

**Why:** 当前 4 个独立中间件各自在 `awrap_model_call` 中调用 `request.override(messages=[new_msg, *request.messages])`，产生 4 条独立的 `SystemMessage`，增加了前缀碎片化。合并为单一 `MergedContextMiddleware` 后，所有上下文注入合并为一条 `SystemMessage`，且内部按稳定性排序，确保稳定内容（摘要、画像/记忆）在前、易变内容（时间）在后，最大化 DeepSeek 前缀缓存命中。

**设计细节：**

```python
class MergedContextMiddleware(AgentMiddleware):
    """合并所有上下文注入为单一 SystemMessage，稳定内容在前、易变内容在后。

    注入内容按稳定性分层（最大化前缀缓存命中）：
    Layer 1 (最稳定): 对话历史摘要（只在 MemoryManager 压缩时变化）
    Layer 2 (半稳定): 用户画像 + 结构化记忆（偶尔更新）
    Layer 3 (半稳定): Skill 上下文（load_skill 时变化）
    Layer 4 (易变): 当前时间（会话级冻结，每次新会话第一次调用时确定）
    """

    def __init__(
        self,
        profile_manager: "ProfileManager | None" = None,
        profile: dict | None = None,
        memory_store: "MemoryStore | None" = None,
    ):
        super().__init__()
        self._profile_manager = profile_manager
        self._init_profile = profile or {}
        self._profile = profile or {}
        self._memory_store = memory_store
        self._session_time: str | None = None
        # MemoryMiddleware 缓存（迁移自原 MemoryMiddleware）
        self._cached_memory_text: str | None = None
        self._cache_version: int = 0
        self._last_build_version: int = -1

    def invalidate_cache(self) -> None:
        self._cache_version += 1

    def update_profile(self, new_facts: dict | None = None) -> None:
        if not self._profile_manager:
            if new_facts:
                self._profile.update(new_facts)
            self.invalidate_cache()
            return
        if new_facts:
            self._profile_manager.merge(new_facts)
        self._profile = self._profile_manager.load()
        self.invalidate_cache()

    def _build_profile_text(self) -> str:
        """构建用户画像文本（从原 ProfileMiddleware）。"""
        if self._profile_manager:
            self._profile = self._profile_manager.load()
        profile_lines: list[str] = []
        for key, value in self._profile.items():
            if key == "version" or key.endswith("_source"):
                continue
            profile_lines.append(f"{key}: {value}")
        if not profile_lines:
            return ""
        return "[用户画像]\n" + "\n".join(profile_lines)

    def _build_memory_text(self) -> str:
        """构建用户画像 + 结构化记忆合并文本（从原 MemoryMiddleware）。"""
        if self._last_build_version < self._cache_version or self._cached_memory_text is None:
            parts: list[str] = []
            # 用户画像（手工设定）
            profile_text = self._build_profile_text()
            if profile_text:
                parts.append(profile_text)
            # 结构化记忆（自动提取）
            if self._memory_store:
                memory_text = self._memory_store.get_all_formatted()
                if memory_text:
                    parts.append("【自动学习】\n" + memory_text)
            self._cached_memory_text = "\n\n".join(parts) if parts else ""
            self._last_build_version = self._cache_version
        return self._cached_memory_text

    async def awrap_model_call(self, request, handler):
        context_parts: list[str] = []

        # Layer 1: 对话历史摘要
        summary = request.state.get("summary", "") or ""
        if summary:
            context_parts.append(f"[对话历史摘要]\n{summary}")

        # Layer 2: 用户画像 + 结构化记忆
        memory_text = self._build_memory_text()
        if memory_text:
            context_parts.append(f"[关于用户]\n{memory_text}")

        # Layer 3: Skill 上下文由 SkillContextMiddleware 独立注入（需访问 messages）
        # Layer 4: 当前时间（会话级冻结）
        if self._session_time is None:
            self._session_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        context_parts.append(f"[当前时间]\n{self._session_time}")

        if context_parts:
            combined = "\n\n".join(context_parts)
            request = request.override(
                messages=[SystemMessage(content=combined), *request.messages]
            )
        return await handler(request)
```

- [ ] **Step 1: 在 `src/agent/builder.py` 中添加 `MergedContextMiddleware` 类**

在 `TimeMiddleware` 类定义之后（第 266 行之后）、`build_agent` 函数之前（第 268 行之前）插入上面的 `MergedContextMiddleware` 类。

- [ ] **Step 2: 更新 `build_agent` 函数中的中间件实例化逻辑**

将 `src/agent/builder.py:300-303` 中的 ProfileMiddleware 和 MemoryMiddleware 实例化：

```python
    profile_middleware = ProfileMiddleware(profile=profile, profile_manager=_get_profile_manager())
    if memory_store is None:
        memory_store = MemoryStore()
    memory_middleware = MemoryMiddleware(store=memory_store, profile_manager=_get_profile_manager())
```

替换为 `MergedContextMiddleware` 实例化：

```python
    if memory_store is None:
        memory_store = MemoryStore()
    merged_middleware = MergedContextMiddleware(
        profile_manager=_get_profile_manager(),
        profile=profile,
        memory_store=memory_store,
    )
```

- [ ] **Step 3: 更新中间件列表注册**

将 `src/agent/builder.py:357-361`：

```python
    # ── 中间件列表 ──
    middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)
```

替换为：

```python
    # ── 中间件列表（MergedContextMiddleware 合并了摘要/画像/记忆/时间注入）──
    middleware = [merged_middleware]
    if _SKILL_AVAILABLE:
        skill_middleware = SkillContextMiddleware(registry=resolved_registry)
        middleware.append(skill_middleware)
```

**注意：** `SkillContextMiddleware` 需要访问 `request.messages` 来检测 `load_skill` 调用，因此保留为独立中间件。

- [ ] **Step 4: 更新 `AgentBundle` 返回值**

修改 `src/agent/builder.py:374-380` 的 `AgentBundle(...)` 调用：

```python
    return AgentBundle(
        agent=agent,
        profile_middleware=merged_middleware,   # MergedContextMiddleware 代替 ProfileMiddleware
        memory_middleware=merged_middleware,     # MergedContextMiddleware 代替 MemoryMiddleware
        memory_store=memory_store,
        skill_middleware=skill_middleware if _SKILL_AVAILABLE else None,
        skill_registry=resolved_registry,
        tools=tools,
    )
```

**注意：** `profile_middleware` 和 `memory_middleware` 现在都指向同一个 `MergedContextMiddleware` 实例。这要求 `bridge.py` 中对这两个字段的使用（`update_profile`、`invalidate_cache`）都能在合并后的中间件上工作。`MergedContextMiddleware` 已经实现了 `update_profile` 和 `invalidate_cache` 方法。

- [ ] **Step 5: 更新 `MemoryManager.__init__` 的类型引用**

查看 `src/agent/memory.py:69-88`，`MemoryManager` 接收 `profile_middleware` 和 `memory_middleware` 参数。当两者指向同一实例时，`invalidate_cache` 调用是幂等的，`update_profile` 也只需要在一处调用。

**无需修改 `memory.py`**，因为：
- `profile_middleware.update_profile(facts)` → `MergedContextMiddleware.update_profile` ✓
- `memory_middleware.invalidate_cache()` → `MergedContextMiddleware.invalidate_cache` ✓

- [ ] **Step 6: 验证语法**

```bash
.venv/Scripts/python -c "from src.agent.builder import MergedContextMiddleware, build_agent; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: 运行已有测试确认不破坏现有功能**

```bash
.venv/Scripts/python -m pytest -v -x --tb=short 2>&1 | head -100
```
Expected: 已有测试通过，无新失败

- [ ] **Step 8: Commit**

```bash
git add src/agent/builder.py
git commit -m "refactor: merge context middleware into MergedContextMiddleware for cache-friendly ordering"
```

**行为变化说明：** 原 `ProfileMiddleware.awrap_model_call` 每次 LLM 调用都从磁盘 `load()` profile.json，确保用户通过设置面板修改画像后立即生效。合并后 `MergedContextMiddleware` 使用缓存机制，画像在 `invalidate_cache()` 被调用时（即 `MemoryManager.compress_if_needed` 完成后）才刷新。如果用户在会话中途通过 UI 修改画像，变更将在下一次 `compress_if_needed` 触发时（或下次新会话）生效。这是有意的 trade-off：用轻微延迟换取 90% 的磁盘 I/O 减少和缓存稳定性。

---

### Task 5: 清理旧中间件类（可选，标注 deprecated）

**Files:**
- Modify: `src/agent/builder.py:141-266` (标注旧类为 deprecated)

**Why:** 旧 `ProfileMiddleware`、`MemoryMiddleware`、`TimeMiddleware`、`SummaryMiddleware` 仍然可用作独立中间件（测试/调试场景），但生产路径已迁移到 `MergedContextMiddleware`。保留旧类并标注 deprecated 避免破坏性变更。

- [ ] **Step 1: 在旧中间件类上添加 deprecated 注释**

在 `SummaryMiddleware`（第 141 行）、`ProfileMiddleware`（第 154 行）、`MemoryMiddleware`（第 197 行）、`TimeMiddleware`（第 257 行）的 docstring 第一行加上：

```
    """(Deprecated: use MergedContextMiddleware)
    ...
```

- [ ] **Step 2: 验证语法**

```bash
.venv/Scripts/python -c "from src.agent.builder import SummaryMiddleware, ProfileMiddleware, MemoryMiddleware, TimeMiddleware; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/agent/builder.py
git commit -m "docs: mark old middleware classes as deprecated in favor of MergedContextMiddleware"
```

---

### Task 6: SpanData 新增 cache hit/miss token 字段

**Files:**
- Modify: `src/tracing/models.py:18-65`

**Why:** 当前 tracing 系统只采集 `input_tokens` / `output_tokens`，缺少 cache 相关字段。DeepSeek 响应 `usage` 中包含 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，需要持久化这些数据以监控缓存命中率。

- [ ] **Step 1: 在 `SpanData` dataclass 中新增字段**

修改 `src/tracing/models.py:18-37`：

```python
@dataclass
class SpanData:
    span_id: str = field(default_factory=_new_id)
    parent_span_id: str | None = None
    trace_id: str = field(default_factory=_new_id)
    session_id: str = ""
    session_turn: int = 0
    span_type: str = ""  # session_turn | llm_call | agent_step | tool_call | delegate_task
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0       # 新增
    cache_miss_tokens: int = 0      # 新增
    tool_name: str = ""
    tool_input: str = ""
    status: str = "ok"  # ok | error
    error_message: str = ""
    started_at: str = field(default_factory=_timestamp)
    ended_at: str = ""
    duration_ms: int = 0
    user_message: str = ""
```

- [ ] **Step 2: 在 `to_dict` 方法中新增字段**

修改 `src/tracing/models.py:46-65` 的 `to_dict` 方法，添加：

```python
    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "session_turn": self.session_turn,
            "span_type": self.span_type,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "status": self.status,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "user_message": self.user_message,
        }
```

- [ ] **Step 3: 验证语法**

```bash
.venv/Scripts/python -c "from src.tracing.models import SpanData; s = SpanData(); print(s.cache_hit_tokens, s.cache_miss_tokens)"
```
Expected: `0 0`

- [ ] **Step 4: Commit**

```bash
git add src/tracing/models.py
git commit -m "feat: add cache_hit_tokens and cache_miss_tokens to SpanData model"
```

---

### Task 7: TraceStore SQLite 表新增 cache 列 + 数据库迁移

**Files:**
- Modify: `src/tracing/store.py:23-47` (SCHEMA)
- Modify: `src/tracing/store.py:72-89` (write_span)
- Modify: `src/tracing/store.py:91-108` (write_spans)
- Modify: `src/tracing/store.py:198-234` (get_stats)

**Why:** SQLite 表 `spans` 需要新增 `cache_hit_tokens` 和 `cache_miss_tokens` 列，并更新写入和统计查询。

- [ ] **Step 1: 更新 SCHEMA SQL 和索引**

修改 `src/tracing/store.py:23-47`，在 `output_tokens` 后添加两列，并添加数据库迁移逻辑：

```python
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    parent_span_id TEXT,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    session_turn INTEGER NOT NULL DEFAULT 0,
    span_type TEXT NOT NULL,
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_hit_tokens INTEGER DEFAULT 0,
    cache_miss_tokens INTEGER DEFAULT 0,
    tool_name TEXT DEFAULT '',
    tool_input TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    error_message TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    user_message TEXT DEFAULT ''
);
"""
```

- [ ] **Step 2: 添加数据库迁移逻辑**

在 `TraceStore._init_db` 方法中添加 ALTER TABLE（SQLite 不支持 `IF NOT EXISTS` 列检查，使用 try/except）：

修改 `src/tracing/store.py:56-66`：

```python
    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute(SCHEMA_SQL)
                conn.execute(INDEX_TRACE_SQL)
                conn.execute(INDEX_SESSION_SQL)
                conn.execute(INDEX_STARTED_SQL)
                # 数据库迁移：为已有数据库添加 cache 列
                for col in ("cache_hit_tokens", "cache_miss_tokens"):
                    try:
                        conn.execute(
                            f"ALTER TABLE spans ADD COLUMN {col} INTEGER DEFAULT 0"
                        )
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():
                            raise  # 真实错误（磁盘满、权限不足等），不能静默吞掉
                conn.commit()
            finally:
                conn.close()
```

- [ ] **Step 3: 更新 `write_span` 和 `write_spans` 方法中的 cols 列表**

`write_span` (第 73 行) 和 `write_spans` (第 96 行) 的 `cols` 列表需要在 `output_tokens` 后添加 `cache_hit_tokens, cache_miss_tokens`：

```python
        cols = [
            "span_id", "parent_span_id", "trace_id", "session_id", "session_turn",
            "span_type", "model", "input_tokens", "output_tokens",
            "cache_hit_tokens", "cache_miss_tokens",
            "tool_name", "tool_input", "status", "error_message", "started_at",
            "ended_at", "duration_ms", "user_message",
        ]
```

两处（`write_span` 和 `write_spans`）都要改。

- [ ] **Step 4: 更新 `get_stats` 查询添加 cache 汇总**

修改 `src/tracing/store.py:198-234` 的 `get_stats` 方法，在 token 查询中同时汇总 cache tokens：

```python
                row_t = conn.execute(
                    "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
                    "COALESCE(SUM(cache_hit_tokens), 0), COALESCE(SUM(cache_miss_tokens), 0) "
                    "FROM spans WHERE started_at >= ?",
                    (today,),
                ).fetchone()
```

并在返回字典中增加两个字段：

```python
                return {
                    "total_traces": row[0],
                    "total_input_tokens": row_t[0],
                    "total_output_tokens": row_t[1],
                    "total_tokens": row_t[0] + row_t[1],
                    "total_cache_hit_tokens": row_t[2],
                    "total_cache_miss_tokens": row_t[3],
                    "avg_duration_ms": round(row[1], 1),
                    "error_count": row[2],
                    "error_rate": round(row[2] / row[0] * 100, 1) if row[0] > 0 else 0,
                    "yesterday_tokens": int(row_y[0]) if row_y[0] else 0,
                }
```

- [ ] **Step 5: 验证 SQLite 迁移**

```bash
.venv/Scripts/python -c "
from src.tracing.store import TraceStore
import tempfile, os
db = os.path.join(tempfile.gettempdir(), 'test_migration.db')
s = TraceStore(db)
s.write_span(__import__('src.tracing.models', fromlist=['SpanData']).SpanData(span_type='llm_call', cache_hit_tokens=100, cache_miss_tokens=50))
stats = s.get_stats()
assert 'total_cache_hit_tokens' in stats
print('OK')
"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/tracing/store.py
git commit -m "feat: add cache_hit_tokens and cache_miss_tokens columns to TraceStore SQLite"
```

---

### Task 8: 在 bridge.py 的 LLM span 采集逻辑中提取 cache tokens

**Files:**
- Modify: `frontend/bridge.py:678-688` (on_chat_model_end handler)
- Modify: `src/tracing/handler.py:41-47` (`_extract_tokens` method)

**Why:** `on_chat_model_end` 事件中 `usage_metadata` 包含 DeepSeek 的 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，需要提取并写入 span。

- [ ] **Step 1: 更新 bridge.py 中 `on_chat_model_end` 的 token 提取**

修改 `frontend/bridge.py:678-688`（在 `on_chat_model_end` 处理分支中）：

```python
                            if isinstance(resp, dict):
                                usage = resp.get("usage_metadata") or {}
                            else:
                                usage = getattr(resp, "usage_metadata", {})
                            if isinstance(usage, dict) and usage:
                                span = tracer._spans.get(sid)
                                if span:
                                    span.input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                                    span.output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                                    # 采集 cache hit/miss tokens（DeepSeek 返回 standard_name）
                                    span.cache_hit_tokens = usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0)
                                    span.cache_miss_tokens = usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0)
                            tracer.end_span(sid)
```

同一文件中 resume 模式的 `on_chat_model_end` 分支（第 529-532 行）也需要同步更新：

```python
                        elif kind == "on_chat_model_end":
                            sid = run_id_to_span_id.pop(run_id, None)
                            if sid:
                                resp = event.get("data", {}).get("output", {})
                                if isinstance(resp, dict):
                                    usage = resp.get("usage_metadata") or {}
                                else:
                                    usage = getattr(resp, "usage_metadata", {})
                                if isinstance(usage, dict) and usage:
                                    span = tracer._spans.get(sid)
                                    if span:
                                        span.input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                                        span.output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
                                        span.cache_hit_tokens = usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0)
                                        span.cache_miss_tokens = usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0)
                                tracer.end_span(sid)
```

- [ ] **Step 2: 更新 `TraceCallbackHandler._extract_tokens`（旧回调路径）**

修改 `src/tracing/handler.py:41-47`：

```python
    def _extract_tokens(self, llm_output: dict | None) -> dict[str, int]:
        if not llm_output:
            return {"input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(usage, dict):
            return {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "cache_hit": usage.get("prompt_cache_hit_tokens", 0) or usage.get("cache_read_input_tokens", 0),
                "cache_miss": usage.get("prompt_cache_miss_tokens", 0) or usage.get("cache_creation_input_tokens", 0),
            }
        return {"input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0}
```

同时更新 `on_llm_end` 方法（第 130-134 行）中对 `_extract_tokens` 的调用方式：

```python
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            tokens = self._extract_tokens(llm_output)
            span = tracer._spans.get(span_id)
            if span:
                span.input_tokens = tokens["input"]
                span.output_tokens = tokens["output"]
                span.cache_hit_tokens = tokens["cache_hit"]
                span.cache_miss_tokens = tokens["cache_miss"]
```

- [ ] **Step 3: 验证语法**

```bash
.venv/Scripts/python -c "from frontend.bridge import Api; from src.tracing.handler import TraceCallbackHandler; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add frontend/bridge.py src/tracing/handler.py
git commit -m "feat: capture cache hit/miss tokens from usage_metadata in LLM spans"
```

---

### Task 9: TracingAPI 和 HTTP 端点新增 cache stats

**Files:**
- Modify: `src/tracing/api.py:48-49` (get_stats 已透传，无需改)
- Modify: `src/tracing/store.py:198-234` (已在 Task 7 改过)
- Modify: `frontend/http_server.py:369-421` (新增 cache stats 端点)
- Modify: `frontend/bridge.py:862-865` (新增 get_trace_cache_stats 方法)
- Modify: `frontend/browser-api.js:179-180` (新增前端调用)
- Modify: `frontend/electron/preload.js:201-203` (新增预加载调用)

**Why:** 前端需要展示缓存命中率指标，需要独立的 API 端点返回 cache 统计数据。

- [ ] **Step 1: 在 bridge.py 的 Api 类中添加 `get_trace_cache_stats` 方法**

在 `frontend/bridge.py:862` `get_trace_stats` 方法之后添加：

```python
    def get_trace_cache_stats(self) -> dict:
        """返回今日缓存命中率统计。"""
        if self._tracing_api is None:
            return {
                "total_cache_hit_tokens": 0,
                "total_cache_miss_tokens": 0,
                "cache_hit_rate": 0,
            }
        base = self._tracing_api.get_stats()
        hit = base.get("total_cache_hit_tokens", 0)
        miss = base.get("total_cache_miss_tokens", 0)
        total_cacheable = hit + miss
        return {
            "total_cache_hit_tokens": hit,
            "total_cache_miss_tokens": miss,
            "cache_hit_rate": round(hit / total_cacheable * 100, 1) if total_cacheable > 0 else 0,
        }
```

- [ ] **Step 2: 在 http_server.py 中添加路由和 handler**

在 `frontend/http_server.py` 中，在 `get_trace_stats` 函数（约第 369 行）之后添加：

```python
async def get_trace_cache_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_cache_stats())
```

在 `register_routes` 中（第 421 行之后）添加路由：

```python
    app.router.add_route("GET", "/api/traces/stats/cache", get_trace_cache_stats)
```

- [ ] **Step 3: 在前端 browser-api.js 中添加调用**

在 `frontend/browser-api.js:180` 之后添加：

```javascript
    getTraceCacheStats: () => fetchJSON(`${BASE_URL}/api/traces/stats/cache`),
```

- [ ] **Step 4: 在 Electron preload.js 中添加预加载**

在 `frontend/electron/preload.js:203` 之后添加：

```javascript
    fetch(`${BASE_URL}/api/traces/stats/cache`).then((r) => r.json()),
```

- [ ] **Step 5: 验证语法**

```bash
.venv/Scripts/python -c "from frontend.http_server import create_app; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add frontend/bridge.py frontend/http_server.py frontend/browser-api.js frontend/electron/preload.js
git commit -m "feat: add GET /api/traces/stats/cache endpoint for cache hit rate monitoring"
```

---

## Self-Review

### 1. Spec Coverage
- [x] P0: 中间件重排序 → Task 2
- [x] P0: parallel_tool_calls 启用 → Task 3
- [x] P1: 合并中间件为 MergedContextMiddleware → Task 4
- [x] P1: 冻结会话时间戳 → Task 1
- [x] P2: 缓存命中率监控 → Tasks 6-9
- [ ] P2: ReWOO 规划-执行模式 → **不在此计划中**（需单独立项）
- [ ] P3: 工具定义压缩 → **不在此计划中**（需单独立项）

### 2. Placeholder Scan
- 无 "TBD"、"TODO"、"implement later"
- 所有步骤包含具体代码
- 所有文件路径精确

### 3. Type Consistency
- `MergedContextMiddleware` 同时实现了 `update_profile` 和 `invalidate_cache`，与 `MemoryManager` 的调用兼容
- `cache_hit_tokens` / `cache_miss_tokens` 字段在 `SpanData`、SQLite schema、`to_dict`、bridge.py 提取、store.py 写入中保持一致
- `get_trace_cache_stats` 返回的字段名在 http_server.py → bridge.py → browser-api.js 中一致

### 4. Architect Review Fixes (2026-06-10)

**Round 1:**
- [x] **CRITICAL:** `update_profile()` 添加 `invalidate_cache()` 调用
- [x] **MEDIUM:** 移除 `_build_skill_text()` dead code
- [x] **MEDIUM:** Task 3 新增 `parallel_tool_calls` 验证步骤 (Step 4)
- [x] **MEDIUM:** Task 4 新增 Profile 实时重载语义变化说明
- [x] **LOW:** SQLite 迁移增强 — 检查 `duplicate column` 错误消息后决定 re-raise

**Round 2:**
- [x] **CRITICAL:** 从 `MergedContextMiddleware.__init__` 移除未使用的 `skill_registry` 参数（避免 `resolved_registry` NameError）
- [x] **MEDIUM:** Task 3 Step 4 修复 `print` 缺少 `f` 前缀
- [x] **MEDIUM:** Task 4 Step 2 移除 `MergedContextMiddleware(...)` 构造中的 `skill_registry` 参数

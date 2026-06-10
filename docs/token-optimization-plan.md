# Token 消耗与上下文缓存优化方案

## 一、现状诊断

### 1.1 工具调用：单步串行，Token 浪费严重

**根因分析：**

当前 agent 使用标准 ReAct 循环（`langchain.agents.create_agent`），每轮对话的流程是：

```
User → LLM(含全部上下文) → 返回1个tool_call → 执行工具 → LLM(含全部上下文+工具结果) → 返回1个tool_call → ...
```

每次 LLM 调用都要重新发送全部上下文（system prompt + tools + 历史消息 + 中间件注入），即使连续调用之间只有一条 ToolMessage 不同。

具体问题点：
- `src/core/llm.py:43-51` — `ChatOpenAI` 构造时**没有设置 `parallel_tool_calls=True`**，虽然默认值是 `True`，但 DeepSeek 模型在未显式配置时行为不稳定
- `src/agent/builder.py:364` — `create_agent` 调用时**没有传 `tool_choice`**，默认为 `None`，模型自行决定是否调用工具，可能过于保守
- 当 agent 需要调用 3 个独立工具时（如同时查天气、搜索、读文件），当前是 3 次 LLM 调用 + 3 次工具执行，而非 1 次 LLM 调用 + 3 次并行工具执行

**浪费量化（典型场景）：**

| 场景 | 当前模式 | 理想模式 | Token 浪费 |
|------|---------|---------|-----------|
| 查询3个城市天气 | 3次LLM调用 | 1次LLM调用(3个tool_call) | ~66% |
| 搜索+读文件+计算 | 3次LLM调用 | 1次LLM调用(3个tool_call) | ~66% |
| Sub-agent委派 | 嵌套完整上下文 | 每次重新发送全部系统提示词 | ~50%+ |

### 1.2 上下文组装：中间件顺序破坏前缀缓存

**根因分析：**

`src/agent/builder.py:358` 的中间件注册顺序：

```python
middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware, skill_middleware]
```

每个中间件通过 `request.override(messages=[new_msg, *request.messages])` 在消息列表头部插入 `SystemMessage`。最终消息顺序为：

```
[skill_context] → [memory] → [summary] → [time(每轮都变!)] → [system_prompt] → [tools] → [历史消息]
```

**关键问题：`TimeMiddleware` 排在第一个执行，其注入的动态时间戳每轮都变，导致整个前缀缓存失效。**

DeepSeek 使用自动前缀缓存（基于 KV block 共享），任何前缀字节变化都会导致该位置之后的所有缓存失效。当前架构下，`time` 注入在最前面，使其后方的 system_prompt、tools（最稳定的内容）每轮都被重新计算。

**缓存利用率估算：**

- system_prompt + tools 约 3-5K tokens（最稳定，应该 100% 缓存命中）
- 历史摘要 + memory 约 0.5-2K tokens（半稳定，偶尔变化）
- 当前时间戳 约 20 tokens（每轮必变）

当前：缓存命中率 ≈ 0%（因为最前面就是变化的）
理想：缓存命中率 ≈ 80-90%（稳定内容在前）

### 1.3 无缓存监控

当前 tracing 系统（`src/tracing/`）已经采集了 `input_tokens` / `output_tokens`，但没有采集/展示 cache hit/miss tokens，无法量化缓存效果。

---

## 二、业界成熟方案

### 2.1 DeepSeek 上下文缓存机制

DeepSeek V4 使用 **自动前缀缓存**（不同于 Anthropic 的显式 `cache_control`）：

- 基于 KV block 共享，相同前缀自动复用
- 缓存命中 token 成本约 10%（节省 ~90%）
- TTL 为"数小时到数天"（best-effort）
- 响应 `usage` 中包含 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`

**关键原则：稳定内容在前，易变内容在后。**

### 2.2 Anthropic Prompt Caching（作为对比参考）

- 显式 `cache_control` breakpoint，最多 4 个
- 5min TTL（1.25x 写入成本）和 1h TTL（2x 写入成本）
- 内容块至少 1024 tokens（Opus/Sonnet）才可缓存
- 缓存失效级联：tools 变化 → system + messages 都失效

### 2.3 工具调用优化模式

| 模式 | 原理 | Token 节省 | 适用场景 |
|------|------|-----------|---------|
| **ReWOO** | 一次规划全部工具 → 并行执行 → 一次合成 | ~80% | 3+ 独立工具调用 |
| **LLMCompiler** | 生成 DAG → 并行执行独立节点 | 3.4x-6.7x | 复杂有依赖的任务 |
| **CodeAct** | 工具编排写成代码块一次执行 | ~64% | 多步计算/文件操作 |
| **Parallel Tool Calling** | 原生支持，一个响应返回多个 tool_call | ~50-66% | 2-5 个独立工具调用 |

### 2.4 三层提示词架构（Amigo/NVIDIA 生产实践）

```
Layer 1 (Static Prefix, 5-8K):   Agent 角色 + 核心指令 + 工具定义 → 永不过期
Layer 2 (Append-Only Log):       对话历史（绝对时间戳，不修改旧消息）→ 部分复用
Layer 3 (Dynamic Suffix, 2-3K):  当前目标 + 动态上下文 → 每轮重算
```

---

## 三、解决方案

### 阶段一：低风险快速优化（预计节省 50-70% token，1-2天）

#### 3.1.1 修复中间件顺序（最高优先级）

**文件：`src/agent/builder.py:358`**

```python
# 当前（错误）：volatile 在前，破坏缓存
middleware = [TimeMiddleware(), SummaryMiddleware(), memory_middleware]

# 修复后：stable 在前，volatile 在后
middleware = [SummaryMiddleware(), memory_middleware, TimeMiddleware()]
```

**原理：** LangChain middleware 按列表顺序执行，后执行的注入的 SystemMessage 在外层（消息列表更靠前）。把 TimeMiddleware 放最后，其注入的动态时间戳在消息列表最前面时会被后续 middleware 的注入覆盖到后面。实际上需要更精细的控制。

**更精确的方案：** 将所有中间件注入合并为一个方法，按稳定性排序输出 SystemMessage：

```python
class MergedContextMiddleware(AgentMiddleware):
    """合并所有上下文注入，确保稳定内容在前、易变内容在后。"""

    async def awrap_model_call(self, request, handler):
        context_parts = []

        # Layer 1: 最稳定 — 摘要（只在压缩后变化）
        summary = request.state.get("summary", "") or ""
        if summary:
            context_parts.append(f"[对话历史摘要]\n{summary}")

        # Layer 2: 半稳定 — 用户画像/记忆（偶尔更新）
        memory_text = self._build_memory_text()
        if memory_text:
            context_parts.append(f"[关于用户]\n{memory_text}")

        # Layer 3: 半稳定 — Skill 上下文
        skill_text = self._build_skill_text()
        if skill_text:
            context_parts.append(skill_text)

        # Layer 4: 易变 — 当前时间（每轮变化，放最后！）
        context_parts.append(f"[当前时间]\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if context_parts:
            combined = "\n\n".join(context_parts)
            request = request.override(
                messages=[SystemMessage(content=combined), *request.messages]
            )

        return await handler(request)
```

**效果：** system_prompt + tools 在消息列表中排在 context 之后，但由于 SystemMessage 是一条合并的消息（而非多条），前缀缓存可以覆盖到 system_prompt + tools 的完整区块。

#### 3.1.2 启用 `parallel_tool_calls`

**文件：`src/core/llm.py:43-51`**

```python
return ChatOpenAI(
    model=cfg.llm_model or default_model,
    api_key=cfg.llm_api_key,
    base_url=cfg.llm_base_url or default_base_url,
    temperature=temperature,
    timeout=cfg.request_timeout,
    max_retries=2,
    streaming=True,
    model_kwargs={"parallel_tool_calls": True},  # 新增
)
```

或在 `builder.py` 中通过 `create_agent` 的 `model_settings` 传入。

**效果：** DeepSeek 在需要多个独立工具时，会在一次响应中返回多个 `tool_call`，LangGraph 的 `Send` 机制自动并行执行，减少 50-66% 的 LLM 调用次数。

#### 3.1.3 冻结动态时间戳

**文件：`src/agent/builder.py:257-266`**

```python
class TimeMiddleware(AgentMiddleware):
    """在每次 LLM 调用前注入当前日期时间（会话级冻结，避免破坏缓存）。"""

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

**效果：** 同一会话内的所有 LLM 调用共享相同的时间戳，不会因时间变化而导致缓存失效。

### 阶段二：架构优化（预计再节省 30-50% token，3-5天）

#### 3.2.1 实现 ReWOO 模式（适用于 3+ 独立工具调用场景）

在监督者模式 (`build_supervisor_agent`) 中增加规划层：

```python
# 新增 src/agent/planner.py
class PlanThenExecute:
    """ReWOO 模式：先规划所有工具调用，再并行执行，最后合成结果。"""

    async def execute(self, llm, task, tools):
        # Step 1: 一次 LLM 调用生成工具计划
        plan = await self._plan(llm, task, tools)

        # Step 2: 并行执行所有独立工具（无 LLM 参与）
        results = await asyncio.gather(*[
            self._execute_tool(tool, args) for tool, args in plan.independent_steps
        ])

        # Step 3: 一次 LLM 调用合成最终答案
        answer = await self._synthesize(llm, task, results)
        return answer
```

**适用时机：** 当 `intent_router` 判定为 `"plan_and_execute"` 且工具调用数 ≥ 3 时触发。

#### 3.2.2 工具定义压缩

当前工具列表 18 个工具，每个工具的 schema（name + description + parameters JSON Schema）约 200-500 tokens，总计约 5K-8K tokens。

**优化方案：**
- 使用 `tool_choice` 按需发送工具：先用精简的"工具目录"（只有 name + 一句话描述），模型选好工具后，第二次调用才发送完整 schema
- 或者用 `toolmux` 模式，两个元工具（`search_tools` + `execute_tool`）替代 18 个具体工具

#### 3.2.3 缓存命中率监控

**文件：`src/tracing/handler.py` / `frontend/bridge.py`**

在 LLM span 中新增字段：

```python
# SpanData 新增
cache_hit_tokens: int = 0
cache_miss_tokens: int = 0
cache_hit_rate: float = 0.0
```

从 `usage_metadata` 中提取 DeepSeek 的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。

**文件：`src/tracing/api.py`** — 新增 `GET /api/traces/stats/cache` 端点展示缓存统计。

### 阶段三：长期演进（按需实施）

#### 3.3.1 CodeAct 模式

将多步 shell/文件操作合并为一个 Python 代码块在沙箱中执行，中间结果不回流到 context，只返回最终 stdout。

#### 3.3.2 语义缓存（L2）

对 `search`、`weather` 等幂等查询增加语义相似度缓存（FAISS），相似问题直接返回缓存结果。

#### 3.3.3 Anthropic Prompt Caching

如果未来切换到 Anthropic 模型，利用 `cache_control` 显式标记缓存断点：
- Breakpoint 1 (1h TTL): tools + SYSTEM_PROMPT
- Breakpoint 2 (5m TTL): soul_description + tool_guide
- Dynamic suffix: 当前时间 + profile + memory

---

## 四、实施优先级

| 优先级 | 改动 | 文件 | 预期节省 | 风险 |
|--------|------|------|---------|------|
| **P0** | 修复中间件顺序 | `builder.py` | 80%+ 缓存命中率 | 低 |
| **P0** | 启用 parallel_tool_calls | `llm.py` | 50-66% LLM 调用次数 | 低 |
| **P1** | 合并中间件为单一 SystemMessage | `builder.py` | 减少消息碎片化 | 低 |
| **P1** | 冻结会话时间戳 | `builder.py` | 消除每轮缓存失效 | 低 |
| **P2** | 缓存命中率监控 | `tracing/` | 可观测性 | 低 |
| **P2** | ReWOO 规划-执行模式 | 新建 `planner.py` | 30-50% 复杂任务 | 中 |
| **P3** | 工具定义压缩 | `builder.py` | 减少 3-5K tokens/轮 | 中 |

# 链路追踪与 Token 监控功能设计

## 概述

为 MiaoGent 新增链路追踪（Tracing）和 Token 消耗监控能力，覆盖 LLM 调用、工具调用、Agent 步骤和 Sub-agent 委派的完整调用链。

设计原则：
- **零侵入** — 不修改业务代码（builder.py / bridge.py / tools/*.py），只通过 LangChain 的 `BaseCallbackHandler` 事件机制采集
- **纯本地** — SQLite 存储，不依赖外部服务，数据不出边界
- **可扩展** — 事件模型兼容 OpenTelemetry GenAI Semantic Conventions，未来可加 OTel Exporter

---

## 事件模型

### Span 核心字段

每个追踪事件（Span）包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `span_id` | TEXT | UUID v4 |
| `parent_span_id` | TEXT | 父 Span ID，null 表示根 |
| `trace_id` | TEXT | 整条追踪链路唯一 ID |
| `session_turn` | INTEGER | 当前会话的第几轮 |
| `span_type` | TEXT | span 类型 |
| `model` | TEXT | 模型名（仅 llm_call） |
| `input_tokens` | INTEGER | 输入 Token（仅 llm_call） |
| `output_tokens` | INTEGER | 输出 Token（仅 llm_call） |
| `tool_name` | TEXT | 工具名（仅 tool_call） |
| `tool_input` | TEXT | 工具入参摘要（仅 tool_call） |
| `status` | TEXT | `ok` / `error` |
| `started_at` | TEXT | ISO8601 开始时间 |
| `ended_at` | TEXT | ISO8601 结束时间 |
| `duration_ms` | INTEGER | 耗时（毫秒） |
| `error_message` | TEXT | 错误信息（status=error 时） |

### Span 类型

| 名称 | type 值 | 父级 | 说明 |
|------|---------|------|------|
| 用户会话 | `session_turn` | null | 每次用户发送消息创建一个根 Span |
| LLM 调用 | `llm_call` | session_turn / agent_step | 每次 LLM 请求 |
| Agent 步骤 | `agent_step` | session_turn | ReAct 循环中的一步 |
| 工具调用 | `tool_call` | agent_step | 每次工具执行 |
| Sub-agent | `delegate_task` | session_turn | 委派子任务 |

---

## 后端架构

### 模块划分

```
src/tracing/              # 新目录，完全独立
├── __init__.py
├── tracer.py             # Tracer — span 生命周期管理
├── handler.py            # TraceCallbackHandler — LangChain 事件采集
├── store.py              # TraceStore — SQLite 持久化
├── models.py             # SpanData 数据类
└── api.py                # 查询接口（供 bridge.py 调用）
```

### Tracer（`tracer.py`）

Span 生命周期管理器：

- `start_span(span_type, **kwargs)` — 创建新 span，自动设置 parent_span_id
- `end_span(span_id, status="ok")` — 结束 span，写入 duration_ms
- 使用 `contextvars` 维护活跃 span 栈，sub-agent 调用链自动传播
- `contextlib.contextmanager` 实现 `with tracer.span(...)` 模式
- 同一 session 内按 human message 计数自动递增 `session_turn`

### TraceCallbackHandler（`handler.py`）

继承 `langchain_core.callbacks.BaseCallbackHandler`，监听事件：

| LangChain 事件 | 对应 Span | 采集数据 |
|---------------|-----------|---------|
| `on_llm_start` / `on_llm_end` | `llm_call` | model, input_tokens, output_tokens, duration, error |
| `on_tool_start` / `on_tool_end` | `tool_call` | tool_name, tool_input, duration, error |
| `on_chain_start` / `on_chain_end` | `agent_step` / `delegate_task` | name, duration, error |

**重点：** `on_llm_end` 从 `llm_output`（`LLMResult`）中提取 `token_usage`，兼容 OpenAI/DeepSeek/Anthropic 的返回格式。

### TraceStore（`store.py`）

SQLite 存储层：

```sql
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    parent_span_id TEXT,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    session_turn INTEGER NOT NULL DEFAULT 0,
    span_type TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    tool_name TEXT,
    tool_input TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error_message TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER DEFAULT 0,
    user_message TEXT      -- 用户消息摘要，用于按会话内容搜索
);
```

**可选 JSONL 导出**：`TRACE_EXPORT_JSONL=1` 时，每个 span 写入 `~/.miaogent/traces/YYYY-MM-DD.jsonl`，用于离线深度分析。

### 集成方式

只在入口层（`http_server.py`）做一件事——创建 handler 并传入 `RunnableConfig`：

```python
# http_server.py init_agent 中
from src.tracing.handler import TraceCallbackHandler
from src.tracing.store import TraceStore

trace_store = TraceStore()
trace_handler = TraceCallbackHandler(trace_store, session_id=thread_id)

# 在聊天请求中传入 callbacks
config = {
    "configurable": {"thread_id": thread_id},
    "callbacks": [trace_handler],
}
```

业务代码（builder.py、bridge.py、tool 实现）完全不感知 tracing 存在。

---

## REST API 端点

通过 `frontend/bridge.py` 的 `Api` 类暴露给前端：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/traces` | GET | 获取 trace 列表，支持 `?q=`（搜索会话内容）和 `?status=` 过滤 |
| `/api/traces/{trace_id}` | GET | 获取单条 trace 的完整 span 树 |
| `/api/traces/stats` | GET | 获取统计数据（今日 token、平均延迟、调用次数、错误率） |
| `/api/traces/stats/daily` | GET | 获取 Token 消耗趋势（按小时/天聚合） |
| `/api/traces/{trace_id}/spans` | GET | 获取 trace 下的所有 span |
| `/api/traces/sessions/{session_id}` | GET | 按 session_id 查询该会话的所有 trace |

---

## 前端功能

### 整体风格

- 深色主题，与现有面板一致（`#1a1a2e` 背景、`#242438` 卡片、紫色 `#6c5ce7` 主色调）
- 平面 UI，无 emoji
- 选项卡布局：总览 / Trace / Token / 延迟

### 总览页

- 四个摘要卡片：今日 Token、平均延迟、今日调用、错误率（均带"较昨日"环比）
- Token 消耗趋势图：过去 12 小时的柱状图
- 近期 Trace：最近的 3 条记录，显示 trace_id、用户消息摘要、token、耗时、状态

### Trace 列表页

- 搜索框：支持按会话内容或 Trace ID 搜索
- 状态筛选：全部 / 成功 / 失败
- 列表显示：trace_id、用户消息片段、时间、Token、耗时、状态
- 点击进入详情

### Trace 详情页

- 摘要：trace_id、时间、总 Token、总耗时
- 四个统计：输入 Token、输出 Token、总耗时、工具数
- 调用链路树：缩进嵌套显示 Span 层级，不同颜色区分类型
  - 紫色=会话、蓝色=LLM、青色=Agent、黄色=工具
  - 每行显示：类型、名称、Token/耗时、状态标签
- 底栏：Trace ID、模型、总耗时、总 Token 等元信息

### Token 分析页

- 累计输入 / 输出 Token 汇总卡片
- Token 分布：按会话显示 Token 用量进度条

### 延迟分析页

- 工具调用延迟分布：按工具名分组，显示平均耗时和调用次数

### 前端集成

- 在 hover menu 新增"监控"按钮（`data-panel="monitoring"`）
- 新增 `monitoring-panel` 面板，复用现有 panel 布局
- 前端 `app.js` 新增 `setupMonitoringPanel()`，通过 `window.api.*` 调用
- `browser-api.js` / preload 新增对应 IPC 通道

---

## 按关键流程追踪事件流

```
用户发送 "最新AI新闻"
  │
  ├─ session_turn (root span)
  │   user_message="最新AI新闻"
  │
  ├─ llm_call #1 (思考需要搜索)
  │   model=deepseek-chat, 4,210+2,892 tokens, 1.8s
  │
  ├─ agent_step #1
  │   │
  │   └─ tool_call: search("最新AI新闻")
  │       0.3s, ok
  │
  ├─ llm_call #2 (总结结果)
  │   model=deepseek-chat, 1,000+240 tokens, 0.7s
  │
  └─ agent_step #2
      (最终回复)
```

---

## 数据清理

- 默认保留最近 30 天的 trace 数据
- `MAX_SPANS = 50000` 软限制，超出后删除最旧记录
- 可选环境变量 `TRACE_RETENTION_DAYS` 控制保留天数
- JSONL 导出文件按天自动轮转

---

## 未来可扩展

- **OTel Exporter** — 将 span 转换为 OTel 格式推送到 Grafana Tempo / SigNoz
- **Token 预算告警** — 当日 Token 超过阈值时推送通知
- **慢 Trace 检测** — 耗时超过阈值的 trace 自动标记
- **会话级 Token 统计** — 在会话列表显示 Token 消耗

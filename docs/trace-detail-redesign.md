# Trace 详情页改造产品方案

## 1. 现状诊断

### 当前 Trace 详情页

```
┌─ 返回列表 ─────────────────────────────────────────────┐
│ Trace 详情                                              │
│ trace_id(前8位) | 时间 | Token总数 | 总耗时              │
│ ┌──────────┬──────────┬──────────┬──────────┐          │
│ │ 输入Token │ 输出Token │ 总耗时    │ 工具数    │          │
│ └──────────┴──────────┴──────────┴──────────┘          │
│ ● 会话  ● LLM  ● Agent  ● 工具           ← 颜色图例     │
│ ┌ 树形结构 ──────────────────────────────────┐          │
│ │ > 用户会话                     1.2K+0.5Kt  2.3s ✓    │
│ │   * LLM: deepseek-chat         800+200t    1.5s ✓    │
│ │   = 工具: web_search             -         0.8s ✓    │
│ └──────────────────────────────────────────────┘          │
│ Trace ID / 总 Token 明细                                 │
└──────────────────────────────────────────────────────────┘
```

### 核心问题

| 问题 | 影响 |
|------|------|
| **看不到 LLM 实际输入/输出** | 无法判断 prompt 是否正确、模型回答是否合理 |
| **看不到工具调用返回结果** | 无法确认工具是否返回了预期数据 |
| **错误信息太简略** | 只显示 error_message，没有堆栈/上下文 |
| **无时间线可视化** | 看不出各步骤的先后顺序和重叠关系 |
| **无单步展开详情** | 点击 span 不展示完整 JSON |
| **无可复现能力** | 无法从 trace 重放请求 |

---

## 2. 业界参考

### LangSmith / LangFuse / Arize Phoenix 共性设计

**业界 Trace 详情页标配能力：**

```
┌─ Trace Detail ──────────────────────────────────────────┐
│ ┌ Header ──────────────────────────────────────────────┐ │
│ │ User Question: "帮我分析这个数据集..."                │ │
│ │ Status: ✓ | Duration: 3.2s | Tokens: 2.4K | Cost: $0.003 │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌ Waterfall Timeline ──────────────────────────────────┐ │
│ │ llm_call     ████████████░░░░░░   1.2s                │ │
│ │ tool_search      ██████░░░░░░░░   0.3s                │ │
│ │ llm_call           ████████████   1.5s                │ │
│ │ tool_write              ████░░░░   0.2s                │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌ Span Tree ───────────────────────────────────────────┐ │
│ │ ▼ session_turn  "帮我分析数据集"   3.2s  2.4Kt        │ │
│ │   ▼ llm_call  deepseek-chat  1.2s  500t              │ │
│ │     [Input ▼]  [Output ▼]   ← 可展开看完整内容        │ │
│ │   ▼ tool_call  web_search  0.3s                      │ │
│ │     [Input ▼]  [Output ▼]  [Error △]                 │ │
│ │   ▼ llm_call  deepseek-chat  1.5s  1.9Kt             │ │
│ │     [Input ▼]  [Output ▼]                            │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌ Actions ─────────────────────────────────────────────┐ │
│ │ [Replay] [Export JSON] [Copy Trace ID]               │ │
│ └──────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**关键设计理念（摘自 LangFuse 官方指南）：**
> "A good trace shows exactly what the LLM received and returned. Empty I/O cards = bad UX. The root cause is often several steps upstream from the final bad output."

---

## 3. 改造目标

### 3.1 核心原则

1. **I/O 可见性优先** — 每个 LLM 调用和工具调用都要能展开查看完整输入/输出
2. **时间线可视化** — 一图看清执行顺序和耗时瓶颈
3. **错误深挖** — 点击错误节点直达根因
4. **渐进式复杂度** — 默认折叠细节，需要时展开，避免信息过载

### 3.2 目标用户场景

| 场景 | 用户行为 | 当前 | 目标 |
|------|---------|------|------|
| 模型回答不对 | 看 LLM 收到了什么 prompt | ❌ 只能看到 token 数 | ✅ 展开看完整 messages 数组 |
| 工具调用失败 | 看工具输入和返回 | ❌ 只有 tool_input 前500字 | ✅ 展开看完整 input/output |
| 某步特别慢 | 定位耗时瓶颈 | ⚠️ 有 duration 数值 | ✅ 时间线图一眼看出 |
| Agent 死循环 | 看调用序列 | ⚠️ 树形列表能看出 | ✅ 时间线 + 重复调用高亮 |

---

## 4. 产品方案

### 4.1 数据模型扩展

当前 spans 表新增 3 个字段：

```sql
ALTER TABLE spans ADD COLUMN llm_input TEXT DEFAULT '';
ALTER TABLE spans ADD COLUMN llm_output TEXT DEFAULT '';
ALTER TABLE spans ADD COLUMN tool_output TEXT DEFAULT '';
```

| 字段 | 类型 | 说明 | 存储策略 |
|------|------|------|---------|
| `llm_input` | TEXT | LLM 调用时的完整 messages（JSON） | 截断 8KB，超过部分写文件 |
| `llm_output` | TEXT | LLM 返回的完整 completion | 截断 4KB |
| `tool_output` | TEXT | 工具执行的返回结果 | 截断 4KB |

**采集时机**（在 `bridge.py` 的 `chat_stream` 中）：
- `on_chat_model_start` → 提取 `event["data"]["input"]["messages"]` → `llm_input`
- `on_chat_model_end` → 提取 `event["data"]["output"]` → `llm_output`
- `on_tool_end` → 提取 `event["data"]["output"]` → `tool_output`

### 4.2 详情页布局

```
┌─ Trace Detail ─────────────────────────────────────────────┐
│ ← 返回列表                                                 │
│                                                            │
│ User: "帮我查一下今天北京天气，然后写个脚本..."              │
│                                                          
│ ┌ Summary Bar ────────────────────────────────────────────┐
│ │ ✓ Success │ 3.2s │ 2.4K tokens │ 4 spans │ 0 errors   │
│ └─────────────────────────────────────────────────────────┘
│                                                            │
│ ┌─ 时间线 (Waterfall) ────────────────────────────────────┐
│ │ llm ████████████████░░░░░░░░  1.2s  deepseek-chat      │
│ │ web ░░░░████████░░░░░░░░░░░░  0.3s  web_search         │
│ │ llm ░░░░░░░░░░░░░░██████████  1.5s  deepseek-chat      │
│ │ py  ░░░░░░░░░░░░░░░░░░░░████  0.2s  run_python         │
│ └─────────────────────────────────────────────────────────┘
│                                                            │
│ ┌─ 调用树 ────────────────────────────────────────────────┐
│ │ ▼ 用户会话                    3.2s  2.4Kt               │
│ │   ▼ llm_call  deepseek-chat   1.2s  500t               │
│ │     [📥 Input]  [📤 Output]    ← 可展开折叠             │
│ │   ▼ tool_call  web_search     0.3s                     │
│ │     [📥 Input]  [📤 Output]                            │
│ │   ▼ llm_call  deepseek-chat   1.5s  1.9Kt              │
│ │     [📥 Input]  [📤 Output]                            │
│ │   ▼ tool_call  run_python     0.2s                     │
│ │     [📥 Input]  [📤 Output] ⚠️ Error                   │
│ └─────────────────────────────────────────────────────────┘
│                                                            │
│ ┌─ 元数据 ────────────────────────────────────────────────┐
│ │ Trace ID: xxx  |  Session: xxx  |  Turn: #3            │
│ │ Created: 06-10 14:32:15                                 │
│ └─────────────────────────────────────────────────────────┘
└────────────────────────────────────────────────────────────┘
```

### 4.3 核心交互

**A. 时间线视图（新增）**

```
每个 span 一行水平条，左对齐，宽度 = duration / max_duration
不同 span_type 不同颜色
hover 显示详情 tooltip（name, duration, tokens, status）
点击跳转到树中对应节点
```

数据结构：直接从现有 `started_at` / `duration_ms` 计算 offset 和宽度。

**B. Input/Output 展开面板**

```
点击 [📥 Input] → 内联展开：
┌─ LLM Input ──────────────────────────────────────┐
│ [system] 你是一个 AI 助手...                       │
│ [human] 帮我查天气                                 │
│ [tool_result] {"city": "北京", "temp": 25}        │
│ [Copy] [Collapse]                                 │
└───────────────────────────────────────────────────┘

点击 [📤 Output] → 内联展开：
┌─ LLM Output ─────────────────────────────────────┐
│ 好的，北京今天天气晴朗，气温25°C...                │
│ [Copy] [Collapse]                                 │
└───────────────────────────────────────────────────┘
```

**C. 错误高亮**

```
错误 span 红色左边框 + 错误图标
自动展开 error_message
如果 error_message 含堆栈，显示格式化的堆栈追踪
```

**D. Span 行信息增强**

当前每行：`> 用户会话  1.2K+0.5Kt  2.3s  ✓`

改为：
```
▼ llm_call  deepseek-chat    1.2s
  📥 3 messages (1.2K chars)  📤 450 chars  🔢 500t
  ─── 点击展开查看完整内容 ───
```

### 4.4 功能优先级

| 优先级 | 功能 | 理由 | 工作量 |
|--------|------|------|--------|
| P0 | LLM I/O 展开 | 排查问题最核心需求 | 中 |
| P0 | Tool I/O 展开 | 判断工具调用是否正确 | 中 |
| P1 | 时间线视图 | 快速定位性能瓶颈 | 小 |
| P1 | 错误高亮 + 展开 | 一眼看到问题点 | 小 |
| P2 | Span 行信息增强 | 摘要信息更丰富 | 小 |
| P2 | Copy 按钮 | 方便复制 prompt/response | 小 |
| P3 | Replay 能力 | 重放请求复现问题 | 大 |
| P3 | Trace 对比 | diff 两次调用的差异 | 大 |

### 4.5 不做的

- **不自动存储完整 prompt/response 到 DB** — 只存截断版本，完整版按需从文件读取，避免 SQLite 膨胀
- **不做实时日志流** — 保持当前"写入-查询"模式，不增加 WebSocket 推送
- **不做 AI 自动诊断** — v1 只提供数据和可视化，让用户自己判断

---

## 5. 实施路径

### Phase 1: 数据采集（后端）
1. `models.py` — SpanData 新增 `llm_input` / `llm_output` / `tool_output` 字段
2. `store.py` — ALTER TABLE 加列 + `write_span` 适配
3. `bridge.py` — `chat_stream` 中捕获 I/O 并写入 span

### Phase 2: Trace 详情页改造（前端）
1. 时间线 waterfall 组件
2. Input/Output 可展开面板
3. 错误高亮样式
4. 行信息增强（显示 input/output 摘要）

### Phase 3: 交互增强（前端）
1. Copy to clipboard
2. JSON 格式化展示
3. 键盘导航（↑↓ 切换 span，Enter 展开）

---

## 6. 参考

- [LangFuse - What does a good trace look like?](https://langfuse.com/faq/all/what-does-a-good-trace-look-like)
- [LangFuse Trace UI log-level filtering (2025)](https://langfuse.com/changelog/2025-02-10-trace-log-level-filter)
- [Monte Carlo - Debug Agent Behavior 4x Faster](https://www.montecarlodata.com/blog-debug-problematic-agent-behavior-4x-faster-with-agent-observability/)
- [MLflow Tracing - Debug and analyze your app](https://www.mlflow.org/docs/latest/genai/tracing/)

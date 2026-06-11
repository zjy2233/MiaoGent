# MiaoGent Tracing 产品方案

## 1. 产品定位

Tracing（链路追踪）是 MiaoGent 的**问题诊断中心**。当 AI 助手行为异常时，用户在此快速定位根因——哪次 LLM 调用失败、哪个工具返回异常、哪个环节消耗了过多 token。

**核心原则**：
1. **以排查流程驱动信息架构**，而非功能罗列
2. **Span 精简**：只保留有诊断价值的 span，去除冗余包装层

## 1.1 Span 层级设计

### 设计原则

- **去除冗余**：`agent_step`（LangGraph 节点边界，如 agent/tools/planner）与 `llm_call` / `tool_call` 重复，已移除
- **标注归属**：LLM 调用通过 `llm_role` 字段区分 `supervisor`（主 Agent）和 `sub`（子 Agent），一目了然
- **缩进 = 父子嵌套**：每一级缩进表示该 span 是上一级的子调用，含义清晰唯一

### Span 类型

| Span 类型 | 含义 | 诊断价值 |
|-----------|------|---------|
| `session_turn` | 一次用户请求的根 span | 标记请求边界，汇总 token/耗时 |
| `llm_call` (llm_role=supervisor) | 主 Agent 的 LLM 推理 | 定位 prompt/response 问题 |
| `llm_call` (llm_role=sub) | 子 Agent 的 LLM 推理 | 区分委派推理的来源 |
| `tool_call` | 工具调用 | 定位工具失败、参数异常 |
| `delegate_task` | 子 Agent 委派 | 标记委派边界，子 span 挂载其下 |

### 缩进规则

```
缩进 0: session_turn        ← 根节点，无缩进
缩进 1: llm_call / tool_call / delegate_task  ← 主 Agent 的直接操作
缩进 2: llm_call / tool_call                  ← delegate_task 内部的子操作
缩进 N: 更深嵌套（通常不超过 2-3 层）
```

### 示例

```
session_turn (根)
├── llm_call (主 LLM · deepseek-chat)        ← 主 Agent 推理
├── tool_call (shell)                         ← 主 Agent 调工具
├── delegate_task (子Agent · code-review)     ← 委派给子 Agent
│   ├── llm_call (子 LLM · deepseek-chat)     ← 子 Agent 推理
│   └── tool_call (read_file)                 ← 子 Agent 调工具
└── llm_call (主 LLM · deepseek-chat)        ← 主 Agent 汇总结果
```

### 与旧版对比

| 旧版 | 新版 |
|------|------|
| session_turn → agent_step("agent") → llm_call | session_turn → llm_call (主 LLM) |
| session_turn → agent_step("tools") → tool_call | session_turn → tool_call |
| 3 层嵌套，agent_step 无额外信息 | 1-2 层嵌套，llm_role 区分归属 |
| 无法区分主/子 LLM 调用 | llm_role 明确标注 |

---

## 2. 用户画像与诊断场景

### 典型用户
- **MiaoGent 使用者**：日常使用 AI 助手，偶尔遇到回答质量差、响应慢、报错，需要查看"刚才发生了什么"
- **开发者/调试者**：主动优化 prompt、排查工具调用链、分析 token 消耗

### 核心诊断场景（按频率排序）

| 场景 | 触发条件 | 用户问题 | 期望路径 |
|------|---------|---------|---------|
| S1 错误定位 | 助手返回"抱歉，出错了" | 哪个环节失败了？为什么？ | 错误入口 → trace 详情 → 失败 span → 错误详情/输入输出 |
| S2 耗时分析 | 响应特别慢 | 哪一步慢？LLM 还是工具？ | 耗时入口 → 瀑布流总览 → 定位长耗时 span |
| S3 Token 异常 | 消耗感觉很大 | 哪些调用最费 token？输入还是输出？ | Token 排行 → 高消耗 trace → span 级 token 分布 |
| S4 行为回溯 | "刚才那个回答怎么来的？" | 助手经历了哪些步骤？ | 会话 → trace 列表 → trace 详情 → 调用树 |
| S5 趋势监控 | 日常查看 | 今天整体正常吗？ | 概览仪表盘 → 异常指标 → 下钻 |

---

## 3. 信息架构

```
┌─────────────────────────────────────────────────────┐
│  Tracing 入口（Monitoring Panel）                     │
│                                                       │
│  ┌─ 顶栏：全局时间范围选择器 + 自动刷新开关            │
│  │                                                   │
│  ├─ Tab 1: 问题看板 (P0)                              │
│  │   ├─ 错误聚焦区：最近错误列表 + 错误趋势迷你图       │
│  │   ├─ 耗时聚焦区：慢 Trace Top 5                    │
│  │   └─ 概览卡片：今日 Token / 调用量 / 平均耗时      │
│  │                                                   │
│  ├─ Tab 2: Trace 列表 (P0)                            │
│  │   ├─ 过滤器栏：搜索 + 状态 + 时间范围 + 排序        │
│  │   ├─ Trace 列表（分页，支持多选）                   │
│  │   └─ 批量操作：对比选中项                           │
│  │                                                   │
│  ├─ Tab 3: Trace 详情 (P0)                            │
│  │   ├─ 上下文导航：所属会话 → 会话其他 Trace           │
│  │   ├─ 摘要栏：状态 / 总耗时 / Token / Span 数        │
│  │   ├─ 用户消息（可复制）                             │
│  │   ├─ 瀑布流时间线（可缩放平移）                     │
│  │   ├─ 调用树（可搜索过滤 span）                      │
│  │   └─ 元信息卡片                                     │
│  │                                                   │
│  └─ Tab 4: 统计分析 (P1)                              │
│      ├─ Token 子标签：趋势图 + 排行榜                  │
│      ├─ 延迟子标签：工具/LLM 耗时分布                  │
│      └─ 缓存子标签：命中率趋势                         │
└─────────────────────────────────────────────────────┘
```

---

## 4. 功能详述

### 4.1 问题看板（P0）— 替代现有 Overview

**目标**：打开即见问题，一目了然。

#### 4.1.1 错误聚焦区
- **最近错误列表**（最多 5 条）：显示 trace 摘要（用户消息截断、失败 span 名称、错误类型、时间）
- 点击进入 trace 详情，自动展开失败 span
- **错误趋势迷你图**：近 7 天错误率折线，高度 40px
- 空状态：「最近 7 天无错误」

#### 4.1.2 慢 Trace 聚焦区
- **耗时 Top 5**：按 duration_ms 降序
- 每行显示：用户消息 / 总耗时 / 最慢 span 名称及耗时
- 点击进入 trace 详情

#### 4.1.3 概览卡片（保留现有，缩小）
- 今日 Token 总量（较昨日变化）
- 今日调用次数
- 平均耗时
- 缓存命中率

#### 4.1.4 数据刷新
- 手动刷新按钮
- 可选的 30s 自动刷新（开关在顶栏）

---

### 4.2 Trace 列表（P0）— 增强现有列表

**目标**：快速定位目标 Trace。

#### 4.2.1 新增过滤维度
- **时间范围**：快捷选项（最近 1h / 6h / 24h / 7d）+ 自定义区间
- **Span 类型过滤**（新增）：LLM 调用 / 工具调用 / 子 Agent（筛选包含特定类型 span 的 trace）

#### 4.2.2 列表展示增强
- 现有：状态点 + 用户消息 + 时间 + Token + 耗时 + 状态标签
- 新增：**错误简述列**（error_message 前 30 字）、**Span 数**
- 行高亮：错误行红色左边框

#### 4.2.3 多选与对比（P1）
- 复选框多选（最多选 3 条）
- "对比选中"按钮 → 进入 Trace 对比视图

#### 4.2.4 Trace 对比视图（P1）
- 并排展示 2-3 个 trace 的摘要栏
- 统一时间轴的瀑布流叠加
- Span 数量/类型分布差异高亮

---

### 4.3 Trace 详情（P0）— 核心诊断视图

**目标**：在一个页面内完成"这个请求发生了什么"的全量分析。

#### 4.3.1 上下文导航（新增 P0）
- **所属会话链接**：`← 返回会话 "xxx"` → 跳转到该会话的消息列表
- **会话其他 Trace**：横向滚动条，展示同一 session 的其他 turn 的 trace 缩略卡片
- 解决从"某个异常 trace"追溯到"整段对话上下文"的需求

#### 4.3.2 摘要栏（增强）
- 现有字段 + 新增：**缓存命中率**（此 trace 的 cache_hit / total_input）

#### 4.3.3 用户消息（保留）
- 现有样式 + 复制按钮

#### 4.3.4 瀑布流时间线（增强 P1）
- **缩放**：滚轮缩放时间轴（放大到 ms 级别）
- **平移**：拖拽平移
- **当前缩放比例指示器**
- 现有：色块按 span 类型着色、悬停 tooltip、内嵌图例

#### 4.3.5 调用树（增强 P1）
- **Span 搜索**：顶部搜索框，输入关键词过滤 span（按 tool_name / model / span_type 匹配），匹配项高亮，未匹配项折叠/淡化
- **全部展开/折叠**按钮
- 现有：缩进层级、类型图标、I/O 展开面板、错误块

#### 4.3.6 元信息（保留现有）

---

### 4.4 统计分析（P1）— 整合现有 Token/延迟/缓存

**目标**：宏观趋势分析，发现长期问题。

#### 4.4.1 Token 分析（保留 + 增强）
- 累计输入/输出 Token 卡片
- **Token 趋势折线图**：替代现有柱状图，展示 14 天输入/输出 token 双线
- 排行 Top 20（保留）

#### 4.4.2 延迟分析（改进 P0）
- **后端聚合**：新增 API `GET /api/traces/stats/latency`，后端按 tool_name 聚合平均耗时（SQL 直接 GROUP BY），消除前端 N+1 查询
- 展示：工具名 + 平均耗时 + 调用次数 + 柱状条
- 排序切换（保留）

#### 4.4.3 缓存分析（保留 + 增强）
- 缓存命中率趋势折线图（14 天）
- 命中/未命中分布（保留现有水平条）

---

### 4.5 后端 API 新增/改造（P1）

| 接口 | 用途 | 优先级 |
|------|------|--------|
| `GET /api/traces?time_range=1h&span_type=llm_call` | 时间范围 + span 类型过滤 | P0 |
| `GET /api/traces/stats/latency` | 工具耗时聚合（后端 SQL） | P0 |
| `GET /api/traces/errors?days=7` | 错误 trace 列表 + 趋势 | P1 |
| `GET /api/traces/{id}/session` | trace 所属会话的相邻 trace | P1 |
| `POST /api/traces/compare` | 接收多个 trace_id，返回归一化对比数据 | P2 |

---

## 5. 交互流程设计

### 5.1 错误排查流程（最高频）
```
问题看板 → 看到红色错误条目 → 点击 → Trace 详情
  → 摘要栏确认 "Status: ERR"
  → 调用树自动展开到失败 span（红色高亮）
  → 展开 "Out" 面板查看错误消息
  → 展开 "In" 面板查看 LLM 输入（定位 prompt 问题）
  → 如需对话上下文 → 点击"所属会话"跳转
```

### 5.2 耗时排查流程
```
Trace 列表 → 按"延迟"排序 → 点击慢 Trace
  → 瀑布流时间线缩放查看细节
  → 定位最长耗时色块 → 悬停查看 tooltip
  → 如果色块是 tool_call → 展开 Out 面板看工具输出
  → 如果色块是 llm_call → 展开 In/Out 看 prompt 和 response
```

### 5.3 Token 排查流程
```
统计分析 → Token 排行榜 → 点击高消耗 Trace
  → 摘要栏看 Token 总量
  → 调用树看每个 span 的 Token 分布
  → 定位"大头"在哪个 LLM 调用
  → 展开该 span 的 In 面板看输入规模
```

### 5.4 会话回溯流程
```
会话消息列表 → 某条消息旁的操作菜单 → "查看 Trace"
  → Trace 详情
  → 会话其他 Trace 缩略卡片 → 点击相邻 trace 横向对比
```

---

## 6. 优先级与迭代计划

### P0（必须完成 — 核心诊断能力）
1. **问题看板**：替代现有 Overview，错误聚焦 + 慢 Trace 聚焦
2. **Trace 列表增强**：时间范围过滤 + 新增字段
3. **Trace 详情上下文导航**：所属会话链接 + 会话其他 Trace
4. **Trace 详情错误自动展开**：进入详情时自动定位失败 span
5. **延迟分析后端聚合**：新 API 消除 N+1
6. **自动刷新**：手动 + 30s 可选

### P1（重要 — 深度诊断能力）
7. **瀑布流缩放平移**
8. **调用树 Span 搜索**
9. **Token 趋势折线图**
10. **Trace 对比视图**
11. **会话内 Trace 横向导航**

### P2（增强 — 锦上添花）
12. **Trace 导出**（JSON 下载）
13. **外部链接分享**（复制可分享的 trace 链接）
14. **异常告警**（错误率超过阈值时面板入口高亮）
15. **深色/浅色主题**跟随系统

---

## 7. 非功能需求

### 7.1 性能
- 问题看板首次加载 < 1s（并行请求 stats + daily + errors）
- Trace 列表分页加载 < 500ms
- Trace 详情（含 50+ span）渲染 < 500ms
- 瀑布流 100 span 内无卡顿

### 7.2 数据保留
- 默认保留 30 天（已实现）
- UI 中显示当前保留策略
- 不提供 UI 修改（低频操作，保留 CLI）

### 7.3 兼容性
- Electron 窗口（420px 宽面板 + 全屏模式）
- 浏览器模式（响应式，最小 480px 宽）
- 所有 API 返回空数组/默认值，前端优雅降级

---

## 8. 与现有系统的差异总结

| 维度 | 现有 | 目标 |
|------|------|------|
| 首页 | 概览（4 卡片 + 趋势图） | 问题看板（错误 + 慢 Trace + 概览） |
| 错误发现 | 需手动翻列表 + 过滤 | 首页直接展示，自动定位 |
| Trace 上下文 | 孤立查看 | 可导航到所属会话及相邻 trace |
| 时间范围 | 无 | 快捷选项 + 自定义 |
| 瀑布流 | 静态 | 可缩放平移 |
| Span 导航 | 全量展开 | 可搜索过滤 |
| 延迟查询 | N+1 前端请求 | 后端聚合一次返回 |
| 刷新 | 手动切换 Tab | 自动刷新可选 |
| Token 分析 | 柱状图 | 折线图双线对比 |

---

## 9. 数据结构与 API 契约

### 9.1 新增/变更 API

#### `GET /api/traces` — 扩展现有参数
```
新增查询参数:
  time_range   string  可选 "1h"|"6h"|"24h"|"7d"|"30d"
  span_type    string  可选 "llm_call"|"tool_call"|"delegate_task" (筛选包含该类型 span 的 trace)
  date_from    string  ISO datetime (与 time_range 互斥)
  date_to      string  ISO datetime
```

#### `GET /api/traces/stats/latency` — 新增
```
返回: [{"tool_name": "shell", "avg_duration_ms": 1234, "count": 42, "p50_ms": 800, "p95_ms": 3000}, ...]
后端 SQL: SELECT tool_name, AVG(duration_ms), COUNT(*), ... FROM spans WHERE span_type='tool_call' GROUP BY tool_name
```

#### `GET /api/traces/{trace_id}/session` — 新增
```
返回: { session_id, traces: [{trace_id, user_message, started_at, duration_ms, status, total_tokens}] }
按 started_at 排序，标记当前 trace 是哪个 turn
```

#### `GET /api/traces/errors` — 新增
```
查询参数: days (默认 7)
返回: { traces: [...], daily_counts: [{day, error_count, total_count}] }
```

### 9.2 现有 API 保持不变
所有现有端点保持兼容，仅做增量添加。

---

## 10. 术语表

| 术语 | 含义 |
|------|------|
| Trace | 一次用户请求的完整链路，包含多个 Span |
| Span | 链路中的单个操作（LLM 调用 / 工具调用 / Agent 步骤） |
| Session | 一次对话会话，包含多个 Turn（即多个 Trace） |
| Turn | 会话中的一轮对话（用户发一次消息 = 一个 Turn = 一个 Trace） |
| Waterfall | 瀑布流时间线，按时间轴横向排列 Span |

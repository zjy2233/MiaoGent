# Knowledge Consolidation — 设计方案（V2）

## 1. 目标

为 MiaoGent 的 auto-memory facts 机制增加定期归总能力，将零散 facts 压缩为结构化知识，避免上下文膨胀。**核心约束：零额外依赖、单次 LLM 调用完成归并、复用现有持久化层。**

## 2. 架构定位

```
MemoryExtractor (已有)
  └─ 实时提取 facts → 写入 MemoryStore.working_memories
        │
        ▼
KnowledgeConsolidator (新增, src/store/knowledge.py)
  └─ 定时/按量触发
  └─ 读取待归并 facts → 单次 LLM 聚类+总结 → 写回 MemoryStore
  └─ 输出通过已有 context 注入通路进入 Agent
```

**不新增持久化层**，全部复用 `MemoryStore`：
- `working_memories` 表新增 `status` 列：`raw` / `consolidated`
- `core_memory` 新增 `consolidated_knowledge` 分类，存储归并后知识

## 3. 数据模型

### working_memories 表（已有，仅新增 status 列）

```sql
-- 已有列不变，新增:
status TEXT DEFAULT 'raw'  -- raw | consolidated
consolidated_at TEXT       -- 归并时间戳，NULL 表示未归并
consolidation_round INT    -- 归并轮次号（用于幂等）
```

### core_memory 扩展（memory.json consolidated 分类）

```json
{
  "consolidated_knowledge": [
    {
      "id": "know_001",
      "content": "用户在前端开发中偏好 TypeScript 和 React",
      "source_ids": ["fact_uuid_1", "fact_uuid_5"],
      "created_at": "2026-06-09T12:00:00",
      "updated_at": "2026-06-09T12:00:00",
      "status": "active"        // active | superseded | archived
    }
  ]
}
```

## 4. 归并管线（精简为 3 步）

### 4.1 触发策略

| 触发 | 条件 | 动作 |
|------|------|------|
| 数量阈值 | `status=raw` 且 `consolidated_at IS NULL` 的 facts > **30 条** | 触发一轮归并 |
| 事件驱动 | 面板关闭 / 会话切换 | 后台异步触发 (`asyncio.create_task`) |

两者同时触发时，通过 `consolidation_round` 字段保证不重复处理同一批数据。

### 4.2 管线流程（单次 LLM 调用）

```
Step 1: 收集
  └─ 查询 MemoryStore: SELECT * FROM working_memories
       WHERE status='raw' AND consolidated_at IS NULL
       ORDER BY created_at ASC LIMIT 50
  └─ 若无数据 → 跳过
  └─ 读取已有 consolidated_knowledge（用于去重参考）

Step 2: LLM 聚类 + 精炼总结（单次调用）
  └─ Prompt 结构:
       [系统] 你是一个知识归纳助手。将以下 facts 按主题分组，
              每组生成一条结构化总结。与已有知识重复的跳过。
       [已有知识] {consolidated_knowledge}
       [待归并 facts] {facts 列表}
       [输出格式] JSON: [{topic, summary, source_indices, confidence}]
  └─ 输入 token 估算: facts ~ 30条 × 50字 = 1500 + 已有知识 ~ 500 ≈ 2000 tokens
  └─ 输出: 结构化 JSON，包含分组总结 + 置信度 + 原文索引

Step 3: 写入 + 状态更新
  └─ 事务开始 (asyncio.Lock + SQLite BEGIN IMMEDIATE)
  ├─ 新知识 → upsert 到 core_memory.consolidated_knowledge
  ├─ 已归并 facts → UPDATE status='consolidated', consolidated_at=NOW,
  │   consolidation_round=round_id
  └─ 事务提交

  若 Step 2 失败 → Step 3 不执行 → 数据完整
  若 Step 3 中途失败 → 事务回滚 → 数据一致
```

### 4.3 幂等保护

```
consolidation_round: 每条 fact 归并后标记轮次号
触发条件: WHERE consolidated_at IS NULL
→ 即使被两个触发同时选中，第一条 SQL 执行后
  该批 facts 的 consolidated_at 已非 NULL，
  第二条的查询结果为空 → 空操作
```

## 5. 冲突解决

| 场景 | 规则 |
|------|------|
| 新旧矛盾 | 新覆盖旧，旧条目 `status=superseded`，保留 `source_ids` 溯源 |
| 与已有知识重复 | LLM Step 2 中判断，输出中跳过，不写入 |
| 低置信度 (< 0.3) | 不生成总结知识，facts 标记为 `archived` 清理 |

## 6. 读路径（知识注入）

```
Agent 构建时 (builder.py):
  1. MemoryStore 读取 consolidated_knowledge
  2. 格式化为文本片段
  3. 通过已有 ProfileMiddleware 注入到 system prompt
     (复用现有 context 注入通路，不新增中间件)

注入位置: system prompt 末尾，格式:
  "## 已知知识\n{consolidated_knowledge 文本}"
```

**知识选择策略**：
- 只注入 `status=active` 的条目
- 全部注入（当前数据量小，未来可改为按 session/主题筛选）
- 注入上限 1000 tokens（超出则按 `updated_at` 降序截取）

## 7. 集成点

| 模块 | 改动 |
|------|------|
| `src/store/memory_store.py` | working_memories 表加 status/consolidated_at/consolidation_round 列；core_memory 加 consolidated_knowledge |
| `src/store/knowledge.py` | **新增**：KnowledgeConsolidator（收集→LLM→写入） |
| `src/agent/builder.py` | ProfileMiddleware 中追加 consolidated_knowledge 到 system prompt |
| `frontend/bridge.py` | 暴露 `trigger_consolidation()` API（会话关闭时调用） |
| `frontend/http_server.py` | 应用退出时触发 |

## 8. 预算控制

| 指标 | 上限 |
|------|------|
| 单次归并 LLM 输入 | ≤ 2500 tokens（含 facts + 已有知识 + prompt） |
| 单次归并 LLM 输出 | ≤ 800 tokens（结构化和精简后） |
| consolidated_knowledge 总条目 | ≤ 100 条，超限按 `updated_at` 淘汰最旧 |
| 每日归并轮次 | ≤ 5 轮 |
| 归并触发阈值 | raw facts > 30 条才触发，避免频繁调用 |
| LLM 调用次数 | 每轮归并 **1 次**（聚类+总结合一），成本极低 |

## 9. 成本与效果分析

| 维度 | 评估 |
|------|------|
| 新增依赖 | **0** — 全部复用已有组件 |
| 每次归并 LLM 成本 | ~2500 in + ~500 out ≈ **3000 tokens**（约 ¥0.015/次，每日≤5次） |
| 代码量预估 | knowledge.py ~150 行，其余改动用 < 50 行 |
| 效果 | facts 数量 > 30 才触发，实际使用中每日 1-3 轮，日均成本 < ¥0.05 |

## 10. 与现有模块关系

```
MemoryStore (已有)
├── working_memories      ← KnowledgeConsolidator 读取 raw facts
├── core_memory
│   └── consolidated_knowledge  ← KnowledgeConsolidator 写入
└── get_all_formatted()   → ProfileMiddleware → Agent system prompt

KnowledgeConsolidator (新增)
├── 触发: 阈值 / 事件
├── 执行: 收集 facts → LLM 总结 → 写回 MemoryStore
└── 幂等: consolidated_at + consolidation_round 双字段保护
```

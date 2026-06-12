# MiaoGent 迭代计划

> 最后更新：2026-06-11

---

## 概述

基于产品和架构分析，MiaoGent 的迭代分为三个层次：

1. **技术债务清理**：消除历史遗留问题，统一代码风格
2. **用户体验增强**：优化前端交互，让对话体验更流畅
3. **架构升级**：基于 LangGraph 原生能力重构，提升可维护性

---

## 迭代一：技术债务清理（已完成）

### 目标

消除 bridge.py 中的技术债务，统一数据路径，移除废弃代码。

### 完成的 Task

| # | Task | 说明 |
|---|------|------|
| 1.1 | 移除 module-level globals | 用 context-safe 替代方案替换 `_CURRENT_TRACER`、`_CURRENT_SESSION_ID` 等模块级全局变量 |
| 1.2 | 提取 TracingStreamHandler | `chat_stream()` 中 span 采集逻辑抽取为独立类，消除代码重复 |
| 1.3 | 提取 orphan tool_calls 清理 | `_cleanup_orphan_tool_calls()` 独立函数，提升可读性 |
| 1.4 | 统一数据路径 | 移除 `get_data_path()` 中对旧 `data/` 目录的回退，统一使用 `~/.miaogent/` |
| 1.5 | 移除废弃中间件类 | 删除旧 middleware 类，统一为 `MergedContextMiddleware` |
| 1.6 | 移除旧 data/ 目录 | 从仓库中删除旧 `data/` 目录及其内容 |

### 关键变更

- `frontend/bridge.py`：全局状态改为上下文变量传递
- `src/core/miaogent_home.py`：`get_data_path()` 只返回 `~/.miaogent/`
- `src/agent/builder.py`：中间件统一

---

## 迭代二：用户体验增强（已完成）

### 目标

优化前端 UI/UX，让 Agent Shell 的对话、工具调用、设置管理更流畅。

### 完成的 Task

| # | Task | 关键文件 | 说明 |
|---|------|---------|------|
| 2.1 | 消息分页加载 | `bridge.py`, `app.js` | `get_messages()` 支持 `limit`/`before_id`，前端"加载更早消息"按钮，返回 `{messages, has_more}` |
| 2.2 | 工具调用进度显示 | `app.js`, `styles.css` | 可展开工具卡片（加载动画 → 完成/失败状态），中文状态标签，输入/输出详情 |
| 2.3 | 消息编辑与重发 | `bridge.py`, `app.js` | `POST /api/chat/edit` 路由，`RemoveMessage` 回溯删除，hover 编辑按钮，编辑模式 UI |
| 2.4 | Shell 确认超时 | `app.js`, `index.html` | 60 秒倒计时，超时自动拒绝，倒计时颜色渐变（绿→黄→红） |
| 2.5 | 设置恢复默认 | `bridge.py`, `app.js` | `GET /api/settings/defaults` 端点，"恢复默认"按钮 |

### 后续优化（迭代二内）

| # | 优化 | 说明 |
|---|------|------|
| 2.2a | 工具卡片匹配修复 | `_activeToolCards` 从 `run_id` key 改为计数器 key + FIFO 按名匹配，解决卡片展示不全 |
| 2.2b | 工具卡片高度修复 | 移除 `overflow: hidden`，卡片 body 改为 `max-height` 过渡动画 |
| 2.3a | 编辑 UI 重设计 | 编辑按钮移到气泡内右上角，添加警告条、快捷键提示、后续消息灰化、自动撑高 textarea |

---

## 迭代三：架构升级（规划中）

### 目标

基于 LangGraph 原生能力重构核心模块，减少自定义基础设施，提升可维护性和稳定性。

### 候选 Task

| # | Task | 来源 | 优先级 |
|---|------|------|--------|
| 3.1 | WebSocket 双向通道 | `docs/optimization/phase1-websocket.md` | P0 |
| 3.2 | 聊天 UI 重构 | `docs/optimization/phase3-chat-ui.md` | P0 |
| 3.3 | `interrupt()` + `Command(resume)` 替代 ConfirmationError | `docs/optimization/phase4-human-in-loop.md` | P1 |
| 3.4 | 异步 Shell 执行器 | `docs/optimization/phase2-shell-executor.md` | P1 |
| 3.5 | StateGraph 显式图替代 create_agent 隐式图 | `docs/optimization/README.md` | P2 |
| 3.6 | SummaryMiddleware / ProfileMiddleware 合并到 prompt callable | `docs/optimization/README.md` | P2 |
| 3.7 | `_drop_orphans` 迁移到 LangGraph checkpoint | `docs/optimization/README.md` | P2 |

### 详细说明

#### 3.1 WebSocket 双向通道

当前使用 SSE（单向）进行流式通信，工具确认通过轮询模式。WebSocket 可实现：
- 真正的双向实时通信
- 中断/恢复流无需新 HTTP 请求
- 服务端主动推送（进度、token 消耗等）

#### 3.3 Human-in-the-Loop 精确中断

当前方案：`shell` 工具抛 `ConfirmationError` → 创建新 `thread_id` 重试 → 丢失部分上下文。

LangGraph 原生方案：
```python
# 工具内部调用 interrupt() 暂停图执行
from langgraph.types import interrupt
approval = interrupt({"command": cmd, "risk": "HIGH"})
# 前端通过 Command(resume=approved) 精确恢复
```

优势：保持完整 checkpoint 上下文，支持任意深度的中断恢复。

#### 3.5 StateGraph 显式图

当前 `create_agent` 构造的隐式图无法插入自定义节点。改用 StateGraph 后：

```
START → [agent] → conditional → [tools] → [agent] → END
                        ↓
                  [human_confirm]
```

可在工具执行前插入确认节点，实现更精细的控制流。

---

## 已完成迭代时间线

```
2026-06-03  Agent Soul/Profile 设计
2026-06-03  Shell 命令工具 4 层安全设计
2026-06-04  Agent Shell 整体架构设计
2026-06-04  Electron 前端重构
2026-06-08  Tracing 链路追踪与 Token 监控
2026-06-10  Token 优化（prompt caching + 压缩工具描述）
2026-06-10  知识库归并机制
2026-06-11  迭代一：技术债务清理
2026-06-11  迭代二：用户体验增强
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| `docs/optimization/README.md` | 完整优化方案（架构升级版） |
| `docs/optimization/phase1-websocket.md` | WebSocket 双向通道设计 |
| `docs/optimization/phase2-shell-executor.md` | Shell 执行器重构 |
| `docs/optimization/phase3-chat-ui.md` | 聊天 UI 重构 |
| `docs/optimization/phase4-human-in-loop.md` | HITL 中断恢复 |
| `docs/skill-system-design.md` | Skill 能力系统设计 |
| `docs/knowledge-consolidation-plan.md` | 知识归并方案 |
| `docs/token-optimization-plan.md` | Token 优化方案 |
| `docs/trace-detail-redesign.md` | 链路追踪详情重构 |

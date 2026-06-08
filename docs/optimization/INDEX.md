# Agent Shell 优化方案 — 索引

> 本文档索引所有优化相关的文档。

---

## 文档树

```
docs/optimization/
├── INDEX.md                  ← 本文件
├── README.md                 ← 完整优化方案（根因分析 + 业界参考 + 总体设计）
├── phase1-websocket.md       ← Phase 1: WebSocket 双向通道
├── phase2-shell-executor.md  ← Phase 2: Shell 执行器重构
├── phase3-chat-ui.md         ← Phase 3: Agent Shell 聊天 UI
└── phase4-human-in-loop.md   ← Phase 4: Human-in-the-Loop 中断恢复
```

## 实施优先级

| 优先级 | Phase | 预估工时 | 核心价值 |
|--------|-------|----------|----------|
| P0 | Phase 1: WebSocket 通道 | 2-3 天 | 打通前后端通信，所有后续功能的基础 |
| P0 | Phase 3: 聊天 UI | 3 天 | 解决「Agent Shell 无法聊天」的核心痛点 |
| P1 | Phase 2: Shell 执行器 | 2 天 | 异步+沙箱+审计，提升安全性和稳定性 |
| P1 | Phase 4: HITL | 1 天 | 精确中断恢复，提升用户体验 |

## 快速开始

从 Phase 1 开始实施：

```bash
# 1. 创建 EventBridge
touch src/event_bridge.py

# 2. 集成 WebSocket
touch src/agent_shell/ws_server.py

# 3. 前端 WebSocket 客户端
# 修改 src/agent_shell/app.js
```

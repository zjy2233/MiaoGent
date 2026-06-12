# 前端代码整改方案

## P0 — 立即修复

### 1. SSE 流读取器去重

**问题**：`electron/preload.js` 和 `browser-api.js` 各有两套近乎相同的 SSE 流读取
**方案**：提取通用 `_startSSEStream(url, body, onDone?)` 到共享模块
- 前端侧：提取到 `frontend/js/sse-stream.js`
- Electron 侧：通过 contextBridge 暴露、或共享模块

### 2. loadLatencyStats 性能陷阱

**问题**：`app.js:2010-2079` 对最近50个trace逐一调用 getTraceSpans()，50次串行 HTTP
**方案**：改为调用后端批量查询接口，或前端缓存 span 数据

---

## P1 — 核心重构

### 3. Api 上帝类拆分

**问题**：`bridge.py` Api 类 800行，混合会话/设置/Soul/工具/Skill/Chat/Tracing
**方案**：拆为独立 Service 类

### 4. 前端 API 双桥合一

**问题**：preload.js contextBridge 和 browser-api.js ~90% 代码重复
**方案**：创建共享 `api-client.js`，两处分别引用

### 5. 全局状态封装

**问题**：app.js 所有状态为模块级全局变量
**方案**：引入简单状态管理模块 `frontend/js/state.js`

### 6. 流式 Markdown 性能

**问题**：每次 token 到达全量重渲染
**方案**：流结束时一次性渲染，或使用基于 diff 的 DOM 更新

---

## P2 — 代码规范

### 7. 错误处理规范化

**问题**：多处 `except Exception: pass` 静默吞异常
**方案**：至少记录 error 日志，关键路径向上传播

### 8. Electron 最大化动画修复

**问题**：setOpacity 定时链 hack
**方案**：试用 native window maximize 或 CSS transition 替代

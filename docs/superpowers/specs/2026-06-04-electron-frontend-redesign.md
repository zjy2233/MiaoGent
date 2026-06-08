# Agent Shell 前端重构设计方案

## 背景

当前 `agent_shell` 使用 **pywebview** 作为窗口封装，存在以下局限：

- `frameless=True` + `transparent=True` 在 Windows 上阴影/抗锯齿表现不稳定
- 前端能力受限于 WebView2（IE/旧 Chromium 内核兼容问题）
- 悬浮态 UI 交互（hover菜单、dialog）的渲染质量受限
- 无法实现更高级的原生窗口效果（亚克力背景、动态模糊）

本次重构保留 Python 业务逻辑不变，**仅替换窗口层**，采用 Electron 重构前端 UI。

---

## 目标

1. 完整无边框悬浮应用：窗口无标题栏、无边框、完全透明背景
2. 可拖拽悬浮（位置持久化到 localStorage）
3. Hover 菜单 + Dialog 面板交互，原生体验
4. Electron 主进程管理窗口状态，Python 子进程处理业务逻辑
5. 保留现有 `Api` 类接口，最小化 Python 侧改动

---

## 技术架构

### 进程模型

```
┌─────────────────────────────────────────────────────────┐
│  Electron Main Process                                  │
│  ┌─────────────────────────────────────────────────┐   │
│  │  BrowserWindow                                    │   │
│  │  - frame: false                                  │   │
│  │  - transparent: true                            │   │
│  │  - alwaysOnTop: true                             │   │
│  │  - width/height: 80x80 (mascot only)             │   │
│  │  - resizable: false                              │   │
│  │  - skipTaskbar: true                             │   │
│  └─────────────────────────────────────────────────┘   │
│                           │ IPC                        │
│                    ┌──────┴──────┐                    │
│  ┌─────────────────▼───────────▼───────────────┐      │
│  │  preload.js (contextBridge 安全暴露API)      │      │
│  └──────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────┘
                            │ IPC (invoke/handle)
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Python Subprocess (spawn)                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │  HTTP Server (FastAPI / aiohttp)                │   │
│  │  - 127.0.0.1:port                               │   │
│  │  - REST API 对应原 pywebview.api 方法           │   │
│  │  - GET/POST JSON                                │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### IPC 通信协议

Python 侧启动本地 HTTP 服务器，Electron preload 通过 `fetch` 调用：

| 方法 | 路由 | 说明 |
|------|------|------|
| `GET` | `/api/sessions` | list sessions |
| `DELETE` | `/api/sessions/:thread_id` | delete session |
| `GET` | `/api/settings` | get settings |
| `POST` | `/api/settings` | save settings |
| `GET` | `/api/soul` | get soul.json |
| `POST` | `/api/soul` | save soul.json |
| `GET` | `/api/profile` | get profile.json |
| `POST` | `/api/profile` | save profile.json |
| `GET` | `/api/tools` | enumerate tools |

### 文件布局

```
src/
  agent_shell/
    index.html          # 保留（改动极小）
    styles.css          # 保留
    app.js              # 小改动：pywebview.api → window.api
    assets/
      mascot.json
      lottie-player.js
  electron/
    main.js             # Electron 主进程
    preload.js          # contextBridge 安全桥接
    http_server.py      # Python HTTP 服务器（新增）
```

---

## 实现步骤

### Phase 1: Python HTTP 服务器

- 新建 `src/agent_shell/http_server.py`
- 复用现有 `Api` 类逻辑，Flask/FastAPI 暴露相同接口
- 端口从环境变量或 `agent_shell` 专属端口分配
- 子进程 `spawn`，stdout 写入 readiness marker

### Phase 2: Electron 主进程

- 新建 `src/agent_shell/electron/main.js`
- `BrowserWindow` 配置：无边框、透明、置顶、80x80
- 实现 `dragMove` 区域（mascot 区域可拖拽）
- 监听 `close` 事件时同时终止 Python 子进程

### Phase 3: Preload 桥接

- 新建 `src/agent_shell/electron/preload.js`
- `contextBridge.exposeInMainWorld('api', {...})`
- 封装 `fetch` 调用为 promise-based API
- 暴露 `startDrag()` 给渲染进程（用于手动拖拽模式）

### Phase 4: 渲染进程适配

- `app.js` 中 `window.pywebview.api` → `window.api`
- 无需修改 HTML 结构
- CSS/JS 基本保留

### Phase 5: 打包与分发

- 使用 `electron-builder` 打包
- Windows: nsis 便携 exe
- 可选: Python 环境打包（PyInstaller / electron-builder python 插件）

---

## 关键设计决策

### 1. 为什么不直接用 Electron Node API 调用 Python？

- Python 业务逻辑重度依赖 asyncio + LangChain/LangGraph
- 直接 pipe/stdin 通信序列化成本高
- HTTP 服务器模式解耦清晰，调试友好

### 2. 为什么不用 Electron 内置 Python？

- `electron-python` 生态老旧，维护不活跃
- subprocess + HTTP 是更稳健的跨进程方案

### 3. 透明窗口在 Windows 上的实现

- Electron 7+ 原生支持 `transparent: true`
- 需要设置 `--disable-gpu` 或特定启动参数避免渲染问题
- `webPreferences` 中 `transparent: true`, `backgroundColor: '#00000000'`

---

## 待验证风险

- [ ] Windows 透明窗口抗锯齿表现（已有方案：`antialiased: true` CSS）
- [ ] Python HTTP 服务器冷启动延迟（可接受范围内）
- [ ] Electron 打包后 Python 子进程路径解析
- [ ] 80x80 mascot 尺寸在 HiDPI 显示器的表现

---

## 改动量估算

| 文件 | 改动 |
|------|------|
| `src/agent_shell/http_server.py` | 新增 (~150行) |
| `src/agent_shell/electron/main.js` | 新增 (~120行) |
| `src/agent_shell/electron/preload.js` | 新增 (~60行) |
| `src/agent_shell/app.js` | 修改 (~5行 API调用方式) |
| `src/agent_shell/index.html` | 无改动 |
| `src/agent_shell/styles.css` | 无改动 |
| `requirements.txt` | 新增 `flask` 或 `aiohttp` |
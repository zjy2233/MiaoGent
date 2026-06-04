# Agent 外壳前端设计

## 概述

为 agent 打造一个独立网页小部件，浮于桌面角落，可拖动位置，承载"设置/对话/工具"三个功能入口。

## 技术方案

### 前端
- **文件**：单 `agent-shell.html`，无构建步骤
- **动画**：Lottie-player (CDN) 加载预设动物动画
- **交互**：原生 JS 实现悬浮菜单、点击弹窗、拖动定位
- **样式**：独立 CSS，毛玻璃/动漫风格

### 桌面包装
- **pywebview** 包装成无边框窗口
- 窗口置顶、跨平台拖动、半透明背景
- 进程退出自动清理窗口

### 架构

```
┌─────────────────────────────────────┐
│ agent-shell.html                    │
│  ├─ Lottie 动画 (🦊 卡通动物形象)   │
│  ├─ 悬浮菜单 (设置/对话/工具)        │
│  ├─ 面板弹窗 (dialog/overlay)       │
│  └─ 拖动逻辑 (mousedown/mousemove)  │
└─────────────────────────────────────┘
           │  pywebview
           ▼
┌─────────────────────────────────────┐
│ 桌面角落独立窗口                     │
│  - 无边框、置顶、可拖动               │
│  - 透明背景，动画直接显示             │
└─────────────────────────────────────┘
```

## 组件设计

### 1. 入口图标
- 加载用户提供的 Lottie 文件 (`814d9de6-f5d5-11ee-9058-7fb43f2edc02.json`)
- 尺寸：80×80px 悬浮显示
- 鼠标按下 / 拖动时样式变化

### 2. 悬浮菜单
- 三个按钮横向排列：`设置` `对话` `工具`
- 动画：`opacity 0→1 + scale 0.8→1`，150ms ease-out
- 位置：图标上方，箭头指向图标

### 3. 面板弹窗
- 点击菜单项 → 弹出 `<dialog>` 或 div overlay
- 内容通过 iframe 或直接 HTML 渲染
- 关闭按钮，右上角 ×

### 4. 面板内容

#### 设置面板
- LLM 供应商切换（DeepSeek / OpenAI / 其他）
- Soul 配置编辑
- Profile 配置编辑
- 保存 / 取消按钮

#### 对话面板
- 会话列表（读取 `.sessions.json`）
- 点击切换 / 删除会话
- 新建会话按钮

#### 工具面板
- 展示当前 agent 工具包列表
- 工具名称 + 描述
- 来源：`src/tools/*.py` 中的 `@tool` 装饰器元数据

### 5. 拖动系统
- mousedown on 图标 → 记录初始位置
- mousemove → 更新窗口 left/top
- mouseup → 释放
- 位置持久化到 localStorage，重启后恢复上次位置

## 启动方式

```bash
python -m src.agent_shell
# 或独立
python agent_shell.py
```

## 文件结构

```
src/
  agent_shell.py      # pywebview 包装器
  agent_shell/
    index.html       # 前端主文件
    styles.css       # 样式
    app.js           # 交互逻辑
    assets/
      mascot.json    # Lottie 动画（复制到 assets/）
```

## 依赖

- pywebview >= 4.0
- webbrowser（内置）
- 前端无需额外依赖（Lottie-player CDN）

## 待确认

- [ ] Lottie 动画文件路径（确认复制到 assets/）
- [ ] pywebview 窗口初始位置（上次位置 or 默认右下角）
- [ ] 面板内容数据来源（直接读配置文件还是通过后端 API）
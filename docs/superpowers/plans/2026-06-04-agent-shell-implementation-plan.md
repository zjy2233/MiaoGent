# Agent Shell 前端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 agent 打造一个独立网页小部件，浮于桌面角落，可拖动位置，显示"设置/对话/工具"三个功能入口。

**Architecture:** 前端单 HTML 文件 + Vanilla JS + Lottie-player (CDN)，桌面包装用 pywebview 无边框窗口实现置顶 + 可拖动。pywebview 通过 `webview.api` 暴露 Python 方法给 JS，实现前后端通信。

**Tech Stack:** pywebview >= 4.0, Lottie-player (CDN), 原生 JS/CSS

---

## 文件结构

```
src/
  agent_shell.py           # pywebview 包装器，暴露 API 给 JS
  agent_shell/
    index.html             # 前端主文件
    styles.css             # 动漫风格样式
    app.js                 # 交互逻辑：悬浮菜单、弹窗、拖动
    assets/
      mascot.json         # Lottie 动画（已从下载目录复制）
```

---

## Task 1: 创建目录结构 & 复制素材

**Files:**
- Create: `src/agent_shell/`
- Create: `src/agent_shell/assets/`
- Modify: `src/agent_shell/assets/mascot.json` (已从下载目录复制)

**Verification:**
- `ls src/agent_shell/assets/mascot.json` → 文件存在

---

## Task 2: 编写 HTML 结构 (index.html)

**Files:**
- Create: `src/agent_shell/index.html`

**Content:** 基础 HTML 结构，包含：
- `<lottie-player>` 加载 `assets/mascot.json`
- 三个菜单按钮容器（隐藏态）
- 三个 `<dialog>` 弹窗（设置/对话/工具）
- 各弹窗内的基础 DOM 结构

**Key Elements:**
```html
<div id="mascot-container">
  <lottie-player id="mascot" src="assets/mascot.json" background="transparent" speed="1" loop></lottie-player>
</div>

<div id="hover-menu" class="hidden">
  <button data-panel="settings">⚙️ 设置</button>
  <button data-panel="chat">💬 对话</button>
  <button data-panel="tools">🛠️ 工具</button>
</div>

<!-- 三个 dialog -->
<dialog id="settings-panel">...</dialog>
<dialog id="chat-panel">...</dialog>
<dialog id="tools-panel">...</dialog>
```

**Verification:**
- 文件创建后，用浏览器直接打开 `index.html` 应能看到 Lottie 动画

---

## Task 3: 编写动漫风格样式 (styles.css)

**Files:**
- Create: `src/agent_shell/styles.css`

**Design:**
- 毛玻璃效果：半透明背景 + backdrop-filter blur
- 渐变按钮：动漫风彩虹渐变
- 弹窗样式：圆角卡片 + 阴影 + 关闭按钮
- 悬浮菜单动画：opacity 0→1 + scale 0.8→1，150ms ease-out
- 悬浮菜单三角箭头指向图标

**Key Classes:**
- `.mascot-container` — 动画容器，cursor: grab
- `.hover-menu` — 悬浮菜单，默认 hidden
- `.hover-menu.visible` — 显示状态
- `.panel-*` — 各弹窗面板样式

---

## Task 4: 编写交互逻辑 (app.js)

**Files:**
- Create: `src/agent_shell/app.js`

**Functions:**

### 悬浮菜单控制
```javascript
mascotContainer.addEventListener('mouseenter', () => showMenu())
mascotContainer.addEventListener('mouseleave', () => hideMenu())
```

### 菜单按钮点击 → 打开对应弹窗
```javascript
menuButtons.forEach(btn => {
  btn.addEventListener('click', (e) => {
    openPanel(btn.dataset.panel)
    hideMenu()
  })
})
```

### 弹窗关闭
```javascript
closeButtons.forEach(btn => {
  btn.addEventListener('click', () => closePanel(btn.closest('dialog')))
})
```

### 拖动定位（更新窗口 left/top）
```javascript
mascotContainer.addEventListener('mousedown', startDrag)
document.addEventListener('mousemove', onDrag)
document.addEventListener('mouseup', stopDrag)
```

### 位置持久化（localStorage）
```javascript
// 窗口初始位置从 localStorage 读取
// 拖动结束后保存到 localStorage
```

### API 桥接（暴露给 pywebview）
```javascript
// JS 调用 pywebview.api.get_sessions()
// JS 调用 pywebview.api.get_settings()
// JS 调用 pywebview.api.save_settings(data)
```

### Lottie 随机待机动画
```javascript
// 读取 Lottie 动画总帧数
// 随机跳转到某一帧作为"随机待机动作"
// 每 N 秒执行一次
```

---

## Task 5: 编写 pywebview 包装器 (agent_shell.py)

**Files:**
- Create: `src/agent_shell.py`

**功能:**

### API 暴露类
```python
class Api:
    def get_sessions(self):
        """读取 .sessions.json，返回会话列表"""

    def delete_session(self, thread_id):
        """从注册表删除会话"""

    def get_settings(self):
        """读取当前配置（LLM 供应商等）"""

    def save_settings(self, settings):
        """保存配置到 .env"""

    def get_soul(self):
        """读取 soul.json"""

    def save_soul(self, soul):
        """保存 soul.json"""

    def get_profile(self):
        """读取 profile.json"""

    def save_profile(self, profile):
        """保存 profile.json"""

    def get_tools(self):
        """解析 src/tools/*.py 中的 @tool 装饰器，返回工具列表"""
```

### 窗口创建
```python
def create_window():
    webview.create_window(
        title="Agent Shell",
        html=open(index.html).read(),
        width=120,
        height=120,
        resizable=False,
        frameless=True,
        always_on_top=True,
    )
```

**Verification:**
- 运行 `python -m src.agent_shell` 能启动窗口，显示动画

---

## Task 6: 完善面板内容

**Files:**
- Modify: `src/agent_shell/index.html`
- Modify: `src/agent_shell/styles.css`

### 设置面板内容
- LLM 供应商下拉选择（DeepSeek / OpenAI / 其他）
- API Key 输入框
- Model 输入框
- Soul textarea 编辑器
- Profile textarea 编辑器
- 保存 / 取消按钮

### 对话面板内容
- 会话列表（从 `pywebview.api.get_sessions()` 获取）
- 每条显示：会话 ID（截断）、创建时间、轮数
- 点击 → 切换（调用 `pywebview.api.switch_session(thread_id)`）
- 删除按钮（调用 `pywebview.api.delete_session(thread_id)`）
- 新建会话按钮

### 工具面板内容
- 静态展示：读取 `src/tools/` 下的工具元数据
- 每个工具显示：名称 + 描述
- 工具以卡片网格排列

---

## Task 7: 集成测试

**Files:**
- 测试前端：`python -m http.server 8080` → 浏览器打开 `index.html`
- 测试完整窗口：`python -m src.agent_shell`
- 验证：
  - [ ] Lottie 动画正常播放
  - [ ] 悬浮显示菜单
  - [ ] 三个面板均能打开和关闭
  - [ ] 设置面板能加载/保存配置
  - [ ] 对话面板能读取会话列表
  - [ ] 工具面板显示工具列表
  - [ ] 窗口可拖动

---

## 依赖安装

```bash
uv pip install pywebview -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 启动方式

```bash
# 方式一：直接运行
python -m src.agent_shell

# 方式二：开发模式（前端热刷新）
python -m http.server 8080
# 浏览器打开 http://localhost:8080/src/agent_shell/index.html
```
# Phase 3: Agent Shell 聊天 UI

> 预估工时：3 天
> 依赖：Phase 1（WebSocket 通道）
> 目标：在 Agent Shell 浮窗中实现完整的聊天交互

---

## 设计目标

1. **渐进式 UI**：默认只有 mascot 浮窗 + 悬浮菜单，聊天面板从右侧滑出
2. **富文本渲染**：支持 markdown、代码块（带语法高亮）、表格
3. **工具调用可视化**：实时显示工具调用状态和结果
4. **确认对话框**：工具需要确认时弹出原生风格对话框
5. **响应式布局**：迷你模式（仅 mascot）→ 聊天模式（300px 宽）

---

## 任务清单

### Task 3.1: 聊天面板 HTML

**文件**：`src/agent_shell/index.html`（改写）

在现有面板基础上增加聊天面板：

```html
<!-- 聊天面板（新增） -->
<dialog id="chat-panel" class="panel wide-panel">
  <div class="panel-header">
    <h3>💬 对话</h3>
    <button class="close-btn" data-close>&times;</button>
  </div>
  <div class="panel-body chat-body">
    <div id="message-list" class="message-list">
      <!-- 动态渲染的消息 -->
    </div>
    <div id="tool-status" class="tool-status hidden">
      <!-- 工具调用状态栏 -->
    </div>
  </div>
  <div class="chat-input-area">
    <textarea id="chat-input" rows="2" placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"></textarea>
    <button id="chat-send" class="btn-primary">发送</button>
  </div>
</dialog>
```

**原有面板保留不变**：设置、会话列表、工具列表。

### Task 3.2: 消息渲染引擎

**文件**：`src/agent_shell/app.js`（改写）

```javascript
class MessageRenderer {
  // 将 markdown 文本转为安全 HTML
  render(text) { /* ... */ }
  // 渲染代码块（带语法高亮）
  renderCodeBlock(code, language) { /* ... */ }
  // 渲染工具调用卡片
  renderToolCall(name, input, output) { /* ... */ }
  // 渲染确认对话框
  renderConfirmDialog(command, reason, alternatives) { /* ... */ }
}
```

**渲染规则**：
- 用户消息 → 右对齐蓝色气泡
- AI 消息 → 左对齐灰色气泡，支持 markdown
- 代码块 → 深色背景，等宽字体，行号
- 工具调用 → 折叠卡片（默认展开），显示名称+参数+结果
- 工具确认 → 居中对话框，Y/N 按钮 + 替代建议

**验收标准**：
- 纯文本消息正常显示
- ```python ... ``` 代码块被渲染为深色代码区块
- 工具调用显示为带图标的状态卡片

### Task 3.3: WebSocket 集成

**文件**：`src/agent_shell/app.js`

```javascript
class ChatClient {
  constructor() {
    this.renderer = new MessageRenderer();
    this.ws = null;
  }

  connect(sessionId) {
    this.ws = new WebSocket(`ws://127.0.0.1:${PORT}/ws?session_id=${sessionId}`);
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case 'text_stream':
          this.renderer.appendStreamingText(msg.payload.text);
          break;
        case 'tool_start':
          this.renderer.showToolCall(msg.payload.name, msg.payload.input);
          break;
        case 'tool_end':
          this.renderer.completeToolCall(msg.payload.name, msg.payload.output);
          break;
        case 'tool_confirm':
          this.renderer.showConfirmDialog(msg.payload)
            .then(approved => {
              this.ws.send(JSON.stringify({type: approved ? 'confirm_yes' : 'confirm_no'}));
            });
          break;
        case 'done':
          this.renderer.finalize();
          break;
        case 'error':
          this.renderer.showError(msg.payload.message);
          break;
      }
    };
  }
}
```

### Task 3.4: 键盘快捷键

- `Enter` → 发送消息
- `Shift+Enter` → 换行
- `Escape` → 关闭聊天面板
- `Ctrl+Up` → 编辑上一条消息（类似 REPL 的上箭头功能）

### Task 3.5: CSS 样式

**文件**：`src/agent_shell/styles.css`（改写）

```css
/* 聊天面板布局 */
.chat-body { display: flex; flex-direction: column; height: 400px; }
.message-list { flex: 1; overflow-y: auto; padding: 12px; }
.chat-input-area { display: flex; gap: 8px; padding: 8px; border-top: 1px solid #eee; }

/* 消息气泡 */
.message-bubble { max-width: 80%; padding: 8px 12px; border-radius: 12px; margin-bottom: 8px; }
.message-bubble.user { background: #007aff; color: white; align-self: flex-end; }
.message-bubble.assistant { background: #e9e9eb; color: black; align-self: flex-start; }

/* 代码块 */
.code-block { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 8px; 
              font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 13px; 
              overflow-x: auto; margin: 8px 0; }

/* 工具调用卡片 */
.tool-card { border: 1px solid #ddd; border-radius: 8px; padding: 8px; margin: 4px 0; 
             background: #f8f9fa; font-size: 13px; }
.tool-card .tool-name { font-weight: 600; color: #555; }

/* 确认对话框 */
.confirm-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); 
                   display: flex; align-items: center; justify-content: center; z-index: 1000; }
.confirm-dialog { background: white; border-radius: 12px; padding: 24px; max-width: 400px; 
                  box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
```

---

## 用户流程

```
1. 用户 hover mascot → 弹出悬浮菜单
2. 点击「对话」→ 从右侧滑出聊天面板
3. 输入"北京天气" → 按 Enter
4. WebSocket 发送 user_message 事件
5. 后端 Agent 开始推理 → 返回 text_stream 事件
6. 前端流式显示 AI 回答
7. 如果需要工具确认 → 弹出确认对话框
8. 用户点击"确认"或"拒绝"
9. 完成后显示 done 事件
```

---

## 兼容性保障

- 无 WebSocket 时自动降级为模拟模式（显示 mock 数据）
- 使用 `<dialog>` 元素，不支持的浏览器回退
- pywebview 旧版本降级为纯配置面板（现有行为）

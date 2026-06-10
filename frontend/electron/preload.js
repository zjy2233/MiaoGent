/**
 * Agent Shell — Electron Preload Script
 *
 * 注意：contextBridge 使用结构化克隆传递返回值给主世界，
 * 不能直接把 fetch Response（含 ReadableStream）传过去。
 * 因此 SSE 流在 preload 里处理完，通过 DOM CustomEvent 推送到主世界。
 */

const { contextBridge, ipcRenderer } = require('electron');

const BASE_URL = 'http://127.0.0.1:18794';

// ── SSE 流式聊天 ──────────────────────────────────────────────────────
// 在 preload 中发起 fetch、读取 SSE 流，逐事件派发 DOM CustomEvent。
// 主世界（app.js）通过 addEventListener('chat-sse', ...) 接收。

async function _startChatStream(threadId, message) {
  try {
    const response = await fetch(`${BASE_URL}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, message }),
    });

    if (!response.ok) {
      window.dispatchEvent(new CustomEvent('chat-sse', {
        detail: { event: 'error', data: { error: `HTTP ${response.status}` } },
      }));
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            window.dispatchEvent(new CustomEvent('chat-sse', {
              detail: { event: currentEvent, data },
            }));
          } catch (_) { /* skip malformed SSE data */ }
        }
      }
    }
  } catch (err) {
    window.dispatchEvent(new CustomEvent('chat-sse', {
      detail: { event: 'error', data: { error: err.message } },
    }));
  }
}

async function _startResumeStream(threadId, approved) {
  try {
    const response = await fetch(`${BASE_URL}/api/chat/resume/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, approved }),
    });

    if (!response.ok) {
      window.dispatchEvent(new CustomEvent('chat-sse', {
        detail: { event: 'error', data: { error: `HTTP ${response.status}` } },
      }));
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            window.dispatchEvent(new CustomEvent('chat-sse', {
              detail: { event: currentEvent, data },
            }));
          } catch (_) { /* skip malformed SSE data */ }
        }
      }
    }
  } catch (err) {
    window.dispatchEvent(new CustomEvent('chat-sse', {
      detail: { event: 'error', data: { error: err.message } },
    }));
  }
}

// ── API 桥接 ──────────────────────────────────────────────────────────

const api = {
  // ── REST（返回 Promise<plain object>，可被结构化克隆） ──────────────
  getSessions: () => fetch(`${BASE_URL}/api/sessions`).then((r) => r.json()),
  createSession: () =>
    fetch(`${BASE_URL}/api/sessions`, { method: 'POST' }).then((r) => r.json()),
  deleteSession: (id) =>
    fetch(`${BASE_URL}/api/sessions/${id}`, { method: 'DELETE' }).then((r) => r.json()),
  getMessages: (threadId) =>
    fetch(`${BASE_URL}/api/sessions/${threadId}/messages`).then((r) => r.json()),
  // 触发会话记忆压缩（退出时调用）
  compressSession: (threadId) =>
    fetch(`${BASE_URL}/api/sessions/${threadId}/compress`, {
      method: 'POST',
    }).then((r) => r.json()),
  // 非流式聊天（备用）
  sendChat: (threadId, message) =>
    fetch(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: threadId, message }),
    }).then((r) => r.json()),
  // 流式聊天：通过 CustomEvent 接收事件，返回 void
  sendChatStream: (threadId, message) => {
    _startChatStream(threadId, message);
  },
  // 流式恢复（interrupt 确认后调用）：也通过 chat-sse CustomEvent 接收事件
  resumeChatStream: (threadId, approved) => {
    _startResumeStream(threadId, approved);
  },
  getSettings: () => fetch(`${BASE_URL}/api/settings`).then((r) => r.json()),
  saveSettings: (s) =>
    fetch(`${BASE_URL}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }).then((r) => r.json()),
  getSoul: () => fetch(`${BASE_URL}/api/soul`).then((r) => r.json()),
  saveSoul: (s) =>
    fetch(`${BASE_URL}/api/soul`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }).then((r) => r.json()),
  getProfile: () => fetch(`${BASE_URL}/api/profile`).then((r) => r.json()),
  saveProfile: (p) =>
    fetch(`${BASE_URL}/api/profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    }).then((r) => r.json()),
  getTools: () => fetch(`${BASE_URL}/api/tools`).then((r) => r.json()),

  // ── Skill 查询（只读） ───────────────────────────────────────────────
  getSkills: () => fetch(`${BASE_URL}/api/skills`).then((r) => r.json()),
  getSkillDetail: (name) =>
    fetch(`${BASE_URL}/api/skills/${name}`).then((r) => r.json()),


  // ── IPC: 窗口控制 ──────────────────────────────────────────────────
  ballDragMove: (dx, dy) => ipcRenderer.send('ball-drag-move', dx, dy),
  panelDragMove: (dx, dy) => ipcRenderer.send('panel-drag-move', dx, dy),
  openPanel: (name) => ipcRenderer.send('open-panel', name),
  closePanel: () => ipcRenderer.send('close-panel'),
  toggleMaximize: () => ipcRenderer.send('toggle-maximize'),
  showContextMenu: () => ipcRenderer.send('show-context-menu'),
  quitApp: () => ipcRenderer.send('quit-app'),

  // ── 面板切换监听（主进程通知渲染进程切换面板，避免 loadFile 闪烁）──
  onSwitchPanel: (callback) => {
    ipcRenderer.on('switch-panel', (_event, panelName) => callback(panelName));
  },

  // ── Tracing ──
  getTraces: (q, status, limit, offset) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (status) params.set('status', status);
    if (limit) params.set('limit', limit);
    if (offset) params.set('offset', offset);
    const qs = params.toString();
    return fetch(`${BASE_URL}/api/traces${qs ? '?' + qs : ''}`).then((r) => r.json());
  },
  getTraceDetail: (traceId) =>
    fetch(`${BASE_URL}/api/traces/${traceId}`).then((r) => r.json()),
  getTraceSpans: (traceId) =>
    fetch(`${BASE_URL}/api/traces/${traceId}/spans`).then((r) => r.json()),
  getTraceStats: () =>
    fetch(`${BASE_URL}/api/traces/stats`).then((r) => r.json()),
  getTraceDailyStats: () =>
    fetch(`${BASE_URL}/api/traces/stats/daily`).then((r) => r.json()),
  getTraceCacheStats: () =>
    fetch(`${BASE_URL}/api/traces/stats/cache`).then((r) => r.json()),
  getTracesBySession: (sessionId) =>
    fetch(`${BASE_URL}/api/traces/sessions/${sessionId}`).then((r) => r.json()),
  getTraceCount: (q, status) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (status) params.set('status', status);
    const qs = params.toString();
    return fetch(`${BASE_URL}/api/traces/count${qs ? '?' + qs : ''}`).then((r) => r.json()).then((r) => r.count);
  },
  getTokenTopTraces: (days, limit) => {
    const params = new URLSearchParams();
    if (days) params.set('days', days);
    if (limit) params.set('limit', limit);
    return fetch(`${BASE_URL}/api/traces/token-top?${params.toString()}`).then((r) => r.json());
  },
};

contextBridge.exposeInMainWorld('api', api);

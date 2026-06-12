/**
 * Agent Shell — Electron Preload Script
 *
 * 注意：contextBridge 使用结构化克隆传递返回值给主世界，
 * 不能直接把 fetch Response（含 ReadableStream）传过去。
 * 因此 SSE 流在 preload 里处理完，通过 DOM CustomEvent 推送到主世界。
 *
 * REST API 调用和 SSE 流读取已提取到前端共享模块：
 *   frontend/js/api-client.js  — apiGet / apiPost / apiDelete
 *   frontend/js/sse-stream.js  — _startSSEStream
 */

const path = require('path');
const { contextBridge, ipcRenderer } = require('electron');
const { apiGet, apiPost, apiDelete } = require(path.join(__dirname, '..', 'js', 'api-client.js'));
const { _startSSEStream } = require(path.join(__dirname, '..', 'js', 'sse-stream.js'));

// ── API 桥接 ──────────────────────────────────────────────────────────

const api = {
  // ── REST（返回 Promise<plain object>，可被结构化克隆） ──────────────
  getSessions: () => apiGet('/api/sessions'),
  createSession: () => apiPost('/api/sessions'),
  deleteSession: (id) => apiDelete(`/api/sessions/${id}`),
  deleteSessionsBatch: (ids) =>
    apiPost('/api/sessions/batch-delete', { thread_ids: ids }),
  getMessages: (threadId, opts) => {
    const params = new URLSearchParams();
    if (opts && opts.include_tool_calls === false) params.set('include_tool_calls', 'false');
    if (opts && opts.limit) params.set('limit', opts.limit);
    if (opts && opts.before_id) params.set('before_id', opts.before_id);
    const qs = params.toString();
    return apiGet(`/api/sessions/${threadId}/messages${qs ? '?' + qs : ''}`);
  },
  // 触发会话记忆压缩（退出时调用）
  compressSession: (threadId) =>
    apiPost(`/api/sessions/${threadId}/compress`),
  // 非流式聊天（备用）
  sendChat: (threadId, message) =>
    apiPost('/api/chat', { thread_id: threadId, message }),
  // 流式聊天：通过 CustomEvent 接收事件，返回 void
  sendChatStream: (threadId, message) => {
    _startSSEStream('/api/chat/stream', { thread_id: threadId, message });
  },
  // 流式恢复（interrupt 确认后调用）：也通过 chat-sse CustomEvent 接收事件
  resumeChatStream: (threadId, approved) => {
    _startSSEStream('/api/chat/resume/stream', { thread_id: threadId, approved });
  },
  getSettings: () => apiGet('/api/settings'),
  getSettingsDefaults: () => apiGet('/api/settings/defaults'),
  saveSettings: (s) => apiPost('/api/settings', s),
  getSoul: () => apiGet('/api/soul'),
  saveSoul: (s) => apiPost('/api/soul', s),
  getProfile: () => apiGet('/api/profile'),
  saveProfile: (p) => apiPost('/api/profile', p),
  getTools: () => apiGet('/api/tools'),

  // ── 消息编辑 ────────────────────────────────────────────────────────
  editMessage: (threadId, messageId, newContent) =>
    apiPost('/api/chat/edit', {
      thread_id: threadId, message_id: messageId, new_content: newContent,
    }),

  // ── Skill 查询（只读） ───────────────────────────────────────────────
  getSkills: () => apiGet('/api/skills'),
  getSkillDetail: (name) => apiGet(`/api/skills/${name}`),

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
    return apiGet(`/api/traces${qs ? '?' + qs : ''}`);
  },
  getTraceDetail: (traceId) => apiGet(`/api/traces/${traceId}`),
  getTraceSpans: (traceId) => apiGet(`/api/traces/${traceId}/spans`),
  getTraceStats: () => apiGet('/api/traces/stats'),
  getTraceDailyStats: () => apiGet('/api/traces/stats/daily'),
  getTraceCacheStats: () => apiGet('/api/traces/stats/cache'),
  getTracesBySession: (sessionId) => apiGet(`/api/traces/sessions/${sessionId}`),
  getTraceCount: (q, status) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (status) params.set('status', status);
    const qs = params.toString();
    return apiGet(`/api/traces/count${qs ? '?' + qs : ''}`).then((r) => r.count);
  },
  getTokenTopTraces: (days, limit) => {
    const params = new URLSearchParams();
    if (days) params.set('days', days);
    if (limit) params.set('limit', limit);
    return apiGet(`/api/traces/token-top?${params.toString()}`);
  },
};

contextBridge.exposeInMainWorld('api', api);

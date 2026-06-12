/**
 * Browser API shim — 当未运行在 Electron 中时提供 window.api 实现。
 * 使用直接 fetch() 替代 Electron IPC，使前端可在浏览器中独立运行。
 *
 * REST API 基础函数 (apiGet/apiPost/apiDelete) 来自 frontend/js/api-client.js。
 * SSE 流读取来自 frontend/js/sse-stream.js。
 */
(function () {
  if (window.api) return; // Electron mode — 跳过

  window.api = {
    // ── REST ──
    getSessions: () => apiGet('/api/sessions'),
    createSession: () => apiPost('/api/sessions'),
    deleteSession: (id) => apiDelete(`/api/sessions/${id}`),
    deleteSessionsBatch: (ids) => apiPost('/api/sessions/batch-delete', { thread_ids: ids }),
    getMessages: (tid, opts) => {
      const params = new URLSearchParams();
      if (opts && opts.include_tool_calls === false) params.set('include_tool_calls', 'false');
      if (opts && opts.limit) params.set('limit', opts.limit);
      if (opts && opts.before_id) params.set('before_id', opts.before_id);
      const qs = params.toString();
      return apiGet(`/api/sessions/${tid}/messages${qs ? '?' + qs : ''}`);
    },
    compressSession: (tid) => apiPost(`/api/sessions/${tid}/compress`),
    sendChat: (tid, msg) => apiPost('/api/chat', { thread_id: tid, message: msg }),
    sendChatStream: (tid, msg) => _startSSEStream('/api/chat/stream', { thread_id: tid, message: msg }),
    resumeChatStream: (tid, approved) => _startSSEStream('/api/chat/resume/stream', { thread_id: tid, approved }),
    getSettings: () => apiGet('/api/settings'),
    getSettingsDefaults: () => apiGet('/api/settings/defaults'),
    saveSettings: (s) => apiPost('/api/settings', s),
    getSoul: () => apiGet('/api/soul'),
    saveSoul: (s) => apiPost('/api/soul', s),
    getProfile: () => apiGet('/api/profile'),
    saveProfile: (p) => apiPost('/api/profile', p),
    getTools: () => apiGet('/api/tools'),
    editMessage: (tid, mid, newContent) =>
      apiPost('/api/chat/edit', { thread_id: tid, message_id: mid, new_content: newContent }),
    getSkills: () => apiGet('/api/skills'),
    getSkillDetail: (name) => apiGet(`/api/skills/${name}`),
    triggerConsolidation: () => apiPost('/api/consolidate'),

    // ── IPC 窗口控制（浏览器模式下为 noop） ──
    ballDragMove: () => {},
    panelDragMove: () => {},
    openPanel: (name) => {
      // 浏览器模式: 跳转到 ?panel=<name>
      const url = new URL(window.location);
      url.searchParams.set('panel', name);
      window.location.href = url.href;
    },
    closePanel: () => {
      // 浏览器模式: 恢复为 ball mode
      window.location.href = window.location.pathname;
    },
    showContextMenu: () => {},
    quitApp: () => window.close(),

    // ── 面板切换监听（浏览器模式下用 hashchange/visibility 模拟） ──
    onSwitchPanel: (callback) => {},

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
      return apiGet(`/api/traces/count${qs ? '?' + qs : ''}`).then(r => r.count);
    },
    getTokenTopTraces: (days, limit) => {
      const params = new URLSearchParams();
      if (days) params.set('days', days);
      if (limit) params.set('limit', limit);
      return apiGet(`/api/traces/token-top?${params.toString()}`);
    },
  };

  console.log('[browser-api] window.api initialized for browser mode');
})();

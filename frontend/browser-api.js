/**
 * Browser API shim — 当未运行在 Electron 中时提供 window.api 实现。
 * 使用直接 fetch() 替代 Electron IPC，使前端可在浏览器中独立运行。
 */
(function () {
  if (window.api) return; // Electron mode — 跳过

  const BASE_URL = 'http://127.0.0.1:18794';

  function fetchJSON(url, options) {
    return fetch(url, options).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
  }

  // ── SSE 流式聊天 ──
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
          if (line.startsWith('event: ')) currentEvent = line.slice(7).trim();
          else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              window.dispatchEvent(new CustomEvent('chat-sse', { detail: { event: currentEvent, data } }));
            } catch (_) {}
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
    let sawDone = false;
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
          if (line.startsWith('event: ')) currentEvent = line.slice(7).trim();
          else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (currentEvent === 'done') sawDone = true;
              window.dispatchEvent(new CustomEvent('chat-sse', { detail: { event: currentEvent, data } }));
            } catch (_) {}
          }
        }
      }
    } catch (err) {
      window.dispatchEvent(new CustomEvent('chat-sse', {
        detail: { event: 'error', data: { error: err.message } },
      }));
      return;
    }
    // 如果服务器端没有发送 done 事件，自己补发一个
    if (!sawDone) {
      window.dispatchEvent(new CustomEvent('chat-sse', {
        detail: { event: 'done', data: {} },
      }));
    }
  }

  window.api = {
    // ── REST ──
    getSessions: () => fetchJSON(`${BASE_URL}/api/sessions`),
    createSession: () => fetchJSON(`${BASE_URL}/api/sessions`, { method: 'POST' }),
    deleteSession: (id) => fetchJSON(`${BASE_URL}/api/sessions/${id}`, { method: 'DELETE' }),
    getMessages: (tid) => fetchJSON(`${BASE_URL}/api/sessions/${tid}/messages`),
    compressSession: (tid) => fetchJSON(`${BASE_URL}/api/sessions/${tid}/compress`, { method: 'POST' }),
    sendChat: (tid, msg) => fetchJSON(`${BASE_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id: tid, message: msg }),
    }),
    sendChatStream: (tid, msg) => _startChatStream(tid, msg),
    resumeChatStream: (tid, approved) => _startResumeStream(tid, approved),
    getSettings: () => fetchJSON(`${BASE_URL}/api/settings`),
    saveSettings: (s) => fetchJSON(`${BASE_URL}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }),
    getSoul: () => fetchJSON(`${BASE_URL}/api/soul`),
    saveSoul: (s) => fetchJSON(`${BASE_URL}/api/soul`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(s),
    }),
    getProfile: () => fetchJSON(`${BASE_URL}/api/profile`),
    saveProfile: (p) => fetchJSON(`${BASE_URL}/api/profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    }),
    getTools: () => fetchJSON(`${BASE_URL}/api/tools`),
    getSkills: () => fetchJSON(`${BASE_URL}/api/skills`),
    getSkillDetail: (name) => fetchJSON(`${BASE_URL}/api/skills/${name}`),

    // ── IPC 窗口控制（浏览器模式下为 noop） ──
    ballDragMove: () => {},
    resizeBall: () => {},
    setWindowShape: () => {},
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
      return fetchJSON(`${BASE_URL}/api/traces${qs ? '?' + qs : ''}`);
    },
    getTraceDetail: (traceId) => fetchJSON(`${BASE_URL}/api/traces/${traceId}`),
    getTraceSpans: (traceId) => fetchJSON(`${BASE_URL}/api/traces/${traceId}/spans`),
    getTraceStats: () => fetchJSON(`${BASE_URL}/api/traces/stats`),
    getTraceDailyStats: () => fetchJSON(`${BASE_URL}/api/traces/stats/daily`),
    getTracesBySession: (sessionId) => fetchJSON(`${BASE_URL}/api/traces/sessions/${sessionId}`),
  };

  console.log('[browser-api] window.api initialized for browser mode');
})();

/**
 * sse-stream.js — Unified SSE stream reader
 *
 * 双模式模块：支持 Node.js (Electron preload) 和浏览器上下文。
 * 在 Electron preload 中用 require() 加载；在浏览器中用 <script> 加载。
 *
 * 统一处理 chat/stream 和 resume/stream 的 SSE 读取逻辑，
 * 自动补发 done 事件（如果服务器未发送）。
 */
(function (root) {
  'use strict';

  var BASE_URL = 'http://127.0.0.1:18794';

  /**
   * 统一 SSE 流读取函数
   * @param {string} url    - API 路径（如 '/api/chat/stream'）
   * @param {object} body   - POST 请求体
   */
  async function _startSSEStream(url, body) {
    try {
      var response = await fetch(BASE_URL + url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        window.dispatchEvent(new CustomEvent('chat-sse', {
          detail: { event: 'error', data: { error: 'HTTP ' + response.status } },
        }));
        return;
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      var currentEvent = '';
      var sawDone = false;

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        buf += decoder.decode(result.value, { stream: true });
        var lines = buf.split('\n');
        buf = lines.pop() || '';

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            try {
              var data = JSON.parse(line.slice(6));
              if (currentEvent === 'done') sawDone = true;
              window.dispatchEvent(new CustomEvent('chat-sse', {
                detail: { event: currentEvent, data: data },
              }));
            } catch (_) { /* skip malformed SSE data */ }
          }
        }
      }

      // 如果服务器未发送 done 事件，补发一个
      if (!sawDone) {
        window.dispatchEvent(new CustomEvent('chat-sse', {
          detail: { event: 'done', data: {} },
        }));
      }
    } catch (err) {
      window.dispatchEvent(new CustomEvent('chat-sse', {
        detail: { event: 'error', data: { error: err.message } },
      }));
    }
  }

  var exports = { _startSSEStream: _startSSEStream };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exports;
  } else {
    root._startSSEStream = _startSSEStream;
  }
})(typeof window !== 'undefined' ? window : global);

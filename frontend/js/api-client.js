/**
 * api-client.js — Shared REST API helpers
 *
 * 双模式模块：支持 Node.js (Electron preload) 和浏览器上下文。
 * 在 Electron preload 中用 require() 加载；在浏览器中用 <script> 加载。
 */
(function (root) {
  'use strict';

  var BASE_URL = 'http://127.0.0.1:18794';

  function apiGet(path) {
    return fetch(BASE_URL + path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function apiPost(path, body) {
    return fetch(BASE_URL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function apiDelete(path) {
    return fetch(BASE_URL + path, { method: 'DELETE' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  var exports = { apiGet: apiGet, apiPost: apiPost, apiDelete: apiDelete, BASE_URL: BASE_URL };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exports;
  } else {
    root.apiGet = apiGet;
    root.apiPost = apiPost;
    root.apiDelete = apiDelete;
    root.BASE_URL = BASE_URL;
  }
})(typeof window !== 'undefined' ? window : global);

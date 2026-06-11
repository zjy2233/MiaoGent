/* ============================================
   Agent Shell — 双模式交互
   Ball Mode: 点击→面板、拖拽→移动、右键→菜单
   Panel Mode: 独立面板窗口（设置/对话/工具）
   ============================================ */

'use strict';

// ── 全局状态 ──────────────────────────────────────────────────────────────
let chatThreadId = null;      // 当前聊天会话 ID
let isChatLoading = false;    // 聊天流式请求是否进行中
let _detailViewCleanup = null; // 技能详情页的 cleanup

// ── 启动 ──────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  const panelName = params.get('panel');

  if (panelName) {
    initPanelMode(panelName);
  } else {
    initBallMode();
  }
});

// ====================================================================
//  BALL MODE
//  点击 → 打开面板（根据鼠标位置区分轻触与拖拽）
//  拖拽 → IPC 移动 OS 窗口
//  右键 → 原生上下文菜单
// ====================================================================

function initBallMode() {
  const wrapper = document.getElementById('ball-wrapper');
  const container = document.getElementById('mascot-container');
  const menu = document.getElementById('hover-menu');
  const menuBtns = document.querySelectorAll('.menu-btn[data-panel]');

  // ── 防止浏览器原生拖拽幽灵图片 ──────────────────────────
  container.addEventListener('dragstart', (e) => e.preventDefault());

  // ── 鼠标悬浮 → 展开窗口显示菜单 ─────────────────────────
  let expandTimer = null;

  wrapper.addEventListener('mouseenter', () => {
    clearTimeout(expandTimer);
    menu.classList.remove('hidden');
  });

  wrapper.addEventListener('mouseleave', () => {
    expandTimer = setTimeout(() => {
      menu.classList.add('hidden');
    }, 300);
  });

  // ── 菜单按钮 → 打开面板 ─────────────────────────────────
  menuBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.classList.add('hidden');
      if (window.api && window.api.openPanel) {
        window.api.openPanel(btn.dataset.panel);
      }
    });
  });

  // ── 关闭按钮（右上角 X）───────────────────────────
  const closeBtn = document.getElementById('ball-close-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (window.api && window.api.quitApp) {
        window.api.quitApp();
      }
    });
  }

  // ── 鼠标拖拽移动窗口（无点击打开面板） ──────────────────
  let isDragging = false;
  let didMove = false;
  let lastX = 0, lastY = 0;
  const DRAG_THRESHOLD = 4;

  container.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    isDragging = true;
    didMove = false;
    lastX = e.screenX;
    lastY = e.screenY;
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    if (!(e.buttons & 1)) { isDragging = false; return; }
    const dx = e.screenX - lastX;
    const dy = e.screenY - lastY;
    if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) {
      didMove = true;
      try {
        if (window.api && window.api.ballDragMove) {
          window.api.ballDragMove(dx, dy);
        }
      } catch (err) {
        console.error('[ball] ballDragMove failed:', err);
      }
      lastX = e.screenX;
      lastY = e.screenY;
    }
  });

  document.addEventListener('mouseup', () => {
    isDragging = false;
  });

  // ── 禁用右键菜单 ──────────────────────────────────
  container.addEventListener('contextmenu', (e) => e.preventDefault());

  // ── 双击猫猫切换动作 ────────────────────────────────
  container.addEventListener('dblclick', () => {
    if (window.mascotController) window.mascotController.cycle();
  });

  // ── 猫猫动画控制器 ──────────────────────────────────
  const mascotFx = document.getElementById('mascot-fx');
  const mascotPlayer = document.getElementById('mascot');
  if (mascotPlayer && mascotFx) {
    window.mascotController = new MascotController(mascotPlayer, mascotFx);
    const startIdle = () => window.mascotController.onIdle();
    mascotPlayer.addEventListener('ready', startIdle, { once: true });
    mascotPlayer.addEventListener('load', startIdle, { once: true });
    if (mascotPlayer.readyState === 1 || mascotPlayer.isReady) startIdle();
  }

  if (typeof window.api === 'undefined') {
    console.warn('Electron API not available — running in dev mode');
  }
}

// ====================================================================
//  PANEL MODE — 面板窗口
// ====================================================================

// ── 关闭前压缩记忆（模块级，供 initPanelMode 和 backToSessionList 共用）──
async function compressCurrentSession() {
  if (chatThreadId && window.api && window.api.compressSession) {
    try { await window.api.compressSession(chatThreadId); }
    catch (e) { console.warn('[memory] compress on close:', e); }
  }
}

function initPanelMode(panelName) {
  document.documentElement.classList.add('panel-mode');
  document.body.classList.add('panel-mode');

  // 隐藏 ball, 显示 panel 容器
  const ballWrapper = document.getElementById('ball-wrapper');
  if (ballWrapper) ballWrapper.classList.add('hidden');
  const panelContainer = document.getElementById('panel-mode');
  panelContainer.classList.remove('hidden');
  panelContainer.classList.add('panel-visible');

  // 只显示匹配的面板
  const allPanels = ['settings-panel', 'chat-panel', 'tools-panel', 'skills-panel', 'monitoring-panel'];
  allPanels.forEach((id) => {
    const el = document.getElementById(id);
    el.classList.toggle('hidden', id !== `${panelName}-panel`);
  });

  // ── 标题栏拖拽移动窗口 ──────────────────
  (() => {
    let dragging = false;
    let lastX = 0, lastY = 0;
    const THRESHOLD = 3;

    panelContainer.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (!e.target.closest('.panel-header')) return;
      dragging = true;
      lastX = e.screenX;
      lastY = e.screenY;
    });

    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      if (!(e.buttons & 1)) { dragging = false; return; }
      const dx = e.screenX - lastX;
      const dy = e.screenY - lastY;
      if (Math.abs(dx) > THRESHOLD || Math.abs(dy) > THRESHOLD) {
        if (window.api && window.api.panelDragMove) {
          window.api.panelDragMove(dx, dy);
        }
        lastX = e.screenX;
        lastY = e.screenY;
      }
    });

    document.addEventListener('mouseup', () => { dragging = false; });
  })();

  // ── 双击标题栏切换全屏 ──────────────────
  panelContainer.addEventListener('dblclick', (e) => {
    const header = e.target.closest('.panel-header');
    if (header && window.api && window.api.toggleMaximize) {
      window.api.toggleMaximize();
    }
  });

  // ── 关闭：按钮 / Escape ─────────────────
  const close = () => {
    // fire-and-forget: 压缩记忆不阻塞关闭
    if (window.api && window.api.compressSession && chatThreadId) {
      window.api.compressSession(chatThreadId).catch(() => {});
    }
    if (window.api && window.api.closePanel) {
      window.api.closePanel();
    } else {
      window.close();
    }
  };

  // 委托监听所有 panel-header 中的关闭按钮
  panelContainer.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-close-panel]');
    if (btn) close();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });

  // ── 直接关闭窗口时（鼠标点击 X）压缩记忆 ──
  window.addEventListener('pagehide', () => {
    if (chatThreadId && window.api && window.api.compressSession) {
      // pagehide 不等待 async，用 sendBeacon 式的 fire-and-forget
      window.api.compressSession(chatThreadId);
    }
  });

  // ── 加载面板数据 ────────────────────────
  switch (panelName) {
    case 'settings':
      setupSettingsPanel();
      break;
    case 'chat':
      setupChatPanel();
      break;
    case 'tools':
      setupToolsPanel();
      break;
    case 'skills':
      setupSkillsPanel();
      break;
    case 'monitoring':
      setupMonitoringPanel();
      break;
  }

  // ── 监听 IPC 面板切换（避免 loadFile 闪烁） ──
  if (window.api && window.api.onSwitchPanel) {
    window.api.onSwitchPanel((name) => {
      const allPanels = ['settings-panel', 'chat-panel', 'tools-panel', 'skills-panel', 'monitoring-panel'];
      allPanels.forEach((id) => {
        document.getElementById(id).classList.add('hidden');
      });
      const target = document.getElementById(`${name}-panel`);
      if (target) target.classList.remove('hidden');

      switch (name) {
        case 'settings':
          if (typeof _bindSettingsListeners === 'function') _bindSettingsListeners();
          if (typeof _refreshSettingsData === 'function') _refreshSettingsData();
          break;
        case 'chat':
          if (!_chatListenersBound) setupChatPanel();
          else loadSessionList();
          break;
        case 'tools':
          setupToolsPanel();
          break;
        case 'skills':
          setupSkillsPanel();
          break;
        case 'monitoring':
          setupMonitoringPanel();
          break;
      }
    });
  }
}

// ── 设置面板 ──────────────────────────────────────────────────────────

// 一次性事件绑定标志
let _settingsListenersBound = false;

async function setupSettingsPanel() {
  _bindSettingsListeners();
  await _refreshSettingsData();
}

function _bindSettingsListeners() {
  if (_settingsListenersBound) return;
  _settingsListenersBound = true;

  // 密码显隐切换
  const toggleBtn = document.querySelector('.toggle-visibility');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const input = document.getElementById(toggleBtn.dataset.target);
      if (!input) return;
      const isPassword = input.type === 'password';
      input.type = isPassword ? 'text' : 'password';
      toggleBtn.innerHTML = isPassword ? '&#128064;' : '&#128065;';
    });
  }

  // 调试模式开关
  const debugToggle = document.getElementById('debug-toggle');
  if (debugToggle) {
    debugToggle.addEventListener('click', () => {
      const isActive = debugToggle.classList.toggle('active');
      debugToggle.dataset.enabled = isActive ? '1' : '0';
      const label = debugToggle.parentElement.querySelector('.toggle-label');
      if (label) label.textContent = isActive ? '开' : '关';
    });
  }

  // LLM 协议切换 → 联动 base-url 占位符
  const providerSelect = document.getElementById('llm-provider');
  const baseUrlInput = document.getElementById('llm-base-url');
  if (providerSelect && baseUrlInput) {
    const updateBaseUrlPlaceholder = () => {
      if (providerSelect.value === 'anthropic') {
        baseUrlInput.placeholder = 'https://api.anthropic.com';
      } else {
        baseUrlInput.placeholder = 'https://api.openai.com/v1';
      }
    };
    providerSelect.addEventListener('change', updateBaseUrlPlaceholder);
    updateBaseUrlPlaceholder(); // 初始状态
  }

  document.getElementById('save-settings').addEventListener('click', async () => {
    const debugToggle = document.getElementById('debug-toggle');
    const settings = {
      llm_api_key: document.getElementById('api-key').value,
      llm_base_url: document.getElementById('llm-base-url').value,
      llm_model: document.getElementById('model-name').value,
      llm_provider: document.getElementById('llm-provider').value,
      debug_enabled: debugToggle ? debugToggle.dataset.enabled === '1' : false,
    };
    let soul, profile;
    try {
      soul = JSON.parse(document.getElementById('soul-config').value || '{}');
      profile = JSON.parse(document.getElementById('profile-config').value || '{}');
    } catch (e) {
      alert('JSON 解析失败: ' + e.message);
      return;
    }
    try {
      await Promise.all([
        window.api.saveSettings(settings),
        window.api.saveSoul(soul),
        window.api.saveProfile(profile),
      ]);
      window.api.closePanel();
    } catch (e) {
      alert('保存失败: ' + (e && e.message ? e.message : e));
    }
  });

  document.getElementById('cancel-settings').addEventListener('click', () => {
    if (window.api) window.api.closePanel();
  });

}

async function _refreshSettingsData() {
  try {
    if (window.api && window.api.getSettings) {
      const [settings, soul, profile] = await Promise.all([
        window.api.getSettings(),
        window.api.getSoul(),
        window.api.getProfile(),
      ]);
      // LLM 设置：读新字段，fallback 到旧 deepseek 字段
      const providerVal = settings.llm_provider || '';
      const providerEl = document.getElementById('llm-provider');
      // 匹配下拉选项值，deepseek 也映射到 openai（兼容）
      if (providerVal === 'deepseek' || providerVal === 'openai' || providerVal === 'anthropic') {
        providerEl.value = providerVal === 'deepseek' ? 'openai' : providerVal;
      }
      document.getElementById('llm-base-url').value = settings.llm_base_url || settings.deepseek_base_url || '';
      document.getElementById('api-key').value = settings.llm_api_key || settings.deepseek_api_key || '';
      document.getElementById('model-name').value = settings.llm_model || settings.deepseek_model || '';
      document.getElementById('soul-config').value = JSON.stringify(soul, null, 2);
      document.getElementById('profile-config').value = JSON.stringify(profile, null, 2);

      // 加载调试开关
      const debugToggle = document.getElementById('debug-toggle');
      if (debugToggle) {
        const enabled = settings.debug_enabled === true;
        debugToggle.classList.toggle('active', enabled);
        debugToggle.dataset.enabled = enabled ? '1' : '0';
        const label = debugToggle.parentElement.querySelector('.toggle-label');
        if (label) label.textContent = enabled ? '开' : '关';
      }
    }
  } catch (e) {
    console.error('Failed to load settings', e);
  }
}

// ── 对话面板 ──────────────────────────────────────────────────────────

let _chatListenersBound = false;

function setupChatPanel() {
  _bindChatListeners();
  loadSessionList();
}

function _bindChatListeners() {
  if (_chatListenersBound) return;
  _chatListenersBound = true;

  // ── 点击 session 项 → 切换至该会话的聊天视图 ──
  document.getElementById('session-list').addEventListener('click', (e) => {
    // 删除按钮 — 阻止冒泡，避免触发 session 选中
    const deleteBtn = e.target.closest('.session-delete-btn');
    if (deleteBtn) {
      e.stopPropagation();
      const item = deleteBtn.closest('.session-item');
      const threadId = item ? item.dataset.threadId : null;
      if (threadId) {
        confirmDelete('确定要删除此会话吗？').then((ok) => {
          if (ok) window.api.deleteSession(threadId).then(() => loadSessionList());
        });
      }
      return;
    }
    // 点击 checkbox — 不触发导航，更新批量删除按钮状态
    if (e.target.classList.contains('session-checkbox')) {
      updateBatchDeleteBtn();
      return;
    }
    const item = e.target.closest('.session-item');
    if (!item) return;
    const threadId = item.dataset.threadId;
    if (threadId) openChat(threadId);
  });

  // ── 全选 checkbox ──
  const selectAllCb = document.getElementById('select-all-sessions');
  if (selectAllCb) {
    selectAllCb.addEventListener('change', () => {
      const checked = selectAllCb.checked;
      document.querySelectorAll('.session-checkbox:not(#select-all-sessions)').forEach(cb => { cb.checked = checked; });
      updateBatchDeleteBtn();
    });
  }

  // ── 批量删除按钮 ──
  const batchDeleteBtn = document.getElementById('batch-delete-btn');
  if (batchDeleteBtn) {
    batchDeleteBtn.addEventListener('click', async () => {
      const ids = _getSelectedSessionIds();
      if (ids.length === 0) return;
      const ok = await confirmDelete(`确定要删除选中的 ${ids.length} 个会话吗？`);
      if (!ok) return;
      try {
        await window.api.deleteSessionsBatch(ids);
        loadSessionList();
      } catch (e) {
        console.error('Batch delete failed', e);
      }
    });
  }

  // ── 新建会话 → 创建 thread 后打开聊天视图 ──
  document.getElementById('new-session').addEventListener('click', async () => {
    try {
      const result = await window.api.createSession();
      if (result && result.thread_id) {
        openChat(result.thread_id, /* isNew */ true);
      }
    } catch (e) {
      console.error('Failed to create session', e);
    }
  });

  // ── 发送消息 ──────────────────────────
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');

  input.addEventListener('input', () => {
    sendBtn.disabled = !input.value.trim() || isChatLoading;
  });

  input.addEventListener('keydown', (e) => {
    // Enter 发送，Shift+Enter 换行
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });

  sendBtn.addEventListener('click', sendChatMessage);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return '';
  const now = new Date();
  const date = new Date(dateStr);
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return '刚刚';
  if (diffMin < 60) return diffMin + '分钟前';
  if (diffHour < 24) return diffHour + '小时前';
  if (diffDay === 1) return '昨天';
  if (diffDay < 7) return diffDay + '天前';
  // 超过一周显示具体日期
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return month + '/' + day;
}

function processInline(text) {
  // Inline code (must come before bold/italic to avoid conflicts)
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Italic
  text = text.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  // Links
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return text;
}

function renderMarkdown(text) {
  if (!text) return '';
  // Escape HTML first to prevent XSS
  const escaped = escapeHtml(text);
  const lines = escaped.split('\n');
  let html = '';
  let inCodeBlock = false;
  let codeContent = '';
  let listTag = ''; // 'ul' or 'ol'
  let tableBuffer = []; // collect table rows for multi-line table

  function flushTable() {
    if (tableBuffer.length < 2) { tableBuffer = []; return; }
    // Second line must be a separator row
    const sep = tableBuffer[1];
    if (!sep.match(/^\|[-:| ]+\|$/)) { tableBuffer = []; return; }
    // Determine alignment from separator row
    const alignCells = sep.split('|').filter(c => c.trim() !== '');
    const alignments = alignCells.map(cell => {
      const left = cell.startsWith(':');
      const right = cell.endsWith(':');
      if (left && right) return 'center';
      if (right) return 'right';
      return 'left';
    });

    html += '<table>';
    // Header row
    html += '<thead><tr>';
    const headerCells = tableBuffer[0].split('|').filter(c => c.trim() !== '');
    headerCells.forEach((cell, i) => {
      const al = alignments[i] || 'left';
      html += '<th style="text-align:' + al + '">' + processInline(cell.trim()) + '</th>';
    });
    html += '</tr></thead>';
    // Body rows
    html += '<tbody>';
    for (let r = 2; r < tableBuffer.length; r++) {
      const cells = tableBuffer[r].split('|').filter(c => c.trim() !== '');
      if (cells.length === 0) continue;
      html += '<tr>';
      cells.forEach((cell, i) => {
        const al = alignments[i] || 'left';
        html += '<td style="text-align:' + al + '">' + processInline(cell.trim()) + '</td>';
      });
      html += '</tr>';
    }
    html += '</tbody></table>';
    tableBuffer = [];
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code blocks
    if (line.startsWith('```')) {
      flushTable();
      if (inCodeBlock) {
        html += '<pre><code>' + codeContent + '</code></pre>';
        codeContent = '';
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeContent += (codeContent ? '\n' : '') + line;
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flushTable();
      html += '<hr>';
      continue;
    }

    // Headers
    if (line.startsWith('### ')) { flushTable(); html += '<h4>' + line.slice(4) + '</h4>'; continue; }
    if (line.startsWith('## ')) { flushTable(); html += '<h3>' + line.slice(3) + '</h3>'; continue; }
    if (line.startsWith('# ')) { flushTable(); html += '<h2>' + line.slice(2) + '</h2>'; continue; }

    // Unordered list items
    if (line.match(/^[-*] /)) {
      flushTable();
      if (listTag && listTag !== 'ul') { html += '</ol>'; listTag = ''; }
      if (!listTag) { html += '<ul>'; listTag = 'ul'; }
      html += '<li>' + processInline(line.slice(2)) + '</li>';
      continue;
    }
    // Ordered list items
    if (line.match(/^\d+\.\s/)) {
      flushTable();
      if (listTag && listTag !== 'ol') { html += '</ul>'; listTag = ''; }
      if (!listTag) { html += '<ol>'; listTag = 'ol'; }
      html += '<li>' + processInline(line.replace(/^\d+\.\s/, '')) + '</li>';
      continue;
    }
    // Close list when non-list line encountered
    if (listTag) {
      flushTable();
      html += '</' + listTag + '>';
      listTag = '';
    }

    // Empty line = paragraph break (also flushes table)
    if (line.trim() === '') {
      flushTable();
      continue;
    }

    // Table row detection: must start with |
    if (line.startsWith('|') && line.endsWith('|')) {
      tableBuffer.push(line);
      continue;
    }

    // Flush table if accumulated then treat line as paragraph
    if (tableBuffer.length) {
      flushTable();
    }

    // Regular paragraph
    html += '<p>' + processInline(line) + '</p>';
  }

  if (inCodeBlock) {
    html += '<pre><code>' + codeContent + '</code></pre>';
  }
  if (listTag) {
    html += '</' + listTag + '>';
  }
  flushTable(); // flush any remaining table

  return html;
}

async function loadSessionList() {
  try {
    const sessions = await window.api.getSessions();
    const list = document.getElementById('session-list');
    const toolbar = document.getElementById('session-toolbar');
    if (!sessions || sessions.length === 0) {
      list.innerHTML = '<div class="empty-state">暂无会话</div>';
      if (toolbar) toolbar.classList.add('hidden');
      return;
    }
    if (toolbar) toolbar.classList.remove('hidden');

    list.innerHTML = sessions
      .map(
        (s) => {
          const preview = (s.last_message || '').slice(0, 50);
          const time = formatRelativeTime(s.created_at);
          return `<div class="session-item" data-thread-id="${escapeHtml(s.thread_id)}">
            <input type="checkbox" class="session-checkbox" data-thread-id="${escapeHtml(s.thread_id)}">
            <div class="session-info">
              <div class="session-preview${preview ? '' : ' empty'}">${escapeHtml(preview) || '(空对话)'}</div>
              <div class="session-meta">${time} · ${s.turn_count} 轮</div>
            </div>
            <button class="session-delete-btn" title="删除此会话">&times;</button>
          </div>`;
        }
      )
      .join('');

    // 重置全选状态
    const selectAll = document.getElementById('select-all-sessions');
    if (selectAll) selectAll.checked = false;
    updateBatchDeleteBtn();
  } catch (e) {
    console.error('Failed to load sessions', e);
  }
}

function _getSelectedSessionIds() {
  const checkboxes = document.querySelectorAll('.session-checkbox:checked:not(#select-all-sessions)');
  return Array.from(checkboxes).map(cb => cb.dataset.threadId);
}

function updateBatchDeleteBtn() {
  const btn = document.getElementById('batch-delete-btn');
  const selectAll = document.getElementById('select-all-sessions');
  const checkedCount = _getSelectedSessionIds().length;
  const totalCount = document.querySelectorAll('.session-checkbox:not(#select-all-sessions)').length;
  if (btn) {
    btn.disabled = checkedCount === 0;
    btn.textContent = checkedCount > 0 ? `删除选中 (${checkedCount})` : '删除选中';
  }
  if (selectAll) {
    selectAll.checked = totalCount > 0 && checkedCount === totalCount;
  }
}

// ── 切换至聊天视图 ──────────────────────────────────────────────────────

function openChat(threadId, isNew) {
  chatThreadId = threadId;
  document.getElementById('session-list-view').classList.add('hidden');
  document.getElementById('chat-view').classList.remove('hidden');

  const title = document.getElementById('chat-panel-title');
  title.textContent = isNew ? '新对话' : threadId.slice(0, 8) + '...';

  // 显示返回按钮
  document.getElementById('chat-back-btn').classList.remove('hidden');

  // 清空旧消息和工具卡片状态
  const container = document.getElementById('chat-messages');
  container.innerHTML = '';
  _activeToolCards = {};
  _earliestMsgId = null;
  _hasMoreMessages = false;

  if (isNew) {
    container.innerHTML = '<div class="chat-empty">开始新对话</div>';
    document.getElementById('chat-input').focus();
    return;
  }

  // 加载历史消息
  loadMessages(threadId);
}

function backToSessionList() {
  compressCurrentSession().then(() => {
    chatThreadId = null;
    document.getElementById('chat-view').classList.add('hidden');
    document.getElementById('session-list-view').classList.remove('hidden');
    document.getElementById('chat-panel-title').textContent = '';
    document.getElementById('chat-back-btn').classList.add('hidden');
    loadSessionList(); // 刷新列表
  });
}

// ── 加载历史消息 ────────────────────────────────────────────────────────

let _earliestMsgId = null;   // 当前已加载最早消息的 ID（分页游标）
let _hasMoreMessages = false; // 是否还有更早的消息

async function loadMessages(threadId) {
  try {
    // 历史模式：不展示工具调用内容，仅保留 human/ai 文本对话
    const result = await window.api.getMessages(threadId, { include_tool_calls: false, limit: 50 });
    const messages = result.messages || result; // 兼容旧格式（纯数组）
    _hasMoreMessages = result.has_more === true;

    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (!messages || messages.length === 0) {
      container.innerHTML = '<div class="chat-empty">开始对话吧</div>';
      document.getElementById('chat-input').focus();
      _earliestMsgId = null;
      _hasMoreMessages = false;
      return;
    }

    // 记录最早消息 ID 作为分页游标
    _earliestMsgId = messages[0].id || null;

    // 如果有更早的消息，顶部加"加载更早消息"按钮
    if (_hasMoreMessages) {
      _addLoadMoreBtn(container, threadId);
    }

    for (const msg of messages) {
      if (msg.role !== 'human' && msg.role !== 'ai') continue;
      if (!msg.content || !msg.content.trim()) continue;
      addMessageBubble(msg.role, msg.content, msg.id);
    }
    scrollChatToBottom();
    document.getElementById('chat-input').focus();
  } catch (e) {
    console.error('Failed to load messages', e);
  }
}

function _addLoadMoreBtn(container, threadId) {
  const btn = document.createElement('button');
  btn.className = 'load-more-btn';
  btn.textContent = '加载更早消息';
  btn.addEventListener('click', async () => {
    btn.textContent = '加载中...';
    btn.disabled = true;
    try {
      const result = await window.api.getMessages(threadId, {
        include_tool_calls: false,
        limit: 50,
        before_id: _earliestMsgId,
      });
      const older = result.messages || result;
      _hasMoreMessages = result.has_more === true;

      if (!older || older.length === 0) {
        btn.remove();
        return;
      }

      _earliestMsgId = older[0].id || null;

      // 记录当前滚动高度，插入后保持位置
      const scrollBefore = container.scrollHeight;

      // 在 load-more 按钮之后插入更早消息
      const fragment = document.createDocumentFragment();
      for (const msg of older) {
        if (msg.role !== 'human' && msg.role !== 'ai') continue;
        if (!msg.content || !msg.content.trim()) continue;
        const div = document.createElement('div');
        div.className = 'chat-msg ' + escapeHtml(msg.role);
        if (msg.role === 'ai' || msg.role === 'assistant') {
          div.innerHTML = renderMarkdown(msg.content);
        } else {
          div.textContent = msg.content;
        }
        fragment.appendChild(div);
      }
      btn.insertAdjacentElement('afterend', fragment);

      if (!_hasMoreMessages) {
        btn.remove();
      } else {
        btn.textContent = '加载更早消息';
        btn.disabled = false;
      }

      // 保持滚动位置
      const scrollAfter = container.scrollHeight;
      container.scrollTop += scrollAfter - scrollBefore;
    } catch (e) {
      btn.textContent = '加载失败，点击重试';
      btn.disabled = false;
      console.error('Failed to load more messages', e);
    }
  });
  container.insertBefore(btn, container.firstChild);
}

// ── 流式发送消息（通过 CustomEvent）────────────────────────────────────

async function sendChatMessage() {
  if (!chatThreadId || isChatLoading) return;

  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  document.getElementById('chat-send').disabled = true;

  // 移除空状态提示
  const emptyEl = document.querySelector('.chat-empty');
  if (emptyEl) emptyEl.remove();

  // 显示用户消息
  addMessageBubble('human', text);
  scrollChatToBottom();

  // 猫猫进入思考状态
  if (window.mascotController) window.mascotController.onThinking();

  // 创建 AI 消息气泡（初始空白，流式填入）
  isChatLoading = true;
  const container = document.getElementById('chat-messages');
  const aiBubble = document.createElement('div');
  aiBubble.className = 'chat-msg ai';
  container.appendChild(aiBubble);
  scrollChatToBottom();

  let hasContent = false;
  let pendingInterrupt = null;  // 存中断信息，等待用户确认
  let startedResume = false;    // 避免重复启动 resume

  function chatCleanup() {
    window.removeEventListener('chat-sse', handler);
    if (!hasContent && !aiBubble.textContent.trim()) {
      aiBubble.textContent = '(空回答)';
    }
    scrollChatToBottom();
    isChatLoading = false;
    document.getElementById('chat-send').disabled = !document.getElementById('chat-input').value.trim();
    pendingInterrupt = null;
    // 猫猫回到待机
    if (window.mascotController) window.mascotController.onIdle();
  }

  // 监听 preload 推送的 SSE 事件（自动清理）
  const handler = (e) => {
    const { event, data } = e.detail;

    // interrupt 事件：工具需要用户确认
    if (event === 'interrupt') {
      pendingInterrupt = data;
      // 在气泡中显示需要确认
      const confirmEl = document.createElement('div');
      confirmEl.className = 'chat-msg system';
      confirmEl.textContent = '⚠️ 需要确认：' + (data.command || data.type);
      container.insertBefore(confirmEl, aiBubble);
      scrollChatToBottom();

      // 显示确认对话框
      showCommandConfirm(data, (approved) => {
        if (!window.api || !window.api.resumeChatStream) return;
        startedResume = true;
        window.api.resumeChatStream(chatThreadId, approved);
      });
      return;
    }

    hasContent = handleStreamEvent(event, data, aiBubble) || hasContent;

    if (event === 'done') {
      if (data.interrupted && pendingInterrupt) {
        // 有中断等待用户确认——不清理，不设 isChatLoading=false
        return;
      }
      chatCleanup();
    }

    if (event === 'error') {
      chatCleanup();
    }
  };

  window.addEventListener('chat-sse', handler);
  // fire & forget — 事件通过 CustomEvent 回到 handler
  window.api.sendChatStream(chatThreadId, text);
}

// ── 命令确认对话框 ──────────────────────────────────────────────────────

function showCommandConfirm(data, callback) {
  const overlay = document.getElementById('command-confirm-dialog');
  const cmdEl = document.getElementById('confirm-command');
  const reasonEl = document.getElementById('confirm-reason');
  const okBtn = document.getElementById('cmd-confirm-ok');
  const cancelBtn = document.getElementById('cmd-confirm-cancel');

  cmdEl.textContent = data.command || data.path || '';
  reasonEl.textContent = data.reason || '此操作需要确认';

  overlay.classList.remove('hidden');

  const cleanup = () => {
    overlay.classList.add('hidden');
    okBtn.removeEventListener('click', onOk);
    cancelBtn.removeEventListener('click', onCancel);
  };

  const onOk = () => { cleanup(); callback(true); };
  const onCancel = () => { cleanup(); callback(false); };

  okBtn.addEventListener('click', onOk);
  cancelBtn.addEventListener('click', onCancel);
}

let _activeToolCards = {};  // run_id → DOM element

// ── SSE 事件处理 ───────────────────────────────────────────────────────

function handleStreamEvent(event, data, aiBubble) {
  const container = document.getElementById('chat-messages');

  switch (event) {
    case 'token':
      if (!aiBubble._rawMarkdown) aiBubble._rawMarkdown = '';
      aiBubble._rawMarkdown += data.text;
      aiBubble.innerHTML = renderMarkdown(aiBubble._rawMarkdown);
      scrollChatToBottom();
      return true;

    case 'context': {
      const el = document.createElement('div');
      el.className = 'chat-msg debug-context';
      el.innerHTML = '<details><summary style="cursor:pointer;font-size:11px;color:rgba(162,155,254,0.7);user-select:none;">🔍 查看完整上下文</summary>' +
        '<pre style="font-size:10px;margin-top:6px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow-y:auto;background:rgba(0,0,0,0.3);padding:8px;border-radius:6px;color:rgba(255,255,255,0.7);line-height:1.5;">' +
        escapeHtml(data.text) + '</pre></details>';
      container.insertBefore(el, aiBubble);
      scrollChatToBottom();
      return true;
    }

    case 'tool_start': {
      const card = _createToolCard(data);
      _activeToolCards[data.run_id] = card;
      container.insertBefore(card, aiBubble);
      scrollChatToBottom();
      return true;
    }

    case 'tool_end': {
      const card = _activeToolCards[data.run_id];
      if (card) {
        _updateToolCard(card, 'done', data.output);
        delete _activeToolCards[data.run_id];
      } else {
        // 未找到对应卡片（可能在页面刷新后），创建完成卡片
        const doneCard = _createToolCard(data);
        _updateToolCard(doneCard, 'done', data.output);
        container.insertBefore(doneCard, aiBubble);
      }
      scrollChatToBottom();
      return true;
    }

    case 'tool_error': {
      const card = _activeToolCards[data.run_id];
      if (card) {
        _updateToolCard(card, 'error', data.error);
        delete _activeToolCards[data.run_id];
      } else {
        const errCard = _createToolCard(data);
        _updateToolCard(errCard, 'error', data.error);
        container.insertBefore(errCard, aiBubble);
      }
      scrollChatToBottom();
      return true;
    }

    case 'error':
      aiBubble.textContent = '出错了：' + data.error;
      aiBubble.className = 'chat-msg error';
      scrollChatToBottom();
      return true;

    case 'done':
      return true;

    default:
      return false;
  }
}

// ── Tool card helpers ──

function _createToolCard(data) {
  const card = document.createElement('div');
  card.className = 'tool-card loading';
  card.innerHTML =
    '<div class="tool-card-header">' +
      '<span class="tool-card-spinner"></span>' +
      '<span class="tool-card-name">' + escapeHtml(data.name) + '</span>' +
      '<span class="tool-card-status loading">执行中...</span>' +
      '<span class="tool-card-arrow">▸</span>' +
    '</div>' +
    '<div class="tool-card-body hidden">' +
      '<div class="tool-card-section"><span class="tool-card-label">输入</span><pre>' + escapeHtml(data.input || '') + '</pre></div>' +
    '</div>';
  card.querySelector('.tool-card-header').addEventListener('click', function() {
    card.classList.toggle('expanded');
    const body = card.querySelector('.tool-card-body');
    const arrow = card.querySelector('.tool-card-arrow');
    body.classList.toggle('hidden');
    arrow.textContent = body.classList.contains('hidden') ? '▸' : '▾';
  });
  return card;
}

function _updateToolCard(card, status, detail) {
  card.classList.remove('loading');
  const statusEl = card.querySelector('.tool-card-status');
  const body = card.querySelector('.tool-card-body');

  if (status === 'done') {
    card.classList.add('done');
    statusEl.className = 'tool-card-status done';
    statusEl.textContent = '完成';
    // Add output section
    const outSection = document.createElement('div');
    outSection.className = 'tool-card-section';
    outSection.innerHTML = '<span class="tool-card-label">输出</span><pre>' + escapeHtml(detail || '') + '</pre>';
    body.appendChild(outSection);
  } else if (status === 'error') {
    card.classList.add('error');
    statusEl.className = 'tool-card-status error';
    statusEl.textContent = '失败';
    // Show error in body
    body.innerHTML = '<div class="tool-card-section error"><span class="tool-card-label">错误</span><pre>' + escapeHtml(detail || '') + '</pre></div>';
    card.classList.add('expanded');
    body.classList.remove('hidden');
    card.querySelector('.tool-card-arrow').textContent = '▾';
  }
}

// ── 消息气泡 ────────────────────────────────────────────────────────────

function addMessageBubble(role, content, msgId) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + escapeHtml(role);
  if (msgId) div.dataset.msgId = msgId;
  if (role === 'ai' || role === 'assistant') {
    div.innerHTML = renderMarkdown(content);
  } else {
    div.textContent = content;
  }
  container.appendChild(div);
}

function scrollChatToBottom() {
  const container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

// ── 返回按钮事件（点击 panel-header 中的 "←"）──
document.addEventListener('click', (e) => {
  const backBtn = e.target.closest('.chat-back-btn');
  if (backBtn) backToSessionList();
});

// ── 自定义确认对话框 ──────────────────────────────────────────────────

function confirmDelete(message) {
  return new Promise((resolve) => {
    const overlay = document.getElementById('confirm-dialog');
    const msgEl = document.getElementById('confirm-message');
    const okBtn = document.getElementById('confirm-ok');
    const cancelBtn = document.getElementById('confirm-cancel');

    msgEl.textContent = message || '确定要删除此会话吗？';
    overlay.classList.remove('hidden');

    const cleanup = () => {
      overlay.classList.add('hidden');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
    };

    const onOk = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
  });
}

// ── 工具面板 ──────────────────────────────────────────────────────────

// ── 工具分类显示配置（key 来自 Python 源码 __category__）──
const CATEGORY_CONFIG = {
  file_system:    { label: '文件系统', initials: 'FILE', color: '#fb923c' },
  web:            { label: '网络信息', initials: 'WEB', color: '#f59e0b' },
  code_execution: { label: '代码执行', initials: 'CODE', color: '#f87171' },
  computation:    { label: '计算工具', initials: 'MATH', color: '#38bdf8' },
  system:         { label: '系统管理', initials: 'SYS', color: '#a78bfa' },
  agent:          { label: 'Agent',    initials: 'AGT', color: '#34d399' },
};
const CATEGORY_ORDER = ['file_system', 'web', 'code_execution', 'computation', 'system', 'agent'];

function getCategoryConfig(key) {
  return CATEGORY_CONFIG[key] || { label: key || '其他', initials: '···', color: '#9ca3af' };
}

function getToolFileName(tool) {
  const f = tool.file || '';
  const parts = f.replace(/\\/g, '/').split('/');
  return parts.slice(-2).join('/');
}

// ── 自定义 hover tooltip（贴合暗色主题）──
let tooltipEl = null;

function ensureTooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'tooltip-floating hidden';
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

function showTooltip(text, anchorEl) {
  const tip = ensureTooltip();
  tip.textContent = text;
  tip.classList.remove('hidden');
  requestAnimationFrame(() => {
    const rect = anchorEl.getBoundingClientRect();
    const tipR = tip.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + tipR.width > window.innerWidth - 8) left = rect.right - tipR.width;
    if (top + tipR.height > window.innerHeight - 8) top = rect.top - tipR.height - 4;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  });
}

function hideTooltip() {
  if (tooltipEl) tooltipEl.classList.add('hidden');
}

async function setupToolsPanel() {
  try {
    const tools = await window.api.getTools();
    const grid = document.getElementById('tools-grid');
    if (!tools || tools.length === 0) {
      grid.innerHTML = '<div class="empty-state">暂无工具</div>';
      return;
    }

    // 按分类分组（category 来自 Python 源码 __category__）
    const grouped = {};
    for (const t of tools) {
      const key = t.category || '';
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(t);
    }

    // 保持分类定义顺序，未定义的排到最后
    const sortedKeys = [...new Set([...CATEGORY_ORDER.filter(k => grouped[k]), ...Object.keys(grouped)])];

    let html = '';
    for (const key of sortedKeys) {
      const groupTools = grouped[key];
      if (!groupTools || !groupTools.length) continue;
      const cfg = getCategoryConfig(key);
      html += `<div class="tool-group" data-cat="${key}">
        <div class="tool-group-header" style="border-color:${cfg.color};">
          <span class="tool-group-arrow">▸</span>
          <span class="tool-group-icon" style="background:${cfg.color};">${cfg.initials}</span>
          <span class="tool-group-name">${cfg.label}</span>
          <span class="tool-group-count">${groupTools.length}</span>
        </div>
        <div class="tool-group-items">`;
      for (const t of groupTools) {
        const fileName = getToolFileName(t);
        html += `<div class="tool-card">
          <div class="tool-card-header">
            <span class="tool-card-badge" style="background:${cfg.color}22;color:${cfg.color};">${cfg.initials}</span>
            <span class="tool-card-file" title="${escapeHtml(t.file || '')}">${escapeHtml(fileName)}</span>
          </div>
          <div class="tool-card-name">${escapeHtml(t.name)}</div>
          <div class="tool-card-desc" data-full="${escapeHtml(t.description || '无描述')}">${escapeHtml(t.description || '无描述')}</div>
        </div>`;
      }
      html += `</div></div>`;
    }

    grid.innerHTML = html;

    // ── 分类展开/收起 ──
    grid.querySelectorAll('.tool-group-header').forEach(header => {
      header.addEventListener('click', () => {
        header.parentElement.classList.toggle('collapsed');
      });
    });

    // ── 描述 hover tooltip（仅截断时显示，固定位置）──
    grid.querySelectorAll('.tool-card-desc').forEach(desc => {
      desc.addEventListener('mouseenter', () => {
        if (desc.scrollHeight > desc.clientHeight) {
          showTooltip(desc.getAttribute('data-full') || desc.textContent, desc);
        }
      });
      desc.addEventListener('mouseleave', hideTooltip);
    });
  } catch (e) {
    console.error('Failed to load tools', e);
  }
}

// ── 技能面板 ──────────────────────────────────────────────────────────

async function setupSkillsPanel() {
  const container = document.getElementById('skills-list');
  if (!container) return;

  try {
    const skills = await window.api.getSkills();

    if (!skills || skills.length === 0) {
      container.innerHTML = '<div class="empty-state">暂无可用技能</div>';
      return;
    }

    let html = `<div id="skill-list-view">`;
    for (const skill of skills) {
      const badges = [];
      if (skill.tools && skill.tools.length > 0) {
        badges.push(`<span class="skill-badge tool-badge">🛠 ${skill.tools.length} 工具</span>`);
      }

      html += `<div class="skill-card" data-skill-name="${escapeHtml(skill.name)}">
        <div class="skill-card-header">
          <span class="skill-card-name">${escapeHtml(skill.name)}</span>
        </div>
        <div class="skill-card-desc">${escapeHtml(skill.description || '无描述')}</div>
        <div class="skill-card-meta">
          ${badges.join('')}
        </div>
        <a class="skill-card-detail-link" data-skill="${escapeHtml(skill.name)}">查看详情 →</a>
      </div>`;
    }
    html += `</div>`;
    html += `<div id="skill-detail-view" class="hidden"></div>`;
    container.innerHTML = html;

    // 详情链接事件
    container.querySelectorAll('.skill-card-detail-link').forEach((link) => {
      link.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const skillName = link.dataset.skill;
        if (!skillName) return;
        await showSkillDetail(skillName, container);
      });
    });

  } catch (e) {
    console.error('Failed to load skills', e);
    const errMsg = e && e.message ? e.message : String(e);
    container.innerHTML = `<div class="empty-state">加载技能失败<br><span style="font-size:10px;color:rgba(255,80,80,0.6);margin-top:6px;display:inline-block;">${escapeHtml(errMsg)}</span></div>`;
  }
}

async function showSkillDetail(skillName, container) {
  try {
    const detail = await window.api.getSkillDetail(skillName);
    if (!detail) {
      container.innerHTML = '<div class="empty-state">技能不存在</div>';
      return;
    }

    // 隐藏列表视图
    const listView = document.getElementById('skill-list-view');
    const detailView = document.getElementById('skill-detail-view');
    if (listView) listView.classList.add('hidden');
    if (detailView) detailView.classList.remove('hidden');

    // 构建详情内容
    let html = `
      <div class="panel-header" style="padding:0 0 10px 0;border-bottom:1px solid rgba(255,255,255,0.08);-webkit-app-region:no-drag;">
        <h3 style="font-size:15px;">${escapeHtml(detail.name)}</h3>
        <button class="skill-back-btn" id="skill-detail-back">← 返回列表</button>
      </div>
      <div id="skill-detail-body" style="padding-top:12px;">
        <div class="skill-detail-section">
          <h4>描述</h4>
          <p>${escapeHtml(detail.description || '无描述')}</p>
        </div>`;

    if (detail.prompt_injection) {
      html += `
        <div class="skill-detail-section">
          <h4>提示注入</h4>
          <pre>${escapeHtml(detail.prompt_injection)}</pre>
        </div>`;
    }

    if (detail.tools && detail.tools.length > 0) {
      html += `
        <div class="skill-detail-section skill-detail-tools">
          <h4>工具 (${detail.tools.length})</h4>
          <ul>`;
      for (const t of detail.tools) {
        html += `<li>${escapeHtml(t.name || t)}</li>`;
      }
      html += `</ul></div>`;
    }

    html += `</div>`; // close skill-detail-body

    detailView.innerHTML = html;

    // 返回按钮
    document.getElementById('skill-detail-back').addEventListener('click', () => {
      const lv = document.getElementById('skill-list-view');
      const dv = document.getElementById('skill-detail-view');
      if (lv) lv.classList.remove('hidden');
      if (dv) dv.classList.add('hidden');
    });

  } catch (e) {
    console.error('Failed to load skill detail:', e);
  }
}

// ── Monitoring Panel ───────────────────────────────────────────────────

async function setupMonitoringPanel() {
  const body = document.getElementById('monitoring-body');
  if (!body) return;

  // ── Render tab bar and view containers ──
  body.innerHTML = `
    <div class="monitoring-tab-bar">
      <button class="monitoring-tab active" data-tab="overview">总览</button>
      <button class="monitoring-tab" data-tab="traces">Trace</button>
      <button class="monitoring-tab" data-tab="tokens">Token</button>
      <button class="monitoring-tab" data-tab="latency">延迟</button>
      <button class="monitoring-tab" data-tab="cache">缓存</button>
    </div>
    <div id="monitoring-overview" class="monitoring-view active"></div>
    <div id="monitoring-traces" class="monitoring-view"></div>
    <div id="monitoring-tokens" class="monitoring-view"></div>
    <div id="monitoring-latency" class="monitoring-view"></div>
    <div id="monitoring-cache" class="monitoring-view"></div>
  `;

  // ── Tab switching ──
  body.querySelectorAll('.monitoring-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      body.querySelectorAll('.monitoring-tab').forEach(t => t.classList.remove('active'));
      body.querySelectorAll('.monitoring-view').forEach(v => v.classList.remove('active'));
      tab.classList.add('active');
      const view = document.getElementById('monitoring-' + tab.dataset.tab);
      if (view) view.classList.add('active');
      // Load data on tab switch
      if (tab.dataset.tab === 'traces') loadTraces();
      else if (tab.dataset.tab === 'tokens') loadTokenStats();
      else if (tab.dataset.tab === 'latency') loadLatencyStats();
      else if (tab.dataset.tab === 'cache') loadCacheStats();
    });
  });

  // ── Load overview data ──
  await loadOverview();
}

async function loadOverview() {
  const container = document.getElementById('monitoring-overview');
  if (!container) return;
  try {
    const stats = await window.api.getTraceStats();
    const daily = await window.api.getTraceDailyStats();

    // Stats cards
    const errorRate = (stats.error_rate || 0).toFixed(1);
    const yesterdayDiff = stats.total_tokens > 0 && stats.yesterday_tokens > 0
      ? ((stats.total_tokens - stats.yesterday_tokens) / stats.yesterday_tokens * 100).toFixed(0)
      : null;
    const yesterdayClass = yesterdayDiff !== null && yesterdayDiff > 0 ? '#f87171' : '#4ade80';
    const yesterdayArrow = yesterdayDiff !== null && yesterdayDiff > 0 ? '↑' : '↓';
    const yesterdayText = yesterdayDiff !== null ? `${yesterdayArrow}${Math.abs(yesterdayDiff)}% 较昨日` : '';

    // Bar chart for past hours (last 12 from daily or mock)
    let barsHtml = '';
    if (daily && daily.length > 0) {
      const maxTokens = Math.max(...daily.map(d => d.total_tokens), 1);
      daily.slice(0, 12).reverse().forEach(d => {
        const pct = (d.total_tokens / maxTokens * 70);
        barsHtml += `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;">
          <div style="background:#2a2a42;border-radius:3px;height:60px;width:100%;display:flex;align-items:flex-end;">
            <div style="background:linear-gradient(180deg,#6c5ce7,#a29bfe);border-radius:3px 3px 0 0;width:100%;height:${pct}%;"></div>
          </div>
          <span style="font-size:8px;color:#666;">${d.day.slice(5)}</span>
        </div>`;
      });
    } else {
      barsHtml = '<div style="text-align:center;padding:20px;color:#888;width:100%;">暂无趋势数据，发送消息后即可生成</div>';
    }

    container.innerHTML = `
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">今日 Token</div>
          <div class="monitoring-stat-value">${formatTokens(stats.total_tokens || 0)}</div>
          <div style="font-size:11px;color:${yesterdayClass};margin-top:2px;">${yesterdayText}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">平均延迟</div>
          <div class="monitoring-stat-value">${formatDuration(stats.avg_duration_ms || 0)}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">今日调用</div>
          <div class="monitoring-stat-value">${stats.total_traces || 0}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">错误率</div>
          <div class="monitoring-stat-value" style="${errorRate > 5 ? 'color:#f87171' : ''}">${errorRate}%</div>
        </div>
      </div>
      <div class="monitoring-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;font-weight:600;color:#ddd;">Token 消耗趋势</span>
          <span style="font-size:11px;color:#8888aa;">过去 ${daily.length || 12} 天</span>
        </div>
        <div style="display:flex;align-items:flex-end;height:70px;gap:4px;">
          ${barsHtml}
        </div>
      </div>
      <div class="monitoring-card">
        <div style="font-size:13px;font-weight:600;color:#ddd;margin-bottom:12px;">近期 Trace</div>
        <div id="monitoring-recent-traces" style="display:flex;flex-direction:column;gap:8px;">
          <div style="text-align:center;padding:20px;color:#888;">加载中...</div>
        </div>
      </div>
    `;

    // Load recent traces
    try {
      const traces = await window.api.getTraces(null, null, 5, 0);
      const recentContainer = document.getElementById('monitoring-recent-traces');
      if (recentContainer && traces.length > 0) {
        recentContainer.innerHTML = traces.map(t => `
          <div class="monitoring-trace-row" onclick="showTraceDetail('${t.trace_id}')">
            <span style="width:8px;height:8px;border-radius:50%;background:${t.status === 'error' ? '#f87171' : '#4ade80'};flex-shrink:0;"></span>
            <span class="mono" style="font-size:11px;color:#aaa;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t.user_message || t.trace_id.slice(0, 8)}</span>
            <span style="font-size:11px;color:#aaa;">${formatTokens((t.input_tokens || 0) + (t.output_tokens || 0))}</span>
            <span style="font-size:11px;color:#888;">${formatDuration(t.duration_ms || 0)}</span>
            <span class="monitoring-badge ${t.status === 'error' ? 'error' : 'success'}">${t.status === 'error' ? '失败' : '完成'}</span>
          </div>
        `).join('');
        if (traces.length >= 5) {
          recentContainer.innerHTML += '<div style="text-align:center;margin-top:8px;"><span class="monitoring-link" onclick="switchMonitoringTab(\'traces\')">查看全部 Trace</span></div>';
        }
      } else if (recentContainer) {
        recentContainer.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">暂无 Trace 数据</div>';
      }
    } catch (e) {
      console.error('Failed to load recent traces:', e);
    }
  } catch (e) {
    console.error('Failed to load overview:', e);
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
  }
}

let traceState = { page: 0, pageSize: 50, sortField: 'time', sortDir: 'desc' };

async function loadTraces() {
  const container = document.getElementById('monitoring-traces');
  if (!container) return;

  container.innerHTML = `
    <div class="monitoring-card">
      <div style="display:flex;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
        <input class="monitoring-search-input" id="trace-search-input" placeholder="搜索会话内容或 Trace ID..." style="flex:2;min-width:140px;" />
        <select class="monitoring-select" id="trace-status-filter">
          <option value="">全部状态</option>
          <option value="ok">成功</option>
          <option value="error">失败</option>
        </select>
        <select class="monitoring-select" id="trace-sort-field">
          <option value="time" ${traceState.sortField === 'time' ? 'selected' : ''}>时间</option>
          <option value="tokens" ${traceState.sortField === 'tokens' ? 'selected' : ''}>Token</option>
          <option value="duration" ${traceState.sortField === 'duration' ? 'selected' : ''}>延迟</option>
        </select>
        <button class="monitoring-page-btn" id="trace-sort-dir" title="${traceState.sortDir === 'desc' ? '降序' : '升序'}">
          ${traceState.sortDir === 'desc' ? '▼ 降序' : '▲ 升序'}
        </button>
      </div>
      <div id="trace-list"></div>
      <div id="trace-pagination" style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;"></div>
    </div>
  `;

  const searchInput = document.getElementById('trace-search-input');
  const statusFilter = document.getElementById('trace-status-filter');
  const sortField = document.getElementById('trace-sort-field');
  const sortDirBtn = document.getElementById('trace-sort-dir');

  async function doSearch() {
    const list = document.getElementById('trace-list');
    const pagination = document.getElementById('trace-pagination');
    if (!list) return;
    list.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">加载中...</div>';
    try {
      const [traces, totalCount] = await Promise.all([
        window.api.getTraces(searchInput.value, statusFilter.value, traceState.pageSize, traceState.page * traceState.pageSize),
        window.api.getTraceCount(searchInput.value, statusFilter.value)
      ]);

      // Client-side sort
      let sorted = [...traces];
      if (traceState.sortField === 'tokens') {
        sorted.sort((a, b) => ((b.input_tokens || 0) + (b.output_tokens || 0)) - ((a.input_tokens || 0) + (a.output_tokens || 0)));
      } else if (traceState.sortField === 'duration') {
        sorted.sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0));
      }
      // 'time' is already sorted by backend DESC
      if (traceState.sortDir === 'asc') sorted.reverse();

      if (sorted.length === 0) {
        list.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">暂无 Trace 记录</div>';
        if (pagination) pagination.innerHTML = '';
        return;
      }

      const totalPages = Math.ceil(totalCount / traceState.pageSize);
      const startNum = traceState.page * traceState.pageSize + 1;
      const endNum = Math.min(startNum + sorted.length - 1, totalCount);

      list.innerHTML = sorted.map(t => {
        const totalTk = (t.input_tokens || 0) + (t.output_tokens || 0);
        return `
        <div class="monitoring-trace-row" onclick="showTraceDetail('${t.trace_id}')">
          <span style="width:8px;height:8px;border-radius:50%;background:${t.status === 'error' ? '#f87171' : '#4ade80'};flex-shrink:0;"></span>
          <span style="font-size:12px;color:#ddd;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t.user_message || t.trace_id.slice(0, 8)}</span>
          <span style="font-size:10px;color:#888;white-space:nowrap;">${formatTime(t.started_at)}</span>
          <span style="font-size:11px;color:#a29bfe;white-space:nowrap;">${formatTokens(totalTk)} t</span>
          <span style="font-size:11px;color:#888;white-space:nowrap;">${formatDuration(t.duration_ms || 0)}</span>
          <span class="monitoring-badge ${t.status === 'error' ? 'error' : 'success'}">${t.status === 'error' ? '失败' : '完成'}</span>
        </div>`;
      }).join('');

      // Render pagination
      if (pagination) {
        pagination.innerHTML = `
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:11px;color:#888;">每页</span>
            <select class="monitoring-select" id="trace-page-size" style="padding:2px 8px;font-size:11px;">
              <option value="25" ${traceState.pageSize === 25 ? 'selected' : ''}>25</option>
              <option value="50" ${traceState.pageSize === 50 ? 'selected' : ''}>50</option>
              <option value="100" ${traceState.pageSize === 100 ? 'selected' : ''}>100</option>
            </select>
            <span style="font-size:11px;color:#888;">条</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="font-size:11px;color:#888;">${startNum}-${endNum} / ${totalCount} 条</span>
            <button class="monitoring-page-btn" id="trace-prev-page" ${traceState.page <= 0 ? 'disabled' : ''}>上一页</button>
            <span style="font-size:11px;color:#ccc;">第 ${traceState.page + 1}/${totalPages} 页</span>
            <button class="monitoring-page-btn" id="trace-next-page" ${traceState.page >= totalPages - 1 ? 'disabled' : ''}>下一页</button>
          </div>
        `;

        document.getElementById('trace-page-size').addEventListener('change', (e) => {
          traceState.pageSize = parseInt(e.target.value);
          traceState.page = 0;
          doSearch();
        });
        document.getElementById('trace-prev-page').addEventListener('click', () => {
          if (traceState.page > 0) { traceState.page--; doSearch(); }
        });
        document.getElementById('trace-next-page').addEventListener('click', () => {
          if (traceState.page < totalPages - 1) { traceState.page++; doSearch(); }
        });
      }
    } catch (e) {
      list.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
    }
  }

  searchInput.addEventListener('input', debounce(() => { traceState.page = 0; doSearch(); }, 300));
  statusFilter.addEventListener('change', () => { traceState.page = 0; doSearch(); });
  sortField.addEventListener('change', (e) => {
    traceState.sortField = e.target.value;
    traceState.sortDir = 'desc';
    sortDirBtn.textContent = '▼ 降序';
    sortDirBtn.title = '降序';
    doSearch();
  });
  sortDirBtn.addEventListener('click', () => {
    traceState.sortDir = traceState.sortDir === 'desc' ? 'asc' : 'desc';
    sortDirBtn.textContent = traceState.sortDir === 'desc' ? '▼ 降序' : '▲ 升序';
    sortDirBtn.title = traceState.sortDir === 'desc' ? '降序' : '升序';
    doSearch();
  });
  doSearch();
}

let tokenRankState = { reversed: false };

async function loadTokenStats() {
  const container = document.getElementById('monitoring-tokens');
  if (!container) return;
  try {
    const [stats, topTraces] = await Promise.all([
      window.api.getTraceStats(),
      window.api.getTokenTopTraces(7, 20)
    ]);
    const totalInput = stats.all_time_input_tokens || stats.total_input_tokens || 0;
    const totalOutput = stats.all_time_output_tokens || stats.total_output_tokens || 0;

    function renderRanking(traces) {
      if (!traces || traces.length === 0) {
        return '<div style="text-align:center;padding:20px;color:#888;">暂无 Token 消耗数据，发送消息后即可统计分析</div>';
      }
      const display = tokenRankState.reversed ? [...traces].reverse() : traces;
      return display.map((t, i) => {
        const tTokens = (t.input_tokens || 0) + (t.output_tokens || 0);
        const origIdx = tokenRankState.reversed ? display.length - 1 - i : i;
        const rankStyle = origIdx === 0 ? 'color:#fbbf24;font-weight:700;' : origIdx === 1 ? 'color:#d1d5db;font-weight:600;' : origIdx === 2 ? 'color:#d97706;font-weight:600;' : 'color:#888;';
        return `<div class="monitoring-trace-row" onclick="showTraceDetail('${t.trace_id}')" style="margin-bottom:6px;">
          <span style="font-size:12px;width:28px;text-align:center;flex-shrink:0;${rankStyle}">#${origIdx + 1}</span>
          <span style="font-size:12px;color:#ddd;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t.user_message || t.trace_id.slice(0, 8)}</span>
          <span style="font-size:10px;color:#888;">${formatTime(t.started_at)}</span>
          <span style="font-size:11px;color:#a29bfe;font-weight:600;">${formatTokens(tTokens)}</span>
          <span style="font-size:11px;color:#888;">${formatDuration(t.duration_ms || 0)}</span>
        </div>`;
      }).join('');
    }

    container.innerHTML = `
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">累计输入 Token</div>
          <div class="monitoring-stat-value" style="color:#a29bfe;">${formatTokens(totalInput)}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">累计输出 Token</div>
          <div class="monitoring-stat-value" style="color:#74b9ff;">${formatTokens(totalOutput)}</div>
        </div>
      </div>
      <div class="monitoring-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;font-weight:600;color:#ddd;">Token 消耗排行 (近 7 天 Top 20)</span>
          <button class="monitoring-page-btn" id="token-ranking-reverse" title="反转排序">${tokenRankState.reversed ? '▲ 正序' : '▼ 倒序'}</button>
        </div>
        <div id="token-ranking-list">
          ${renderRanking(topTraces)}
        </div>
      </div>
    `;

    const reverseBtn = document.getElementById('token-ranking-reverse');
    if (reverseBtn) {
      reverseBtn.addEventListener('click', () => {
        tokenRankState.reversed = !tokenRankState.reversed;
        reverseBtn.textContent = tokenRankState.reversed ? '▲ 正序' : '▼ 倒序';
        reverseBtn.title = tokenRankState.reversed ? '正序' : '倒序';
        const list = document.getElementById('token-ranking-list');
        if (list) list.innerHTML = renderRanking(topTraces);
      });
    }
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
  }
}

let latencyState = { reversed: false };

async function loadLatencyStats() {
  const container = document.getElementById('monitoring-latency');
  if (!container) return;
  try {
    const traces = await window.api.getTraces(null, null, 50, 0);
    let toolLatencies = {};
    let toolCounts = {};
    for (const t of traces) {
      try {
        const spans = await window.api.getTraceSpans(t.trace_id);
        spans.filter(s => s.span_type === 'tool_call' && s.tool_name).forEach(s => {
          if (!toolLatencies[s.tool_name]) { toolLatencies[s.tool_name] = 0; toolCounts[s.tool_name] = 0; }
          toolLatencies[s.tool_name] += s.duration_ms || 0;
          toolCounts[s.tool_name] += 1;
        });
      } catch(e) {}
    }

    const toolNames = Object.keys(toolLatencies);
    if (toolNames.length === 0) {
      container.innerHTML = '<div class="monitoring-card"><div style="text-align:center;padding:20px;color:#888;">暂无工具调用数据</div></div>';
      return;
    }

    function renderLatencyList() {
      const sorted = [...toolNames]
        .map(name => ({ name, avg: toolLatencies[name] / toolCounts[name], count: toolCounts[name] }))
        .sort((a, b) => latencyState.reversed ? a.avg - b.avg : b.avg - a.avg);
      const maxAvg = Math.max(...sorted.map(x => x.avg), 1);

      return sorted.map(({ name, avg, count }) => {
        const pct = (avg / maxAvg * 100);
        return `<div style="margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
            <span style="color:#aaa;">${name}</span>
            <span style="color:#888;">${formatDuration(avg)} (${count}次)</span>
          </div>
          <div class="monitoring-progress-bar">
            <div class="monitoring-progress-fill" style="width:${pct}%;background:linear-gradient(90deg,#fbbf24,#f59e0b);"></div>
          </div>
        </div>`;
      }).join('');
    }

    container.innerHTML = `
      <div class="monitoring-card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;font-weight:600;color:#ddd;">工具调用延迟分布</span>
          <button class="monitoring-page-btn" id="latency-reverse-btn" title="反转排序">${latencyState.reversed ? '▲ 正序' : '▼ 倒序'}</button>
        </div>
        <div id="latency-list">
          ${renderLatencyList()}
        </div>
      </div>
    `;

    const reverseBtn = document.getElementById('latency-reverse-btn');
    if (reverseBtn) {
      reverseBtn.addEventListener('click', () => {
        latencyState.reversed = !latencyState.reversed;
        reverseBtn.textContent = latencyState.reversed ? '▲ 正序' : '▼ 倒序';
        reverseBtn.title = latencyState.reversed ? '正序' : '倒序';
        const list = document.getElementById('latency-list');
        if (list) list.innerHTML = renderLatencyList();
      });
    }
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
  }
}

async function loadCacheStats() {
  const container = document.getElementById('monitoring-cache');
  if (!container) return;
  try {
    const stats = await window.api.getTraceCacheStats();
    // cache_hit_rate is already a percentage (0-100) from the backend
    const hitRate = stats.cache_hit_rate != null ? stats.cache_hit_rate : 0;
    const hitTokens = stats.total_cache_hit_tokens || 0;
    const missTokens = stats.total_cache_miss_tokens || 0;
    const totalCached = hitTokens + missTokens;
    const hitRateColor = hitRate > 50 ? '#4ade80' : hitRate > 20 ? '#fbbf24' : '#f87171';

    container.innerHTML = `
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">缓存命中率</div>
          <div class="monitoring-stat-value" style="color:${hitRateColor};">${hitRate}%</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">命中 Token</div>
          <div class="monitoring-stat-value" style="color:#4ade80;">${formatTokens(hitTokens)}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">未命中 Token</div>
          <div class="monitoring-stat-value" style="color:#f87171;">${formatTokens(missTokens)}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">总计 Token</div>
          <div class="monitoring-stat-value">${formatTokens(totalCached)}</div>
        </div>
      </div>
      <div class="monitoring-card">
        <div style="font-size:13px;font-weight:600;color:#ddd;margin-bottom:12px;">缓存命中分布</div>
        <div style="display:flex;height:24px;border-radius:6px;overflow:hidden;background:#2a2a42;">
          ${totalCached > 0 ? `
            <div style="width:${hitRate}%;background:linear-gradient(90deg,#22c55e,#4ade80);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff;min-width:${hitRate > 10 ? '60px' : '30px'};">命中 ${hitRate}%</div>
            <div style="flex:1;background:linear-gradient(90deg,#ef4444,#f87171);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff;">未命中 ${(100 - hitRate).toFixed(1)}%</div>
          ` : '<div style="width:100%;display:flex;align-items:center;justify-content:center;font-size:11px;color:#888;">暂无缓存数据 (需要 LLM 返回 usage_metadata)</div>'}
        </div>
      </div>
      <div class="monitoring-card">
        <div style="font-size:12px;color:#8888aa;line-height:1.6;">
          缓存数据来自 LLM 返回的 <code style="background:#2a2a42;padding:2px 6px;border-radius:3px;">usage_metadata</code>，
          包含 <code>prompt_cache_hit_tokens</code> 和 <code>prompt_cache_miss_tokens</code>。
          仅 DeepSeek 等支持 Prompt Caching 的模型会返回此数据。
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载缓存统计失败</div>';
  }
}

// ── Trace Detail ──

async function showTraceDetail(traceId) {
  const body = document.getElementById('monitoring-body');
  if (!body) return;
  body.innerHTML = '<div style="text-align:center;padding:40px;color:#888;">加载中...</div>';
  try {
    const detail = await window.api.getTraceDetail(traceId);
    const spans = detail.spans || [];
    const root = detail.tree || {};
    const totalInput = spans.reduce((s, sp) => s + (sp.input_tokens || 0), 0);
    const totalOutput = spans.reduce((s, sp) => s + (sp.output_tokens || 0), 0);
    const errorCount = spans.filter(s => s.status === 'error').length;
    const toolCount = spans.filter(s => s.span_type === 'tool_call').length;
    const llmCount = spans.filter(s => s.span_type === 'llm_call').length;
    const delegateCount = spans.filter(s => s.span_type === 'delegate_task').length;

    // Helper: format JSON content nicely
    function formatIOContent(raw, kind) {
      if (!raw) return '<span style="color:#666;">(无数据)</span>';
      try {
        const obj = JSON.parse(raw);
        return JSON.stringify(obj, null, 2);
      } catch (_) {
        return raw;
      }
    }

    // Helper: copy button
    function copyBtn(targetId) {
      return `<button class="trace-copy-btn" onclick="(function(){
        const el = document.getElementById('${targetId}');
        if (!el) return;
        navigator.clipboard.writeText(el.textContent);
        const btn = event.target;
        btn.textContent = '已复制';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 1500);
      })()">复制</button>`;
    }

    // I/O panel HTML
    function renderIOPanel(span, ioType) {
      const raw = ioType === 'input' ? (span.llm_input || span.tool_input || '')
                  : (span.llm_output || span.tool_output || '');
      const label = ioType === 'input' ? '输入' : '输出';
      const contentId = `io-content-${span.span_id}-${ioType}`;
      return `
        <div class="trace-io-panel" id="io-panel-${span.span_id}-${ioType}" style="display:none;">
          <div class="trace-io-panel-header">
            <span>${span.span_type === 'llm_call' ? 'LLM' : span.span_type === 'delegate_task' ? '子Agent' : 'Tool'} ${label}</span>
            ${copyBtn(contentId)}
          </div>
          <div class="trace-io-content" id="${contentId}">${formatIOContent(raw)}</div>
        </div>
      `;
    }

    // Waterfall timeline (enhanced: nesting, time axis, grid, tooltips)
    function renderWaterfall(spans) {
      if (!spans || spans.length === 0) return '';

      // 1. Flatten tree with depth for nesting display
      const flatSpans = [];
      function collectFlat(node, depth) {
        if (!node) return;
        flatSpans.push({ ...node, _depth: depth });
        (node.children || []).forEach(c => collectFlat(c, depth + 1));
      }
      collectFlat(root, 0);
      if (flatSpans.length === 0) return '';

      // 2. Compute time range
      const timestamps = flatSpans.map(s => {
        const ts = s.started_at ? new Date(s.started_at).getTime() : 0;
        return { ts, dur: s.duration_ms || 0 };
      }).filter(t => t.ts > 0);
      if (timestamps.length === 0) return '';
      const earliest = Math.min(...timestamps.map(t => t.ts));
      const latest = Math.max(...timestamps.map(t => t.ts + t.dur));
      const totalRange = latest - earliest || 1;

      // 3. Auto-calculate nice tick interval
      const totalMs = totalRange;
      let tickInterval;
      if (totalMs <= 500) tickInterval = 100;
      else if (totalMs <= 2000) tickInterval = 500;
      else if (totalMs <= 5000) tickInterval = 1000;
      else if (totalMs <= 30000) tickInterval = 5000;
      else tickInterval = 10000;

      const ticks = [];
      for (let t = 0; t <= totalMs + tickInterval; t += tickInterval) {
        ticks.push(Math.min(t, totalMs));
      }
      // Deduplicate last tick
      if (ticks.length >= 2 && ticks[ticks.length - 1] === ticks[ticks.length - 2]) {
        ticks.pop();
      }

      // 4. Label width based on max depth (base 120 + 16px per indent level)
      const maxDepth = Math.max(...flatSpans.map(s => s._depth || 0), 0);
      const labelWidth = Math.min(120 + maxDepth * 16, 280);

      // 5. Tick formatter
      function formatTick(ms) {
        if (ms >= 1000) return (ms / 1000).toFixed(1).replace(/\.0$/, '') + 's';
        return ms + 'ms';
      }

      // 6. Tick HTML for ruler
      const tickHtml = ticks.map(t => {
        const pct = (t / totalRange) * 100;
        return `<span class="trace-waterfall-tick" style="left:${pct}%;">${formatTick(t)}</span>`;
      }).join('');

      // 7. Grid lines (identical for each track row, pre-computed)
      const gridHtml = ticks.map(t => {
        const pct = (t / totalRange) * 100;
        return `<div class="trace-waterfall-grid" style="left:${pct}%;"></div>`;
      }).join('');

      // 8. Render rows
      const rows = flatSpans.map(s => {
        const ts = s.started_at ? new Date(s.started_at).getTime() : 0;
        if (!ts) return '';
        const offset = (ts - earliest) / totalRange * 100;
        const width = Math.max((s.duration_ms || 0) / totalRange * 100, 0.4);
        const depth = s._depth || 0;

        const typeClass = s.span_type === 'llm_call' ? 'llm'
          : s.span_type === 'tool_call' ? 'tool'
          : s.span_type === 'delegate_task' ? 'delegate'
          : s.span_type === 'session_turn' ? 'session' : 'step';

        const icon = s.span_type === 'session_turn' ? '&#9654;'
          : s.span_type === 'llm_call' ? '&#9679;'
          : s.span_type === 'agent_step' ? '&#9632;'
          : s.span_type === 'delegate_task' ? '&#9881;'
          : s.span_type === 'tool_call' ? '&#9670;' : '&#9654;';

        const name = s.span_type === 'llm_call' ? (s.model || 'LLM')
          : s.span_type === 'tool_call' ? (s.tool_name || 'tool')
          : s.span_type === 'delegate_task' ? '子Agent'
          : s.span_type === 'session_turn' ? '会话'
          : s.span_type === 'agent_step' ? 'Agent' : s.span_type;

        const startTime = s.started_at ? new Date(s.started_at).toLocaleTimeString() : '';
        const dur = formatDuration(s.duration_ms || 0);

        return `
        <div class="trace-waterfall-row">
          <div class="trace-waterfall-label" style="padding-left:${8 + depth * 14}px;">
            <span class="wf-icon">${icon}</span>
            <span class="wf-name" title="${name}">${name}</span>
          </div>
          <div class="trace-waterfall-track">
            ${gridHtml}
            <div class="trace-waterfall-bar ${typeClass}"
                 style="left:${offset}%;width:${width}%;">
              <div class="trace-waterfall-tooltip">
                <div style="font-weight:600;margin-bottom:3px;color:#fff;">${name}</div>
                <div>开始: ${startTime}</div>
                <div>耗时: ${dur}</div>
                ${s.input_tokens || s.output_tokens ? `<div>Token: ${s.input_tokens || 0} 入 + ${s.output_tokens || 0} 出</div>` : ''}
                ${s.status === 'error' ? '<div style="color:#f87171;">状态: 错误</div>' : ''}
              </div>
            </div>
          </div>
          <span class="trace-waterfall-dur">${dur}</span>
        </div>`;
      }).filter(Boolean).join('');

      // 9. Inline legend
      const legend = `
        <div class="trace-waterfall-legend">
          <span><span class="trace-waterfall-legend-dot" style="background:#8b5cf6;"></span>会话</span>
          <span><span class="trace-waterfall-legend-dot" style="background:#3b82f6;"></span>LLM</span>
          <span><span class="trace-waterfall-legend-dot" style="background:#10b981;"></span>Agent</span>
          <span><span class="trace-waterfall-legend-dot" style="background:#f59e0b;"></span>工具</span>
          <span><span class="trace-waterfall-legend-dot" style="background:#ec4899;"></span>子Agent</span>
          <span style="margin-left:auto;color:#555;">缩进 = 嵌套深度</span>
        </div>`;

      return `
        <div class="trace-waterfall" style="--wf-label-w:${labelWidth}px;">
          <div class="trace-waterfall-header">
            <span class="trace-waterfall-title">时间线 (Waterfall)</span>
            <span class="trace-waterfall-hint">悬停色块查看详情</span>
          </div>
          <div class="trace-waterfall-ruler" style="--wf-label-w:${labelWidth}px;">${tickHtml}</div>
          <div class="trace-waterfall-rows">${rows}</div>
          ${legend}
        </div>
      `;
    }

    // Enhanced span tree with I/O
    function renderEnhancedTree(node, depth) {
      if (!node) return '';
      const typeLabel = node.span_type === 'session_turn' ? '会话'
        : node.span_type === 'llm_call' ? 'LLM'
        : node.span_type === 'agent_step' ? 'Agent'
        : node.span_type === 'delegate_task' ? '子Agent'
        : node.span_type === 'tool_call' ? '工具' : node.span_type;
      const typeIcon = node.span_type === 'session_turn' ? '&gt;'
        : node.span_type === 'llm_call' ? '*'
        : node.span_type === 'agent_step' ? '-'
        : node.span_type === 'delegate_task' ? '&diams;'
        : node.span_type === 'tool_call' ? '#' : '&gt;';
      const nameInfo = node.span_type === 'llm_call' ? (node.model || '')
        : node.span_type === 'tool_call' ? node.tool_name || ''
        : node.span_type === 'delegate_task' ? (node.tool_name || 'sub-agent')
        : node.span_type === 'session_turn' ? '' : (node.model || '');
      const tokenInfo = (node.input_tokens || node.output_tokens)
        ? `${node.input_tokens || 0}+${node.output_tokens || 0} t` : '';
      const durInfo = node.duration_ms ? formatDuration(node.duration_ms) : '';
      const hasIO = (node.llm_input || node.llm_output || node.tool_input || node.tool_output);
      const hasError = node.status === 'error' && node.error_message;
      const spanId = node.span_id || 's' + Math.random().toString(36).slice(2, 8);
      const errorClass = hasError ? 'has-error' : '';
      const children = node.children || [];

      let html = `
        <div class="trace-span-row ${node.span_type === 'session_turn' ? 'session' : node.span_type === 'llm_call' ? 'llm' : node.span_type === 'tool_call' ? 'tool' : node.span_type === 'delegate_task' ? 'delegate' : 'step'} ${errorClass}" style="margin-left:${depth * 20}px;">
          <span class="trace-span-icon">${typeIcon}</span>
          <span class="trace-span-label">${typeLabel}${nameInfo ? ': ' + nameInfo : ''}</span>
          ${tokenInfo ? `<span style="font-size:10px;color:#888;white-space:nowrap;">${tokenInfo}</span>` : ''}
          ${durInfo ? `<span style="font-size:10px;color:#666;white-space:nowrap;">${durInfo}</span>` : ''}
          <span class="monitoring-badge ${node.status === 'error' ? 'error' : 'success'}">${node.status === 'error' ? '失败' : '成功'}</span>
          ${hasIO ? `
            <span class="trace-span-io-summary">
              ${(node.llm_input || node.tool_input) ? `<span class="trace-span-io-badge" onclick="event.stopPropagation();toggleIOPanel('${spanId}','input')">In</span>` : ''}
              ${(node.llm_output || node.tool_output) ? `<span class="trace-span-io-badge" onclick="event.stopPropagation();toggleIOPanel('${spanId}','output')">Out</span>` : ''}
            </span>
          ` : ''}
        </div>
        ${hasIO ? `
          <div class="trace-io-panels">
            ${renderIOPanel({span_id: spanId, llm_input: node.llm_input, tool_input: node.tool_input, llm_output: node.llm_output, tool_output: node.tool_output, span_type: node.span_type}, 'input')}
            ${renderIOPanel({span_id: spanId, llm_input: node.llm_input, tool_input: node.tool_input, llm_output: node.llm_output, tool_output: node.tool_output, span_type: node.span_type}, 'output')}
          </div>
        ` : ''}
        ${hasError ? `
          <div class="trace-error-block">
            <div class="trace-error-header">Error</div>
            <div class="trace-error-stack">${node.error_message}</div>
          </div>
        ` : ''}
      `;
      children.forEach(c => { html += renderEnhancedTree(c, depth + 1); });
      return html;
    }

    // Build the full HTML
    body.innerHTML = `
      <div style="margin-bottom:12px;">
        <span class="monitoring-link" onclick="setupMonitoringPanel()">&larr; 返回列表</span>
      </div>

      ${root.user_message ? `
      <div class="trace-user-msg">
        ${root.user_message}
      </div>
      ` : ''}

      <div class="trace-summary-bar">
        <span class="trace-summary-item ${errorCount > 0 ? 'error' : 'ok'}">Status: ${errorCount > 0 ? 'ERR' : 'OK'} (${detail.status || 'ok'})</span>
        <span class="trace-summary-item" title="总耗时">Time: ${formatDuration(detail.total_duration_ms || 0)}</span>
        <span class="trace-summary-item" title="总 Token">Tokens: ${detail.total_tokens || 0}</span>
        <span class="trace-summary-item" title="Span 数量">Spans: ${spans.length}</span>
        ${errorCount > 0 ? `<span class="trace-summary-item error">Errors: ${errorCount}</span>` : ''}
      </div>

      ${renderWaterfall(spans)}

      <div class="monitoring-card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
          <span style="font-size:13px;font-weight:600;color:#ddd;">调用树</span>
          <span style="font-size:9px;color:#555;" title="每层缩进 20px，表示 Span 的父子嵌套关系：最左为根节点，向右缩进表示更深层的子调用">缩进 = 父子嵌套层级</span>
        </div>
        <div class="trace-span-tree">
          ${renderEnhancedTree(root, 0)}
        </div>
      </div>

      <div class="trace-meta-card">
        <span class="trace-meta-label">Trace ID</span>
        <span class="trace-meta-value">${traceId}</span>
        <span class="trace-meta-label">开始时间</span>
        <span class="trace-meta-value">${formatTime(root.started_at, true)}</span>
        <span class="trace-meta-label">Token 详情</span>
        <span class="trace-meta-value">输入 ${totalInput} + 输出 ${totalOutput} = ${detail.total_tokens || 0}</span>
        <span class="trace-meta-label">Span 统计</span>
        <span class="trace-meta-value">${spans.length} 个（LLM: ${llmCount} / Tool: ${toolCount}${delegateCount > 0 ? ` / 子Agent: ${delegateCount}` : ''} / Error: ${errorCount}）</span>
      </div>
    `;
  } catch (e) {
    console.error('Failed to load trace detail:', e);
    body.innerHTML = `<div style="text-align:center;padding:40px;"><span class="monitoring-link" onclick="setupMonitoringPanel()">&larr; 返回列表</span><div style="color:#f87171;margin-top:12px;">加载失败: ${e.message}</div></div>`;
  }
}

// ── Helper: toggle I/O panel in trace detail ──
function toggleIOPanel(spanId, ioType) {
  const panel = document.getElementById(`io-panel-${spanId}-${ioType}`);
  const badge = document.querySelector(`.trace-span-io-badge[onclick*="${spanId}"][onclick*="${ioType}"]`);
  if (!panel) return;
  const isVisible = panel.style.display !== 'none';
  panel.style.display = isVisible ? 'none' : 'block';
  if (badge) badge.classList.toggle('active', !isVisible);
}

// ── Helper: switch monitoring tab ──
function switchMonitoringTab(tab) {
  const btn = document.querySelector(`.monitoring-tab[data-tab="${tab}"]`);
  if (btn) btn.click();
}

// ── Helper: debounce ──
function debounce(fn, delay) {
  let timer;
  return function(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

// ── Helper: format duration (ms if < 1000, s otherwise) ──
function formatDuration(ms) {
  if (ms == null || ms === 0) return '0ms';
  if (ms < 1000) return Math.round(ms) + 'ms';
  return (ms / 1000).toFixed(1) + 's';
}

// ── Helper: format token count ──
function formatTokens(n) {
  if (n == null || n === 0) return '0';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

// ── Helper: format ISO timestamp to local time ──
function formatTime(iso, full) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const h = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    if (full) return `${y}-${mo}-${day} ${h}:${mi}:${s}`;
    return `${mo}-${day} ${h}:${mi}`;
  } catch (_) { return iso; }
}



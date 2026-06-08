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

  // ── 鼠标悬浮 → 展开环状菜单 ────────────────────────────
  let expandTimer = null;

  function positionRingMenu() {
    const buttons = menu.querySelectorAll('.menu-btn');
    const count = buttons.length;
    if (count === 0) return;
    // 以菜单容器中心为圆心，偏上留出按钮空间
    const cx = menu.offsetWidth / 2;
    const cy = menu.offsetHeight * 0.38;
    const radius = Math.min(cx, cy + 20) * 0.85;
    const startAngle = -Math.PI * 0.6;
    const endAngle = Math.PI * 0.6;
    buttons.forEach((btn, i) => {
      const ratio = count > 1 ? i / (count - 1) : 0.5;
      const angle = startAngle + (endAngle - startAngle) * ratio;
      const x = cx + radius * Math.sin(angle) - btn.offsetWidth / 2;
      const y = cy - radius * Math.cos(angle) - btn.offsetHeight / 2;
      btn.style.left = Math.round(x) + 'px';
      btn.style.top = Math.round(y) + 'px';
      btn.style.opacity = '1';
      btn.style.transform = 'scale(1)';
      btn.style.transitionDelay = (i * 40) + 'ms';
    });

    // 计算窗口裁剪区域：只保留吉祥物 + 按钮区域，消除方形裁切
    const shapeRects = [];
    // 吉祥物区域
    const mRect = container.getBoundingClientRect();
    shapeRects.push({
      x: Math.round(mRect.left), y: Math.round(mRect.top),
      width: Math.round(mRect.width), height: Math.round(mRect.height),
    });
    // 各按钮
    buttons.forEach(btn => {
      const r = btn.getBoundingClientRect();
      shapeRects.push({
        x: Math.round(r.left), y: Math.round(r.top),
        width: Math.round(r.width), height: Math.round(r.height),
      });
    });
    // 关闭按钮
    const closeBtn = document.getElementById('ball-close-btn');
    if (closeBtn) {
      const r = closeBtn.getBoundingClientRect();
      shapeRects.push({
        x: Math.round(r.left), y: Math.round(r.top),
        width: Math.round(r.width), height: Math.round(r.height),
      });
    }
    if (window.api && window.api.setWindowShape) {
      window.api.setWindowShape(shapeRects);
    }
  }

  wrapper.addEventListener('mouseenter', () => {
    clearTimeout(expandTimer);
    menu.classList.remove('hidden');
    // 先放大窗口确保环形菜单有足够空间
    if (window.api && window.api.resizeBall) {
      window.api.resizeBall(320, 340);
    }
    // 尝试立即定位（若窗口已够大则生效）
    requestAnimationFrame(() => requestAnimationFrame(positionRingMenu));
  });

  // 窗口真正完成 resize 后重新定位（IPC resize 异步到达）
  window.addEventListener('resize', () => {
    if (!menu.classList.contains('hidden')) {
      requestAnimationFrame(() => requestAnimationFrame(positionRingMenu));
    }
  });

  wrapper.addEventListener('mouseleave', () => {
    expandTimer = setTimeout(() => {
      // 重置按钮位置
      menu.querySelectorAll('.menu-btn').forEach(btn => {
        btn.style.opacity = '0';
        btn.style.transform = 'scale(0.8)';
        btn.style.transitionDelay = '0ms';
        btn.style.left = '';
        btn.style.top = '';
      });
      // 恢复矩形窗口
      if (window.api && window.api.setWindowShape) {
        window.api.setWindowShape([]);
      }
      menu.classList.add('hidden');
      if (window.api && window.api.resizeBall) {
        window.api.resizeBall(148, 155);
      }
    }, 300);
  });

  // ── 菜单按钮 → 打开面板 ─────────────────────────────────
  menuBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.classList.add('hidden');
      if (window.api && window.api.resizeBall) {
        window.api.resizeBall(148, 155);
      }
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

  // ── 双击标题栏切换全屏 ──────────────────
  panelContainer.addEventListener('dblclick', (e) => {
    const header = e.target.closest('.panel-header');
    if (header && window.api && window.api.toggleMaximize) {
      window.api.toggleMaximize();
    }
  });

  // ── 关闭：按钮 / Escape ─────────────────
  const close = () => {
    // 不管 compressCurrentSession 是否完成，最多等 2 秒就关闭
    let closed = false;
    const doClose = () => {
      if (closed) return;
      closed = true;
      if (window.api && window.api.closePanel) {
        window.api.closePanel();
      } else {
        // 浏览器模式或无 API 时直接关闭
        window.close();
      }
    };
    compressCurrentSession().then(doClose).catch(doClose);
    setTimeout(doClose, 2000); // 2 秒超时强制关闭
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
    const item = e.target.closest('.session-item');
    if (!item) return;
    const threadId = item.dataset.threadId;
    if (threadId) openChat(threadId);
  });

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
    if (!sessions || sessions.length === 0) {
      list.innerHTML = '<div class="empty-state">暂无会话</div>';
      return;
    }
    list.innerHTML = sessions
      .map(
        (s) =>
          `<div class="session-item" data-thread-id="${escapeHtml(s.thread_id)}">
            <div class="session-id">${escapeHtml(s.thread_id.slice(0, 8))}...</div>
            <div class="session-meta">${escapeHtml(s.created_at)} · ${s.turn_count} 轮</div>
            <button class="session-delete-btn" title="删除此会话">&times;</button>
          </div>`
      )
      .join('');
  } catch (e) {
    console.error('Failed to load sessions', e);
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

  // 清空旧消息
  const container = document.getElementById('chat-messages');
  container.innerHTML = '';

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

async function loadMessages(threadId) {
  try {
    const messages = await window.api.getMessages(threadId);
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (!messages || messages.length === 0) {
      container.innerHTML = '<div class="chat-empty">开始对话吧</div>';
      document.getElementById('chat-input').focus();
      return;
    }

    for (const msg of messages) {
      // 历史只展示 human/ai 对话，不展示工具调用、system 等中间消息
      if (msg.role !== 'human' && msg.role !== 'ai') continue;
      addMessageBubble(msg.role, msg.content);
    }
    scrollChatToBottom();
    document.getElementById('chat-input').focus();
  } catch (e) {
    console.error('Failed to load messages', e);
  }
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
      const el = document.createElement('div');
      el.className = 'chat-msg system';
      el.textContent = '🔧 ' + data.name + '(' + data.input + ')';
      container.insertBefore(el, aiBubble);
      scrollChatToBottom();
      return true;
    }

    case 'tool_end': {
      const el = document.createElement('div');
      el.className = 'chat-msg system';
      el.textContent = '✅ ' + data.name + ' → ' + data.output;
      container.insertBefore(el, aiBubble);
      scrollChatToBottom();
      return true;
    }

    case 'error':
      aiBubble.textContent = '出错了：' + data.error;
      aiBubble.className = 'chat-msg error';
      scrollChatToBottom();
      return true;

    case 'done':
      // 正常结束，无需额外操作
      return true;

    default:
      return false;
  }
}

// ── 消息气泡 ────────────────────────────────────────────────────────────

function addMessageBubble(role, content) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'chat-msg ' + escapeHtml(role);
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

async function setupToolsPanel() {
  try {
    const tools = await window.api.getTools();
    const grid = document.getElementById('tools-grid');
    if (!tools || tools.length === 0) {
      grid.innerHTML = '<div class="empty-state">暂无工具</div>';
      return;
    }
    grid.innerHTML = tools
      .map(
        (t) =>
          `<div class="tool-card">
            <h4>${escapeHtml(t.name)}</h4>
            <p>${escapeHtml(t.description || '无描述')}</p>
          </div>`
      )
      .join('');
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
    </div>
    <div id="monitoring-overview" class="monitoring-view active"></div>
    <div id="monitoring-traces" class="monitoring-view"></div>
    <div id="monitoring-tokens" class="monitoring-view"></div>
    <div id="monitoring-latency" class="monitoring-view"></div>
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
      // Mock bars
      for (let i = 0; i < 12; i++) {
        const h = 20 + Math.random() * 50;
        barsHtml += `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;">
          <div style="background:#2a2a42;border-radius:3px;height:60px;width:100%;display:flex;align-items:flex-end;">
            <div style="background:linear-gradient(180deg,#6c5ce7,#a29bfe);border-radius:3px 3px 0 0;width:100%;height:${h}%;"></div>
          </div>
        </div>`;
      }
    }

    container.innerHTML = `
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">今日 Token</div>
          <div class="monitoring-stat-value">${(stats.total_tokens || 0) >= 1000 ? (stats.total_tokens / 1000).toFixed(1) + 'K' : stats.total_tokens || 0}</div>
          <div style="font-size:11px;color:${yesterdayClass};margin-top:2px;">${yesterdayText}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">平均延迟</div>
          <div class="monitoring-stat-value">${(stats.avg_duration_ms / 1000 || 0).toFixed(1)}s</div>
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
            <span style="font-size:11px;color:#aaa;">${((t.input_tokens || 0) + (t.output_tokens || 0)) >= 1000 ? (((t.input_tokens || 0) + (t.output_tokens || 0)) / 1000).toFixed(1) + 'K' : (t.input_tokens || 0) + (t.output_tokens || 0)}</span>
            <span style="font-size:11px;color:#888;">${(t.duration_ms / 1000 || 0).toFixed(1)}s</span>
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

async function loadTraces() {
  const container = document.getElementById('monitoring-traces');
  if (!container) return;

  // Search bar
  container.innerHTML = `
    <div class="monitoring-card">
      <div style="display:flex;gap:8px;margin-bottom:12px;">
        <input class="monitoring-search-input" id="trace-search-input" placeholder="搜索会话内容或 Trace ID..." />
        <select class="monitoring-select" id="trace-status-filter">
          <option value="">全部</option>
          <option value="ok">成功</option>
          <option value="error">失败</option>
        </select>
      </div>
      <div id="trace-list"></div>
    </div>
  `;

  const searchInput = document.getElementById('trace-search-input');
  const statusFilter = document.getElementById('trace-status-filter');

  async function doSearch() {
    const list = document.getElementById('trace-list');
    if (!list) return;
    list.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">加载中...</div>';
    try {
      const traces = await window.api.getTraces(searchInput.value, statusFilter.value, 50, 0);
      if (traces.length === 0) {
        list.innerHTML = '<div style="text-align:center;padding:20px;color:#888;">暂无 Trace 记录</div>';
        return;
      }
      list.innerHTML = traces.map(t => `
        <div class="monitoring-trace-row" onclick="showTraceDetail('${t.trace_id}')">
          <span style="width:8px;height:8px;border-radius:50%;background:${t.status === 'error' ? '#f87171' : '#4ade80'};flex-shrink:0;"></span>
          <span style="font-size:12px;color:#ddd;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${t.user_message || t.trace_id.slice(0, 8)}</span>
          <span style="font-size:10px;color:#888;">${t.started_at ? t.started_at.slice(11, 19) : ''}</span>
          <span style="font-size:11px;color:#aaa;">${((t.input_tokens || 0) + (t.output_tokens || 0)) >= 1000 ? (((t.input_tokens || 0) + (t.output_tokens || 0)) / 1000).toFixed(1) + 'K' : (t.input_tokens || 0) + (t.output_tokens || 0)}</span>
          <span style="font-size:11px;color:#888;">${(t.duration_ms / 1000 || 0).toFixed(1)}s</span>
          <span class="monitoring-badge ${t.status === 'error' ? 'error' : 'success'}">${t.status === 'error' ? '失败' : '完成'}</span>
        </div>
      `).join('');
    } catch (e) {
      list.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
    }
  }

  searchInput.addEventListener('input', debounce(doSearch, 300));
  statusFilter.addEventListener('change', doSearch);
  doSearch();
}

async function loadTokenStats() {
  const container = document.getElementById('monitoring-tokens');
  if (!container) return;
  try {
    const stats = await window.api.getTraceStats();
    const traces = await window.api.getTraces(null, null, 100, 0);
    const totalInput = stats.total_input_tokens || 0;
    const totalOutput = stats.total_output_tokens || 0;
    const totalTokens = totalInput + totalOutput;

    container.innerHTML = `
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">累计输入 Token</div>
          <div class="monitoring-stat-value" style="color:#a29bfe;">${totalInput >= 1000 ? (totalInput / 1000).toFixed(1) + 'K' : totalInput}</div>
        </div>
        <div class="monitoring-stat-box">
          <div class="monitoring-stat-label">累计输出 Token</div>
          <div class="monitoring-stat-value" style="color:#74b9ff;">${totalOutput >= 1000 ? (totalOutput / 1000).toFixed(1) + 'K' : totalOutput}</div>
        </div>
      </div>
      <div class="monitoring-card">
        <div style="font-size:13px;font-weight:600;color:#ddd;margin-bottom:12px;">Token 分布（按会话）</div>
        <div id="token-distribution">
          ${traces.length === 0 ? '<div style="text-align:center;padding:20px;color:#888;">暂无数据</div>' :
            traces.slice(0, 15).map((t, i) => {
              const tTokens = (t.input_tokens || 0) + (t.output_tokens || 0);
              const pct = totalTokens > 0 ? (tTokens / totalTokens * 100) : 0;
              return `<div style="margin-bottom:10px;">
                <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
                  <span style="color:#aaa;">${t.user_message || 'Trace ' + (i + 1)}</span>
                  <span style="color:#888;">${tTokens}</span>
                </div>
                <div class="monitoring-progress-bar">
                  <div class="monitoring-progress-fill" style="width:${pct}%;"></div>
                </div>
              </div>`;
            }).join('')
          }
        </div>
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
  }
}

async function loadLatencyStats() {
  const container = document.getElementById('monitoring-latency');
  if (!container) return;
  try {
    // Get spans to compute per-tool latency
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

    const maxAvg = Math.max(...toolNames.map(n => toolLatencies[n] / toolCounts[n]), 1);

    container.innerHTML = `
      <div class="monitoring-card">
        <div style="font-size:13px;font-weight:600;color:#ddd;margin-bottom:12px;">工具调用延迟分布</div>
        ${toolNames.map(name => {
          const avg = toolLatencies[name] / toolCounts[name];
          const pct = (avg / maxAvg * 100);
          return `<div style="margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px;">
              <span style="color:#aaa;">${name}</span>
              <span style="color:#888;">${(avg / 1000).toFixed(2)}s (${toolCounts[name]}次)</span>
            </div>
            <div class="monitoring-progress-bar">
              <div class="monitoring-progress-fill" style="width:${pct}%;background:linear-gradient(90deg,#fbbf24,#f59e0b);"></div>
            </div>
          </div>`;
        }).join('')}
      </div>
    `;
  } catch (e) {
    container.innerHTML = '<div style="text-align:center;padding:20px;color:#f87171;">加载失败</div>';
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

    // Build tree HTML
    function renderSpanTree(node, depth) {
      if (!node) return '';
      const typeClass = node.span_type === 'session_turn' ? 'session'
        : node.span_type === 'llm_call' ? 'llm'
        : node.span_type === 'agent_step' ? 'step' : 'tool';
      const typeIcon = node.span_type === 'session_turn' ? '>'
        : node.span_type === 'llm_call' ? '*'
        : node.span_type === 'agent_step' ? '-'
        : node.span_type === 'tool_call' ? '=' : '>';
      const typeLabel = node.span_type === 'session_turn' ? '会话'
        : node.span_type === 'llm_call' ? 'LLM'
        : node.span_type === 'agent_step' ? 'Agent'
        : node.span_type === 'tool_call' ? '工具' : node.span_type;
      const nameInfo = node.span_type === 'llm_call' ? (node.model || '')
        : node.span_type === 'tool_call' ? `${node.tool_name}`
        : node.span_type === 'session_turn' ? '用户会话' : (node.model || '');
      const tokenInfo = (node.input_tokens || node.output_tokens)
        ? `${node.input_tokens || 0}+${node.output_tokens || 0}t` : '';
      const durInfo = node.duration_ms ? `${(node.duration_ms / 1000).toFixed(1)}s` : '';
      const children = node.children || [];

      let html = `
        <div class="monitoring-span-item ${typeClass}" style="margin-left:${depth * 24}px;">
          <span style="color:${node.span_type === 'session_turn' ? '#a29bfe' : node.span_type === 'llm_call' ? '#74b9ff' : node.span_type === 'agent_step' ? '#2dd4bf' : '#fbbf24'};font-size:13px;">${typeIcon}</span>
          <span style="font-size:12px;color:#ccc;flex:1;">${typeLabel}: ${nameInfo}</span>
          ${tokenInfo ? `<span class="mono" style="font-size:10px;color:#888;">${tokenInfo}</span>` : ''}
          ${durInfo ? `<span class="mono" style="font-size:10px;color:#666;">${durInfo}</span>` : ''}
          <span class="monitoring-badge ${node.status === 'error' ? 'error' : 'success'}">${node.status === 'error' ? '失败' : '成功'}</span>
        </div>`;
      children.forEach(c => { html += renderSpanTree(c, depth + 1); });
      return html;
    }

    body.innerHTML = `
      <div style="margin-bottom:12px;">
        <span class="monitoring-link" onclick="setupMonitoringPanel()">&larr; 返回列表</span>
      </div>
      <h2 style="font-size:16px;font-weight:700;color:#fff;margin-bottom:4px;">Trace 详情</h2>
      <p style="font-size:12px;color:#8888aa;margin-bottom:12px;">
        <span class="mono" style="background:#2a2a42;padding:2px 8px;border-radius:4px;">${traceId.slice(0, 8)}</span>
        <span style="color:#555;margin:0 8px;">|</span>${root.started_at ? root.started_at.slice(11, 19) : ''}
        <span style="color:#555;margin:0 8px;">|</span><span style="color:#a29bfe;">${detail.total_tokens || 0} Token</span>
        <span style="color:#555;margin:0 8px;">|</span>${(detail.total_duration_ms / 1000 || 0).toFixed(1)}秒
      </p>
      <div class="monitoring-stat-grid">
        <div class="monitoring-stat-box"><div class="monitoring-stat-label">输入 Token</div><div class="monitoring-stat-value" style="color:#a29bfe;">${totalInput}</div></div>
        <div class="monitoring-stat-box"><div class="monitoring-stat-label">输出 Token</div><div class="monitoring-stat-value" style="color:#74b9ff;">${totalOutput}</div></div>
        <div class="monitoring-stat-box"><div class="monitoring-stat-label">总耗时</div><div class="monitoring-stat-value">${(detail.total_duration_ms / 1000 || 0).toFixed(1)}秒</div></div>
        <div class="monitoring-stat-box"><div class="monitoring-stat-label">工具数</div><div class="monitoring-stat-value">${spans.filter(s => s.span_type === 'tool_call').length}</div></div>
      </div>
      <div class="monitoring-legend">
        <span><span class="monitoring-legend-dot" style="background:#4a2a6a;"></span> 会话</span>
        <span><span class="monitoring-legend-dot" style="background:#1a3a6a;"></span> LLM</span>
        <span><span class="monitoring-legend-dot" style="background:#1a4a4a;"></span> Agent</span>
        <span><span class="monitoring-legend-dot" style="background:#4a4a1a;"></span> 工具</span>
      </div>
      <div class="monitoring-card">
        ${renderSpanTree(root, 0)}
      </div>
      <div class="monitoring-card">
        <div style="display:grid;grid-template-columns:70px 1fr;gap:6px 16px;font-size:12px;">
          <span style="color:#8888aa;">Trace ID</span>
          <span class="mono" style="color:#ccc;">${traceId}</span>
          <span style="color:#8888aa;">总 Token</span>
          <span style="color:#ccc;">${detail.total_tokens || 0}（输入 ${totalInput} + 输出 ${totalOutput}）</span>
        </div>
      </div>
    `;
  } catch (e) {
    console.error('Failed to load trace detail:', e);
    body.innerHTML = `<div style="text-align:center;padding:40px;"><span class="monitoring-link" onclick="setupMonitoringPanel()">&larr; 返回列表</span><div style="color:#f87171;margin-top:12px;">加载失败: ${e.message}</div></div>`;
  }
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



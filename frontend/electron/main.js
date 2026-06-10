/**
 * Agent Shell — Electron Main Process
 *
 * 双窗口架构（单例模式）
 * - Ball: 280x220 常驻透明浮窗（不再动态 resize）
 * - Panel: 420x520 独立面板窗口，启动时预创建，show/hide 切换
 */

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execSync } = require('child_process');

const PORT = 18794;
const BALL_W = 280;
const BALL_H = 220;
const PANEL_W = 420;
const PANEL_H = 520;

let ballWindow = null;
let panelWindow = null;
let panelPreMaxBounds = null;
let pythonServer = null;

// 球位置持久化
const posFile = path.join(__dirname, '..', '..', 'data', '.ball-pos.json');

function loadBallPos() {
  try { return JSON.parse(fs.readFileSync(posFile, 'utf-8')); }
  catch { return { x: 200, y: 200 }; }
}
function saveBallPos(x, y) {
  try { fs.writeFileSync(posFile, JSON.stringify({ x, y })); } catch {}
}

// ── Python ──────────────────────────────────────────────────────────

function findPidOnPort(port) {
  try {
    const output = require('child_process').execSync(
      `netstat -ano | findstr ":${port}" | findstr LISTENING`,
      { encoding: 'utf-8', windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] }
    );
    const lines = output.trim().split('\n').filter(Boolean);
    if (lines.length > 0) {
      const parts = lines[0].trim().split(/\s+/);
      return parts[parts.length - 1];
    }
  } catch (_) { /* no process found or command failed */ }
  return null;
}

function killPid(pid) {
  try {
    require('child_process').execSync(
      `taskkill /F /PID ${pid}`,
      { windowsHide: true, stdio: 'ignore' }
    );
    return true;
  } catch (_) { return false; }
}

function startPythonServer() {
  return new Promise((resolve, reject) => {
    // 先清理占用端口的旧进程
    const oldPid = findPidOnPort(PORT);
    if (oldPid) {
      console.log(`[agent-shell] Port ${PORT} in use by PID ${oldPid}, attempting to kill...`);
      killPid(oldPid);
    }

    const script = path.join(__dirname, '..', 'http_server.py');
    const rootDir = path.join(__dirname, '..', '..');
    const pythonExe = process.platform === 'win32'
      ? path.join(rootDir, '.venv', 'Scripts', 'python.exe')
      : path.join(rootDir, '.venv', 'bin', 'python3');
    pythonServer = spawn(pythonExe, [script, '--port', String(PORT)], {
      cwd: rootDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, AGENT_SHELL_PORT: String(PORT) },
      windowsHide: true,
    });
    let resolved = false;
    pythonServer.stdout.on('data', (chunk) => {
      if (!resolved && chunk.toString().includes('Starting HTTP server')) {
        resolved = true; resolve();
      }
    });
    pythonServer.stderr.on('data', (chunk) => {
      const text = chunk.toString().trim();
      if (text) console.error('[agent-shell:python]', text);
    });
    pythonServer.on('error', (err) => { if (!resolved) reject(err); });
    pythonServer.on('exit', (code) => {
      if (code !== 0 && code !== null) console.warn('[agent-shell] Python exited with code', code);
    });
    setTimeout(() => { if (!resolved) { resolved = true; resolve(); } }, 8000);
  });
}

function stopPythonServer() {
  if (!pythonServer) return;
  try {
    if (process.platform === 'win32')
      execSync(`taskkill /pid ${pythonServer.pid} /f /t`, { windowsHide: true, stdio: 'ignore' });
    else pythonServer.kill('SIGTERM');
  } catch {}
  pythonServer = null;
}

// ── Ball Window（固定 280x220，不再动态 resize）────────────────────

function createBallWindow() {
  const pos = loadBallPos();
  ballWindow = new BrowserWindow({
    width: BALL_W, height: BALL_H,
    x: pos.x, y: pos.y,
    frame: false, transparent: true, alwaysOnTop: true,
    resizable: false, skipTaskbar: true, show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
      sandbox: false, backgroundThrottling: false,
    },
  });
  ballWindow.loadFile(path.join(__dirname, '..', 'index.html'));

  ballWindow.once('ready-to-show', () => {
    ballWindow.show();
  });
  ballWindow.on('closed', () => { ballWindow = null; });
}

// ── Panel Window（启动时预创建，show/hide 切换）────────────────

function precreatePanelWindow() {
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workArea;
  const x = Math.round((screenW - PANEL_W) / 2);
  const y = Math.round((screenH - PANEL_H) / 2);
  panelWindow = new BrowserWindow({
    width: PANEL_W, height: PANEL_H,
    x, y,
    frame: false, thickFrame: false, show: false,
    backgroundColor: '#1e1e32',
    paintWhenInitiallyHidden: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
    },
  });
  panelWindow.setBackgroundColor('#1e1e32');
  panelWindow.loadFile(path.join(__dirname, '..', 'index.html'), {
    query: { panel: 'chat' },
  });
  // 预建窗口就绪后不立即显示，等用户点击菜单
  panelWindow.once('ready-to-show', () => {
    // 保持隐藏，预建完成
  });
  panelWindow.on('close', (e) => {
    // 用户点击关闭 → 隐藏而不是销毁
    e.preventDefault();
    panelWindow.hide();
  });
  panelWindow.on('closed', () => { panelWindow = null; panelPreMaxBounds = null; });
}

// ── 统一错误日志 ──────────────────────────────────────────────────

function logError(context, err) {
  const ts = new Date().toISOString();
  console.error(`[agent-shell][${ts}][${context}]`, err instanceof Error ? err.message : String(err));
  if (err instanceof Error && err.stack) {
    console.error(`[agent-shell][${ts}][${context}] stack:`, err.stack);
  }
}

// 全局未捕获异常
process.on('uncaughtException', (err) => {
  logError('uncaughtException', err);
});
process.on('unhandledRejection', (reason) => {
  logError('unhandledRejection', reason instanceof Error ? reason : new Error(String(reason)));
});

// ── IPC handler 包装器 ───────────────────────────────────────────

function safeHandler(name, fn) {
  ipcMain.on(name, (...args) => {
    try {
      const ret = fn(...args);
      if (ret && typeof ret.catch === 'function') {
        ret.catch(err => logError('ipc:' + name, err));
      }
    } catch (err) {
      logError('ipc:' + name, err);
    }
  });
}

// ── IPC ───────────────────────────────────────────────────────────

// 窗口拖动
safeHandler('ball-drag-move', (_event, dx, dy) => {
  if (!ballWindow) return;
  const [x, y] = ballWindow.getPosition();
  const nx = Math.round(x + (typeof dx === 'number' ? dx : 0));
  const ny = Math.round(y + (typeof dy === 'number' ? dy : 0));
  ballWindow.setPosition(nx, ny);
  saveBallPos(nx, ny);
});

safeHandler('panel-drag-move', (_event, dx, dy) => {
  if (!panelWindow || panelPreMaxBounds) return;
  const [x, y] = panelWindow.getPosition();
  const nx = Math.round(x + (typeof dx === 'number' ? dx : 0));
  const ny = Math.round(y + (typeof dy === 'number' ? dy : 0));
  panelWindow.setPosition(nx, ny);
});

// 面板 show/hide（预建窗口，不再每次创建）
safeHandler('open-panel', (_event, name) => {
  if (!panelWindow) return;
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workArea;
  const x = Math.round((screenW - PANEL_W) / 2);
  const y = Math.round((screenH - PANEL_H) / 2);
  panelWindow.setPosition(x, y);
  panelWindow.webContents.send('switch-panel', name);
  panelWindow.show();
  panelWindow.focus();
});
safeHandler('close-panel', () => { if (panelWindow) panelWindow.hide(); });

// 双击标题栏切换最大化/还原
// 策略：纯色遮罩覆盖内容 → resize → 遮罩淡出。
// 用实色 div 代替 opacity/filter，避免 GPU 合成层在 resize 时产生黑闪。
safeHandler('toggle-maximize', async () => {
  if (!panelWindow) return;

  if (panelPreMaxBounds) {
    // ── 还原 ──
    await panelWindow.webContents.executeJavaScript(`
      (() => {
        const c = document.createElement('div');
        c.id = '_resize-curtain';
        c.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#1e1e32';
        document.body.appendChild(c);
      })();
      new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    `);
    panelWindow.setBounds(panelPreMaxBounds);
    panelPreMaxBounds = null;
    await new Promise(r => setTimeout(r, 120));
    await panelWindow.webContents.executeJavaScript(`
      (() => {
        const c = document.getElementById('_resize-curtain');
        if (!c) return;
        c.style.transition = 'opacity 0.3s ease-out';
        c.style.opacity = '0';
        setTimeout(() => c.remove(), 320);
      })();
    `);
  } else {
    // ── 最大化 ──
    const display = screen.getPrimaryDisplay();
    if (!display) return;
    const b = panelWindow.getBounds();
    panelPreMaxBounds = { x: b.x, y: b.y, width: b.width, height: b.height };
    const { x, y, width, height } = display.workArea;

    await panelWindow.webContents.executeJavaScript(`
      (() => {
        const c = document.createElement('div');
        c.id = '_resize-curtain';
        c.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#1e1e32';
        document.body.appendChild(c);
      })();
      new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    `);
    panelWindow.setBounds({ x, y, width, height });
    await new Promise(r => setTimeout(r, 150));
    await panelWindow.webContents.executeJavaScript(`
      (() => {
        const c = document.getElementById('_resize-curtain');
        if (!c) return;
        c.style.transition = 'opacity 0.3s ease-out';
        c.style.opacity = '0';
        setTimeout(() => c.remove(), 320);
      })();
    `);
  }
});

safeHandler('quit-app', () => {
  if (panelWindow) {
    panelWindow.removeAllListeners('close');
    panelWindow.close();
  }
  setTimeout(() => app.quit(), 500);
});

// ── 应用生命周期（单例）───────────────────────────────────────────────

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (ballWindow) {
      if (ballWindow.isMinimized()) ballWindow.restore();
      ballWindow.focus();
    }
  });
}

app.whenReady().then(async () => {
  try { await startPythonServer(); console.log('[agent-shell] Python ready'); }
  catch (err) { console.error('[agent-shell] Python failed:', err); }
  createBallWindow();
  precreatePanelWindow();  // 后台预建面板窗口
});

app.on('window-all-closed', () => {});
app.on('before-quit', stopPythonServer);
app.on('activate', () => { if (!ballWindow) createBallWindow(); });

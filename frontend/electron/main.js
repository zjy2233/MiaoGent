/**
 * Agent Shell — Electron Main Process
 *
 * 双窗口架构 + 动态尺寸 Ball Window（单例模式）
 * - Ball: 160x160 常态 / 260x210 悬停展开菜单
 * - Panel: 420x520 独立窗口，点击菜单项创建
 */

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, execSync } = require('child_process');

const PORT = 18794;
const BALL_W = 148;
const BALL_H = 155;
const BALL_W_EXPANDED = 280;
const BALL_H_EXPANDED = 220;
const PANEL_W = 420;
const PANEL_H = 520;

let ballWindow = null;
let panelWindow = null;
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

// ── Ball Window ────────────────────────────────────────────────────

function createBallWindow() {
  const pos = loadBallPos();
  ballWindow = new BrowserWindow({
    width: BALL_W, height: BALL_H,
    x: pos.x, y: pos.y,
    frame: false, transparent: true, alwaysOnTop: true,
    resizable: false, skipTaskbar: true, show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
      sandbox: false, backgroundThrottling: false,
    },
  });
  ballWindow.loadFile(path.join(__dirname, '..', 'index.html'));
  ballWindow.once('ready-to-show', () => ballWindow.show());
  ballWindow.on('closed', () => { ballWindow = null; });
}

// ── Panel Window ──────────────────────────────────────────────────

function createPanelWindow(panelName) {
  if (panelWindow) {
    // 复用已有面板窗口，加载新面板
    panelWindow.loadFile(path.join(__dirname, '..', 'index.html'), {
      query: { panel: panelName },
    });
    panelWindow.focus();
    return;
  }
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;
  const x = Math.round((screenW - PANEL_W) / 2);
  const y = Math.round((screenH - PANEL_H) / 2);
  panelWindow = new BrowserWindow({
    width: PANEL_W, height: PANEL_H,
    x, y,
    frame: false, show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: false,
    },
  });
  panelWindow.loadFile(path.join(__dirname, '..', 'index.html'), {
    query: { panel: panelName },
  });
  panelWindow.once('ready-to-show', () => panelWindow.show());
  panelWindow.on('closed', () => { panelWindow = null; });
}

// ── IPC ───────────────────────────────────────────────────────────

// 窗口拖动
ipcMain.on('ball-drag-move', (_event, dx, dy) => {
  if (!ballWindow) return;
  try {
    const [x, y] = ballWindow.getPosition();
    const nx = Math.round(x + (typeof dx === 'number' ? dx : 0));
    const ny = Math.round(y + (typeof dy === 'number' ? dy : 0));
    ballWindow.setPosition(nx, ny);
    saveBallPos(nx, ny);
  } catch (err) { console.error('[agent-shell] drag error:', err); }
});

// 窗口尺寸（悬停展开/收缩）
ipcMain.on('resize-ball', (_event, w, h) => {
  if (!ballWindow) return;
  try {
    ballWindow.setSize(Math.round(w), Math.round(h));
  } catch (err) { console.error('[agent-shell] resize error:', err); }
});

// 面板控制
ipcMain.on('open-panel', (_event, name) => {
  if (panelWindow) {
    // 面板已存在 → IPC 通知切换，避免 loadFile 白屏闪烁
    panelWindow.webContents.send('switch-panel', name);
    panelWindow.focus();
  } else {
    createPanelWindow(name);
  }
});
ipcMain.on('close-panel', () => { if (panelWindow) panelWindow.close(); });

// 双击标题栏切换最大化/还原
ipcMain.on('toggle-maximize', () => {
  if (!panelWindow) return;
  if (panelWindow.isMaximized()) {
    panelWindow.unmaximize();
  } else {
    panelWindow.maximize();
  }
});

ipcMain.on('quit-app', () => {
  // 先关闭面板窗口（触发 pagehide → compress），再退出
  if (panelWindow) panelWindow.close();
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
});

app.on('window-all-closed', () => {});
app.on('before-quit', stopPythonServer);
app.on('activate', () => { if (!ballWindow) createBallWindow(); });

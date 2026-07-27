console.log("[BUILD ID]: main-v4-2026-07-25-20:16");
console.log("[BUILD ID]: watchdog-fix-v3-2026-07-25-17:28");
const { app, BrowserWindow, BaseWindow, WebContentsView, ipcMain, screen, shell, powerMonitor, session, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, exec, execSync } = require('child_process');
const http = require('http');
const net = require('net');
if (net.setDefaultAutoSelectFamily) { net.setDefaultAutoSelectFamily(false); }
const os = require('os');

// ── [v4] File-based debug logger (GUI app has no console) ──
const _dbgLogPath = path.join(process.env.APPDATA || 'C:\\', 'daon-agent-system', 'main_debug.log');
function dbgLog(msg) {
  const ts = new Date().toISOString();
  const line = `[${ts}] ${msg}\n`;
  try { fs.appendFileSync(_dbgLogPath, line); } catch (e) { try { fs.appendFileSync('C:\\daon_debug.log', line + ' ERR:' + e.message + '\n'); } catch (_) { } }
  console.log(msg);
}
dbgLog("MAIN START 1 - before single instance lock");

// ── Temp Folder Cleanup (PyInstaller _MEI* orphan prevention) ──
// PyInstaller onefile mode extracts ~1.76GB to %TEMP%\_MEIxxxxx on every launch.
// If the app crashes or is force-killed, these folders are never cleaned up.
// This function removes orphaned _MEI* and playwright-artifacts-* folders.
// Runs asynchronously so it NEVER blocks app startup.
function cleanupOrphanedTemp() {
  setTimeout(() => {
    const tempDir = process.env.TEMP || os.tmpdir();
    let entries;
    try {
      entries = fs.readdirSync(tempDir);
    } catch (e) {
      console.warn('[Cleanup] Cannot read temp dir:', e.message);
      return;
    }

    // Find which _MEI folders are currently in use by running server.exe processes
    let activeMEIs = new Set();
    try {
      const wmicOut = execSync(
        'wmic process where "name=\'server.exe\'" get ExecutablePath /FORMAT:LIST 2>nul',
        { windowsHide: true, encoding: 'utf-8', timeout: 5000 }
      );
      for (const line of wmicOut.split('\n')) {
        const match = line.match(/(_MEI\d+)/i);
        if (match) activeMEIs.add(match[1]);
      }
    } catch (_) { }

    let freedCount = 0;

    for (const entry of entries) {
      const isMEI = entry.startsWith('_MEI');
      const isPlaywright = entry.startsWith('playwright-artifacts-');
      if (!isMEI && !isPlaywright) continue;

      if (isMEI && activeMEIs.has(entry)) continue;

      const fullPath = path.join(tempDir, entry);
      try {
        const stat = fs.statSync(fullPath);
        if (!stat.isDirectory()) continue;
        fs.rmSync(fullPath, { recursive: true, force: true });
        freedCount++;
      } catch (_) { }
    }

    if (freedCount > 0) {
      console.log(`[Cleanup] Removed ${freedCount} orphaned temp folder(s)`);
    }
  }, 5000); // Run 5 seconds after app starts — never block startup
}

// ── Single Instance Lock ──
// Each instance needs exclusive access to CDP port 9222 and spawns its own
// Python server.  A second launch must bail immediately to avoid port wars.
dbgLog("MAIN START 2 - requesting single instance lock");
const gotTheLock = app.requestSingleInstanceLock();
dbgLog(`MAIN START 3 - gotTheLock=${gotTheLock}`);
if (!gotTheLock) {
  // Another instance is already running — quit silently.
  dbgLog("MAIN EXIT - another instance holds the lock, quitting");
  app.quit();
  // Prevent app.whenReady() from ever firing.
  // On Windows, app.quit() may not exit immediately; we force it.
  process.exit(0);
}
dbgLog("MAIN START 4 - lock acquired, continuing");

// When a second instance tries to launch, focus the existing window
// instead of silently doing nothing.
app.on('second-instance', (_event, _commandLine, _workingDirectory) => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

let mainWindow;
let tabManager;
let pythonProcess = null;
let ttsProcess = null;
let serverPort = 9090;  // Updated to match DEFAULT_PORT; was 8000 which caused confusion
let ttsPort = 9091;
let watchdogTimer = null;
let isQuitting = false;
let tray = null;
let trayStatusTimer = null;

// ── Always-on: 트레이 아이콘 경로 해석 (dev / packaged 둘 다 지원) ──
function findTrayIcon() {
  const candidates = [
    path.join(process.resourcesPath, 'static', 'favicon.png'),
    path.join(process.resourcesPath, 'favicon.png'),
    path.join(__dirname, '..', 'static', 'favicon.png'),
    path.join(__dirname, '..', 'dist_new', 'static', 'favicon.png'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (_) { }
  }
  return null;
}

// ── Always-on ⑦ 관측성: 트레이 상태 표시 ──
// /api/system/status를 폴링해 트레이 툴팁/메뉴에 서버·워커·기억·큐 상태를 표시한다.
function buildTrayMenu(status) {
  const items = [];
  if (status && status.ok) {
    const q = status.queue || {};
    const s = status.store || {};
    const failed = q.failed || 0;
    items.push({ label: '● 서버: 정상', enabled: false });
    items.push({ label: status.worker_running ? '● 워커: 동작 중' : '○ 워커: 정지', enabled: false });
    items.push({ label: `● 기억: facts ${s.facts || 0} · 프로필 ${s.profile || 0} · 요약 ${s.summaries || 0}`, enabled: false });
    items.push({ label: `● 큐: 대기 ${q.pending || 0} · 처리중 ${q.processing || 0} · 완료 ${q.done || 0} · 실패 ${failed}`, enabled: false });
  } else {
    items.push({ label: '○ 서버: 응답 없음', enabled: false });
  }
  items.push({ type: 'separator' });
  items.push({
    label: 'DAON 열기',
    click: () => {
      if (mainWindow) {
        if (!mainWindow.isVisible()) mainWindow.show();
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.focus();
      }
    }
  });
  items.push({ type: 'separator' });
  items.push({
    label: '종료',
    click: () => {
      isQuitting = true;
      app.quit();
    }
  });
  return Menu.buildFromTemplate(items);
}

function refreshTrayStatus() {
  if (!tray) return;
  const req = http.get({ host: '127.0.0.1', port: serverPort, path: '/api/system/status', family: 4 }, (res) => {
    let body = '';
    res.on('data', (c) => { body += c; });
    res.on('end', () => {
      try {
        const status = JSON.parse(body);
        tray.setContextMenu(buildTrayMenu(status));
        if (status && status.ok) {
          const q = status.queue || {};
          tray.setToolTip(`DAON — 서버정상 | 큐 대기:${q.pending || 0} 실패:${q.failed || 0}`);
        } else {
          tray.setToolTip('DAON — 서버 오류');
        }
      } catch (_) {
        tray.setContextMenu(buildTrayMenu(null));
        tray.setToolTip('DAON — 상태 파싱 실패');
      }
    });
  });
  req.on('error', () => {
    try {
      tray.setContextMenu(buildTrayMenu(null));
      tray.setToolTip('DAON — 서버 응답 없음');
    } catch (_) { }
  });
  req.setTimeout(2000, () => req.destroy());
}

function startTrayStatusPolling() {
  if (trayStatusTimer) return;
  refreshTrayStatus();
  trayStatusTimer = setInterval(refreshTrayStatus, 10000);
}

function createTray() {
  if (tray) return;
  try {
    const iconPath = findTrayIcon();
    // 아이콘이 없으면 16x16 빈 이미지로라도 트레이 생성 (기능 유지)
    const image = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
    tray = new Tray(image.isEmpty() ? image : image.resize({ width: 16, height: 16 }));
    tray.setToolTip('DAON Agent System');
    tray.setContextMenu(buildTrayMenu(null));
    tray.on('double-click', () => {
      if (mainWindow) {
        if (!mainWindow.isVisible()) mainWindow.show();
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.focus();
      }
    });
    console.log('[Tray] Always-on tray icon created.');
    startTrayStatusPolling();
  } catch (e) {
    console.warn('[Tray] Failed to create tray icon (non-fatal):', e.message);
  }
}

// --- CDP (Chrome DevTools Protocol) port for browser automation ---
// app.commandLine.appendSwitch is NOT reliable in packaged Electron builds.
// The only guaranteed way to open a Chromium debugging port is to have
const NEEDED_CDP_PORT = '9222';
app.commandLine.appendSwitch('remote-debugging-port', NEEDED_CDP_PORT);
app.commandLine.appendSwitch('remote-allow-origins', '*');

// --- Helper: Find Free Port ---
function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(startPort, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        resolve(findFreePort(startPort + 1));
      } else {
        reject(err);
      }
    });
  });
}

// --- Helper: Check Server Health ---
function checkServerHealth(port, retries = 180, delayMs = 1000) {
  return new Promise((resolve, reject) => {
    let attempted = 0;
    const poll = () => {
      attempted++;
      const req = http.get({ host: '127.0.0.1', port: port, path: '/health', family: 4 }, (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else if (attempted >= retries) {
          reject(new Error(`Server returned status code ${res.statusCode}`));
        } else {
          setTimeout(poll, delayMs);
        }
      });
      req.on('error', (err) => {
        if (attempted >= retries) {
          reject(err);
        } else {
          setTimeout(poll, delayMs);
        }
      });
      req.setTimeout(1000, () => {
        req.destroy();
      });
    };
    poll();
  });
}

function checkCdpHealth(retries = 10, delayMs = 500) {
  let attempted = 0;
  const poll = () => {
    attempted++;
    const req = http.get(`http://127.0.0.1:9222/json/version`, (res) => {
      if (res.statusCode === 200) {
        console.log('[Electron] CDP port 9222 is active and ready for Playwright!');
      } else if (attempted < retries) {
        setTimeout(poll, delayMs);
      }
    });
    req.on('error', () => {
      if (attempted < retries) setTimeout(poll, delayMs);
      else console.warn('[Electron] CDP port 9222 non-responsive (browser automation tools may fail).');
    });
    req.setTimeout(1000, () => req.destroy());
  };
  poll();
}

function killProcessTree(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      // /T (Tree), /F (Force) 동기 실행하여 자식 프로세스까지 완전히 kill 완료 후 진행
      execSync(`taskkill /pid ${pid} /T /F 2>nul`, { windowsHide: true });
      console.log(`[Cleanup] Successfully killed process tree for PID: ${pid}`);
    } else {
      process.kill(-pid, 'SIGKILL');
    }
  } catch (e) {
    // 이미 종료되었거나 존재하지 않는 프로세스인 경우 무시
  }
}

// ── Helper: Unified Server EXE Path Resolution ──
function findServerExe() {
  const candidates = [
    path.join(process.resourcesPath, 'server.exe'),
    path.join(process.resourcesPath, 'server', 'server.exe'),
    path.join(path.dirname(process.execPath), 'server.exe'),
    path.join(__dirname, '..', 'dist', 'server.exe'),
    path.join(__dirname, '..', 'server.exe'),
    path.join(__dirname, 'server.exe'),
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }

  return null;
}

// ── Process Spawning & Auto-Restart Safety Net ──

function startPythonProcess(port) {
  if (isQuitting) return;
  const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
  const exePath = findServerExe();

  if (exePath) {
    console.log(`[Electron] Spawning server executable: ${exePath}`);
    const cwd = path.dirname(exePath);
    pythonProcess = spawn(exePath, ['--no-browser', '--port', port.toString()], {
      cwd,
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  } else {
    console.log(`[Electron] server.exe not found. Falling back to python server.py...`);
    pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', port.toString()], {
      cwd: path.join(__dirname, '..'),
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  }

  const serverLogPath = path.join(app.getPath('userData'), 'server.log');
  const serverLogStream = fs.createWriteStream(serverLogPath, { flags: 'a' });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python]: ${data}`);
    try { serverLogStream.write(`[STDOUT] ${data}`); } catch (_) { }
  });
  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python Error]: ${data}`);
    try { serverLogStream.write(`[STDERR] ${data}`); } catch (_) { }
  });

  // Safety Net: If Python crashes or exits unexpectedly, auto-restart immediately
  pythonProcess.on('exit', (code, signal) => {
    console.warn(`[Electron] Main Python server exited (code=${code}, signal=${signal})`);
    pythonProcess = null;
    if (!isQuitting) {
      console.log('[Electron] Server exit detected — Auto-restarting Python server in 2s...');
      setTimeout(() => {
        if (!isQuitting && !pythonProcess) {
          startPythonProcess(port);
        }
      }, 2000);
    }
  });

  // Boost main server CPU priority so faster-whisper inference isn't starved
  if (process.platform === 'win32' && pythonProcess.pid) {
    try {
      exec(`powershell -NoProfile -Command "(Get-Process -Id ${pythonProcess.pid}).PriorityClass = 'AboveNormal'"`, { windowsHide: true });
      console.log(`[Electron] Main server (pid=${pythonProcess.pid}) priority → AboveNormal`);
    } catch (_) { }
  }
}

function startTtsProcess(port) {
  if (isQuitting) return;
  const isPackaged = app.isPackaged;

  if (isPackaged) {
    const ttsExePath = findServerExe();
    const cwd = path.dirname(ttsExePath);
    ttsProcess = spawn(ttsExePath, ['--tts-mode', '--tts-port', port.toString()], {
      cwd,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  } else {
    ttsProcess = spawn('python', ['server.py', '--tts-mode', '--tts-port', port.toString()], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  }

  ttsProcess.stdout.on('data', (data) => console.log(`[TTS]: ${data}`));
  ttsProcess.stderr.on('data', (data) => console.error(`[TTS Error]: ${data}`));

  // Safety Net: Auto-restart TTS server if it crashes
  ttsProcess.on('exit', (code, signal) => {
    console.warn(`[Electron] TTS server exited (code=${code}, signal=${signal})`);
    ttsProcess = null;
    if (!isQuitting) {
      console.log('[Electron] TTS exit detected — Auto-restarting TTS server in 2s...');
      setTimeout(() => {
        if (!isQuitting && !ttsProcess) {
          startTtsProcess(port);
        }
      }, 2000);
    }
  });

  if (process.platform === 'win32' && ttsProcess.pid) {
    try {
      exec(`powershell -NoProfile -Command "(Get-Process -Id ${ttsProcess.pid}).PriorityClass = 'BelowNormal'"`, { windowsHide: true });
      console.log(`[Electron] TTS server (pid=${ttsProcess.pid}) priority → BelowNormal`);
    } catch (_) { }
  }
}

// --- Watchdog: Periodic health check + auto-restart if server dies ---
let watchdogRestartCount = 0;
const WATCHDOG_INTERVAL = 30_000;
const MAX_RESTARTS = 3;

function handleWatchdogFailure(port) {
  watchdogRestartCount++;
  console.warn(`[Watchdog] Health check failure (${watchdogRestartCount}/${MAX_RESTARTS})`);
  if (watchdogRestartCount >= MAX_RESTARTS) {
    console.error('[Watchdog] Server unresponsive after 3 consecutive checks. Restarting Python server...');
    watchdogRestartCount = 0;
    if (pythonProcess && pythonProcess.pid) {
      killProcessTree(pythonProcess.pid);
      pythonProcess = null;
    }
    startPythonProcess(port);
  }
}

function startWatchdog(port) {
  if (watchdogTimer) return;
  console.log('[Watchdog] Starting health monitor (every 30s)...');
  watchdogRestartCount = 0;

  if (powerMonitor) {
    powerMonitor.on('resume', () => {
      console.log('[Electron] System resumed from sleep/idle - resetting watchdog...');
      watchdogRestartCount = 0;
      checkServerHealth(port, 10).catch(() => {
        if (pythonProcess && pythonProcess.pid) {
          killProcessTree(pythonProcess.pid);
          pythonProcess = null;
        }
        startPythonProcess(port);
      });
    });
  }

  watchdogTimer = setInterval(() => {
    if (isQuitting) return;

    const req = http.get({ host: '127.0.0.1', port: port, path: '/health', family: 4 }, (res) => {
      if (res.statusCode === 200) {
        watchdogRestartCount = 0;
      } else {
        console.warn(`[Watchdog] Health check non-200: ${res.statusCode}`);
        handleWatchdogFailure(port);
      }
    });
    req.on('error', (err) => {
      console.warn(`[Watchdog] Health check request failed: ${err.message}`);
      handleWatchdogFailure(port);
    });
    req.setTimeout(5000, () => {
      req.destroy();
      console.warn('[Watchdog] Health check timed out');
      handleWatchdogFailure(port);
    });
  }, WATCHDOG_INTERVAL);
}

function stopWatchdog() {
  if (watchdogTimer) {
    clearInterval(watchdogTimer);
    watchdogTimer = null;
    console.log('[Watchdog] Stopped.');
  }
}

app.whenReady().then(async () => {
  // ── Always-on: 로그인 시 자동 시작 등록 (Windows) ──
  try {
    app.setLoginItemSettings({ openAtLogin: true, openAsHidden: false });
    console.log('[AlwaysOn] Login item registered (openAtLogin=true).');
  } catch (e) {
    console.warn('[AlwaysOn] setLoginItemSettings failed (non-fatal):', e.message);
  }

  // ── Always-on: 트레이 아이콘 생성 (서버 백그라운드 상주) ──
  createTray();

  // ── STEP 0: Show splash window safely on ready-to-show without any white/black blank flash ──
  let splashWindow = new BrowserWindow({
    width: 420,
    height: 340,
    frame: false,
    transparent: false,
    resizable: false,
    center: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: '#0a0a0f',
    show: false,  // Prevents blank white/black window flash completely!
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    }
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => {
    splashWindow.show();
    try { splashWindow.focus(); } catch (_) { }
  });
  try {
    // ── STEP 1: Run cleanup & cache clear in PARALLEL (not sequentially) ──
    const DEFAULT_PORT = 9090;
    const myPid = process.pid;

    // Clear HTTP cache
    try {
      if (session && session.defaultSession) {
        await session.defaultSession.clearCache();
        console.log('[Electron] HTTP session cache cleared.');
      }
    } catch (e) {
      console.warn('[Electron] Cache clear failed:', e);
    }

    // Kill old server processes synchronously BEFORE spawning new server
    try {
      if (process.platform === 'win32') {
        execSync('taskkill /F /IM server.exe /T 2>nul', { windowsHide: true });
      }
    } catch (_) { }

    // ── STEP 1b: Cleanup orphaned PyInstaller _MEI* temp folders ──
    // This runs AFTER server processes are killed so their _MEI folders are unlocked.
    try {
      cleanupOrphanedTemp();
    } catch (e) {
      console.warn('[Cleanup] Temp cleanup failed (non-fatal):', e.message);
    }

    // Brief pause for processes to fully terminate
    await new Promise(r => setTimeout(r, 500));

    // ── STEP 2: Start Main Python Server ──
    serverPort = DEFAULT_PORT;
    console.log(`[Electron] Using default port: ${serverPort}`);
    startPythonProcess(serverPort);

    // ── STEP 3: Wait for server health (splash is showing during this wait) ──
    console.log(`[Electron] Waiting for server on port ${serverPort}...`);
    await checkServerHealth(serverPort, 180, 1000);
    console.log(`[Electron] Main server is ready!`);

    // ── STEP 3b: Start TTS Server (non-blocking) ──
    startTtsProcess(ttsPort);
    checkServerHealth(ttsPort, 30).then(() => {
      console.log(`[Electron] TTS server is ready!`);
    }).catch((err) => {
      console.error(`[Electron] TTS server health check failed (app will continue): ${err.message}`);
    });

    // ── STEP 3c: Verify CDP port 9222 ──
    checkCdpHealth(10);

    // ── STEP 3d: Start Watchdog ──
    startWatchdog(serverPort);

    console.log(`[Electron] All services ready — Launching UI...`);

    // ── STEP 4: Create Main Window (using BrowserWindow for stability) ──
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    mainWindow = new BrowserWindow({
      width: Math.floor(width * 0.8),
      height: Math.floor(height * 0.8),
      show: true,
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    mainWindow.center();
    mainWindow.setMenu(null);

    // ── Always-on: 창 X 버튼 → 종료 대신 트레이로 최소화 (서버 백그라운드 유지) ──
    mainWindow.on('close', (event) => {
      if (!isQuitting && tray) {
        event.preventDefault();
        mainWindow.hide();
        try { tray.displayBalloon({ title: 'DAON Agent System', content: '백그라운드에서 실행 중입니다. 트레이 아이콘에서 열 수 있습니다.' }); } catch (_) { }
        console.log('[AlwaysOn] Window hidden to tray (server keeps running).');
      }
    });

    // ── STEP 5: Load UI (force clear cache to prevent old UI loading) ──
    try {
      if (session && session.defaultSession) {
        await session.defaultSession.clearCache();
      }
    } catch (_) { }
    mainWindow.loadURL(`http://127.0.0.1:${serverPort}`, {
      extraHeaders: 'pragma: no-cache\r\nCache-Control: no-cache\r\n'
    });

    // ── Keyboard shortcuts with debouncing ──
    let _lastF5Time = 0;
    let _lastF12Time = 0;
    const DEBOUNCE_MS = 500;

    mainWindow.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const now = Date.now();

      if (input.key === 'F5' || (input.control && input.key.toLowerCase() === 'r')) {
        if (now - _lastF5Time > DEBOUNCE_MS) {
          _lastF5Time = now;
          mainWindow.webContents.reload();
        }
        event.preventDefault();
      } else if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
        if (now - _lastF12Time > DEBOUNCE_MS) {
          _lastF12Time = now;
          // Use 'detach' mode to avoid CDP port 9222 lock contention
          mainWindow.webContents.openDevTools({ mode: 'detach' });
        }
        event.preventDefault();
      }
    });

    // Resize callback for tabManager
    const updateBounds = () => {
      if (tabManager) tabManager.resize();
    };

    mainWindow.on('resize', updateBounds);
    mainWindow.on('maximize', updateBounds);
    mainWindow.on('unmaximize', updateBounds);
    mainWindow.on('restore', updateBounds);

    // ── STEP 6: Instantly close splash window & show main window safely ──
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    mainWindow.show();
    mainWindow.center();
    mainWindow.focus();
    console.log('[Electron] Main window shown!');

    tabManager = new TabManager(mainWindow);

  } catch (err) {
    console.error("[Electron Startup Error]", err);
    try {
      const { dialog } = require('electron');
      dialog.showErrorBox("Startup Error", "Failed to start DAON Agent System:\n" + err.message);
    } catch (e) { }
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
    app.quit();
  }
});


class TabManager {
  constructor(mainWindow) {
    this.mainWindow = mainWindow;
    this.tabs = new Map();
    this.activeTabId = null;
    this.bounds = { x: 300, y: 50, width: 800, height: 600 };
    this.isVisible = false;
  }

  createTab(tabId, url) {
    const view = new WebContentsView({
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    this.tabs.set(tabId, view);

    // Prevent new BrowserWindows from opening — navigate in the same view instead
    view.webContents.setWindowOpenHandler(({ url: newUrl }) => {
      if (newUrl && newUrl !== 'about:blank') {
        view.webContents.loadURL(newUrl);
      }
      return { action: 'deny' };
    });

    view.webContents.loadURL(url);
    return view;
  }

  switchTab(tabId) {
    if (this.activeTabId && this.tabs.has(this.activeTabId)) {
      this.mainWindow.contentView.removeChildView(this.tabs.get(this.activeTabId));
    }
    this.activeTabId = tabId;
    if (this.isVisible && this.tabs.has(tabId)) {
      const view = this.tabs.get(tabId);
      this.mainWindow.contentView.addChildView(view);
      view.setBounds(this.bounds);
    }
  }

  navigate(tabId, url) {
    let view = this.tabs.get(tabId);
    if (!view) {
      view = this.createTab(tabId, url);
      // Ensure visibility — if navigate is called, the user/frontend wants to see it
      this.isVisible = true;
      this.activeTabId = tabId;
      try { this.mainWindow.contentView.addChildView(view); } catch (e) { }
      view.setBounds(this.bounds);
    } else {
      view.webContents.loadURL(url);
      // Ensure the existing tab is visible and active
      if (this.activeTabId !== tabId) {
        this.switchTab(tabId);
      }
    }
  }

  setBounds(bounds) {
    this.bounds = bounds;
    this.resize();
  }

  setVisibility(visible) {
    this.isVisible = visible;
    if (visible && this.activeTabId && this.tabs.has(this.activeTabId)) {
      const view = this.tabs.get(this.activeTabId);
      try { this.mainWindow.contentView.addChildView(view); } catch (e) { }
      view.setBounds(this.bounds);
    } else if (!visible && this.activeTabId && this.tabs.has(this.activeTabId)) {
      try { this.mainWindow.contentView.removeChildView(this.tabs.get(this.activeTabId)); } catch (e) { }
    }
  }

  resize() {
    if (this.isVisible && this.activeTabId && this.tabs.has(this.activeTabId)) {
      this.tabs.get(this.activeTabId).setBounds(this.bounds);
    }
  }
}

// --- IPC Commands ---
ipcMain.on('browser-navigate', (event, { id, url }) => {
  if (tabManager) tabManager.navigate(id || 'tab1', url);
});

ipcMain.on('browser-set-bounds', (event, bounds) => {
  if (tabManager) tabManager.setBounds(bounds);
});

ipcMain.on('browser-set-visibility', (event, visible) => {
  if (tabManager) tabManager.setVisibility(visible);
});

ipcMain.on('browser-set-ignore-mouse-events', (event, ignore) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    if (ignore) {
      view.webContents.setIgnoreMouseEvents(true, { forward: true });
    } else {
      view.webContents.setIgnoreMouseEvents(false);
    }
  }
});

ipcMain.on('browser-go-back', (event, { id }) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    if (view.webContents.canGoBack()) {
      view.webContents.goBack();
    }
  }
});

ipcMain.on('browser-go-forward', (event, { id }) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    if (view.webContents.canGoForward()) {
      view.webContents.goForward();
    }
  }
});

ipcMain.on('browser-reload', (event, { id }) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    view.webContents.reload();
  }
});

ipcMain.on('open-external', (event, url) => {
  // Open URL in the user's default system browser (not in-app browser)
  shell.openExternal(url).catch(err => console.error('[IPC] openExternal failed:', err));
});

ipcMain.on('open-system-browser', (event, filePath) => {
  // Open a local HTML file in the user's default system browser
  const { spawn } = require('child_process');
  const cmd = process.platform === 'win32'
    ? `start "" "${filePath}"`
    : process.platform === 'darwin'
      ? `open "${filePath}"`
      : `xdg-open "${filePath}"`;
  exec(cmd, { windowsHide: true }, (err) => {
    if (err) console.error('[IPC] openSystemBrowser failed:', err);
    else console.log('[IPC] Opened in system browser:', filePath);
  });
});

ipcMain.on('install-update', (event, installerPath) => {
  console.log(`Starting update process with installer: ${installerPath}`);

  // 1. Clean up processes explicitly before quitting (Graceful shutdown attempt)
  stopWatchdog();
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
    pythonProcess = null;
  }

  // 2. Close all UI windows
  if (mainWindow) {
    mainWindow.close();
  }

  // 3. Spawn a detached cmd process that acts as our final safety net and updater:
  //    - Wait ~3 seconds (ping -n 4)
  //    - If processes are still alive, force kill them (taskkill)
  //    - Launch installer
  try {
    const cmdStr = [
      'ping 127.0.0.1 -n 4 > nul',
      'taskkill /F /IM "DAON Agent System.exe" /T > nul 2>&1',
      'taskkill /F /IM "server.exe" /T > nul 2>&1',
      `"${installerPath}" /S`
    ].join(' & ');

    const updaterProcess = spawn('cmd.exe', ['/c', cmdStr], {
      detached: true,
      windowsHide: true,
      stdio: 'ignore'
    });
    updaterProcess.unref();
    console.log('Updater detached process spawned with safety net. Quitting app now.');
  } catch (err) {
    console.error('Failed to spawn updater process:', err);
  }

  // 4. Force quit the app to release all file locks
  app.quit();
});

// --- Cleanup on Quit ---
app.on('before-quit', () => {
  isQuitting = true;
  stopWatchdog();
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
    pythonProcess = null;
  }
  if (ttsProcess && ttsProcess.pid) {
    killProcessTree(ttsProcess.pid);
    ttsProcess = null;
  }

  // Wait briefly for processes to die, then clean up their _MEI folders
  setTimeout(() => {
    try {
      cleanupOrphanedTemp();
    } catch (e) {
      console.warn('[Cleanup] Shutdown temp cleanup failed:', e.message);
    }
  }, 1500);
});

app.on('window-all-closed', () => {
  // Always-on: 트레이가 살아있으면 창이 모두 닫혀도 종료하지 않고 서버 유지
  if (tray && !isQuitting) return;
  if (process.platform !== 'darwin') app.quit();
});

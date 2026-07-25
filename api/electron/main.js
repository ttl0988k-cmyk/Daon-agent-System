console.log("[BUILD ID]: main-v3-2026-07-25-17:30");
console.log("[BUILD ID]: watchdog-fix-v3-2026-07-25-17:28");

const { app, BrowserWindow, BaseWindow, WebContentsView, ipcMain, screen, shell } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const http = require('http');
const net = require('net');

// ── Single Instance Lock ──
// Each instance needs exclusive access to CDP port 9222 and spawns its own
// Python server.  A second launch must bail immediately to avoid port wars.
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  // Another instance is already running — quit silently.
  app.quit();
  // Prevent app.whenReady() from ever firing.
  // On Windows, app.quit() may not exit immediately; we force it.
  process.exit(0);
}

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
let serverPort = 8000;
let ttsPort = 9091;
let watchdogTimer = null;

// --- CDP (Chrome DevTools Protocol) port for browser automation ---
// app.commandLine.appendSwitch is NOT reliable in packaged Electron builds.
// The only guaranteed way to open a Chromium debugging port is to have
// "--remote-debugging-port=9222" in process.argv BEFORE app is ready.
// Pattern: detect → relaunch → on second launch the flag is present.
const NEEDED_CDP_PORT = '9222';
const hasCdpInArgv = process.argv.some(a => a.startsWith('--remote-debugging-port='));
if (!hasCdpInArgv) {
  // First launch without the flag — relaunch WITH it.
  // The relaunched process will have --remote-debugging-port=9222 in argv,
  // which Electron passes directly to Chromium.
  //
  // #36 fix: release the single-instance lock BEFORE relaunching.
  // Without this, app.exit(0) may not release the lock synchronously,
  // causing the relaunched instance to fail requestSingleInstanceLock()
  // and immediately quit — leaving no instance with CDP 9222 active.
  console.log('[Electron] CDP flag missing from argv — relaunching with --remote-debugging-port=9222');
  app.releaseSingleInstanceLock();
  app.relaunch({ args: process.argv.slice(1).concat([`--remote-debugging-port=${NEEDED_CDP_PORT}`]) });
  app.exit(0);
  // Execution stops here — app.exit is synchronous in this context.
}

// At this point process.argv has --remote-debugging-port=9222 (from relaunch).
// Still call appendSwitch as belt-and-suspenders — it's harmless if redundant.
app.commandLine.appendSwitch('remote-debugging-port', NEEDED_CDP_PORT);
app.commandLine.appendSwitch('remote-allow-origins', '*');

// --- Helper: Find Free Port ---
function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(startPort, () => {
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

// --- Helper: Health Check ---
function checkServerHealth(port, retries = 60) {
  return new Promise((resolve, reject) => {
    const attempt = (currentRetry) => {
      const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else {
          retryOrReject(currentRetry);
        }
      });
      req.on('error', () => {
        retryOrReject(currentRetry);
      });
      req.end();
    };

    const retryOrReject = (currentRetry) => {
      if (currentRetry <= 0) {
        reject(new Error("Server health check timed out"));
      } else {
        setTimeout(() => attempt(currentRetry - 1), 500);
      }
    };

    attempt(retries);
  });
}

// --- Helper: CDP Health Check ---
function checkCdpHealth(retries = 10) {
  return new Promise((resolve) => {
    const attempt = (currentRetry) => {
      const req = http.get('http://localhost:9222/json/version', (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          if (res.statusCode === 200) {
            console.log('[Electron] CDP port 9222 is active');
            resolve(true);
          } else {
            retryOrResolve(currentRetry);
          }
        });
      });
      req.on('error', () => retryOrResolve(currentRetry));
      req.setTimeout(2000, () => { req.destroy(); retryOrResolve(currentRetry); });
      req.end();
    };

    const retryOrResolve = (currentRetry) => {
      if (currentRetry <= 0) {
        console.warn('[Electron] CDP port 9222 not responding — browser tools may not work');
        resolve(false);
      } else {
        setTimeout(() => attempt(currentRetry - 1), 1000);
      }
    };

    attempt(retries);
  });
}

// --- Helper: Force Kill Process Tree (Windows) ---
function killProcessTree(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      exec(`taskkill /pid ${pid} /T /F`, { windowsHide: true }, () => {
        console.log(`[Cleanup] Killed process tree for PID: ${pid}`);
      });
    } else {
      process.kill(-pid, 'SIGKILL');
    }
  } catch (e) {
    console.error(`[Cleanup] Failed to kill PID ${pid}:`, e);
  }
}

// --- Watchdog: Periodic health check + auto-restart if server dies ---
let watchdogRestartCount = 0;
const WATCHDOG_INTERVAL = 30_000;  // Check every 30 seconds
const MAX_RESTARTS = 5;            // Give up after 5 consecutive failures

function startWatchdog(port) {
  if (watchdogTimer) return;  // Already running
  console.log('[Watchdog] Starting health monitor (every 30s)...');
  watchdogRestartCount = 0;

  watchdogTimer = setInterval(() => {
    let timedOut = false;
    const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
      // Server responded — reset restart counter
      if (res.statusCode === 200) {
        watchdogRestartCount = 0;
      }
      res.resume(); // Consume response data so the socket can be reused
    });
    req.on('error', async (err) => {
      // Ignore errors caused by our own timeout destroy — these are NOT real failures
      if (timedOut) {
        console.log('[Watchdog] Health check timed out (server may be busy); skipping restart.');
        return;
      }
      console.error('[Watchdog] Server health check FAILED:', err.message);
      watchdogRestartCount++;

      if (watchdogRestartCount > MAX_RESTARTS) {
        console.error(`[Watchdog] ${MAX_RESTARTS} consecutive failures. Giving up.`);
        stopWatchdog();
        return;
      }

      console.log(`[Watchdog] Attempting restart (${watchdogRestartCount}/${MAX_RESTARTS})...`);

      // Kill only the managed Python process by PID — never blanket-kill all Python processes
      if (pythonProcess && pythonProcess.pid) {
        killProcessTree(pythonProcess.pid);
        // Fallback: ensure the specific exe is gone without nuking unrelated processes
        try {
          exec(`taskkill /F /PID ${pythonProcess.pid} /T 2>nul`, { windowsHide: true }, () => { });
        } catch (e) { }
        pythonProcess = null;
      }

      // Give processes time to die
      await new Promise(r => setTimeout(r, 2000));

      // Restart the server
      const isPackaged = app.isPackaged;
      if (isPackaged) {
        const exePath = path.join(process.resourcesPath, 'server.exe');
        const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
        pythonProcess = spawn(exePath, ['--no-browser', '--port', port.toString()], {
          cwd: process.resourcesPath,
          env,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      } else {
        const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
        pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', port.toString()], {
          cwd: path.join(__dirname, '..'),
          env,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe']
        });
      }

      pythonProcess.stdout.on('data', (data) => console.log(`[Python]: ${data}`));
      pythonProcess.stderr.on('data', (data) => console.error(`[Python Error]: ${data}`));

      // Wait for server to become healthy
      try {
        await checkServerHealth(port, 60);  // Up to 30 seconds
        console.log('[Watchdog] Server restarted successfully!');
        watchdogRestartCount = 0;
      } catch (e) {
        console.error('[Watchdog] Server failed to restart:', e.message);
      }
    });
    req.setTimeout(5000, () => {
      timedOut = true;
      req.destroy();
    });
    req.end();
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
  try {
    // 0. Kill orphaned server processes from previous runs (startup cleanup)
    // Strategy: kill packaged server.exe + free our target port (9090) from any stale holder.
    // We do NOT blanket-kill python.exe — that would nuke unrelated user work.
    console.log('[Electron] Cleaning up orphaned server processes...');
    const DEFAULT_PORT = 9090;
    try {
      const myPid = process.pid;
      // (a) Kill other DAON Agent System.exe instances — they conflict on CDP port 9222.
      //     Exclude our own PID to avoid self-kill.
      exec(`for /f "tokens=2" %a in ('tasklist /FI "IMAGENAME eq DAON Agent System.exe" /FO TABLE /NH 2^>nul') do @if NOT %a==${myPid} taskkill /F /PID %a /T 2>nul`, { windowsHide: true }, (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Killed other DAON Agent System.exe instances.');
      });
      // (b) Kill leftover packaged server.exe
      exec('taskkill /F /IM server.exe /T 2>nul', { windowsHide: true }, (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Killed orphaned server.exe processes.');
      });
      // (d) Free port 9090 from ANY stale process (Python or otherwise) — safer than killing all python.exe
      //     Exclude our own PID to avoid self-kill.
      exec(`for /f "tokens=5" %a in ('netstat -ano ^| findstr :${DEFAULT_PORT} ^| findstr LISTENING') do @if NOT %a==${myPid} taskkill /F /PID %a /T 2>nul`, { windowsHide: true }, (err, stdout, stderr) => {
        if (!err) console.log(`[Electron] Freed port ${DEFAULT_PORT} from stale process.`);
      });
      // (e) Free port 9222 (CDP) from any stale Chrome/Edge debug instance.
      //     IMPORTANT: Exclude our own PID — this app itself listens on 9222 for CDP.
      exec(`for /f "tokens=5" %a in ('netstat -ano ^| findstr :9222 ^| findstr LISTENING') do @if NOT %a==${myPid} taskkill /F /PID %a /T 2>nul`, { windowsHide: true }, (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Freed port 9222 (CDP) from stale debug instance.');
      });
      // (f) Free port 9091 (TTS) from any stale process — exclude own PID.
      exec(`for /f "tokens=5" %a in ('netstat -ano ^| findstr :9091 ^| findstr LISTENING') do @if NOT %a==${myPid} taskkill /F /PID %a /T 2>nul`, { windowsHide: true }, (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Freed port 9091 (TTS) from stale process.');
      });
    } catch (e) {
      console.error('[Electron] Cleanup error (ignored):', e);
    }
    // Small delay to let processes fully terminate
    await new Promise(r => setTimeout(r, 1000));

    // 1. Use fixed default port (9090)
    serverPort = DEFAULT_PORT;
    console.log(`[Electron] Using default port: ${serverPort}`);

    // 2. Start Python Server
    const isPackaged = app.isPackaged;
    if (isPackaged) {
      const exePath = path.join(process.resourcesPath, 'server.exe');
      const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
      pythonProcess = spawn(exePath, ['--no-browser', '--port', serverPort.toString()], {
        cwd: process.resourcesPath,
        env,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      });
    } else {
      const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
      pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', serverPort.toString()], {
        cwd: path.join(__dirname, '..'),
        env,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      });
    }

    pythonProcess.stdout.on('data', (data) => console.log(`[Python]: ${data}`));
    pythonProcess.stderr.on('data', (data) => console.error(`[Python Error]: ${data}`));

    // Boost main server CPU priority so faster-whisper inference isn't starved
    // by Chromium + TTS.  ABOVE_NORMAL (0x8000) on Windows.
    if (process.platform === 'win32' && pythonProcess.pid) {
      try {
        exec(`powershell -NoProfile -Command "(Get-Process -Id ${pythonProcess.pid}).PriorityClass = 'AboveNormal'"`, { windowsHide: true });
        console.log(`[Electron] Main server (pid=${pythonProcess.pid}) priority → AboveNormal`);
      } catch (_) { /* best-effort */ }
    }

    // 2b. Start TTS Server (port 9091) — dedicated edge-tts synthesis server
    //     TTS runs on its own process so long-running synthesis (up to 25s)
    //     never blocks the main agent server or CDP browser tools.
    if (isPackaged) {
      const ttsExePath = path.join(process.resourcesPath, 'server.exe');
      ttsProcess = spawn(ttsExePath, ['--tts-mode', '--tts-port', ttsPort.toString()], {
        cwd: process.resourcesPath,
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      });
    } else {
      ttsProcess = spawn('python', ['server.py', '--tts-mode', '--tts-port', ttsPort.toString()], {
        cwd: path.join(__dirname, '..'),
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'pipe']
      });
    }
    ttsProcess.stdout.on('data', (data) => console.log(`[TTS]: ${data}`));
    ttsProcess.stderr.on('data', (data) => console.error(`[TTS Error]: ${data}`));

    // Drop TTS process priority so it never starves the main server's
    // faster-whisper CPU inference.  BELOW_NORMAL (0x4000) on Windows.
    if (process.platform === 'win32' && ttsProcess.pid) {
      try {
        exec(`powershell -NoProfile -Command "(Get-Process -Id ${ttsProcess.pid}).PriorityClass = 'BelowNormal'"`, { windowsHide: true });
        console.log(`[Electron] TTS server (pid=${ttsProcess.pid}) priority → BelowNormal`);
      } catch (_) { /* best-effort */ }
    }

    // 3. Wait for Health Check (Up to 60 seconds: 120 * 500ms)
    console.log(`[Electron] Waiting for server on port ${serverPort}...`);
    await checkServerHealth(serverPort, 120);
    console.log(`[Electron] Server is ready!`);

    // 3b. TTS server health check (non-blocking — TTS failure must not kill the app)
    console.log(`[Electron] Waiting for TTS server on port ${ttsPort}...`);
    checkServerHealth(ttsPort, 30).then(() => {
      console.log(`[Electron] TTS server is ready!`);
    }).catch((err) => {
      console.error(`[Electron] TTS server health check failed (app will continue): ${err.message}`);
    });

    // 3c. Verify CDP port 9222 is active (for browser_navigate / Playwright tools)
    checkCdpHealth(10);

    // 3d. Start Watchdog (periodic health check + auto-restart)
    startWatchdog(serverPort);

    console.log(`[Electron] All services ready — Launching UI...`);

    // 4. Create Main Window
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    mainWindow = new BaseWindow({
      width: Math.floor(width * 0.8),
      height: Math.floor(height * 0.8),
      show: false, // Wait until load is done
    });

    // Remove the default Windows menu bar which can mess up content bounds
    mainWindow.setMenu(null);

    // 5. Setup UI and TabManager
    const uiView = new WebContentsView({
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    mainWindow.contentView.addChildView(uiView);
    uiView.setBounds({ x: 0, y: 0, width: mainWindow.getContentBounds().width, height: mainWindow.getContentBounds().height });

    uiView.webContents.loadURL(`http://127.0.0.1:${serverPort}`);

    // [NEW] Global shortcuts for reload and devtools
    uiView.webContents.on('before-input-event', (event, input) => {
      if (input.type === 'keyDown') {
        if (input.key === 'F5' || (input.control && input.key.toLowerCase() === 'r')) {
          uiView.webContents.reload();
          event.preventDefault();
        } else if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
          uiView.webContents.toggleDevTools();
          event.preventDefault();
        }
      }
    });

    // Resize UI when window resizes or maximizes
    const updateBounds = () => {
      const bounds = mainWindow.getContentBounds();
      uiView.setBounds({ x: 0, y: 0, width: bounds.width, height: bounds.height });
      if (tabManager) tabManager.resize();
    };

    mainWindow.on('resize', updateBounds);
    mainWindow.on('maximize', updateBounds);
    mainWindow.on('unmaximize', updateBounds);
    mainWindow.on('restore', updateBounds);

    uiView.webContents.on('did-finish-load', () => {
      mainWindow.show();
    });

    tabManager = new TabManager(mainWindow);

  } catch (err) {
    console.error("[Electron Startup Error]", err);
    try {
      const { dialog } = require('electron');
      dialog.showErrorBox("Startup Error", "Failed to start DAON Agent System:\n" + err.message);
    } catch (e) { }
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
  stopWatchdog();
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
  }
  if (ttsProcess && ttsProcess.pid) {
    killProcessTree(ttsProcess.pid);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

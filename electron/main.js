const { app, BrowserWindow, BaseWindow, WebContentsView, ipcMain, screen } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const http = require('http');
const net = require('net');

let mainWindow;
let tabManager;
let pythonProcess = null;
let serverPort = 8000;
let watchdogTimer = null;

app.commandLine.appendSwitch('remote-debugging-port', '9222');

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

// --- Helper: Force Kill Process Tree (Windows) ---
function killProcessTree(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      exec(`taskkill /pid ${pid} /T /F`, () => {
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
          exec(`taskkill /F /PID ${pythonProcess.pid} /T 2>nul`, () => { });
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
          detached: process.platform !== 'win32'
        });
      } else {
        const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
        pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', port.toString()], {
          cwd: path.join(__dirname, '..'),
          env,
          detached: process.platform !== 'win32'
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

app.whenReady().asyncReady = async () => {
  try {
    // 0. Kill orphaned server processes from previous runs (startup cleanup)
    console.log('[Electron] Cleaning up orphaned server processes...');
    try {
      exec('taskkill /F /IM server.exe /T 2>nul', (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Killed orphaned server.exe processes.');
      });
      exec('taskkill /F /IM python.exe /T 2>nul', (err, stdout, stderr) => {
        if (!err) console.log('[Electron] Killed orphaned python.exe processes.');
      });
    } catch (e) {
      console.error('[Electron] Cleanup error (ignored):', e);
    }
    // Small delay to let processes fully terminate
    await new Promise(r => setTimeout(r, 1000));

    // 1. Find Free Port
    serverPort = await findFreePort(8000);
    console.log(`[Electron] Selected port: ${serverPort}`);

    // 2. Start Python Server
    const isPackaged = app.isPackaged;
    if (isPackaged) {
      const exePath = path.join(process.resourcesPath, 'server.exe');
      const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
      pythonProcess = spawn(exePath, ['--no-browser', '--port', serverPort.toString()], {
        cwd: process.resourcesPath,
        env,
        detached: process.platform !== 'win32'
      });
    } else {
      const env = { ...process.env, BROWSER_CDP_URL: 'ws://localhost:9222' };
      pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', serverPort.toString()], {
        cwd: path.join(__dirname, '..'),
        env,
        detached: process.platform !== 'win32'
      });
    }

    pythonProcess.stdout.on('data', (data) => console.log(`[Python]: ${data}`));
    pythonProcess.stderr.on('data', (data) => console.error(`[Python Error]: ${data}`));

    // 3. Wait for Health Check (Up to 60 seconds: 120 * 500ms)
    console.log(`[Electron] Waiting for server on port ${serverPort}...`);
    await checkServerHealth(serverPort, 120);
    console.log(`[Electron] Server is ready! Launching UI...`);

    // 3b. Start Watchdog (periodic health check + auto-restart)
    startWatchdog(serverPort);

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
};

// Trigger the async ready wrapper
app.whenReady().then(app.whenReady().asyncReady);

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
    if (view.webContents.navigationHistory.canGoBack()) {
      view.webContents.navigationHistory.goBack();
    }
  }
});

ipcMain.on('browser-go-forward', (event, { id }) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    if (view.webContents.navigationHistory.canGoForward()) {
      view.webContents.navigationHistory.goForward();
    }
  }
});

ipcMain.on('browser-reload', (event, { id }) => {
  if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
    const view = tabManager.tabs.get(tabManager.activeTabId);
    view.webContents.reload();
  }
});

// --- Cleanup on Quit ---
app.on('before-quit', () => {
  stopWatchdog();
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

// @ts-check
/**
 * WindowManager
 * Manages creation, lifecycle, guards, and shortcuts for Splash and Main BrowserWindow.
 */
const { BrowserWindow, screen, session } = require('electron');
const path = require('path');

class WindowManager {
  /**
   * @param {Object} options
   * @param {Function} options.mlog
   * @param {Function} options.merr
   */
  constructor({ mlog, merr }) {
    this.mlog = mlog || console.log;
    this.merr = merr || console.error;
    this.splashWindow = null;
    this.mainWindow = null;

    this.RENDERER_RECOVERY_WINDOW_MS = 60_000;
    this.MAX_RENDERER_RECOVERY = 3;
    this._rendererRecoveryCount = 0;
    this._rendererRecoveryWindowStart = 0;
    this._rendererRecovering = false;
  }

  createSplashWindow() {
    this.splashWindow = new BrowserWindow({
      width: 420,
      height: 340,
      frame: false,
      transparent: false,
      resizable: false,
      center: true,
      alwaysOnTop: true,
      skipTaskbar: false,
      backgroundColor: '#0a0a0f',
      show: false,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
      }
    });

    this.splashWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    this.splashWindow.loadFile(path.join(__dirname, '..', 'splash.html'));
    this.splashWindow.once('ready-to-show', () => {
      if (this.splashWindow && !this.splashWindow.isDestroyed()) {
        this.splashWindow.show();
        try { this.splashWindow.focus(); } catch (_) { }
      }
    });

    return this.splashWindow;
  }

  closeSplashWindow() {
    if (this.splashWindow && !this.splashWindow.isDestroyed()) {
      try { this.splashWindow.close(); } catch (_) { }
      this.splashWindow = null;
    }
  }

  createMainWindow({ serverPort, supervisor, getTabManager, getTray, isQuittingGetter }) {
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    this.mainWindow = new BrowserWindow({
      width: Math.floor(width * 0.8),
      height: Math.floor(height * 0.8),
      show: false, // will show after splash closes
      webPreferences: {
        preload: path.join(__dirname, '..', 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    this.mainWindow.center();
    this.mainWindow.setMenu(null);

    // Guard: Prevent mainWindow from navigating away to external URLs
    try {
      this.mainWindow.webContents.on('will-navigate', (event, url) => {
        let host = '';
        let proto = '';
        try {
          const u = new URL(url);
          host = u.hostname;
          proto = u.protocol;
        } catch (_) { }
        const isLocalUi = proto === 'file:' || host === '127.0.0.1' || host === 'localhost' || host === '::1';
        if (!isLocalUi) {
          event.preventDefault();
          this.mlog(`[Guard] Blocked mainWindow navigate to external URL: ${url}`);
        }
      });
    } catch (e) {
      this.merr('[Guard] mainWindow will-navigate guard setup failed:', e && e.message);
    }

    // Setup crash & reload recovery guards
    this._setupRendererGuards(serverPort, supervisor, getTabManager);

    // Setup close-to-tray
    let _balloonShownOnce = false;
    this.mainWindow.on('close', (event) => {
      const isQuitting = isQuittingGetter();
      const tray = getTray();
      if (!isQuitting && tray) {
        event.preventDefault();
        this.mainWindow.hide();
        if (!_balloonShownOnce) {
          _balloonShownOnce = true;
          try {
            tray.displayBalloon({
              title: 'DAON Agent System',
              content: '백그라운드에서 실행 중입니다. 트레이 아이콘에서 열 수 있습니다.'
            });
            setTimeout(() => { try { tray.removeBalloon(); } catch (_) { } }, 2000);
          } catch (_) { }
        }
        this.mlog('[AlwaysOn] Window hidden to tray (server keeps running).');
      }
    });

    // Keyboard shortcuts (F5, Ctrl+R, F12)
    this._setupKeyboardShortcuts(serverPort, supervisor, getTabManager);

    // Resize events for TabManager
    const updateBounds = () => {
      const tabManager = getTabManager();
      if (tabManager) tabManager.resize();
    };
    this.mainWindow.on('resize', updateBounds);
    this.mainWindow.on('maximize', updateBounds);
    this.mainWindow.on('unmaximize', updateBounds);
    this.mainWindow.on('restore', updateBounds);

    return this.mainWindow;
  }

  _setupRendererGuards(serverPort, supervisor, getTabManager) {
    const win = this.mainWindow;
    if (!win) return;

    win.webContents.on('render-process-gone', (event, details) => {
      this.merr(`[RendererCrash] mainWindow render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
      const now = Date.now();
      if (now - this._rendererRecoveryWindowStart > this.RENDERER_RECOVERY_WINDOW_MS) {
        this._rendererRecoveryCount = 0;
        this._rendererRecoveryWindowStart = now;
      }
      if (this._rendererRecovering) {
        this.merr('[RendererCrash] recovery already in flight, skipping reload.');
        return;
      }
      if (this._rendererRecoveryCount >= this.MAX_RENDERER_RECOVERY) {
        this.merr(`[RendererCrash] recovery limit reached (${this.MAX_RENDERER_RECOVERY} per ${this.RENDERER_RECOVERY_WINDOW_MS / 1000}s) — NOT reloading.`);
        return;
      }
      this._rendererRecoveryCount++;
      this._rendererRecovering = true;
      this.mlog(`[RendererCrash] auto-reload attempt ${this._rendererRecoveryCount}/${this.MAX_RENDERER_RECOVERY}`);
      try { win.webContents.reload(); } catch (_) { }
      setTimeout(() => { this._rendererRecovering = false; }, 5000);
    });

    win.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      if (!isMainFrame) return;
      this.merr(`[RendererFail] did-fail-load: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);

      if (errorCode === -102 || errorCode === -105) {
        this.merr('[RendererFail] Server connection refused — triggering emergency restart...');
        if (supervisor && supervisor.pythonProcess && supervisor.pythonProcess.pid && !supervisor.pythonProcess._adopted) {
          supervisor.killProcessTree(supervisor.pythonProcess.pid);
          supervisor.pythonProcess = null;
        }
        if (supervisor) supervisor.startPythonProcess(serverPort);
      }

      if (supervisor) {
        supervisor.checkServerHealth(serverPort, 30, 1000).then(() => {
          if (win && !win.isDestroyed()) {
            try { win.loadURL(`http://127.0.0.1:${serverPort}`); } catch (_) { }
          }
        }).catch(() => {
          setTimeout(() => {
            try { win.loadURL(`http://127.0.0.1:${serverPort}`); } catch (_) { }
          }, 3000);
        });
      }
    });

    win.webContents.on('did-finish-load', () => {
      this._rendererRecoveryCount = 0;
      this._rendererRecoveryWindowStart = 0;
      this._rendererRecovering = false;
      const tabManager = getTabManager();
      if (tabManager) {
        try { tabManager.setVisibility(false); } catch (_) { }
        setTimeout(() => {
          try { tabManager._notifyTabs(); } catch (_) { }
        }, 300);
      }
    });

    win.webContents.on('console-message', (event, level, message, line, sourceId) => {
      try {
        if (level >= 2) {
          this.merr(`[RendererConsole] [L${level}] ${message} (${sourceId}:${line})`);
        }
      } catch (_) { }
    });
  }

  _setupKeyboardShortcuts(serverPort, supervisor, getTabManager) {
    const win = this.mainWindow;
    if (!win) return;

    let _lastF5Time = 0;
    let _lastF12Time = 0;
    const DEBOUNCE_MS = 500;

    win.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const now = Date.now();
      const isF5 = input.key === 'F5' || input.code === 'F5';
      const isReload = input.control && (input.key.toLowerCase() === 'r' || input.code === 'KeyR');
      const isDevTools = input.key === 'F12' || input.code === 'F12' ||
        (input.control && input.shift && (input.key.toLowerCase() === 'i' || input.code === 'KeyI'));

      if (isF5 || isReload) {
        if (now - _lastF5Time > DEBOUNCE_MS) {
          _lastF5Time = now;
          if (supervisor) {
            supervisor.watchdogSuppressUntil = Date.now() + 3 * supervisor.WATCHDOG_INTERVAL;
          }
          const tabManager = getTabManager();
          if (tabManager) {
            try { tabManager.setVisibility(false); } catch (_) { }
          }
          try {
            win.loadURL(`http://127.0.0.1:${serverPort}`, {
              extraHeaders: 'pragma: no-cache\r\nCache-Control: no-cache\r\n'
            });
          } catch (_) {
            try { win.webContents.reload(); } catch (__) { }
          }
        }
        event.preventDefault();
      } else if (isDevTools) {
        if (now - _lastF12Time > DEBOUNCE_MS) {
          _lastF12Time = now;
          win.webContents.openDevTools({ mode: 'detach' });
        }
        event.preventDefault();
      }
    });
  }

  async loadAppUi(serverPort) {
    try {
      if (session && session.defaultSession) {
        await session.defaultSession.clearCache();
      }
    } catch (_) { }

    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.loadURL(`http://127.0.0.1:${serverPort}`, {
        extraHeaders: 'pragma: no-cache\r\nCache-Control: no-cache\r\n'
      });
      this.closeSplashWindow();
      this.mainWindow.show();
      this.mainWindow.center();
      this.mainWindow.focus();
      this.mlog('[WindowManager] Main window shown and focused.');
    }
  }
}

module.exports = {
  WindowManager,
};

// @ts-check
/**
 * IpcHandlers
 * Registers all IPC event handlers between Electron Main and Renderer processes.
 */
const { ipcMain, shell, app } = require('electron');
const { spawn, exec } = require('child_process');

/**
 * Register all IPC channels
 * @param {Object} context
 * @param {import('./TabManager').TabManager} [context.tabManager]
 * @param {import('./ServerSupervisor').ServerSupervisor} context.supervisor
 * @param {import('electron').BrowserWindow} context.mainWindow
 * @param {Function} context.merr
 * @param {Function} context.mlog
 */
function registerIpcHandlers({ tabManager, supervisor, mainWindow, merr, mlog }) {
  // ── Browser Tab IPC ──
  ipcMain.on('browser-navigate', (event, { id, url }) => {
    try {
      if (tabManager) tabManager.navigate(id || 'tab1', url);
    } catch (e) {
      merr('[IPC] browser-navigate failed:', (e && e.stack) || e);
    }
  });

  ipcMain.on('browser-tab-new', (event, { id, url }) => {
    if (!tabManager) return;
    const tabId = id || ('tab' + Date.now());
    tabManager.createTab(tabId, url || 'about:blank');
    tabManager.switchTab(tabId);
  });

  ipcMain.on('browser-tab-switch', (event, { id }) => {
    if (tabManager && id && tabManager.tabs.has(id)) tabManager.switchTab(id);
  });

  ipcMain.on('browser-tab-close', (event, { id }) => {
    if (tabManager && id) tabManager.closeTab(id);
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
      if (view && view.webContents && !view.webContents.isDestroyed()) {
        if (ignore) {
          view.webContents.setIgnoreMouseEvents(true, { forward: true });
        } else {
          view.webContents.setIgnoreMouseEvents(false);
        }
      }
    }
  });

  ipcMain.on('browser-go-back', (event, { id }) => {
    if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
      const view = tabManager.tabs.get(tabManager.activeTabId);
      if (view && view.webContents && !view.webContents.isDestroyed() && view.webContents.canGoBack()) {
        view.webContents.goBack();
      }
    }
  });

  ipcMain.on('browser-go-forward', (event, { id }) => {
    if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
      const view = tabManager.tabs.get(tabManager.activeTabId);
      if (view && view.webContents && !view.webContents.isDestroyed() && view.webContents.canGoForward()) {
        view.webContents.goForward();
      }
    }
  });

  ipcMain.on('browser-reload', (event, { id }) => {
    if (tabManager && tabManager.activeTabId && tabManager.tabs.has(tabManager.activeTabId)) {
      const view = tabManager.tabs.get(tabManager.activeTabId);
      if (view && view.webContents && !view.webContents.isDestroyed()) {
        view.webContents.reload();
      }
    }
  });

  // ── External Browser Links ──
  ipcMain.on('open-external', (event, url) => {
    shell.openExternal(url).catch(err => merr('[IPC] openExternal failed:', err));
  });

  ipcMain.on('open-system-browser', (event, filePath) => {
    const cmd = process.platform === 'win32'
      ? `start "" "${filePath}"`
      : process.platform === 'darwin'
        ? `open "${filePath}"`
        : `xdg-open "${filePath}"`;
    exec(cmd, { windowsHide: true }, (err) => {
      if (err) merr('[IPC] openSystemBrowser failed:', err);
      else mlog('[IPC] Opened in system browser:', filePath);
    });
  });

  // ── Auto-Update Installer Trigger ──
  ipcMain.on('install-update', (event, installerPath) => {
    mlog(`[IPC] Starting update process with installer: ${installerPath}`);

    // Stop supervisor processes
    if (supervisor) {
      supervisor.shutdown();
    }

    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.close();
    }

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
      mlog('[IPC] Updater detached process spawned with safety net. Quitting app now.');
    } catch (err) {
      merr('[IPC] Failed to spawn updater process:', err);
    }

    app.quit();
  });
}

module.exports = {
  registerIpcHandlers,
};

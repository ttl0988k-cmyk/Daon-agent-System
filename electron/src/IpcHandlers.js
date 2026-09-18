// @ts-check
/**
 * IpcHandlers
 * Registers all IPC event handlers between Electron Main and Renderer processes.
 */
const { ipcMain, shell, app } = require('electron');
const { spawn, exec } = require('child_process');
const path = require('path');
const fs = require('fs');

/**
 * SECURITY (audit R14 / P1-2): validate an installer path before it is handed
 * to `cmd.exe`. The renderer (and any page loaded in the embedded browser) can
 * reach the `install-update` channel, so an unvalidated path is a command
 * injection / arbitrary-execution vector. This is FAIL-CLOSED: anything that
 * does not satisfy every check is rejected.
 *
 * Rules:
 *   1. must be a non-empty string
 *   2. must be an absolute path (no relative traversal)
 *   3. must resolve (realpath) to a location inside a trusted root
 *   4. must exist and be a regular file
 *   5. must have a `.exe` extension (Windows installer)
 *   6. must not contain shell metacharacters
 *
 * @param {string} installerPath
 * @returns {{ ok: true, path: string } | { ok: false, reason: string }}
 */
function validateInstallerPath(installerPath) {
  if (typeof installerPath !== 'string' || installerPath.trim() === '') {
    return { ok: false, reason: 'installerPath must be a non-empty string' };
  }

  // Reject shell metacharacters outright — defence in depth even though we
  // quote the path below.
  if (/[&|<>^%!`\r\n]/.test(installerPath)) {
    return { ok: false, reason: 'installerPath contains shell metacharacters' };
  }

  if (!path.isAbsolute(installerPath)) {
    return { ok: false, reason: 'installerPath must be absolute' };
  }

  if (path.extname(installerPath).toLowerCase() !== '.exe') {
    return { ok: false, reason: 'installerPath must be a .exe file' };
  }

  // Trusted roots: the app's own userData dir (where self-update downloads the
  // installer) and the OS temp dir. Anything else is refused.
  const trustedRoots = [];
  try {
    trustedRoots.push(path.resolve(app.getPath('userData')));
  } catch (_) { /* ignore */ }
  try {
    trustedRoots.push(path.resolve(app.getPath('temp')));
  } catch (_) { /* ignore */ }

  let real;
  try {
    real = fs.realpathSync(installerPath);
  } catch (e) {
    return { ok: false, reason: `installerPath does not exist: ${String(e)}` };
  }

  let stat;
  try {
    stat = fs.statSync(real);
  } catch (e) {
    return { ok: false, reason: `installerPath is not accessible: ${String(e)}` };
  }
  if (!stat.isFile()) {
    return { ok: false, reason: 'installerPath is not a regular file' };
  }

  const inTrustedRoot = trustedRoots.some((root) => {
    const rel = path.relative(root, real);
    return rel !== '' && !rel.startsWith('..') && !path.isAbsolute(rel);
  });
  if (!inTrustedRoot) {
    return { ok: false, reason: 'installerPath is outside the trusted update directories' };
  }

  return { ok: true, path: real };
}

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
    // SECURITY (audit R14 / P1-2): the renderer supplies filePath and it is
    // interpolated into a shell command. Reject shell metacharacters and
    // require an absolute path so a crafted value cannot inject a command.
    if (typeof filePath !== 'string' || filePath.trim() === '' ||
      /[&|<>^%!`\r\n]/.test(filePath) || !path.isAbsolute(filePath)) {
      merr(`[IPC] open-system-browser rejected: invalid path (path=${filePath})`);
      return;
    }
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
    // SECURITY (audit R14 / P1-2): FAIL-CLOSED validation. Never hand an
    // unvalidated renderer-supplied path to cmd.exe. If validation fails we
    // abort WITHOUT quitting the app, so the user can retry with a good path.
    const check = validateInstallerPath(installerPath);
    if (!check.ok) {
      merr(`[IPC] install-update rejected: ${check.reason} (path=${installerPath})`);
      return;
    }
    const safeInstallerPath = check.path;
    mlog(`[IPC] Starting update process with installer: ${safeInstallerPath}`);

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
        `"${safeInstallerPath}" /S`
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

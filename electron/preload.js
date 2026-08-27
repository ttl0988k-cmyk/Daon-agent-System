const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  setBounds: (bounds) => ipcRenderer.send('browser-set-bounds', bounds),
  setVisibility: (visible) => ipcRenderer.send('browser-set-visibility', visible),
  setIgnoreMouseEvents: (ignore) => ipcRenderer.send('browser-set-ignore-mouse-events', ignore),
  navigate: (id, url) => ipcRenderer.send('browser-navigate', { id, url }),
  goBack: (id) => ipcRenderer.send('browser-go-back', { id }),
  goForward: (id) => ipcRenderer.send('browser-go-forward', { id }),
  reload: (id) => ipcRenderer.send('browser-reload', { id }),
  // ── Tab management (2026-08-27) ──
  newTab: (id, url) => ipcRenderer.send('browser-tab-new', { id, url }),
  switchTab: (id) => ipcRenderer.send('browser-tab-switch', { id }),
  closeTab: (id) => ipcRenderer.send('browser-tab-close', { id }),
  onTabsUpdated: (cb) => ipcRenderer.on('browser-tabs-updated', (e, tabs) => cb(tabs)),
  installUpdate: (installerPath) => ipcRenderer.send('install-update', installerPath),
  openExternal: (url) => ipcRenderer.send('open-external', url),
  openSystemBrowser: (filePath) => ipcRenderer.send('open-system-browser', filePath),
});

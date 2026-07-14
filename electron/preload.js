const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  setBounds: (bounds) => ipcRenderer.send('browser-set-bounds', bounds),
  setVisibility: (visible) => ipcRenderer.send('browser-set-visibility', visible),
  setIgnoreMouseEvents: (ignore) => ipcRenderer.send('browser-set-ignore-mouse-events', ignore),
  navigate: (id, url) => ipcRenderer.send('browser-navigate', { id, url }),
  goBack: (id) => ipcRenderer.send('browser-go-back', { id }),
  goForward: (id) => ipcRenderer.send('browser-go-forward', { id }),
  reload: (id) => ipcRenderer.send('browser-reload', { id }),
});

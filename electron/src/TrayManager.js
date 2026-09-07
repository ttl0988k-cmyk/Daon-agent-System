// @ts-check
/**
 * TrayManager
 * Manages the always-on system tray icon, context menu, and system status polling.
 */
const { Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');

class TrayManager {
  constructor({ getMainWindow, getServerPort, onQuit }) {
    this.getMainWindow = getMainWindow;
    this.getServerPort = getServerPort;
    this.onQuit = onQuit;
    this.tray = null;
    this.trayStatusTimer = null;
  }

  findTrayIcon() {
    const candidates = [
      path.join(process.resourcesPath, 'static', 'favicon.png'),
      path.join(process.resourcesPath, 'favicon.png'),
      path.join(__dirname, '..', '..', 'static', 'favicon.png'),
      path.join(__dirname, '..', '..', 'dist_new', 'static', 'favicon.png'),
    ];
    for (const p of candidates) {
      try { if (fs.existsSync(p)) return p; } catch (_) { }
    }
    return null;
  }

  buildTrayMenu(status) {
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
        const mainWindow = this.getMainWindow();
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
        if (this.onQuit) this.onQuit();
      }
    });
    return Menu.buildFromTemplate(items);
  }

  refreshTrayStatus() {
    if (!this.tray) return;
    const port = this.getServerPort();
    const req = http.get({ host: '127.0.0.1', port, path: '/api/system/status', family: 4 }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try {
          const status = JSON.parse(body);
          this.tray.setContextMenu(this.buildTrayMenu(status));
          if (status && status.ok) {
            const q = status.queue || {};
            this.tray.setToolTip(`DAON — 서버정상 | 큐 대기:${q.pending || 0} 실패:${q.failed || 0}`);
          } else {
            this.tray.setToolTip('DAON — 서버 오류');
          }
        } catch (_) {
          this.tray.setContextMenu(this.buildTrayMenu(null));
          this.tray.setToolTip('DAON — 상태 파싱 실패');
        }
      });
    });
    req.on('error', () => {
      try {
        this.tray.setContextMenu(this.buildTrayMenu(null));
        this.tray.setToolTip('DAON — 서버 응답 없음');
      } catch (_) { }
    });
    req.setTimeout(4000, () => req.destroy());
  }

  startTrayStatusPolling() {
    if (this.trayStatusTimer) return;
    this.refreshTrayStatus();
    this.trayStatusTimer = setInterval(() => this.refreshTrayStatus(), 10000);
  }

  stopTrayStatusPolling() {
    if (this.trayStatusTimer) {
      clearInterval(this.trayStatusTimer);
      this.trayStatusTimer = null;
    }
  }

  createTray() {
    if (this.tray) return this.tray;
    try {
      const iconPath = this.findTrayIcon();
      const image = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
      this.tray = new Tray(image.isEmpty() ? image : image.resize({ width: 16, height: 16 }));
      this.tray.setToolTip('DAON Agent System');
      this.tray.setContextMenu(this.buildTrayMenu(null));
      this.tray.on('double-click', () => {
        const mainWindow = this.getMainWindow();
        if (mainWindow) {
          if (!mainWindow.isVisible()) mainWindow.show();
          if (mainWindow.isMinimized()) mainWindow.restore();
          mainWindow.focus();
        }
      });
      console.log('[Tray] Always-on tray icon created.');
      this.startTrayStatusPolling();
      return this.tray;
    } catch (e) {
      console.warn('[Tray] Failed to create tray icon (non-fatal):', e.message);
      return null;
    }
  }

  destroy() {
    this.stopTrayStatusPolling();
    if (this.tray) {
      try { this.tray.destroy(); } catch (_) { }
      this.tray = null;
    }
  }
}

module.exports = {
  TrayManager,
};

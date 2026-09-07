// @ts-check
/**
 * TabManager
 * Manages multi-tab WebContentsViews, bounds clipping, crash recovery, and tab thumbnails.
 */
const { WebContentsView } = require('electron');

class TabManager {
  /**
   * @param {Object} options
   * @param {import('electron').BrowserWindow} options.mainWindow
   * @param {Function} [options.mlog]
   * @param {Function} [options.merr]
   */
  constructor({ mainWindow, mlog, merr }) {
    this.mainWindow = mainWindow;
    this.mlog = mlog || console.log;
    this.merr = merr || console.error;

    this.tabs = new Map();
    this.activeTabId = null;
    this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    this.isVisible = false;

    // Tab crash recovery tracking
    this._tabRecovery = new Map();   // tabId -> { count, windowStart }
    this._recoveringTabs = new Set(); // tabIds currently recovering

    // Periodic thumbnail & tab update for mini-grid (3s interval)
    this._notifyTimer = setInterval(() => {
      if (this.tabs.size > 0 && this.mainWindow && !this.mainWindow.isDestroyed()) {
        this._notifyTabs();
      }
    }, 3000);
  }

  static get MAX_TAB_RECOVERY() { return 5; }
  static get TAB_RECOVERY_WINDOW_MS() { return 300_000; }

  // ── Single Source of Truth for Native View Clipping & Attachment ──
  _syncViewState() {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;
    const contentView = this.mainWindow.contentView;
    if (!contentView) return;

    const validBounds = this.bounds &&
      typeof this.bounds.width === 'number' &&
      typeof this.bounds.height === 'number' &&
      this.bounds.width > 0 &&
      this.bounds.height > 0;

    const shouldShow = this.isVisible && !!this.activeTabId && this.tabs.has(this.activeTabId) && validBounds;

    if (shouldShow) {
      const activeView = this.tabs.get(this.activeTabId);
      if (this._isWebContentsAlive(activeView)) {
        // Detach all inactive views first so they never intercept clicks or linger
        for (const [id, view] of this.tabs) {
          if (id !== this.activeTabId) {
            try { contentView.removeChildView(view); } catch (_) { }
          }
        }
        // Attach active view with strict container bounds
        try {
          contentView.addChildView(activeView);
          activeView.setBounds(this.bounds);
        } catch (e) {
          this.merr('[TabManager] _syncViewState addChildView failed:', e && e.message);
        }
      } else {
        this._recreateTab(this.activeTabId);
      }
    } else {
      // Completely detach ALL native browser views from window layer
      this._detachAllViews();
    }
  }

  _detachAllViews() {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;
    const contentView = this.mainWindow.contentView;
    if (!contentView) return;

    for (const [_, view] of this.tabs) {
      try { contentView.removeChildView(view); } catch (_) { }
    }
  }

  createTab(tabId, url) {
    const view = new WebContentsView({
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        javascript: true,
      }
    });
    this.tabs.set(tabId, view);

    try {
      view.webContents.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
      );
    } catch (e) {
      console.warn('[TabManager] Failed to set Chrome user agent:', e && e.message);
    }

    const CHROME_FP_PATCH =
      'try{Object.defineProperty(navigator,"userAgentData",{get:()=>({'
      + 'brands:[{brand:"Chromium",version:"138"},{brand:"Google Chrome",version:"138"},{brand:"Not)A;Brand",version:"99"}],'
      + 'mobile:false,platform:"Windows",'
      + 'getHighEntropyValues:()=>Promise.resolve({architecture:"x86",bitness:"64",model:"",platform:"Windows",platformVersion:"15.0.0",uaFullVersion:"138.0.0.0",fullVersionList:[{brand:"Chromium",version:"138.0.0.0"},{brand:"Google Chrome",version:"138.0.0.0"},{brand:"Not)A;Brand",version:"99.0.0.0"}]}),'
      + 'toJSON:function(){return{brands:this.brands,mobile:this.mobile,platform:this.platform}}'
      + ')})}catch(e){};'
      + 'try{Object.defineProperty(navigator,"vendor",{get:()=>"Google Inc."})}catch(e){};'
      + 'try{delete navigator.webdriver}catch(e){};'
      + 'try{Object.defineProperty(navigator,"webdriver",{get:()=>undefined})}catch(e){};';

    const notifyThrottled = () => { try { this._notifyTabs(); } catch (_) { } };

    view.webContents.on('did-finish-load', () => {
      this._tabRecovery.delete(tabId);
      view.webContents.executeJavaScript(CHROME_FP_PATCH).catch(() => { });
      setTimeout(notifyThrottled, 300);
    });
    view.webContents.on('did-navigate', notifyThrottled);
    view.webContents.on('did-navigate-in-page', notifyThrottled);
    view.webContents.on('dom-ready', () => {
      view.webContents.executeJavaScript(CHROME_FP_PATCH).catch(() => { });
    });
    view.webContents.on('page-title-updated', notifyThrottled);
    view.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const isF5 = input.key === 'F5' || input.code === 'F5';
      const isReload = input.control && (input.key.toLowerCase() === 'r' || input.code === 'KeyR');
      if (isF5 || isReload) {
        try { view.webContents.reload(); } catch (_) { }
        event.preventDefault();
      }
    });

    view.webContents.once('destroyed', () => {
      if (this.tabs.get(tabId) === view) {
        this.tabs.delete(tabId);
        this._tabRecovery.delete(tabId);
        this._recoveringTabs.delete(tabId);
        try { this.mainWindow.contentView.removeChildView(view); } catch (e) { }
        if (this.activeTabId === tabId) {
          const next = this.tabs.keys().next();
          this.activeTabId = next.done ? null : next.value;
        }
        this._syncViewState();
        this._notifyTabs();
      }
    });

    view.webContents.on('render-process-gone', (event, details) => {
      this.merr(`[TabCrash] tab=${tabId} render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
      const now = Date.now();
      let st = this._tabRecovery.get(tabId) || { count: 0, windowStart: 0 };
      if (now - st.windowStart > TabManager.TAB_RECOVERY_WINDOW_MS) {
        st = { count: 0, windowStart: now };
      }
      if (this._recoveringTabs.has(tabId)) {
        this.merr(`[TabCrash] tab=${tabId} recovery already in flight, skipping.`);
        return;
      }
      if (st.count >= TabManager.MAX_TAB_RECOVERY) {
        this.merr(`[TabCrash] tab=${tabId} recovery limit reached (${TabManager.MAX_TAB_RECOVERY} per ${TabManager.TAB_RECOVERY_WINDOW_MS / 1000}s) — NOT recreating to avoid crash loop.`);
        return;
      }
      st.count++;
      this._tabRecovery.set(tabId, st);
      this._recoveringTabs.add(tabId);
      this.mlog(`[TabCrash] tab=${tabId} auto-recovery attempt ${st.count}/${TabManager.MAX_TAB_RECOVERY} — recreating view.`);
      this._recreateTab(tabId);
      setTimeout(() => { this._recoveringTabs.delete(tabId); }, 5000);
    });

    view.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      if (!isMainFrame) return;
      if (errorCode === -3) {
        this.mlog(`[TabFail] tab=${tabId} aborted navigation ignored (code=-3) url=${validatedURL}`);
        return;
      }
      this.merr(`[TabFail] tab=${tabId} did-fail-load: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
      const now = Date.now();
      let st = this._tabRecovery.get(tabId) || { count: 0, windowStart: 0 };
      if (now - st.windowStart > TabManager.TAB_RECOVERY_WINDOW_MS) {
        st = { count: 0, windowStart: now };
      }
      if (st.count >= TabManager.MAX_TAB_RECOVERY) {
        this.merr(`[TabFail] tab=${tabId} retry limit reached — giving up.`);
        return;
      }
      st.count++;
      this._tabRecovery.set(tabId, st);
      const retryUrl = validatedURL || url;
      setTimeout(() => {
        try {
          if (!view.webContents.isDestroyed()) view.webContents.loadURL(retryUrl);
        } catch (_) { }
      }, 3000);
    });

    view.webContents.setWindowOpenHandler(({ url: newUrl }) => {
      if (newUrl && newUrl !== 'about:blank') {
        view.webContents.loadURL(newUrl);
      }
      return { action: 'deny' };
    });

    view.webContents.loadURL(url);
    return view;
  }

  _recreateTab(tabId) {
    const old = this.tabs.get(tabId);
    let url = 'about:blank';
    try { url = (old && old.webContents.getURL()) || url; } catch (_) { }
    if (old) {
      try { this.mainWindow.contentView.removeChildView(old); } catch (_) { }
      try { old.webContents.close(); } catch (_) { }
      this.tabs.delete(tabId);
    }
    this.mlog(`[TabCrash] tab=${tabId} recreating WebContentsView (url=${url})`);
    this.createTab(tabId, url);
    this._syncViewState();
    this._notifyTabs();
  }

  switchTab(tabId) {
    if (this.tabs.has(tabId)) {
      this.activeTabId = tabId;
      this._syncViewState();
      this._notifyTabs();
    }
  }

  closeTab(tabId) {
    const view = this.tabs.get(tabId);
    if (!view) return;
    try { this.mainWindow.contentView.removeChildView(view); } catch (_) { }
    try { view.webContents.close(); } catch (_) { }
    this.tabs.delete(tabId);
    this._tabRecovery.delete(tabId);
    this._recoveringTabs.delete(tabId);

    if (this.activeTabId === tabId) {
      const next = this.tabs.keys().next();
      this.activeTabId = next.done ? null : next.value;
    }
    this._syncViewState();
    this._notifyTabs();
  }

  async _notifyTabs() {
    const tabs = [];
    for (const [id, view] of this.tabs) {
      let title = id;
      let url = '';
      let thumbnail = '';
      try {
        if (view.webContents && !view.webContents.isDestroyed()) {
          title = view.webContents.getTitle() || id;
          url = view.webContents.getURL() || '';
          if (url && url !== 'about:blank' && !view.webContents.isLoading()) {
            try {
              const img = await view.webContents.capturePage();
              if (img && !img.isEmpty()) {
                const resized = img.resize({ width: 320 });
                const buf = resized.toJPEG(60);
                thumbnail = 'data:image/jpeg;base64,' + buf.toString('base64');
              }
            } catch (_) { }
          }
        }
      } catch (_) { }
      tabs.push({ id, title, url, active: id === this.activeTabId, thumbnail });
    }
    try {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send('browser-tabs-updated', tabs);
      }
    } catch (_) { }
  }

  _isWebContentsAlive(view) {
    try {
      return !!(view && view.webContents && !view.webContents.isDestroyed());
    } catch (_) {
      return false;
    }
  }

  navigate(tabId, url) {
    let view = this.tabs.get(tabId);
    if (view && !this._isWebContentsAlive(view)) {
      this.merr(`[TabManager] navigate: tab=${tabId} webContents destroyed — recreating view.`);
      try { this.mainWindow.contentView.removeChildView(view); } catch (_) { }
      this.tabs.delete(tabId);
      this._tabRecovery.delete(tabId);
      view = null;
    }
    if (!view) {
      view = this.createTab(tabId, url);
    } else {
      try {
        view.webContents.loadURL(url);
      } catch (e) {
        this.merr(`[TabManager] navigate: loadURL failed tab=${tabId}: ${e && e.message}`);
        return;
      }
    }
    this.activeTabId = tabId;
    this._syncViewState();
    this._notifyTabs();
  }

  setBounds(bounds) {
    if (bounds && typeof bounds.width === 'number' && typeof bounds.height === 'number') {
      this.bounds = {
        x: Math.max(0, Math.round(bounds.x || 0)),
        y: Math.max(0, Math.round(bounds.y || 0)),
        width: Math.max(0, Math.round(bounds.width || 0)),
        height: Math.max(0, Math.round(bounds.height || 0))
      };
    } else {
      this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    }
    this._syncViewState();
  }

  setVisibility(visible) {
    this.isVisible = !!visible;
    this._syncViewState();
  }

  resize() {
    this._syncViewState();
  }

  destroy() {
    if (this._notifyTimer) {
      clearInterval(this._notifyTimer);
      this._notifyTimer = null;
    }
    this._detachAllViews();
    for (const [_, view] of this.tabs) {
      try { view.webContents.close(); } catch (_) { }
    }
    this.tabs.clear();
  }
}

module.exports = {
  TabManager,
};

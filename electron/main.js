// @ts-check
/**
 * DAON Agent System - Electron Main Entrypoint
 * Refactored modular architecture (Phase 3: God Object Dismantling)
 */

console.log("[BUILD ID]: main-v6-modular-2026-09-08");

const { app, session } = require('electron');
const path = require('path');
const net = require('net');
const fs = require('fs');
const { execSync } = require('child_process');

if (net.setDefaultAutoSelectFamily) {
  net.setDefaultAutoSelectFamily(false);
}

// ── CDP 9222 Port Relaunch Guarantee ──
// Ensure remote-debugging-port switch lands on the real command line of the main process
const NEEDED_CDP_PORT = '9222';
app.commandLine.appendSwitch('remote-debugging-port', NEEDED_CDP_PORT);
app.commandLine.appendSwitch('remote-allow-origins', '*');
app.commandLine.appendSwitch('disable-blink-features', 'AutomationControlled');
app.commandLine.appendSwitch('disable-features', 'WebAuthentication');

try {
  app.userAgentFallback =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36';
} catch (e) {
  console.warn('[Electron] Failed to set userAgentFallback:', e && e.message);
}

if (!process.argv.some((a) => String(a).indexOf('--remote-debugging-port=') === 0)) {
  console.log('[CDP] --remote-debugging-port missing in argv — relaunching once to guarantee CDP 9222.');
  app.relaunch({
    args: process.argv.slice(1).concat(['--remote-debugging-port=' + NEEDED_CDP_PORT]),
  });
  app.exit(0);
}

// ── Single Instance Lock ──
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  process.exit(0);
}

// ── Internal Modules ──
const { mainLogInit, mlog, merr } = require('./src/Logger');
const { cleanupOrphanedTemp } = require('./src/TempCleaner');
const { attachChromeHeaderNormalization } = require('./src/HeaderNormalizer');
const { TrayManager } = require('./src/TrayManager');
const { ServerSupervisor } = require('./src/ServerSupervisor');
const { WindowManager } = require('./src/WindowManager');
const { TabManager } = require('./src/TabManager');
const { registerIpcHandlers } = require('./src/IpcHandlers');
const { createRestartOrchestrator } = require('./restart_orchestrator');
const { createSelfUpdate } = require('./self_update');

// ── Global State ──
const DEFAULT_PORT = 9090;
const TTS_PORT = 9091;
let isQuitting = false;

let supervisor = null;
let trayManager = null;
let windowManager = null;
let tabManager = null;
let restartOrchestrator = null;

// ── Build Root Resolution (packaged self-update) ──
// [근본 수정 2026-09-12] Phase 3 리팩터링(f4c1a73)에서 누락된 resolveBuildRoot 를
// 복원한다. 이전에는 `path.join(__dirname, '..')` 를 무검증으로 반환해,
// 패키징 앱에서는 __dirname 이 asar 내부 가상 경로(<앱>/resources/app.asar/electron)
// 이므로 buildRoot 가 <앱>/resources 로 해석되고 그곳에 daon-server.spec /
// _sync_build.py 가 없어 rebuild 가 조용히 거부됐다(2026-09-07 이후).
// 이제 후보를 순회하며 daon-server.spec 존재로 build root 를 검증한다.
//   - dev      : <repo>                       (__dirname = <repo>/electron)
//   - packaged : process.resourcesPath        (<앱>/resources — extraResources 위치)
function resolveBuildRoot() {
  const candidates = [
    process.env.DAON_BUILD_ROOT,
    process.resourcesPath,        // packaged: extraResources 들이 놓이는 실제 디렉터리
    path.join(__dirname, '..'),   // dev: 레포 루트
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      if (fs.existsSync(path.join(c, 'daon-server.spec'))) return c;
    } catch (_) { /* keep scanning */ }
  }
  return null;
}

// git 롤백은 실제 git 저장소(.git)가 있는 dev 트리에서만 유효하다.
// packaged 번들에는 .git 이 없으므로 null 을 반환하고 gitRollback 이 사전 차단된다.
function resolveRepoRoot() {
  const candidates = [
    process.env.DAON_REPO_ROOT,
    path.join(__dirname, '..'),
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      if (fs.existsSync(path.join(c, '.git'))) return c;
    } catch (_) { /* keep scanning */ }
  }
  return null;
}

// Handle second instance launch
app.on('second-instance', () => {
  if (windowManager && windowManager.mainWindow) {
    const win = windowManager.mainWindow;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  }
});

// ── App Ready Lifecycle ──
app.whenReady().then(async () => {
  mainLogInit();
  mlog('[BUILD] DAON Agent System Electron main initialized.');

  // Chrome Header normalization for WebAuthn / User-Agent consistency
  attachChromeHeaderNormalization(session.defaultSession, 'defaultSession');

  // Window Manager: Splash
  windowManager = new WindowManager({ mlog, merr });
  windowManager.createSplashWindow();

  // Clean orphaned temp files (_MEI* and playwright-artifacts-*).
  // Note: TempCleaner has a 5-second internal delay and actively skips in-use _MEI folders,
  // making early background execution safe. Port-specific zombie servers are killed below via killPortOwner.
  cleanupOrphanedTemp();

  // Server Supervisor
  supervisor = new ServerSupervisor({
    mlog,
    merr,
    onServerRestarted: () => {
      if (windowManager && windowManager.mainWindow && !windowManager.mainWindow.isDestroyed()) {
        try { windowManager.mainWindow.webContents.reload(); } catch (_) { }
        mlog('[Watchdog] mainWindow reloaded after server restart.');
      }
    }
  });

  // Tray Manager
  trayManager = new TrayManager({
    getMainWindow: () => windowManager && windowManager.mainWindow,
    getServerPort: () => DEFAULT_PORT,
    onQuit: () => {
      isQuitting = true;
      app.quit();
    }
  });

  try {
    // ── STEP 1: Check if an already-healthy server exists on port 9090 ──
    const existingServer = await supervisor.probeHealthStable(DEFAULT_PORT);

    if (existingServer && existingServer.healthy && existingServer.pid) {
      mlog(`[Startup] Reusing existing healthy server PID: ${existingServer.pid} on port ${DEFAULT_PORT}`);
      supervisor.adoptRunningServer(existingServer.pid, DEFAULT_PORT);
    } else {
      mlog(`[Startup] No healthy server on port ${DEFAULT_PORT}. Terminating port-owner process if any and starting fresh...`);
      // Targeted kill: only kill the process occupying DEFAULT_PORT (prevents killing unrelated server.exe processes)
      supervisor.killPortOwner(DEFAULT_PORT);

      supervisor.startPythonProcess(DEFAULT_PORT);

      // Wait for server health (180s timeout accommodates PyInstaller onefile _MEI extraction on slow machines)
      const healthy = await supervisor.checkServerHealth(DEFAULT_PORT, 180, 1000);
      if (!healthy) {
        throw new Error(`Server failed to become healthy on port ${DEFAULT_PORT}`);
      }
    }

    // ── STEP 2: Start TTS process ──
    supervisor.startTtsProcess(TTS_PORT);

    // ── STEP 3: Start Watchdog ──
    supervisor.startWatchdog(DEFAULT_PORT);

    // ── STEP 4: Start Self-Modify Restart Orchestrator (Gap E-3) ──
    // buildRoot(재빌드 대상: daon-server.spec 이 있는 곳)와 repoRoot(git 롤백용)
    // 를 분리한다. packaged 에서는 buildRoot=<앱>/resources, repoRoot=null.
    const buildRoot = resolveBuildRoot();
    const repoRoot = resolveRepoRoot();
    mlog(`[Startup] buildRoot=${buildRoot || '(none)'} repoRoot=${repoRoot || '(none)'}`);
    const selfUpdate = createSelfUpdate({
      log: mlog,
      errLog: merr,
      findTargetExe: () => supervisor.findServerExe(),
      resolveBuildRoot: () => buildRoot,
      probeHealth: (port) => supervisor.probeHealth(port, 1500),
      findFreePort: (port) => supervisor.findFreePort(port),
    });

    restartOrchestrator = createRestartOrchestrator({
      repoRoot,
      log: mlog,
      pollMs: 5000,
      settleMs: 800,
      killServer: async () => {
        supervisor.selfModifyRestartActive = true;
        supervisor.watchdogSuppressUntil = Date.now() + 4 * supervisor.WATCHDOG_INTERVAL;
        if (supervisor.pythonProcess && supervisor.pythonProcess.pid) {
          supervisor.killProcessTree(supervisor.pythonProcess.pid);
          supervisor.pythonProcess = null;
        }
        if (supervisor.ttsProcess && supervisor.ttsProcess.pid) {
          supervisor.killProcessTree(supervisor.ttsProcess.pid);
          supervisor.ttsProcess = null;
        }
      },
      spawnServer: async () => {
        supervisor.startPythonProcess(DEFAULT_PORT);
      },
      healthCheck: async () => {
        const h = await supervisor.probeHealthStable(DEFAULT_PORT);
        return !!(h && h.healthy);
      },
      deepHealthCheck: async () => {
        const h = await supervisor.probeHealthGrace(DEFAULT_PORT, {
          maxWaitMs: supervisor.POST_SWAP_HEALTH_GRACE_MS,
          stableHits: 3,
          isAlive: () => {
            if (!supervisor.pythonProcess) return false;
            if (supervisor.pythonProcess._adopted && supervisor.pythonProcess.pid) {
              return supervisor.isProcessAlive(supervisor.pythonProcess.pid);
            }
            return supervisor.pythonProcess.exitCode === null && supervisor.pythonProcess.signalCode === undefined;
          },
        });
        return !!(h && h.healthy);
      },
      rebuildAndSwap: selfUpdate.rebuildAndSwap,
      restoreBackup: selfUpdate.restoreBackup,
      gitRollback: async (ref) => {
        if (!repoRoot) {
          merr('[RestartOrch] git rollback unavailable — no git repo root (packaged build). Skipping.');
          return false;
        }
        try {
          execSync(`git reset --hard ${ref}`, { cwd: repoRoot, windowsHide: true, timeout: 30000 });
          execSync('git clean -fd', { cwd: repoRoot, windowsHide: true, timeout: 30000 });
          return true;
        } catch (e) {
          merr('[RestartOrch] git rollback error: ' + (e && e.message));
          return false;
        }
      },
      afterCycle: async (result) => {
        supervisor.selfModifyRestartActive = false;
        supervisor.watchdogRestartCount = 0;
        supervisor.watchdogSuppressUntil = Date.now() + 3 * supervisor.WATCHDOG_INTERVAL;
        mlog('[RestartOrch] cycle done: ' + JSON.stringify(result));
        try {
          if (!supervisor.ttsProcess) supervisor.startTtsProcess(TTS_PORT);
        } catch (e) {
          merr('[RestartOrch] TTS respawn failed: ' + (e && e.message));
        }
        if (windowManager && windowManager.mainWindow && !windowManager.mainWindow.isDestroyed()) {
          try { windowManager.mainWindow.webContents.reload(); } catch (_) { }
        }
      },
    });
    restartOrchestrator.start();

    // ── STEP 5: Create Main Window & TabManager ──
    const mainWindow = windowManager.createMainWindow({
      serverPort: DEFAULT_PORT,
      supervisor,
      getTabManager: () => tabManager,
      getTray: () => trayManager && trayManager.tray,
      isQuittingGetter: () => isQuitting,
    });

    tabManager = new TabManager({
      mainWindow,
      mlog,
      merr,
    });

    // ── STEP 6: Register IPC Handlers ──
    registerIpcHandlers({
      tabManager,
      supervisor,
      mainWindow,
      merr,
      mlog,
    });

    // ── STEP 7: Create Tray & Load UI ──
    trayManager.createTray();
    await windowManager.loadAppUi(DEFAULT_PORT);

  } catch (err) {
    merr('[Electron Startup Error]', err);
    try {
      const { dialog } = require('electron');
      dialog.showErrorBox('Startup Error', 'Failed to start DAON Agent System:\n' + err.message);
    } catch (_) { }
    if (windowManager) windowManager.closeSplashWindow();
    app.quit();
  }
});

// ── App Shutdown ──
app.on('before-quit', () => {
  isQuitting = true;
  if (restartOrchestrator) {
    try { restartOrchestrator.stop(); } catch (_) { }
  }
  if (supervisor) {
    supervisor.shutdown();
  }
  if (tabManager) {
    tabManager.destroy();
  }
  if (trayManager) {
    trayManager.destroy();
  }
  setTimeout(() => {
    try { cleanupOrphanedTemp(); } catch (_) { }
  }, 1500);
});

app.on('window-all-closed', () => {
  if (trayManager && trayManager.tray && !isQuitting) return;
  if (process.platform !== 'darwin') app.quit();
});

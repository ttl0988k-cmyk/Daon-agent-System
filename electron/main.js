console.log("[BUILD ID]: main-v5-2026-08-03-22:50");
console.log("[BUILD ID]: watchdog-fix-v3-2026-07-25-17:28");
console.log("[BUILD ID]: restore-aug3-browser-2026-08-14-23:35");
const { app, BrowserWindow, BaseWindow, WebContentsView, ipcMain, screen, shell, powerMonitor, session, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, exec, execSync } = require('child_process');
const http = require('http');
const net = require('net');
if (net.setDefaultAutoSelectFamily) { net.setDefaultAutoSelectFamily(false); }
const os = require('os');
// ── Gap E-3: 자기 수정 부트스트랩 (서버 재시작 오케스트레이션) ──
// 서버는 재시작 요청 파일만 기록하고(restart_request.py), 실제 kill/재시작/
// 헬스체크/롤백은 감시자인 일렉트론 메인 측 오케스트레이터가 수행한다.
const { createRestartOrchestrator } = require('./restart_orchestrator');

// ── Electron main 로그 파일화 (서버 사망 원인 추적용) ──
// 기존엔 main process의 console.log/console.error가 어떤 파일에도 저장되지 않아,
// 서버가 왜 죽는지(exit code / watchdog / taskkill) 전혀 추적할 수 없었다.
// userData/daon-main.log 로 append 하여 다음 사망 시 정확한 원인을 확정한다.
// 경로: %APPDATA%\daon-agent-system\daon-main.log
let _mainLogStream = null;
function _mainLogInit() {
  try {
    const logPath = path.join(app.getPath('userData'), 'daon-main.log');
    _mainLogStream = fs.createWriteStream(logPath, { flags: 'a' });
    _mainLogStream.write(`\n===== Electron main started ${new Date().toISOString()} (pid=${process.pid}) =====\n`);
  } catch (_) { }
}
function mlog(...args) {
  try {
    const line = `[${new Date().toISOString()}] ` + args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
    console.log(line);
    if (_mainLogStream) _mainLogStream.write(line + '\n');
  } catch (_) { }
}
function merr(...args) {
  try {
    const line = `[${new Date().toISOString()}] [ERR] ` + args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
    console.error(line);
    if (_mainLogStream) _mainLogStream.write(line + '\n');
  } catch (_) { }
}

// ── Temp Folder Cleanup (PyInstaller _MEI* orphan prevention) ──
// PyInstaller onefile mode extracts ~1.76GB to %TEMP%\_MEIxxxxx on every launch.
// If the app crashes or is force-killed, these folders are never cleaned up.
// This function removes orphaned _MEI* and playwright-artifacts-* folders.
// Runs asynchronously so it NEVER blocks app startup.
function cleanupOrphanedTemp() {
  setTimeout(() => {
    const tempDir = process.env.TEMP || os.tmpdir();
    let entries;
    try {
      entries = fs.readdirSync(tempDir);
    } catch (e) {
      console.warn('[Cleanup] Cannot read temp dir:', e.message);
      return;
    }

    // Find which _MEI folders are currently in use by running server.exe processes
    let activeMEIs = new Set();
    try {
      const wmicOut = execSync(
        'wmic process where "name=\'server.exe\'" get ExecutablePath /FORMAT:LIST 2>nul',
        { windowsHide: true, encoding: 'utf-8', timeout: 5000 }
      );
      for (const line of wmicOut.split('\n')) {
        const match = line.match(/(_MEI\d+)/i);
        if (match) activeMEIs.add(match[1]);
      }
    } catch (_) { }

    let freedCount = 0;

    for (const entry of entries) {
      const isMEI = entry.startsWith('_MEI');
      const isPlaywright = entry.startsWith('playwright-artifacts-');
      if (!isMEI && !isPlaywright) continue;

      if (isMEI && activeMEIs.has(entry)) continue;

      const fullPath = path.join(tempDir, entry);
      try {
        const stat = fs.statSync(fullPath);
        if (!stat.isDirectory()) continue;
        fs.rmSync(fullPath, { recursive: true, force: true });
        freedCount++;
      } catch (_) { }
    }

    if (freedCount > 0) {
      console.log(`[Cleanup] Removed ${freedCount} orphaned temp folder(s)`);
    }
  }, 5000); // Run 5 seconds after app starts — never block startup
}

// ── CDP (Chrome DevTools Protocol) port for browser automation ──
// 8월 3일 정상 빌드 복원: CDP 9222는 앱 시작 시 항상 ON (무조건 appendSwitch).
// 에이전트(browser_*)는 connect_over_cdp("http://127.0.0.1:9222")로
// 사용자와 같은 WebContentsView(세션/로그인 상태)를 공유·제어한다.
// 127.0.0.1 명시: Windows가 localhost를 IPv6(::1)로 우선 해석해 CDP 리스너
// (IPv4)와 불일치하면 ECONNREFUSED ::1:9222 로 연결이 실패하므로 피한다.
const NEEDED_CDP_PORT = '9222';
app.commandLine.appendSwitch('remote-debugging-port', NEEDED_CDP_PORT);
app.commandLine.appendSwitch('remote-allow-origins', '*');

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
let serverPort = 9090;  // Updated to match DEFAULT_PORT; was 8000 which caused confusion
let ttsPort = 9091;
let watchdogTimer = null;
let watchdogSuppressUntil = 0;  // F5 reload 후 일시적으로 watchdog 실패 감지 보류
let isQuitting = false;
let selfModifyRestartActive = false;  // E-3: 오케스트레이션 재시작 진행 중 (exit/watchdog 자동재시작 억제)
let restartOrchestrator = null;
let tray = null;
let trayStatusTimer = null;
let splashWindow = null;

// ── 8월 3일 정상 빌드 복원 ──
// 모듈 스코프 전역 'browser-window-created' 가드는 제거했다. 이 가드는 CDP가
// 만든 BrowserWindow를 destroy 했는데, 구글 OAuth 팝업/리다이렉트 창까지 함께
// 파괴해 로그인을 끊었다(384e8fb/06e1c87 에서 도입 후 로그인 차단 확인).
// 백업(로그인 정상)은 전역 창 가드 없이, (a) 뷰 단위 setWindowOpenHandler로
// target=_blank/window.open 을 같은 뷰 내비게이션으로 흡수하고, (b) 백엔드
// browser_routes.py 가 Electron 모드에서 new_page()를 절대 호출하지 않아
// 에이전트가 외부 창을 만들지 않도록 했다. 이 두 가지를 아래에서 복원한다.

// ── Always-on: 트레이 아이콘 경로 해석 (dev / packaged 둘 다 지원) ──
function findTrayIcon() {
  const candidates = [
    path.join(process.resourcesPath, 'static', 'favicon.png'),
    path.join(process.resourcesPath, 'favicon.png'),
    path.join(__dirname, '..', 'static', 'favicon.png'),
    path.join(__dirname, '..', 'dist_new', 'static', 'favicon.png'),
  ];
  for (const p of candidates) {
    try { if (fs.existsSync(p)) return p; } catch (_) { }
  }
  return null;
}

// ── Always-on ⑦ 관측성: 트레이 상태 표시 ──
// /api/system/status를 폴링해 트레이 툴팁/메뉴에 서버·워커·기억·큐 상태를 표시한다.
function buildTrayMenu(status) {
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
      isQuitting = true;
      app.quit();
    }
  });
  return Menu.buildFromTemplate(items);
}

function refreshTrayStatus() {
  if (!tray) return;
  const req = http.get({ host: '127.0.0.1', port: serverPort, path: '/api/system/status', family: 4 }, (res) => {
    let body = '';
    res.on('data', (c) => { body += c; });
    res.on('end', () => {
      try {
        const status = JSON.parse(body);
        tray.setContextMenu(buildTrayMenu(status));
        if (status && status.ok) {
          const q = status.queue || {};
          tray.setToolTip(`DAON — 서버정상 | 큐 대기:${q.pending || 0} 실패:${q.failed || 0}`);
        } else {
          tray.setToolTip('DAON — 서버 오류');
        }
      } catch (_) {
        tray.setContextMenu(buildTrayMenu(null));
        tray.setToolTip('DAON — 상태 파싱 실패');
      }
    });
  });
  req.on('error', () => {
    try {
      tray.setContextMenu(buildTrayMenu(null));
      tray.setToolTip('DAON — 서버 응답 없음');
    } catch (_) { }
  });
  // 4초: 서버의 일시적 지연(예: GIL 경합)에 여유를 준다. 진짜 죽은 서버
  // (connection refused)는 즉시 실패하므로 감지 속도는 그대로 유지된다.
  // 기존 2초는 서버가 잠시 느려졌을 뿐인데 '서버 응답 없음'으로 오판했다.
  req.setTimeout(4000, () => req.destroy());
}

function startTrayStatusPolling() {
  if (trayStatusTimer) return;
  refreshTrayStatus();
  trayStatusTimer = setInterval(refreshTrayStatus, 10000);
}

function createTray() {
  if (tray) return;
  try {
    const iconPath = findTrayIcon();
    // 아이콘이 없으면 16x16 빈 이미지로라도 트레이 생성 (기능 유지)
    const image = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
    tray = new Tray(image.isEmpty() ? image : image.resize({ width: 16, height: 16 }));
    tray.setToolTip('DAON Agent System');
    tray.setContextMenu(buildTrayMenu(null));
    tray.on('double-click', () => {
      if (mainWindow) {
        if (!mainWindow.isVisible()) mainWindow.show();
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.focus();
      }
    });
    console.log('[Tray] Always-on tray icon created.');
    startTrayStatusPolling();
  } catch (e) {
    console.warn('[Tray] Failed to create tray icon (non-fatal):', e.message);
  }
}

// NOTE: CDP relaunch logic now lives at the top of this file (before the
// single instance lock) so the --remote-debugging-port switch lands on the
// real command line of the main (browser) process — appendSwitch alone is not
// reliable in packaged builds and only leaked the flag onto renderers.

// --- Helper: Find Free Port ---
function findFreePort(startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(startPort, '127.0.0.1', () => {
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

// --- Helper: Check Server Health ---
function checkServerHealth(port, retries = 180, delayMs = 1000) {
  return new Promise((resolve, reject) => {
    let attempted = 0;
    const poll = () => {
      attempted++;
      const req = http.get({ host: '127.0.0.1', port: port, path: '/health', family: 4 }, (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else if (attempted >= retries) {
          reject(new Error(`Server returned status code ${res.statusCode}`));
        } else {
          setTimeout(poll, delayMs);
        }
      });
      req.on('error', (err) => {
        if (attempted >= retries) {
          reject(err);
        } else {
          setTimeout(poll, delayMs);
        }
      });
      req.setTimeout(1000, () => {
        req.destroy();
      });
    };
    poll();
  });
}

// --- Helper: Probe Server Health (returns parsed JSON body or null) ---
// Used to detect an already-running healthy server so the app can REUSE it
// instead of killing it on every restart (fixes server dying after app reopen).
function probeServerHealth(port, timeoutMs = 1000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    const req = http.get({ host: '127.0.0.1', port: port, path: '/health', family: 4 }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => {
        try {
          done(JSON.parse(body));
        } catch (_) {
          done(null);
        }
      });
    });
    req.on('error', () => done(null));
    req.setTimeout(timeoutMs, () => { req.destroy(); done(null); });
  });
}

// [stability] 안전 재사용 판정: 1회 probe 실패(오탐 가능성)는 곧바로
// '재사용 불가'로 확정하지 않고, 짧은 재시도를 거쳐 진짜 죽었는지 확인한다.
// 기존엔 probe 1회 실패 → taskkill /F /IM server.exe 로 healthy 서버까지 죽였다.
async function probeServerHealthStable(port) {
  const first = await probeServerHealth(port, 1500);
  if (first && first.healthy && first.pid) return first;
  // 1차 실패 — 500ms 후 2차 시도 (진짜 다운인지 일시 지연인지 구분)
  await new Promise(r => setTimeout(r, 500));
  const second = await probeServerHealth(port, 2000);
  if (second && second.healthy && second.pid) return second;
  return null;
}

function checkCdpHealth(retries = 10, delayMs = 500) {
  let attempted = 0;
  const poll = () => {
    attempted++;
    const req = http.get(`http://127.0.0.1:9222/json/version`, (res) => {
      if (res.statusCode === 200) {
        console.log('[Electron] CDP port 9222 is active and ready for Playwright!');
      } else if (attempted < retries) {
        setTimeout(poll, delayMs);
      }
    });
    req.on('error', () => {
      if (attempted < retries) setTimeout(poll, delayMs);
      else console.warn('[Electron] CDP port 9222 non-responsive (browser automation tools may fail).');
    });
    req.setTimeout(1000, () => req.destroy());
  };
  poll();
}

function killProcessTree(pid) {
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      // /T (Tree), /F (Force) 동기 실행하여 자식 프로세스까지 완전히 kill 완료 후 진행
      execSync(`taskkill /pid ${pid} /T /F 2>nul`, { windowsHide: true });
      console.log(`[Cleanup] Successfully killed process tree for PID: ${pid}`);
    } else {
      process.kill(-pid, 'SIGKILL');
    }
  } catch (e) {
    // 이미 종료되었거나 존재하지 않는 프로세스인 경우 무시
  }
}

// ── Helper: Unified Server EXE Path Resolution ──
function findServerExe() {
  const candidates = [
    path.join(process.resourcesPath, 'server.exe'),
    path.join(process.resourcesPath, 'server', 'server.exe'),
    path.join(path.dirname(process.execPath), 'server.exe'),
    path.join(__dirname, '..', 'dist', 'server.exe'),
    path.join(__dirname, '..', 'server.exe'),
    path.join(__dirname, 'server.exe'),
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }

  return null;
}

// ── Process Spawning & Auto-Restart Safety Net ──

function startPythonProcess(port) {
  if (isQuitting) return;
  // 127.0.0.1 명시: IPv6(::1) 우선 해석으로 인한 ECONNREFUSED ::1:9222 방지
  // (browser_routes.py 의 connect_over_cdp 와 동일한 주소를 사용해야 한다).
  const env = { ...process.env, BROWSER_CDP_URL: 'ws://127.0.0.1:9222' };
  const exePath = findServerExe();

  if (exePath) {
    console.log(`[Electron] Spawning server executable: ${exePath}`);
    const cwd = path.dirname(exePath);
    pythonProcess = spawn(exePath, ['--no-browser', '--port', port.toString()], {
      cwd,
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  } else {
    console.log(`[Electron] server.exe not found. Falling back to python server.py...`);
    pythonProcess = spawn('python', ['server.py', '--no-browser', '--port', port.toString()], {
      cwd: path.join(__dirname, '..'),
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  }

  const serverLogPath = path.join(app.getPath('userData'), 'server.log');
  const serverLogStream = fs.createWriteStream(serverLogPath, { flags: 'a' });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python]: ${data}`);
    try { serverLogStream.write(`[STDOUT] ${data}`); } catch (_) { }
  });
  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python Error]: ${data}`);
    try { serverLogStream.write(`[STDERR] ${data}`); } catch (_) { }
  });

  // Safety Net: If Python crashes or exits unexpectedly, auto-restart immediately
  pythonProcess.on('exit', (code, signal) => {
    const msg = `[Electron] Main Python server exited (code=${code}, signal=${signal}, isQuitting=${isQuitting})`;
    console.warn(msg);
    pythonProcess = null;
    if (!isQuitting) {
      if (selfModifyRestartActive) {
        // E-3: 오케스트레이터가 재시작을 직접 관리 — 이중 스폰 방지
        console.log('[Electron] Server exit during self-modify restart — orchestrator owns respawn.');
      } else {
        console.log('[Electron] Server exit detected — Auto-restarting Python server in 2s...');
        setTimeout(() => {
          if (!isQuitting && !pythonProcess) {
            startPythonProcess(port);
          }
        }, 2000);
      }
    }
  });

  // Boost main server CPU priority so faster-whisper inference isn't starved
  if (process.platform === 'win32' && pythonProcess.pid) {
    try {
      exec(`powershell -NoProfile -Command "(Get-Process -Id ${pythonProcess.pid}).PriorityClass = 'AboveNormal'"`, { windowsHide: true });
      console.log(`[Electron] Main server (pid=${pythonProcess.pid}) priority → AboveNormal`);
    } catch (_) { }
  }
}

function startTtsProcess(port) {
  if (isQuitting) return;
  const isPackaged = app.isPackaged;

  if (isPackaged) {
    const ttsExePath = findServerExe();
    const cwd = path.dirname(ttsExePath);
    ttsProcess = spawn(ttsExePath, ['--tts-mode', '--tts-port', port.toString()], {
      cwd,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  } else {
    ttsProcess = spawn('python', ['server.py', '--tts-mode', '--tts-port', port.toString()], {
      cwd: path.join(__dirname, '..'),
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    });
  }

  ttsProcess.stdout.on('data', (data) => console.log(`[TTS]: ${data}`));
  ttsProcess.stderr.on('data', (data) => console.error(`[TTS Error]: ${data}`));

  // Safety Net: Auto-restart TTS server if it crashes
  ttsProcess.on('exit', (code, signal) => {
    console.warn(`[Electron] TTS server exited (code=${code}, signal=${signal})`);
    ttsProcess = null;
    if (!isQuitting) {
      console.log('[Electron] TTS exit detected — Auto-restarting TTS server in 2s...');
      setTimeout(() => {
        if (!isQuitting && !ttsProcess) {
          startTtsProcess(port);
        }
      }, 2000);
    }
  });

  if (process.platform === 'win32' && ttsProcess.pid) {
    try {
      exec(`powershell -NoProfile -Command "(Get-Process -Id ${ttsProcess.pid}).PriorityClass = 'BelowNormal'"`, { windowsHide: true });
      console.log(`[Electron] TTS server (pid=${ttsProcess.pid}) priority → BelowNormal`);
    } catch (_) { }
  }
}

// --- Watchdog: Periodic health check + auto-restart if server dies ---
let watchdogRestartCount = 0;
const WATCHDOG_INTERVAL = 30_000;
const MAX_RESTARTS = 3;

// ── Renderer recovery guards: crash→reload→crash 무한 루프 방지 ──
// render-process-gone / did-fail-load 자동 복구는 횟수 제한 + 시간창 +
// "이미 복구 중" 플래그로 보호한다. 성공 로드(did-finish-load) 시 리셋.
let _rendererRecoveryCount = 0;
let _rendererRecoveryWindowStart = 0;
let _rendererRecovering = false;
const MAX_RENDERER_RECOVERY = 3;
const RENDERER_RECOVERY_WINDOW_MS = 60000;
let _failLoadRetryCount = 0;
let _failLoadWindowStart = 0;
const MAX_FAILLOAD_RETRY = 3;
const FAILLOAD_WINDOW_MS = 60000;
// watchdog 재시작 후 reload 중복 방지 (연속 failure 시 여러 reload가 겹치지 않게)
let _watchdogReloadPending = false;

async function handleWatchdogFailure(port) {
  // F5 reload 직후 보류 구간: 일시적 과부하로 오탐할 수 있으므로 카운트하지 않음
  if (Date.now() < watchdogSuppressUntil) {
    return;
  }
  watchdogRestartCount++;
  merr(`[Watchdog] Health check failure (${watchdogRestartCount}/${MAX_RESTARTS})`);
  if (watchdogRestartCount < MAX_RESTARTS) {
    return;
  }
  // [stability] 3회 연속 실패해도 즉시 kill하지 않고, 실제 서버 생존 여부를 2차 확인한다.
  // watchdog probe 오탐(일시 과부하/타임아웃)으로 healthy 서버를 taskkill로 죽이는 사고를 차단.
  const confirm = await probeServerHealthStable(port);
  if (confirm && confirm.healthy && confirm.pid) {
    mlog(`[Watchdog] ${MAX_RESTARTS} failures but server is ALIVE (pid=${confirm.pid}) — false positive, resetting counter (no kill).`);
    watchdogRestartCount = 0;
    return;
  }
  watchdogRestartCount = 0;
  merr(`[Watchdog] Confirmed server dead after ${MAX_RESTARTS} consecutive checks. Restarting Python server...`);
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
    pythonProcess = null;
  }
  startPythonProcess(port);
  // After server restart, reload mainWindow once server is healthy again.
  // Without this, mainWindow stays white (old dead page) after watchdog restart.
  // NOTE: this runs ONLY on the confirmed-dead path (3 consecutive failures +
  // secondary probe). Normal/intentional restarts go through
  // restartOrchestrator.afterCycle which does its own reload — so this does
  // NOT blow away the UI on every routine restart.
  if (_watchdogReloadPending) return;
  _watchdogReloadPending = true;
  checkServerHealth(port, 30, 2000).then(() => {
    _watchdogReloadPending = false;
    if (mainWindow && !mainWindow.isDestroyed()) {
      try { mainWindow.webContents.reload(); } catch (_) { }
      mlog('[Watchdog] mainWindow reloaded after server restart.');
    }
  }).catch(() => { _watchdogReloadPending = false; });
}

function startWatchdog(port) {
  if (watchdogTimer) return;
  mlog('[Watchdog] Starting health monitor (every 30s)...');
  watchdogRestartCount = 0;

  if (powerMonitor) {
    powerMonitor.on('resume', () => {
      mlog('[Electron] System resumed from sleep/idle - resetting watchdog...');
      watchdogRestartCount = 0;
      checkServerHealth(port, 10).catch(() => {
        merr('[Watchdog] Post-resume health check failed - re-verifying before kill...');
        probeServerHealthStable(port).then((confirm) => {
          if (confirm && confirm.healthy && confirm.pid) {
            mlog(`[Watchdog] Post-resume false positive — server alive (pid=${confirm.pid}), no kill.`);
            return;
          }
          merr('[Watchdog] Confirmed server dead after resume - restarting.');
          if (pythonProcess && pythonProcess.pid) {
            killProcessTree(pythonProcess.pid);
            pythonProcess = null;
          }
          startPythonProcess(port);
        });
      });
    });
  }

  watchdogTimer = setInterval(() => {
    if (isQuitting) return;

    const req = http.get({ host: '127.0.0.1', port: port, path: '/health', family: 4 }, (res) => {
      if (res.statusCode === 200) {
        watchdogRestartCount = 0;
      } else {
        merr(`[Watchdog] Health check non-200: ${res.statusCode}`);
        handleWatchdogFailure(port);
      }
    });
    req.on('error', (err) => {
      merr(`[Watchdog] Health check request failed: ${err.message}`);
      handleWatchdogFailure(port);
    });
    req.setTimeout(5000, () => {
      req.destroy();
      merr('[Watchdog] Health check timed out');
      handleWatchdogFailure(port);
    });
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
  _mainLogInit();
  mlog('[BUILD] electron main ready');

  // ── Always-on: 로그인 자동 시작은 사용하지 않는다 (대표님 지시) ──
  // 과거엔 openAtLogin:true 로 앱이 켜질 때마다 레지스트리 Run 키를 재등록했다.
  // 그 결과 작업관리자에서 시작프로그램을 지워도 부활하고, 부팅 시 중복 인스턴스가
  // 뜨며 "연결 프로그램 선택" 팝업/유령 트레이가 발생했다.
  // 이제는 (1) 자동 시작을 등록하지 않고, (2) 남아있던 등록도 명시적으로 해제한다.
  // 실행은 바탕화면 바로가기 하나로만 한다. 트레이 상주/창닫기 최소화는 그대로 유지.
  try {
    app.setLoginItemSettings({ openAtLogin: false, openAsHidden: false });
    console.log('[AlwaysOn] Login item unregistered (openAtLogin=false) — manual launch only.');
  } catch (e) {
    console.warn('[AlwaysOn] setLoginItemSettings failed (non-fatal):', e.message);
  }

  // ── Always-on: 트레이 아이콘 생성 (서버 백그라운드 상주) ──
  createTray();

  // ── STEP 0: Show splash window safely on ready-to-show without any white/black blank flash ──
  splashWindow = new BrowserWindow({
    width: 420,
    height: 340,
    frame: false,
    transparent: false,
    resizable: false,
    center: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: '#0a0a0f',
    show: false,  // Prevents blank white/black window flash completely!
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    }
  });
  // Splash is a static loader page — never allow it to spawn popup windows.
  splashWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.show();
      try { splashWindow.focus(); } catch (_) { }
    }
  });
  try {
    // ── STEP 1: Run cleanup & cache clear in PARALLEL (not sequentially) ──
    const DEFAULT_PORT = 9090;
    const myPid = process.pid;

    // Clear HTTP cache
    try {
      if (session && session.defaultSession) {
        await session.defaultSession.clearCache();
        console.log('[Electron] HTTP session cache cleared.');
      }
    } catch (e) {
      console.warn('[Electron] Cache clear failed:', e);
    }

    // ── STEP 1: Reuse an already-healthy server on the default port if present ──
    // Fix: we previously ran `taskkill /F /IM server.exe /T` unconditionally here,
    // which killed a healthy long-running server (live sessions / harness state)
    // on every app restart. Now we probe port 9090 first and reuse a healthy server.
    let reusedServer = false;
    // [stability] 1회 probe 오탐으로 healthy 서버를 taskkill 로 죽이지 않도록,
    // 안정 판정(1차 실패 시 2차 재시도)을 거친 뒤에만 '재사용 불가'로 확정한다.
    const healthProbe = await probeServerHealthStable(DEFAULT_PORT);
    if (healthProbe && healthProbe.healthy && healthProbe.pid) {
      reusedServer = true;
      mlog(`[Electron] Reusing healthy main server on ${DEFAULT_PORT} (pid=${healthProbe.pid}, uptime=${healthProbe.uptime_display || '?'}) — skipping taskkill & spawn.`);
      // Adopt the running server so quit / watchdog cleanup can still target it.
      pythonProcess = { pid: healthProbe.pid, _adopted: true };
    } else {
      merr(`[Electron] No healthy server to reuse on ${DEFAULT_PORT} — will taskkill + spawn fresh. probe=`, healthProbe);
    }

    if (!reusedServer) {
      // Kill old server processes synchronously BEFORE spawning new server
      try {
        if (process.platform === 'win32') {
          execSync('taskkill /F /IM server.exe /T 2>nul', { windowsHide: true });
        }
      } catch (_) { }
    }

    // ── STEP 1b: Cleanup orphaned PyInstaller _MEI* temp folders ──
    // This runs AFTER server processes are killed so their _MEI folders are unlocked.
    try {
      cleanupOrphanedTemp();
    } catch (e) {
      console.warn('[Cleanup] Temp cleanup failed (non-fatal):', e.message);
    }

    // Brief pause for processes to fully terminate
    await new Promise(r => setTimeout(r, 500));

    // ── STEP 2: Start Main Python Server (unless a healthy server was reused) ──
    serverPort = DEFAULT_PORT;
    if (!reusedServer) {
      console.log(`[Electron] Using default port: ${serverPort}`);
      startPythonProcess(serverPort);
    } else {
      console.log(`[Electron] Using existing server on port ${serverPort} (adopted — not respawned).`);
    }

    // ── STEP 3: Wait for server health (splash is showing during this wait) ──
    console.log(`[Electron] Waiting for server on port ${serverPort}...`);
    await checkServerHealth(serverPort, 180, 1000);
    console.log(`[Electron] Main server is ready!`);

    // ── STEP 3a: CDP 9222는 앱 시작부터 항상 ON (8월 3일 정상 빌드 복원) ──
    // 온디맨드 relaunch 폴링(restart-for-cdp.flag)은 제거했다. 백업은 앱 시작
    // 시 appendSwitch 로 9222를 무조건 열었고, 그 상태에서 구글 로그인과
    // 에이전트 공유가 모두 정상 동작했다.

    // ── STEP 3b: Start TTS Server (reuse if healthy, else spawn non-blocking) ──
    let reusedTts = false;
    const ttsHealthProbe = await probeServerHealth(ttsPort, 800);
    if (ttsHealthProbe && ttsHealthProbe.healthy && ttsHealthProbe.pid) {
      reusedTts = true;
      console.log(`[Electron] Reusing healthy TTS server on ${ttsPort} (pid=${ttsHealthProbe.pid})`);
      ttsProcess = { pid: ttsHealthProbe.pid, _adopted: true };
    }
    if (!reusedTts) {
      startTtsProcess(ttsPort);
      checkServerHealth(ttsPort, 30).then(() => {
        console.log(`[Electron] TTS server is ready!`);
      }).catch((err) => {
        console.error(`[Electron] TTS server health check failed (app will continue): ${err.message}`);
      });
    } else {
      console.log(`[Electron] TTS server adopted — health check skipped.`);
    }

    // ── STEP 3c: Verify CDP port 9222 ──
    checkCdpHealth(10);

    // ── STEP 3d: Start Watchdog ──
    startWatchdog(serverPort);

    // ── STEP 3e (Gap E-3): self-modify restart orchestrator ──
    // 서버가 STATE_DIR/restart-request.json 을 기록하면 여기서 감지해
    // kill -> 재시작 -> 헬스체크 -> 실패 시 git 롤백 후 재기동한다.
    const repoRoot = path.join(__dirname, '..');
    restartOrchestrator = createRestartOrchestrator({
      repoRoot,
      log: mlog,
      pollMs: 5000,
      settleMs: 800,
      killServer: async () => {
        selfModifyRestartActive = true;
        // 재시작 구간 전체를 watchdog 오탐에서 제외
        watchdogSuppressUntil = Date.now() + 4 * WATCHDOG_INTERVAL;
        if (pythonProcess && pythonProcess.pid) {
          killProcessTree(pythonProcess.pid);
          pythonProcess = null;
        }
      },
      spawnServer: async () => {
        startPythonProcess(serverPort);
      },
      healthCheck: async () => {
        const h = await probeServerHealthStable(serverPort);
        return !!(h && h.healthy);
      },
      gitRollback: async (ref) => {
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
        selfModifyRestartActive = false;
        watchdogRestartCount = 0;
        watchdogSuppressUntil = Date.now() + 3 * WATCHDOG_INTERVAL;
        mlog('[RestartOrch] cycle done: ' + JSON.stringify(result));
        if (mainWindow && !mainWindow.isDestroyed()) {
          try { mainWindow.webContents.reload(); } catch (_) { }
        }
      },
    });
    restartOrchestrator.start();

    console.log(`[Electron] All services ready — Launching UI...`);

    // ── STEP 4: Create Main Window (using BrowserWindow for stability) ──
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    mainWindow = new BrowserWindow({
      width: Math.floor(width * 0.8),
      height: Math.floor(height * 0.8),
      show: true,
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    mainWindow.center();
    mainWindow.setMenu(null);

    // ── STEP 4a: 8월 3일 정상 빌드 복원 ──
    // mainWindow 의 setWindowOpenHandler(deny) + 광범위한 will-navigate 차단 가드는
    // 제거했다. 이 가드들은 구글 OAuth 팝업/리다이렉트 창을 차단해 로그인
    // 흐름을 끊었다(06e1c87 도입). 백업은 mainWindow 에 창 가드를 두지 않았고,
    // 팝업 억제는 브라우저 뷰 단위의 setWindowOpenHandler(TabManager.createTab)로만
    // 수행했다. 에이전트의 외부 창 생성은 백엔드(browser_routes.py)에서 차단한다.

    // ── STEP 4a-2: mainWindow 보호 가드 (에이전트가 메인 윈도우를 외부 URL로
    //   바꾸는 것을 방지) ──
    // mainWindow(UI)는 반드시 로컬 UI(127.0.0.1 / localhost / file://)만 표시한다.
    // 에이전트(browser_*)는 CDP로 WebContentsView(내부 공유 브라우저)만 제어하므로
    // mainWindow 가 외부 URL로 navigate 되는 일은 절대 없어야 한다.
    // 구글 OAuth 로그인은 내부 브라우저 뷰(WebContentsView)에서 일어나므로 이
    // 가드는 로그인 흐름과 무관하다 — 로컬 UI 호스트만 허용해 OAuth를 건드리지
    // 않는다(06e1c87 처럼 광범위하게 막지 않음).
    try {
      mainWindow.webContents.on('will-navigate', (event, url) => {
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
          console.log(`[Guard] Blocked mainWindow navigate to external URL: ${url}`);
        }
      });
    } catch (e) {
      console.warn('[Guard] mainWindow will-navigate guard setup failed:', e && e.message);
    }

    // ── STEP 4a-3: Renderer crash/failure detection & auto-recovery ──
    // Without these handlers, a renderer crash or load failure leaves
    // mainWindow as a permanent white screen with no log evidence.
    try {
      mainWindow.webContents.on('render-process-gone', (event, details) => {
        merr(`[RendererCrash] render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
        // Auto-reload with loop protection: max N recoveries per time window,
        // and never start a second recovery while one is in flight.
        const now = Date.now();
        if (now - _rendererRecoveryWindowStart > RENDERER_RECOVERY_WINDOW_MS) {
          _rendererRecoveryCount = 0;
          _rendererRecoveryWindowStart = now;
        }
        if (_rendererRecovering) {
          merr('[RendererCrash] recovery already in flight, skipping reload.');
          return;
        }
        if (_rendererRecoveryCount >= MAX_RENDERER_RECOVERY) {
          merr(`[RendererCrash] recovery limit reached (${MAX_RENDERER_RECOVERY} per ${RENDERER_RECOVERY_WINDOW_MS / 1000}s) — NOT reloading to avoid crash loop.`);
          return;
        }
        _rendererRecoveryCount++;
        _rendererRecovering = true;
        mlog(`[RendererCrash] auto-reload attempt ${_rendererRecoveryCount}/${MAX_RENDERER_RECOVERY}`);
        try { mainWindow.webContents.reload(); } catch (_) { }
        setTimeout(() => { _rendererRecovering = false; }, 5000);
      });
      mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
        if (!isMainFrame) return; // ignore subframe failures
        merr(`[RendererFail] did-fail-load: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
        // Retry loading the UI after a short delay (server may be restarting),
        // with a retry limit to avoid infinite fail→retry loops.
        const now = Date.now();
        if (now - _failLoadWindowStart > FAILLOAD_WINDOW_MS) {
          _failLoadRetryCount = 0;
          _failLoadWindowStart = now;
        }
        if (_failLoadRetryCount >= MAX_FAILLOAD_RETRY) {
          merr(`[RendererFail] retry limit reached (${MAX_FAILLOAD_RETRY} per ${FAILLOAD_WINDOW_MS / 1000}s) — giving up.`);
          return;
        }
        _failLoadRetryCount++;
        mlog(`[RendererFail] retry loadURL attempt ${_failLoadRetryCount}/${MAX_FAILLOAD_RETRY}`);
        setTimeout(() => {
          try { mainWindow.loadURL(`http://127.0.0.1:${serverPort}`); } catch (_) { }
        }, 3000);
      });
      mainWindow.webContents.on('unresponsive', () => {
        merr('[RendererHang] mainWindow unresponsive detected.');
      });
      // Successful load resets all recovery counters (page is alive again).
      mainWindow.webContents.on('did-finish-load', () => {
        _rendererRecoveryCount = 0;
        _rendererRecoveryWindowStart = 0;
        _rendererRecovering = false;
        _failLoadRetryCount = 0;
        _failLoadWindowStart = 0;
      });
      mainWindow.webContents.on('responsive', () => {
        mlog('[RendererHang] mainWindow responsive again.');
      });
      // Forward renderer console errors to daon-main.log for post-mortem analysis
      mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
        try {
          if (level >= 2) { // error=2, fatal=3
            merr(`[RendererConsole] [L${level}] ${message} (${sourceId}:${line})`);
          }
        } catch (_) { }
      });
    } catch (e) {
      console.warn('[RendererGuard] crash detection setup failed:', e && e.message);
    }

    // ── Always-on: 창 X 버튼 → 종료 대신 트레이로 최소화 (서버 백그라운드 유지) ──
    let _balloonShownOnce = false;
    mainWindow.on('close', (event) => {
      if (!isQuitting && tray) {
        event.preventDefault();
        mainWindow.hide();
        if (!_balloonShownOnce) {
          _balloonShownOnce = true;
          try {
            tray.displayBalloon({ title: 'DAON Agent System', content: '백그라운드에서 실행 중입니다. 트레이 아이콘에서 열 수 있습니다.' });
            setTimeout(() => { try { tray.removeBalloon(); } catch (_) { } }, 2000);
          } catch (_) { }
        }
        console.log('[AlwaysOn] Window hidden to tray (server keeps running).');
      }
    });

    // ── STEP 5: Load UI (force clear cache to prevent old UI loading) ──
    try {
      if (session && session.defaultSession) {
        await session.defaultSession.clearCache();
      }
    } catch (_) { }
    mainWindow.loadURL(`http://127.0.0.1:${serverPort}`, {
      extraHeaders: 'pragma: no-cache\r\nCache-Control: no-cache\r\n'
    });

    // ── Keyboard shortcuts with debouncing ──
    let _lastF5Time = 0;
    let _lastF12Time = 0;
    const DEBOUNCE_MS = 500;

    mainWindow.webContents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown') return;
      const now = Date.now();

      if (input.key === 'F5' || (input.control && input.key.toLowerCase() === 'r')) {
        if (now - _lastF5Time > DEBOUNCE_MS) {
          _lastF5Time = now;
          // F5 reload 직후 서버가 일시 과부하로 /health에 늦게 응답해도
          // watchdog이 오탐하지 않도록 90초 보류 구간 설정 (MAX_RESTARTS=3, 30s 간격)
          watchdogSuppressUntil = Date.now() + 3 * WATCHDOG_INTERVAL;
          mainWindow.webContents.reload();
        }
        event.preventDefault();
      } else if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
        if (now - _lastF12Time > DEBOUNCE_MS) {
          _lastF12Time = now;
          // Use 'detach' mode to avoid CDP port 9222 lock contention
          mainWindow.webContents.openDevTools({ mode: 'detach' });
        }
        event.preventDefault();
      }
    });

    // Resize callback for tabManager
    const updateBounds = () => {
      if (tabManager) tabManager.resize();
    };

    mainWindow.on('resize', updateBounds);
    mainWindow.on('maximize', updateBounds);
    mainWindow.on('unmaximize', updateBounds);
    mainWindow.on('restore', updateBounds);

    // ── STEP 6: Instantly close splash window & show main window safely ──
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    mainWindow.show();
    mainWindow.center();
    mainWindow.focus();
    console.log('[Electron] Main window shown!');

    tabManager = new TabManager(mainWindow);

  } catch (err) {
    console.error("[Electron Startup Error]", err);
    try {
      const { dialog } = require('electron');
      dialog.showErrorBox("Startup Error", "Failed to start DAON Agent System:\n" + err.message);
    } catch (e) { }
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
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
    // 8월 3일 정상 빌드 구조: 기본 세션(partition 없음) WebContentsView.
    // 앱 기본 세션과 동일한 세션/쿠키를 써서 내부 패널에서 구글 로그인이
    // 가능하고, 에이전트(browser_*)가 CDP 9222로 이 동일한 뷰를 공유·조작한다.
    const view = new WebContentsView({
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      }
    });
    this.tabs.set(tabId, view);

    // Prevent new BrowserWindows from opening — navigate in the same view instead
    // (뷰 단위 팝업 억제: 외부 창 튀어나옴을 막되, 구글 OAuth 팝업/리다이렉트는
    //  같은 뷰 내 탐색으로 처리되어 로그인 흐름이 끊기지 않는다 — 8월 3일 방식)
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
      view = this.createTab(tabId, url); // createTab already loads the URL
    } else {
      view.webContents.loadURL(url);
    }
    // Ensure visibility — if navigate is called, the user/frontend wants to see it.
    // This also re-attaches the view when it was previously hidden (editor shown),
    // fixing the case where a hidden WebContentsView never returned to the screen.
    this.isVisible = true;
    this.activeTabId = tabId;
    try { this.mainWindow.contentView.addChildView(view); } catch (e) { }
    view.setBounds(this.bounds);
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
  isQuitting = true;
  stopWatchdog();
  // Gap E-3: 앱 종료 시 재시작 오케스트레이터 폴링 중단
  if (restartOrchestrator) {
    try { restartOrchestrator.stop(); } catch (_) { }
  }
  if (pythonProcess && pythonProcess.pid) {
    killProcessTree(pythonProcess.pid);
    pythonProcess = null;
  }
  if (ttsProcess && ttsProcess.pid) {
    killProcessTree(ttsProcess.pid);
    ttsProcess = null;
  }

  // Wait briefly for processes to die, then clean up their _MEI folders
  setTimeout(() => {
    try {
      cleanupOrphanedTemp();
    } catch (e) {
      console.warn('[Cleanup] Shutdown temp cleanup failed:', e.message);
    }
  }, 1500);
});

app.on('window-all-closed', () => {
  // Always-on: 트레이가 살아있으면 창이 모두 닫혀도 종료하지 않고 서버 유지
  if (tray && !isQuitting) return;
  if (process.platform !== 'darwin') app.quit();
});

console.log("[BUILD ID]: main-v5-2026-08-03-22:50");
console.log("[BUILD ID]: watchdog-fix-v3-2026-07-25-17:28");
console.log("[BUILD ID]: restore-aug3-browser-2026-08-14-23:35");
console.log("[BUILD ID]: self-update-canary-v1-2026-08-24");
console.log("[BUILD ID]: firefox-ua-webauthn-block-2026-08-27");
console.log("[BUILD ID]: cdp-safe-no-debugger-attach-2026-08-27");
console.log("[BUILD ID]: webcontents-null-guard-2026-08-27");
console.log("[BUILD ID]: cdp-relaunch-guarantee-2026-08-27");
console.log("[BUILD ID]: tab-bar-ui-2026-08-27");
console.log("[BUILD ID]: chrome-ua-revert-2026-08-31");
console.log("[BUILD ID]: tabfail-aborted-skip-2026-09-01");
console.log("[BUILD ID]: tab-navigate-dead-wc-guard-2026-09-02");
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
const { createSelfUpdate } = require('./self_update');

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
// [2026-08-31 캡챠/로그인 차단 완화] Chromium의 자동화 플래그(navigator.webdriver)
// 비활성화 — Cloudflare 등이 이 신호로 봇 판정해 캡챠를 강제하는 것을 줄인다.
app.commandLine.appendSwitch('disable-blink-features', 'AutomationControlled');
// ── 패스키(암호 키) 유도 차단 (2026-08-27 실측) ──
// Chrome 완전 위장 시 구글이 WebAuthn/패스키 로그인을 강제 제안하고, Electron에선
// 플로우가 완결되지 않아 "USB 보안 키 삽입" 요구로 막힌다(실측). WebAuthentication
// 피처를 비활성화하면 navigator.credentials WebAuthn 요청이 실패하고, 구글은 이
// 브라우저를 "패스키 미지원"으로 보고 비밀번호 로그인 플로우를 제공한다.
app.commandLine.appendSwitch('disable-features', 'WebAuthentication');

// ── 구글 로그인 신뢰 신호: User-Agent를 Chrome 138로 위장 (2026-08-31 재전환) ──
// [변천] 순정 Electron UA → "자바스크립트 미지원" 거부. Chrome 138 위장 →
// Client Hints 정합성 검사 + 암호 키 강제로 차단(실측). Firefox 위장 → 로그인은
// 됐으나 체감 딜레이/사용성 문제. 이번 재전환은 두 가지 정합 장치로 보완한다:
// 1) disable-features=WebAuthentication(위)이 이전 Chrome 시도를 죽인 패스키 강제를
//    원천 차단한다.
// 2) Sec-CH-UA* 헤더와 navigator.userAgentData를 Chrome 138 정합 값으로 "재작성"해
//    UA-CH 정합성 검사를 통과한다(이전 시도는 Electron 기본 brands가 그대로
//    송신되어 정합성 검사에 걸렸음).
// Electron 37 = Chromium 138 이라 엔진 지문과 Chrome/138 UA는 본질적으로 정합.
try {
  app.userAgentFallback =
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36';
} catch (e) {
  console.warn('[Electron] Failed to set userAgentFallback:', e && e.message);
}

// ── Chrome 정합 헤더 정규화 ──
// 1) Chromium이 자동 송신하는 Sec-CH-UA* 헤더(Electron 기본 brands 포함)를
//    Chrome 138 정합 값으로 재작성 — UA-CH 정합성 검사 통과가 목적.
//    (삭제하지 않는다: Chrome은 Sec-CH-UA*를 정상 송신하는 브라우저다)
// 2) Accept-Language가 비정상적으로 짧으면 표준 형태로 보정
const FULL_ACCEPT_LANG = 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7';
const CHROME_UA_HEADER = '"Chromium";v="138", "Google Chrome";v="138", "Not)A;Brand";v="99"';
const CHROME_UA_FULL_LIST = '"Chromium";v="138.0.0.0", "Google Chrome";v="138.0.0.0", "Not)A;Brand";v="99.0.0.0"';
function normalizeChromeHeaders(headers, url) {
  const h = { ...headers };
  for (const k of Object.keys(h)) {
    const lk = k.toLowerCase();
    if (lk === 'sec-ch-ua') h[k] = CHROME_UA_HEADER;
    else if (lk === 'sec-ch-ua-mobile') h[k] = '?0';
    else if (lk === 'sec-ch-ua-platform') h[k] = '"Windows"';
    else if (lk === 'sec-ch-ua-full-version-list') h[k] = CHROME_UA_FULL_LIST;
  }
  const alKey = Object.keys(h).find((k) => k.toLowerCase() === 'accept-language');
  if (alKey && (h[alKey] === 'ko' || h[alKey] === 'en' || !h[alKey])) h[alKey] = FULL_ACCEPT_LANG;
  return h;
}
function attachChromeHeaderNormalization(ses, label) {
  try {
    ses.webRequest.onBeforeSendHeaders((details, callback) => {
      callback({ requestHeaders: normalizeChromeHeaders(details.requestHeaders, details.url) });
    });
    console.log('[ChromeUA] Header normalization attached: ' + label);
  } catch (e) {
    console.warn('[ChromeUA] attach failed (' + label + '):', e && e.message);
  }
}

// ── CDP 9222 부팅 보장 (2026-08-27, B안: 커맨드라인 relaunch) ──
// appendSwitch는 패키지 빌드에서 main 프로세스 CDP에 스위치가 확실히 안 심긴다
// (실측: 9222 LISTENING 부재, 렌더러로만 플래그 누수 — 본 파일 하단 주석도 인정).
// 커맨드라인에 --remote-debugging-port가 없으면 앱을 1회 재실행해 확실히 심는다.
// argv 체크 덕에 relaunch는 최대 1회 — 무한 루프 없음. 반드시 single instance
// lock "앞"에 둔다: lock이 먼저 걸리면 relaunch된 새 프로세스가 즉사한다.
if (!process.argv.some(function (a) { return String(a).indexOf('--remote-debugging-port=') === 0; })) {
  console.log('[CDP] --remote-debugging-port missing in argv — relaunching once to guarantee CDP 9222.');
  app.relaunch({
    args: process.argv.slice(1).concat(['--remote-debugging-port=' + NEEDED_CDP_PORT]),
  });
  app.exit(0);
}

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
  // 타임아웃 상향(1.5/2s → 3/4s): 서버가 무거운 작업 중이면 /health 응답이
  // 수 초 지연된다. 짧은 타임아웃은 '바쁜 살아있는 서버'를 '죽음'으로 오판해
  // taskkill → 재시작 → 세션 유실의 악순환을 만들었다.
  const first = await probeServerHealth(port, 3000);
  if (first && first.healthy && first.pid) return first;
  // 1차 실패 — 500ms 후 2차 시도 (진짜 다운인지 일시 지연인지 구분)
  await new Promise(r => setTimeout(r, 500));
  const second = await probeServerHealth(port, 4000);
  if (second && second.healthy && second.pid) return second;
  return null;
}

// [근본 수정 2026-08-28] TCP 레벨 포트 LISTENING 확인.
// HTTP /health probe 실패 = 서버 죽음이 아니다 — 서버가 바쁘면 응답이 늦을 뿐.
// TCP connect는 커널 백로그에서 처리되므로 서버가 아무리 바빠도 즉시 성공한다.
// connect 성공 = 서버 프로세스 생존 확정 → 절대 taskkill 하지 않는다.
function isPortListening(port, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    const done = (v) => {
      if (!settled) {
        settled = true;
        try { socket.destroy(); } catch (_) { }
        resolve(v);
      }
    };
    socket.once('connect', () => done(true));
    socket.once('timeout', () => done(false));
    socket.once('error', () => done(false));
    socket.setTimeout(timeoutMs);
    socket.connect(port, '127.0.0.1');
  });
}

// [결함④ 수정①] 스왑 후 첫 기동 유예: PyInstaller onefile exe 는 기동 시
// _MEI 추출(실측 ~60s @1.15GB) 때문에 수십 초 뒤에야 /health 에 응답한다.
// 짧은 단발 판정은 '느린 정상'을 '불량'으로 오판해 위험한 롤백을 유발했다.
const POST_SWAP_HEALTH_GRACE_MS = 120000;

// 긴 유예창 안에서 연속 stableHits 회 성공해야 healthy 로 인정 (카나리의
// stableDeepHealth 와 동일 기준). isAlive 콜백이 false 를 반환하면 프로세스가
// 이미 죽었다는 뜻이므로 남은 유예를 기다리지 않고 즉시 포기한다.
async function probeServerHealthGrace(port, opts = {}) {
  const maxWaitMs = opts.maxWaitMs != null ? opts.maxWaitMs : POST_SWAP_HEALTH_GRACE_MS;
  const pollMs = opts.pollMs != null ? opts.pollMs : 1000;
  const stableHits = opts.stableHits != null ? opts.stableHits : 3;
  const timeoutMs = opts.timeoutMs != null ? opts.timeoutMs : 1500;
  const isAlive = typeof opts.isAlive === 'function' ? opts.isAlive : null;
  const deadline = Date.now() + maxWaitMs;
  let hits = 0;
  let lastLogAt = 0;
  while (Date.now() < deadline) {
    if (isAlive && !isAlive()) {
      console.log('[Electron] grace health aborted — server process already exited.');
      return null;
    }
    const h = await probeServerHealth(port, timeoutMs);
    if (h && h.healthy && h.pid) {
      hits += 1;
      if (hits >= stableHits) return h;
    } else {
      if (hits > 0) console.log('[Electron] grace health: stability reset (flapping).');
      hits = 0;
      if (Date.now() - lastLogAt > 10000) {
        lastLogAt = Date.now();
        console.log(`[Electron] grace health: waiting for server (${Math.max(0, Math.round((deadline - Date.now()) / 1000))}s left)...`);
      }
    }
    await new Promise((r) => setTimeout(r, pollMs));
  }
  console.log(`[Electron] grace health: deadline exceeded (${maxWaitMs}ms).`);
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

function isProcessAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return !!(e && e.code === 'EPERM');
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

// ── Self-Update (Gap E-3 확장): server.exe 재빌드 + 카나리 검증 + 교체 ──
// 실제 파이프라인은 electron/self_update.js (주입형 deps 팩토리)가 담당하고
// 여기서는 프로덕션 구현체만 주입해 위임한다:
//   백업(server.exe.bak) → PyInstaller 재빌드 → 카나리(③b: 임시 포트 선구동 +
//   ③: 심층 헬스체크 연속 2회 성공·페이로드 검증) → 스왑.
// 어느 단계든 실패하면 old exe를 유지한 채 {swapped:false} 반환 — 재시작은 계속.
function resolveBuildRoot() {
  const candidates = [
    process.env.DAON_BUILD_ROOT,
    path.join(__dirname, '..'),
    'C:\\daon\\Daon agent System',
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      if (fs.existsSync(path.join(c, 'daon-server.spec'))) return c;
    } catch (_) { /* keep scanning */ }
  }
  return null;
}

const selfUpdate = createSelfUpdate({
  log: mlog,
  errLog: merr,
  findTargetExe: findServerExe,
  resolveBuildRoot,
  probeHealth: (port) => probeServerHealth(port, 1500),
  findFreePort,
});

const rebuildAndSwapServerExe = selfUpdate.rebuildAndSwap;
const restoreBackupServerExe = selfUpdate.restoreBackup;

// ── Process Spawning & Auto-Restart Safety Net ──

function startPythonProcess(port) {
  if (isQuitting) return;
  // 127.0.0.1 명시: IPv6(::1) 우선 해석으로 인한 ECONNREFUSED ::1:9222 방지
  // (browser_routes.py 의 connect_over_cdp 와 동일한 주소를 사용해야 한다).
  const env = { ...process.env, BROWSER_CDP_URL: 'ws://127.0.0.1:9222' };
  const exePath = findServerExe();

  try {
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
  } catch (err) {
    console.error(`[Electron] startPythonProcess spawn failed (EBUSY/lock): ${err.message}`);
    pythonProcess = null;
    if (!isQuitting) {
      setTimeout(() => {
        if (!isQuitting && !pythonProcess) startPythonProcess(port);
      }, 1500);
    }
    return;
  }

  if (!pythonProcess) return;

  pythonProcess.on('error', (err) => {
    console.error(`[Electron] Python process error: ${err && err.message}`);
    pythonProcess = null;
    if (!isQuitting) {
      setTimeout(() => {
        if (!isQuitting && !pythonProcess) startPythonProcess(port);
      }, 1500);
    }
  });

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

  try {
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
  } catch (err) {
    console.error(`[Electron] startTtsProcess spawn failed (EBUSY/lock): ${err.message}`);
    ttsProcess = null;
    if (!isQuitting && !selfModifyRestartActive) {
      setTimeout(() => {
        if (!isQuitting && !ttsProcess) startTtsProcess(port);
      }, 1500);
    }
    return;
  }

  if (!ttsProcess) return;

  ttsProcess.on('error', (err) => {
    console.error(`[Electron] TTS process error: ${err && err.message}`);
    ttsProcess = null;
  });

  ttsProcess.stdout.on('data', (data) => console.log(`[TTS]: ${data}`));
  ttsProcess.stderr.on('data', (data) => console.error(`[TTS Error]: ${data}`));

  // Safety Net: Auto-restart TTS server if it crashes
  ttsProcess.on('exit', (code, signal) => {
    console.warn(`[Electron] TTS server exited (code=${code}, signal=${signal})`);
    ttsProcess = null;
    if (!isQuitting) {
      // [Self-Update 근본 수정 ②] 자가수정 재시작 구간에서는 TTS 자동 재시작 금지.
      // TTS 는 server.exe 와 동일 바이너리(--tts-mode)라 재빌드 중 리스폰되면
      // resources\server.exe 이미지 락을 홀드해 스왑 EBUSY 를 유발한다.
      // afterCycle 에서 오케스트레이터가 TTS 를 되살린다.
      if (selfModifyRestartActive) {
        console.log('[Electron] TTS exit during self-modify restart — orchestrator owns respawn.');
        return;
      }
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
  // [Self-Update 근본 수정 ①] 자가수정 재시작 구간 전체에서 watchdog 리스폰 금지.
  // 기존 watchdogSuppressUntil(2분)은 재빌드(실측 7분)보다 짧아, 재빌드 도중
  // watchdog 이 OLD exe를 리스폰해 resources\server.exe 락을 홀드 → 스왑 EBUSY.
  // selfModifyRestartActive 플래그는 재빌드 시간과 무관하게 구간 전체를 커버한다.
  if (selfModifyRestartActive) {
    return;
  }
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

    // If server process was adopted, check if PID is still alive
    if (pythonProcess && pythonProcess._adopted && pythonProcess.pid && !isProcessAlive(pythonProcess.pid)) {
      merr(`[Watchdog] Adopted server PID ${pythonProcess.pid} died — spawning new server...`);
      pythonProcess = null;
      startPythonProcess(port);
      return;
    }

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

  // ── Chrome 정합 헤더: 기본 세션 (메인 UI + 공유 브라우저) ──
  // 공유 브라우저(WebContentsView)도 기본 세션을 쓰므로 defaultSession 하나만
  // attach 하면 동일 적용된다 (24aab44 이후 구조).
  attachChromeHeaderNormalization(session.defaultSession, 'defaultSession');

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
      mlog(`[Electron] No healthy server on ${DEFAULT_PORT} — killing any unresponsive/zombie server.exe before fresh spawn.`);
      reusedServer = false;
    }

    if (!reusedServer) {
      // Kill old server processes synchronously BEFORE spawning new server
      // (포트가 닫혀 있어 서버가 정말 없을 때만 도달한다)
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
    // [근본 수정 2026-08-28] busy 서버를 adopt한 경우 pid를 이 시점에 확정한다 —
    // watchdog/quit 정리가 대상 pid를 알아야 동작한다.
    if (pythonProcess && pythonProcess._busy && !pythonProcess.pid) {
      try {
        const h = await probeServerHealth(serverPort, 3000);
        if (h && h.pid) {
          pythonProcess = { pid: h.pid, _adopted: true };
          mlog(`[Electron] Busy server adopted — pid resolved: ${h.pid}`);
        }
      } catch (_) { }
    }

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
        // 재시작 구간 전체를 watchdog 오탐에서 제외 (플래그가 1차 방어,
        // 타임스탬프는 플래그 유실 대비 2차 방어)
        watchdogSuppressUntil = Date.now() + 4 * WATCHDOG_INTERVAL;
        if (pythonProcess && pythonProcess.pid) {
          killProcessTree(pythonProcess.pid);
          pythonProcess = null;
        }
        // [Self-Update 근본 수정 ②] TTS 도 같은 server.exe 바이너리를 실행하므로
        // 함께 트리킬하지 않으면 exe 이미지 락이 남아 백업/스왑이 EBUSY 로 실패한다.
        if (ttsProcess && ttsProcess.pid) {
          killProcessTree(ttsProcess.pid);
          ttsProcess = null;
        }
      },
      spawnServer: async () => {
        startPythonProcess(serverPort);
      },
      healthCheck: async () => {
        const h = await probeServerHealthStable(serverPort);
        return !!(h && h.healthy);
      },
      // [결함④ 수정①·②] 스왑 후 심층 헬스체크: onefile _MEI 추출 유예창.
      // 프로세스가 즉사하면 isAlive 로 조기 중단되어 불량 바이너리 판정이 빨라진다.
      deepHealthCheck: async () => {
        const h = await probeServerHealthGrace(serverPort, {
          maxWaitMs: POST_SWAP_HEALTH_GRACE_MS,
          stableHits: 3,
          isAlive: () => !!pythonProcess && pythonProcess.exitCode === null && pythonProcess.signalCode === undefined,
        });
        return !!(h && h.healthy);
      },
      rebuildAndSwap: rebuildAndSwapServerExe,
      restoreBackup: restoreBackupServerExe,
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
        // [Self-Update 근본 수정 ②] killServer 가 TTS 를 정리했으면 여기서 복구.
        // 새로 스왑된(또는 유지된) exe 로 TTS 를 다시 띄운다.
        try {
          if (!ttsProcess) startTtsProcess(ttsPort);
        } catch (e) {
          merr('[RestartOrch] TTS respawn failed: ' + (e && e.message));
        }
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

        // ECONNREFUSED (-102 / -105): Server is dead or unreachable — trigger emergency server restart
        if (errorCode === -102 || errorCode === -105) {
          merr('[RendererFail] Server connection refused — triggering instant emergency server restart...');
          if (pythonProcess && pythonProcess.pid && !pythonProcess._adopted) {
            killProcessTree(pythonProcess.pid);
          }
          pythonProcess = null;
          startPythonProcess(serverPort);
        }

        // Wait for server health, then load URL as soon as ready
        checkServerHealth(serverPort, 30, 1000).then(() => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            try { mainWindow.loadURL(`http://127.0.0.1:${serverPort}`); } catch (_) { }
          }
        }).catch(() => {
          setTimeout(() => {
            try { mainWindow.loadURL(`http://127.0.0.1:${serverPort}`); } catch (_) { }
          }, 3000);
        });
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
        if (tabManager) {
          try { tabManager.setVisibility(false); } catch (_) { }
          setTimeout(() => {
            try { tabManager._notifyTabs(); } catch (_) { }
          }, 300);
        }
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
      const isF5 = input.key === 'F5' || input.code === 'F5';
      const isReload = input.control && (input.key.toLowerCase() === 'r' || input.code === 'KeyR');
      const isDevTools = input.key === 'F12' || input.code === 'F12' || (input.control && input.shift && (input.key.toLowerCase() === 'i' || input.code === 'KeyI'));

      if (isF5 || isReload) {
        if (now - _lastF5Time > DEBOUNCE_MS) {
          _lastF5Time = now;
          watchdogSuppressUntil = Date.now() + 3 * WATCHDOG_INTERVAL;
          if (tabManager) {
            try { tabManager.setVisibility(false); } catch (_) { }
          }
          try {
            mainWindow.loadURL(`http://127.0.0.1:${serverPort}`, {
              extraHeaders: 'pragma: no-cache\r\nCache-Control: no-cache\r\n'
            });
          } catch (_) {
            try { mainWindow.webContents.reload(); } catch (__) { }
          }
        }
        event.preventDefault();
      } else if (isDevTools) {
        if (now - _lastF12Time > DEBOUNCE_MS) {
          _lastF12Time = now;
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
    this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    this.isVisible = false;
    // ── 탭 렌더러 크래시 자동 복구 상태 (2026-08-28) ──
    this._tabRecovery = new Map();   // tabId → { count, windowStart }
    this._recoveringTabs = new Set(); // 복구 진행 중인 탭

    // 미니뷰 그리드용 주기적 썸네일/탭 동기화 (3초 간격)
    this._notifyTimer = setInterval(() => {
      if (this.tabs.size > 0 && this.mainWindow && !this.mainWindow.isDestroyed()) {
        this._notifyTabs();
      }
    }, 3000);
  }

  static get MAX_TAB_RECOVERY() { return 5; }
  static get TAB_RECOVERY_WINDOW_MS() { return 300000; }

  // ── Single Source of Truth for Native View Clipping & Attachment ──
  _syncViewState() {
    if (!this.mainWindow || this.mainWindow.isDestroyed()) return;
    const contentView = this.mainWindow.contentView;
    if (!contentView) return;

    const validBounds = this.bounds && typeof this.bounds.width === 'number' && typeof this.bounds.height === 'number' && this.bounds.width > 0 && this.bounds.height > 0;
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
          merr('[TabManager] _syncViewState addChildView failed:', e && e.message);
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
      merr(`[TabCrash] tab=${tabId} render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
      const now = Date.now();
      let st = this._tabRecovery.get(tabId) || { count: 0, windowStart: 0 };
      if (now - st.windowStart > TabManager.TAB_RECOVERY_WINDOW_MS) {
        st = { count: 0, windowStart: now };
      }
      if (this._recoveringTabs.has(tabId)) {
        merr(`[TabCrash] tab=${tabId} recovery already in flight, skipping.`);
        return;
      }
      if (st.count >= TabManager.MAX_TAB_RECOVERY) {
        merr(`[TabCrash] tab=${tabId} recovery limit reached (${TabManager.MAX_TAB_RECOVERY} per ${TabManager.TAB_RECOVERY_WINDOW_MS / 1000}s) — NOT recreating to avoid crash loop.`);
        return;
      }
      st.count++;
      this._tabRecovery.set(tabId, st);
      this._recoveringTabs.add(tabId);
      mlog(`[TabCrash] tab=${tabId} auto-recovery attempt ${st.count}/${TabManager.MAX_TAB_RECOVERY} — recreating view.`);
      this._recreateTab(tabId);
      setTimeout(() => { this._recoveringTabs.delete(tabId); }, 5000);
    });

    view.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
      if (!isMainFrame) return;
      if (errorCode === -3) {
        mlog(`[TabFail] tab=${tabId} aborted navigation ignored (code=-3) url=${validatedURL}`);
        return;
      }
      merr(`[TabFail] tab=${tabId} did-fail-load: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
      const now = Date.now();
      let st = this._tabRecovery.get(tabId) || { count: 0, windowStart: 0 };
      if (now - st.windowStart > TabManager.TAB_RECOVERY_WINDOW_MS) {
        st = { count: 0, windowStart: now };
      }
      if (st.count >= TabManager.MAX_TAB_RECOVERY) {
        merr(`[TabFail] tab=${tabId} retry limit reached — giving up.`);
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
    mlog(`[TabCrash] tab=${tabId} recreating WebContentsView (url=${url})`);
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
    try { this.mainWindow.contentView.removeChildView(view); } catch (e) { }
    try { view.webContents.close(); } catch (e) { }
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
      } catch (e) { }
      tabs.push({ id, title, url, active: id === this.activeTabId, thumbnail });
    }
    try {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send('browser-tabs-updated', tabs);
      }
    } catch (e) { }
  }

  _isWebContentsAlive(view) {
    try {
      return !!(view && view.webContents && !view.webContents.isDestroyed());
    } catch (e) {
      return false;
    }
  }

  navigate(tabId, url) {
    let view = this.tabs.get(tabId);
    if (view && !this._isWebContentsAlive(view)) {
      merr(`[TabManager] navigate: tab=${tabId} webContents destroyed — recreating view.`);
      try { this.mainWindow.contentView.removeChildView(view); } catch (e) { }
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
        merr(`[TabManager] navigate: loadURL failed tab=${tabId}: ${e && e.message}`);
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
}

// --- IPC Commands ---
ipcMain.on('browser-navigate', (event, { id, url }) => {
  // [2026-09-02 보강] navigate 내부 오류가 main 프로세스 Uncaught Exception
  // 다이얼로그(앱 전체 팝업)로 번지지 않게 격리한다.
  try {
    if (tabManager) tabManager.navigate(id || 'tab1', url);
  } catch (e) {
    merr('[IPC] browser-navigate failed:', (e && e.stack) || e);
  }
});

// ── Tab management IPC (2026-08-27) ──
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
    // [2026-08-27 크래시 수정] 탭 객체가 남아있어도 webContents가 이미 파괴되면
    // undefined다 — canGoBack/reload 등 접근 시 메인 프로세스 Uncaught Exception
    // 로 앱 전체가 죽는다(실측: main.js:1355 TypeError canGoBack of undefined).
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

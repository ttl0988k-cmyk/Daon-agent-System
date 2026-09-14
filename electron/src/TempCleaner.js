// @ts-check
/**
 * TempCleaner
 * PyInstaller onefile 추출 디렉터리(_MEI*)와 playwright-artifacts-* 고아 폴더를 청소한다.
 *
 * ── [근본 수정 2026-09-14] 왜 15GB가 누적됐는가 ──────────────────────────────
 * 기존 구현은 두 가지 이유로 청소에 완전히 실패했다.
 *
 *   ① 위치 오판
 *      `%TEMP%` 만 스캔했다. 그러나 daon-server.spec 의
 *      `runtime_tmpdir='daon_runtime'` 지정 때문에 onefile 추출물은
 *      <server.exe 가 있는 폴더>/daon_runtime/ 안에 쌓인다.
 *      실측: %TEMP% 에는 _MEI 가 0개, 설치본 resources/daon_runtime 에 9개(15.5GB).
 *
 *   ② 활성(active) 판별 무력
 *      `ExecutablePath` 문자열에서 `/_MEI\d+/` 를 찾았으나,
 *      ExecutablePath 는 `...\resources\server.exe` 라 매칭될 수 없다.
 *      → activeMEIs 가 영원히 비어 "사용 중" 보호가 작동하지 않았다.
 *
 * 이제 ⑴ 추출물이 실제로 놓이는 후보 경로를 전부 훑고,
 *      ⑵ 실행 중 server.exe 의 PID 로 `_MEI<pid>*` 를 식별하며,
 *      ⑶ rename 성공 여부로 "미사용"을 확정한 뒤에만 삭제한다.
 *        (사용 중인 _MEI 는 내부 파일 핸들 때문에 Windows 가 rename 을 거부한다)
 */
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');

const MEI_PREFIX = '_MEI';
const PW_PREFIX = 'playwright-artifacts-';
const TRASH_MARK = '.del-';

/**
 * 실행 중인 server.exe 의 PID 집합.
 * PyInstaller onefile 은 `_MEI<pid><n>` 규칙으로 추출 폴더를 만들므로,
 * PID 를 알면 어떤 폴더가 사용 중인지 정확히 가려낼 수 있다.
 * @returns {Set<string>}
 */
function _runningServerPids() {
  const pids = new Set();

  // 1차: PowerShell CIM
  try {
    const psOut = execSync(
      'powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process -Filter \\"name=\'server.exe\'\\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessId"',
      { windowsHide: true, encoding: 'utf-8', timeout: 8000 }
    );
    for (const line of psOut.split(/\r?\n/)) {
      const t = line.trim();
      if (/^\d+$/.test(t)) pids.add(t);
    }
  } catch (_) { /* 폴백으로 진행 */ }

  // 2차: tasklist 폴백 (PowerShell 이 정책/권한으로 막힌 환경)
  if (pids.size === 0) {
    try {
      const tl = execSync('tasklist /FI "IMAGENAME eq server.exe" /FO CSV /NH',
        { windowsHide: true, encoding: 'utf-8', timeout: 8000 });
      for (const line of tl.split(/\r?\n/)) {
        const m = line.match(/"server\.exe","(\d+)"/i);
        if (m) pids.add(m[1]);
      }
    } catch (_) { }
  }
  return pids;
}

/**
 * PyInstaller 추출물이 실제로 쌓이는 후보 디렉터리 목록.
 * @returns {string[]}
 */
function _candidateRoots() {
  const roots = new Set();

  // 1) server.exe 옆의 daon_runtime (spec: runtime_tmpdir='daon_runtime')
  const exeCandidates = [];
  if (process.resourcesPath) {
    exeCandidates.push(path.join(process.resourcesPath, 'server.exe'));
    roots.add(path.join(process.resourcesPath, 'daon_runtime'));
  }
  exeCandidates.push(path.join(path.dirname(process.execPath), 'server.exe'));
  exeCandidates.push(path.join(__dirname, '..', '..', 'server.exe'));
  exeCandidates.push(path.join(__dirname, '..', '..', 'dist', 'server.exe'));

  for (const exe of exeCandidates) {
    try {
      if (fs.existsSync(exe)) {
        const dir = path.dirname(exe);
        roots.add(path.join(dir, 'daon_runtime'));
        roots.add(dir);          // _MEI 가 exe 바로 옆에 풀린 빌드 변형 대비
      }
    } catch (_) { }
  }

  // 2) CWD 기준 (Electron 이 cwd 를 리소스 경로로 지정한 경우)
  try { roots.add(path.join(process.cwd(), 'daon_runtime')); } catch (_) { }

  // 3) 과거 버전이 사용하던 %TEMP% — 잔재 정리를 위해 유지
  roots.add(process.env.TEMP || os.tmpdir());

  return [...roots];
}

/**
 * 고아 추출 폴더를 청소한다. (5초 지연 — 서버 스폰과 겹치지 않게)
 */
function cleanupOrphanedTemp() {
  setTimeout(() => {
    const pids = _runningServerPids();
    const roots = _candidateRoots();

    let freedCount = 0;
    let freedBytes = 0;
    let scannedRoots = 0;

    for (const root of roots) {
      let entries;
      try {
        entries = fs.readdirSync(root);
      } catch (_) {
        continue;   // 없는 경로는 정상
      }
      scannedRoots++;

      for (const entry of entries) {
        const isMEI = entry.startsWith(MEI_PREFIX);
        const isPlaywright = entry.startsWith(PW_PREFIX);
        // 이전 기동에서 rename 만 되고 삭제되지 못한 잔재.
        // 오탐 방지를 위해 (추출물 접두사 + .del- 마커) 를 모두 만족할 때만 쓰레기로 인정한다.
        const isStaleTrash = entry.includes(TRASH_MARK)
          && (isMEI || isPlaywright);

        if (!isMEI && !isPlaywright && !isStaleTrash) continue;

        // ── 안전장치 ①: 실행 중 프로세스가 붙잡은 _MEI<pid>* 는 절대 건드리지 않는다.
        if (isMEI && !isStaleTrash) {
          let held = false;
          for (const p of pids) {
            if (entry.startsWith(MEI_PREFIX + p)) { held = true; break; }
          }
          if (held) continue;
        }

        const full = path.join(root, entry);
        try {
          const stat = fs.statSync(full);
          if (!stat.isDirectory()) continue;
        } catch (_) {
          continue;
        }

        // ── 안전장치 ②: rename 이 성공해야 "미사용"으로 확정한다.
        //    사용 중인 _MEI 는 내부 파일 핸들 때문에 Windows 가 rename 을 거부한다.
        const trash = full + TRASH_MARK + Date.now();
        try {
          fs.renameSync(full, trash);
        } catch (_) {
          continue;   // 락 걸림 → 다음 기동 때 재시도
        }

        // ── 안전장치 ③: 삭제 실패해도 .del-* 로 남아 다음 기동에 정리된다.
        try {
          const size = _dirSize(trash);
          fs.rmSync(trash, { recursive: true, force: true });
          freedCount++;
          freedBytes += size;
        } catch (_) { /* .del-* 잔존 → 다음 기동 재시도 */ }
      }
    }

    if (freedCount > 0) {
      console.log(
        `[Cleanup] Removed ${freedCount} orphaned extraction folder(s) `
        + `(${(freedBytes / 1048576).toFixed(1)} MB) from ${scannedRoots} root(s).`
      );
    }
  }, 5000);
}

/**
 * @param {string} dir
 * @returns {number} bytes
 */
function _dirSize(dir) {
  let total = 0;
  const stack = [dir];
  while (stack.length) {
    const cur = stack.pop();
    let ents;
    try { ents = fs.readdirSync(cur, { withFileTypes: true }); } catch (_) { continue; }
    for (const e of ents) {
      const p = path.join(cur, e.name);
      if (e.isDirectory()) stack.push(p);
      else {
        try { total += fs.statSync(p).size; } catch (_) { }
      }
    }
  }
  return total;
}

module.exports = {
  cleanupOrphanedTemp,
};

// Gap E-3 확장: Self-Update 파이프라인 — rebuild → canary 검증 → swap → restore.
//
// request_server_update 툴이 rebuild:true 로 요청하면 재시작 오케스트레이터가
// 서버를 죽인 직후(exe 파일 락 해제 상태) deps.rebuildAndSwap() 을 호출한다.
//
//   1) 현재 server.exe → server.exe.bak 백업 (마지막 known-good 바이너리)
//   2) PyInstaller 로 dist/server.exe 재빌드 (수 분 소요 — 논블로킹 spawn)
//   3) 카나리(③b): 새 exe를 별도 포트에 선구동해 심층 헬스체크(③: 연속
//      N회 성공 + 페이로드 검증) — 실패 시 카나리를 kill 하고 old exe 유지
//   4) 카나리 통과 시에만 빌드 산출물을 원래 자리로 복사(스왑)
//
// 스왑 후 서버가 여전히 비정상이면 오케스트레이터가 deps.restoreBackup() 을
// 호출해 server.exe.bak 을 복원한다.
//
// 모든 부작용(fs/spawn/헬스프로브/포트할당)은 주입 가능하므로 _probe 의
// 프레인 노드 프로브로 전 경로를 검증할 수 있다 — Electron 런타임 불필요.

'use strict';

const path = require('path');

const REBUILD_TIMEOUT_MS = 20 * 60 * 1000;   // PyInstaller 재빌드 상한 (20분)
const CANARY_START_TIMEOUT_MS = 90 * 1000;   // 카나리 기동 상한 (onefile 압축해제 감안)
const CANARY_POLL_MS = 500;                  // 카나리 헬스 폴링 간격
const CANARY_STABLE_HITS = 2;                // 연속 성공 횟수 = 안정 판정
const CANARY_EXIT_WAIT_MS = 5000;            // 스왑 전 카나리 종료 대기 상한
const SWAP_RETRIES = 6;                      // 스왑 EBUSY 재시도 횟수 (총 7회 시도)
const SWAP_RETRY_DELAY_MS = 500;             // 스왑 재시도 간격

// [Self-Update 근본 수정 ③] 트리킬 기본 구현.
// PyInstaller onefile 은 bootloader 부모 + 실제 앱 자식 2단계 프로세스다.
// child.kill() 은 부모만 죽여 자식이 exe 이미지 락을 홀드한 채 좀비로 남는다
// (실측: 카나리 pid 4540 이 dist\server.exe 락 보유 → 스왑 EBUSY).
// Windows 는 taskkill /T /F, POSIX 는 프로세스 그룹 SIGKILL 로 자식까지 정리.
function defaultKillTree(pid) {
    if (!pid) return;
    try {
        if (process.platform === 'win32') {
            require('child_process').execSync(`taskkill /pid ${pid} /T /F 2>nul`, { windowsHide: true });
        } else {
            process.kill(-pid, 'SIGKILL');
        }
    } catch (_) { /* 이미 종료된 프로세스 — 무시 */ }
}

/**
 * Create the self-update pipeline.
 *
 * deps (all optional — defaults suit the Electron main process):
 *   fs:               { existsSync, copyFileSync }
 *   spawnFn:          child-process spawn (default child_process.spawn)
 *   log / errLog:     (msg) => void
 *   sleep:            async (ms) => void          (injectable for probes)
 *   findTargetExe:    () => path|null             (live server.exe location)
 *   resolveBuildRoot: () => path|null             (dir containing daon-server.spec)
 *   probeHealth:      async (port) => {healthy,pid,...}|null   (③ deep probe)
 *   findFreePort:     async (startPort) => port   (canary alternate port)
 *   canaryStartMs:    canary startup budget (default 90s)
 *   rebuildTimeoutMs: PyInstaller timeout (default 20min)
 *   canaryPollMs:     canary health poll interval (injectable for probes)
 *   canaryStableHits: consecutive healthy probes required (default 2)
 *   killTree:         (pid) => void — 프로세스 트리킬 (기본 taskkill /T /F)
 *   swapRetries:      스왑(copyFile) EBUSY 재시도 횟수 (default 6)
 *   swapRetryDelayMs: 스왑 재시도 간격 ms (default 500)
 */
function createSelfUpdate(deps = {}) {
    const fs = deps.fs || require('fs');
    const spawnFn = deps.spawnFn || require('child_process').spawn;
    const log = deps.log || (() => { });
    const errLog = deps.errLog || log;
    const sleep = deps.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    const findTargetExe = deps.findTargetExe || (() => null);
    const resolveBuildRoot = deps.resolveBuildRoot || (() => null);
    const probeHealth = deps.probeHealth || null;   // async (port) -> {healthy,pid,...}|null
    const findFreePort = deps.findFreePort || null; // async (startPort) -> port
    const canaryStartMs = deps.canaryStartMs != null ? deps.canaryStartMs : CANARY_START_TIMEOUT_MS;
    const rebuildTimeoutMs = deps.rebuildTimeoutMs != null ? deps.rebuildTimeoutMs : REBUILD_TIMEOUT_MS;
    const canaryPollMs = deps.canaryPollMs != null ? deps.canaryPollMs : CANARY_POLL_MS;
    const canaryStableHits = deps.canaryStableHits != null ? deps.canaryStableHits : CANARY_STABLE_HITS;
    const killTree = deps.killTree || defaultKillTree;   // 프로세스 트리킬 (주입형)
    const swapRetries = deps.swapRetries != null ? deps.swapRetries : SWAP_RETRIES;
    const swapRetryDelayMs = deps.swapRetryDelayMs != null ? deps.swapRetryDelayMs : SWAP_RETRY_DELAY_MS;

    function runPyInstallerAsync(buildRoot) {
        return new Promise((resolve) => {
            let stderrTail = '';
            let child;
            try {
                child = spawnFn('python', ['-m', 'PyInstaller', 'daon-server.spec', '--noconfirm'], {
                    cwd: buildRoot,
                    windowsHide: true,
                    env: { ...process.env },
                    stdio: ['ignore', 'pipe', 'pipe'],
                });
            } catch (e) {
                resolve({ ok: false, error: `spawn failed: ${e.message}` });
                return;
            }
            const timer = setTimeout(() => {
                try { child.kill(); } catch (_) { }
                resolve({ ok: false, error: 'rebuild timeout' });
            }, rebuildTimeoutMs);
            child.stderr.on('data', (d) => { stderrTail = (stderrTail + String(d)).slice(-2000); });
            child.on('exit', (code) => {
                clearTimeout(timer);
                resolve(code === 0
                    ? { ok: true }
                    : { ok: false, error: `PyInstaller exit ${code}${stderrTail ? ' :: ' + stderrTail.slice(-400) : ''}` });
            });
            child.on('error', (e) => {
                clearTimeout(timer);
                resolve({ ok: false, error: `spawn failed: ${e.message} (python/PyInstaller 필요)` });
            });
        });
    }

    // ③ 헬스체크 강화: 단발 성공으로는 안정으로 보지 않는다 — 연속
    // canaryStableHits 회 성공해야 하며, 중간에 한 번이라도 실패하면 리셋.
    // 페이로드도 검증한다(healthy===true && pid 양의 정수).
    async function stableDeepHealth(port, deadline) {
        let hits = 0;
        let last = null;
        while (Date.now() < deadline) {
            const h = await probeHealth(port);
            if (h && h.healthy === true && Number.isInteger(h.pid) && h.pid > 0) {
                hits += 1;
                last = h;
                if (hits >= canaryStableHits) return { ok: true, payload: last };
            } else {
                hits = 0;
            }
            await sleep(canaryPollMs);
        }
        return { ok: false, reason: hits > 0 ? 'health flapping (unstable)' : 'health never became ready' };
    }

    // ③b 카나리: 새 exe를 프로덕션 포트가 아닌 임시 포트에서 선구동하여
    // 실제로 기동·응답하는 바이너리인지 확인한다. 어떤 결과든 반드시 kill 하고
    // 프로세스 종료까지 기다린 뒤 돌아온다 — 스왑 시 파일 락 방지.
    async function canaryVerify(builtExe) {
        if (typeof probeHealth !== 'function') return { ok: false, reason: 'canary disabled (no probeHealth)' };
        if (typeof findFreePort !== 'function') return { ok: false, reason: 'canary disabled (no findFreePort)' };

        let port;
        try {
            port = await findFreePort(8765);
        } catch (e) {
            return { ok: false, reason: `canary port alloc failed: ${e && e.message}` };
        }

        let child;
        try {
            child = spawnFn(builtExe, ['--no-browser', '--port', String(port)], {
                windowsHide: true,
                stdio: 'ignore',
                env: { ...process.env, DAON_CANARY: '1' },
            });
        } catch (e) {
            return { ok: false, reason: `canary spawn failed: ${e.message}` };
        }
        let spawnError = null;
        child.on('error', (e) => { spawnError = e; });

        const deadline = Date.now() + canaryStartMs;
        const verdict = await stableDeepHealth(port, deadline);

        // cleanup: 트리킬(onefile 자식까지) → exit 대기 → 재시도 (순서 보장).
        // child.kill() 단독은 bootloader 부모만 죽인다 — 실측에서 실제 앱 자식이
        // dist\server.exe 락을 홀드한 채 좀비로 남아 스왑 EBUSY 를 낳았다.
        try { killTree(child.pid); } catch (_) { }
        try { child.kill(); } catch (_) { }
        const killDeadline = Date.now() + CANARY_EXIT_WAIT_MS;
        while (child.exitCode === null && child.signalCode === undefined && Date.now() < killDeadline) {
            await sleep(100);
        }
        try {
            if (child.exitCode === null && child.signalCode === undefined) {
                killTree(child.pid);
                child.kill('SIGKILL');
            }
        } catch (_) { }

        if (verdict.ok) return { ok: true, pid: verdict.payload.pid, port };
        if (spawnError) return { ok: false, reason: `canary spawn error: ${spawnError.message}` };
        if (child.exitCode != null && child.exitCode !== 0) {
            return { ok: false, reason: `canary exited early (code ${child.exitCode})` };
        }
        return { ok: false, reason: verdict.reason || 'canary not healthy in time' };
    }

    // [Self-Update 근본 수정 ④] 목표 exe 쓰기 가능 검사. 실행 중인 exe 이미지는
    // Windows 가 쓰기 공유를 거부하므로 'r+' 오픈 성공 = 락 없음. 일시적 락
    // (AV 스캔 등) 감안해 짧게 재시도한다. 주입 fs 에 openSync 가 없으면 생략.
    async function targetWritable(targetExe) {
        if (typeof fs.openSync !== 'function') return { ok: true };
        let lastErr = null;
        for (let attempt = 0; attempt <= swapRetries; attempt++) {
            if (attempt > 0) await sleep(swapRetryDelayMs);
            try {
                const fd = fs.openSync(targetExe, 'r+');
                fs.closeSync(fd);
                return { ok: true };
            } catch (e) {
                lastErr = e;
            }
        }
        return { ok: false, error: (lastErr && lastErr.message) || 'unknown' };
    }

    async function rebuildAndSwap() {
        const targetExe = findTargetExe();
        if (!targetExe) return { swapped: false, reason: 'no server.exe target (dev python mode)' };
        const buildRoot = resolveBuildRoot();
        if (!buildRoot) return { swapped: false, reason: 'daon-server.spec not found — set DAON_BUILD_ROOT to enable packaged self-update' };

        // 0) [Self-Update 근본 수정 ④] 스왑 프리플라이트: 재빌드는 수 분 걸리므로
        // 목표 exe 가 지금 쓰기 불가(누군가 이미지 락 홀드 — 생존한 OLD/TTS 프로세스)
        // 상태라면 빌드 전에 즉시 실패 처리한다. 7분 재빌드 후 EBUSY 로 헛돈 실측 교훈.
        const pf = await targetWritable(targetExe);
        if (!pf.ok) {
            errLog('[SelfUpdate] swap preflight failed — target exe is locked: ' + pf.error);
            return { swapped: false, reason: `swap preflight failed (target locked): ${pf.error}` };
        }

        // 1) 백업 (마지막 known-good 바이너리)
        const backupExe = `${targetExe}.bak`;
        try {
            fs.copyFileSync(targetExe, backupExe);
        } catch (e) {
            return { swapped: false, reason: `backup failed: ${e.message}` };
        }

        // 2) 재빌드 — 서버가 죽어 있을 때만 호출되므로 exe 락 없음
        log('[SelfUpdate] PyInstaller rebuild started (this can take minutes)...');
        const rb = await runPyInstallerAsync(buildRoot);
        if (!rb.ok) {
            errLog('[SelfUpdate] rebuild failed — keeping old exe: ' + rb.error);
            return { swapped: false, reason: 'rebuild failed (old exe kept)' };
        }

        const builtExe = path.join(buildRoot, 'dist', 'server.exe');
        if (!fs.existsSync(builtExe)) {
            return { swapped: false, reason: 'built exe missing after rebuild' };
        }

        // 3) 카나리 검증 — 새 바이너리가 실제로 기동되고 /health 로 응답하는지
        log('[SelfUpdate] canary verification: launching new exe on an alternate port...');
        const cv = await canaryVerify(builtExe);
        if (!cv.ok) {
            errLog('[SelfUpdate] canary FAILED — keeping old exe: ' + (cv.reason || 'unknown'));
            return { swapped: false, reason: `canary failed: ${cv.reason || 'not healthy'}`, canary: cv };
        }
        log(`[SelfUpdate] canary healthy (pid=${cv.pid}, port=${cv.port}) — swapping in.`);

        // 4) 스왑 — EBUSY 재시도 루프(카나리/락 홀더 완전 종료 지연 등 일시적
        // 락 흡수). 모든 재시도 실패 시 old exe 유지.
        let swapErr = null;
        for (let attempt = 0; ; attempt++) {
            try {
                fs.copyFileSync(builtExe, targetExe);
                swapErr = null;
                break;
            } catch (e) {
                swapErr = e;
                if (attempt >= swapRetries) break;
                log(`[SelfUpdate] swap attempt ${attempt + 1} failed (${e.code || e.message}) — retrying...`);
                await sleep(swapRetryDelayMs);
            }
        }
        if (swapErr) {
            errLog('[SelfUpdate] swap failed after retries — keeping old exe: ' + swapErr.message);
            return { swapped: false, reason: `swap failed after ${swapRetries + 1} attempts: ${swapErr.message}` };
        }
        log(`[SelfUpdate] server.exe swapped in: ${targetExe}`);
        return { swapped: true, canary: { pid: cv.pid, port: cv.port } };
    }

    async function restoreBackup() {
        const targetExe = findTargetExe();
        if (!targetExe) return false;
        const backupExe = `${targetExe}.bak`;
        if (!fs.existsSync(backupExe)) return false;
        try {
            fs.copyFileSync(backupExe, targetExe);
            log('[SelfUpdate] backup server.exe restored (last-known-good).');
            return true;
        } catch (e) {
            errLog('[SelfUpdate] backup restore failed: ' + (e && e.message));
            return false;
        }
    }

    return { rebuildAndSwap, restoreBackup, canaryVerify, stableDeepHealth, runPyInstallerAsync };
}

module.exports = { createSelfUpdate, REBUILD_TIMEOUT_MS, CANARY_START_TIMEOUT_MS };

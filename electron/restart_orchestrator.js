// Gap E-3: Self-modification bootstrap — server restart orchestration.
//
// Watcher/watched separation (plan 3.3): the Python server only RECORDS a
// restart request file; the Electron main process (the watcher) performs the
// actual restart:
//
//   poll request file -> kill server -> respawn -> health check
//     -> on failure: git rollback to checkpoint -> respawn again -> re-check
//
// [결함④ 수정] 스왑 직후 오판 방지 (onefile _MEI 추출로 수십 초 늦게 리슨):
//   ② unhealthy 선고 전 심층 재판정 — deepHealthCheck(긴 유예창)으로 재검증.
//   ③ 롤백 가드 — restoreBackup 이 실패(EBUSY)하면 새 exe 가 살아있다는
//      신호이므로 복구를 포기하지 않고 심층 헬스체크로 최종 판정한다.
//
// Everything with side effects (fs, process kill/spawn, health probe, git) is
// injectable so the probe (_probe/probe_gap_e3.js) can run this under plain
// node with fakes — no Electron runtime required.

'use strict';

const RESTART_FILE = 'restart-request.json';

// Candidate STATE_DIR locations (mirrors api/api/config.py):
//  - dev:      <repoRoot>/data
//  - packaged: %LOCALAPPDATA%/DAON Agent System/data
function defaultCandidateDirs(repoRoot) {
    const dirs = [];
    if (repoRoot) dirs.push(require('path').join(repoRoot, 'data'));
    if (process.env.LOCALAPPDATA) {
        dirs.push(require('path').join(process.env.LOCALAPPDATA, 'DAON Agent System', 'data'));
    }
    return dirs;
}

/**
 * Create a restart orchestrator.
 *
 * deps (all optional — defaults use real implementations):
 *   fs:            { existsSync, readFileSync, unlinkSync }
 *   dirs:          candidate STATE_DIR paths to scan for the request file
 *   killServer:    async () -> void            (kill the watched server)
 *   spawnServer:   async () -> void            (respawn the watched server)
 *   healthCheck:   async () -> boolean         (probe /health)
 *   deepHealthCheck: async () -> boolean       (long-grace verdict for slow onefile boots)
 *   gitRollback:   async (ref) -> boolean      (git reset --hard ref + clean)
 *   log:           (msg) -> void
 *   pollMs:        request-file poll interval (default 5000)
 *   settleMs:      pause after kill before respawn (default 500)
 *   sleep:         async (ms) -> void          (injectable for probes)
 */
function createRestartOrchestrator(deps = {}) {
    const fs = deps.fs || require('fs');
    const dirs = deps.dirs || defaultCandidateDirs(deps.repoRoot);
    const log = deps.log || (() => { });
    const sleep = deps.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    const pollMs = deps.pollMs != null ? deps.pollMs : 5000;
    const settleMs = deps.settleMs != null ? deps.settleMs : 500;

    const state = {
        running: false,      // poll loop active
        busy: false,         // a restart cycle is in progress
        timer: null,
        lastResult: null,    // result of the most recent cycle (or null)
        cycles: 0,           // number of restart cycles performed
    };

    function requestFilePaths() {
        return dirs.map((d) => require('path').join(d, RESTART_FILE));
    }

    // Read + remove the first request file found. Returns the payload or null.
    // A corrupt file is removed so it cannot block future restarts.
    function consumeRequest() {
        for (const p of requestFilePaths()) {
            try {
                if (!fs.existsSync(p)) continue;
                let payload = null;
                try {
                    payload = JSON.parse(fs.readFileSync(p, 'utf-8'));
                    // Only a plain object is a valid request; arrays/primitives are corrupt.
                    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) payload = null;
                } catch (_) {
                    payload = null;
                }
                try { fs.unlinkSync(p); } catch (_) { }
                return payload;
            } catch (_) { /* keep scanning */ }
        }
        return null;
    }

    // One full restart cycle. Returns a result dict:
    //   { ok, rolledBack, restored, rebuilt, swapped, attempts, reason, checkpointRef, deepVerdict }
    async function performRestart(payload) {
        const checkpointRef = (payload && payload.checkpoint_ref) || null;
        const reason = (payload && payload.reason) || 'unspecified';
        const wantsRebuild = !!(payload && payload.rebuild);
        log(`[RestartOrch] restart requested: ${reason} (checkpoint=${checkpointRef || 'none'}, rebuild=${wantsRebuild})`);

        // 1. kill the watched server
        try {
            await deps.killServer();
        } catch (e) {
            log(`[RestartOrch] kill failed: ${e && e.message}`);
            return { ok: false, rolledBack: false, restored: false, rebuilt: wantsRebuild, swapped: false, attempts: 0, reason: `kill failed: ${e && e.message}`, checkpointRef };
        }
        await sleep(settleMs);

        // 1b. packaged self-update: rebuild + swap server.exe while it is unlocked
        //     (between kill and spawn). Failure here is NOT fatal — the old exe
        //     stays in place and the plain restart still applies source-level
        //     changes for dev mode / non-exe resources.
        let swapped = false;
        if (wantsRebuild && typeof deps.rebuildAndSwap === 'function') {
            try {
                const rb = await deps.rebuildAndSwap();
                swapped = !!(rb && rb.swapped);
                log(`[RestartOrch] rebuildAndSwap -> ${swapped ? 'new exe swapped in' : 'old exe kept'}${rb && rb.reason ? ` (${rb.reason})` : ''}`);
            } catch (e) {
                log(`[RestartOrch] rebuildAndSwap threw: ${e && e.message} — continuing with old exe.`);
            }
        }

        // 2. respawn + health check (단발 판정: 즉사 불량 바이너리를 빠르게 걸러냄)
        let healthy = false;
        let deepRescued = false;
        try {
            await deps.spawnServer();
            healthy = await deps.healthCheck();
        } catch (e) {
            log(`[RestartOrch] respawn/health failed: ${e && e.message}`);
        }

        // [결함④ 수정②] unhealthy 선고 전 심층 재판정: 스왑된 onefile exe 는
        // _MEI 추출 때문에 수십 초 뒤에야 포트가 열린다. 한 번의 실패로
        // '불량 바이너리'로 확정하지 않고 긴 유예창(deepHealthCheck)으로 재판정한다.
        if (!healthy && swapped && typeof deps.deepHealthCheck === 'function') {
            log('[RestartOrch] first health check failed after swap — deep re-verdict before rollback.');
            try {
                healthy = await deps.deepHealthCheck() === true;
                if (healthy) {
                    deepRescued = true;
                    log('[RestartOrch] deep re-verdict passed — slow boot accepted, no rollback.');
                }
            } catch (e) {
                log(`[RestartOrch] deep re-verdict threw: ${e && e.message}`);
            }
        }

        if (healthy) {
            log('[RestartOrch] server restarted and healthy.');
            return { ok: true, rolledBack: false, restored: false, rebuilt: wantsRebuild, swapped, attempts: 1, reason, checkpointRef, deepVerdict: deepRescued };
        }

        // 3. unhealthy recovery:
        //   3a. if we swapped a new exe in, restore the last-known-good backup first
        //       (the new binary itself may be the failure cause).
        //   3b. then git rollback to checkpoint (if provided) and retry once.
        let restored = false;
        let rolledBack = false;
        if (swapped && typeof deps.restoreBackup === 'function') {
            try {
                restored = await deps.restoreBackup() === true;
                if (restored) log('[RestartOrch] backup server.exe restored (last-known-good).');
            } catch (e) {
                log(`[RestartOrch] backup restore failed: ${e && e.message}`);
            }
            // [결함④ 수정③] 롤백 가드: restore 실패(대상 락 = EBUSY 계열)는
            // 새 exe 가 아직 살아있다는 신호다. 복구를 포기하지 말고 실행 중인
            // 새 exe 를 심층 헬스체크로 최종 판정한다 — 통과 시 롤백을 건너뛰고
            // 새 exe 를 유지한다.
            if (!restored && typeof deps.deepHealthCheck === 'function') {
                log('[RestartOrch] restore failed (target locked?) — new exe appears alive; final deep verdict.');
                try {
                    if (await deps.deepHealthCheck() === true) {
                        log('[RestartOrch] final deep verdict: new exe healthy — keeping it (rollback skipped).');
                        return { ok: true, rolledBack: false, restored: false, rebuilt: wantsRebuild, swapped, attempts: 2, reason: `${reason} (healthy on final deep verdict)`, checkpointRef, deepVerdict: true };
                    }
                } catch (e) {
                    log(`[RestartOrch] final deep verdict threw: ${e && e.message}`);
                }
            }
        }
        if (checkpointRef && deps.gitRollback) {
            try {
                rolledBack = await deps.gitRollback(checkpointRef) === true;
            } catch (e) {
                log(`[RestartOrch] git rollback failed: ${e && e.message}`);
                rolledBack = false;
            }
            if (rolledBack) {
                log(`[RestartOrch] rolled back to ${checkpointRef} — respawning clean server.`);
                try {
                    // The unhealthy process may still be alive — kill it again before
                    // respawning the rolled-back (clean) server.
                    try { await deps.killServer(); } catch (_) { }
                    await sleep(settleMs);
                    await deps.spawnServer();
                    healthy = await deps.healthCheck();
                } catch (e) {
                    log(`[RestartOrch] post-rollback respawn failed: ${e && e.message}`);
                    healthy = false;
                }
                if (healthy) {
                    return { ok: true, rolledBack: true, restored, rebuilt: wantsRebuild, swapped, attempts: 2, reason, checkpointRef };
                }
            }
        } else if (restored) {
            // No checkpoint to roll back to, but the backup exe was restored —
            // retry once with the known-good binary.
            log('[RestartOrch] respawning with restored backup exe.');
            try {
                try { await deps.killServer(); } catch (_) { }
                await sleep(settleMs);
                await deps.spawnServer();
                healthy = await deps.healthCheck();
            } catch (e) {
                log(`[RestartOrch] post-restore respawn failed: ${e && e.message}`);
                healthy = false;
            }
            if (healthy) {
                return { ok: true, rolledBack: false, restored: true, rebuilt: wantsRebuild, swapped, attempts: 2, reason, checkpointRef };
            }
        }
        return { ok: false, rolledBack, restored, rebuilt: wantsRebuild, swapped, attempts: rolledBack || restored ? 2 : 1, reason: 'server unhealthy after restart', checkpointRef };
    }

    async function tick() {
        if (state.busy) return;
        const payload = consumeRequest();
        if (!payload) return;
        state.busy = true;
        try {
            state.cycles += 1;
            state.lastResult = await performRestart(payload);
            if (deps.afterCycle) {
                try { await deps.afterCycle(state.lastResult); } catch (_) { }
            }
        } finally {
            state.busy = false;
        }
    }

    function start() {
        if (state.running) return;
        state.running = true;
        state.timer = setInterval(() => { tick().catch((e) => log(`[RestartOrch] tick error: ${e && e.message}`)); }, pollMs);
        if (state.timer && state.timer.unref) state.timer.unref();
        log('[RestartOrch] started (polling for self-modify restart requests).');
    }

    function stop() {
        state.running = false;
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
    }

    return {
        start,
        stop,
        tick,               // exposed for probes (manual poll)
        consumeRequest,     // exposed for probes
        performRestart,     // exposed for probes
        state,
    };
}

module.exports = { createRestartOrchestrator, defaultCandidateDirs, RESTART_FILE };

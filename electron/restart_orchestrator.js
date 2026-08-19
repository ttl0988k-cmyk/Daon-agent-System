// Gap E-3: Self-modification bootstrap — server restart orchestration.
//
// Watcher/watched separation (plan 3.3): the Python server only RECORDS a
// restart request file; the Electron main process (the watcher) performs the
// actual restart:
//
//   poll request file -> kill server -> respawn -> health check
//     -> on failure: git rollback to checkpoint -> respawn again -> re-check
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
    //   { ok, rolledBack, attempts, reason, checkpointRef }
    async function performRestart(payload) {
        const checkpointRef = (payload && payload.checkpoint_ref) || null;
        const reason = (payload && payload.reason) || 'unspecified';
        log(`[RestartOrch] restart requested: ${reason} (checkpoint=${checkpointRef || 'none'})`);

        // 1. kill the watched server
        try {
            await deps.killServer();
        } catch (e) {
            log(`[RestartOrch] kill failed: ${e && e.message}`);
            return { ok: false, rolledBack: false, attempts: 0, reason: `kill failed: ${e && e.message}`, checkpointRef };
        }
        await sleep(settleMs);

        // 2. respawn + health check
        let healthy = false;
        try {
            await deps.spawnServer();
            healthy = await deps.healthCheck();
        } catch (e) {
            log(`[RestartOrch] respawn/health failed: ${e && e.message}`);
        }

        if (healthy) {
            log('[RestartOrch] server restarted and healthy.');
            return { ok: true, rolledBack: false, attempts: 1, reason, checkpointRef };
        }

        // 3. unhealthy -> git rollback to checkpoint (if provided) and retry once
        let rolledBack = false;
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
                    return { ok: true, rolledBack: true, attempts: 2, reason, checkpointRef };
                }
            }
        }
        return { ok: false, rolledBack, attempts: rolledBack ? 2 : 1, reason: 'server unhealthy after restart', checkpointRef };
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

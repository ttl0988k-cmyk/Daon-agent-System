// Gap E-3 probe (node): electron/restart_orchestrator.js under fake deps.
//
// Verifies the watcher side of the self-modify restart bootstrap without any
// Electron runtime: request-file consumption, kill -> respawn -> health check
// ordering, git rollback + re-kill + retry on unhealthy restart, busy guard,
// corrupt-file handling, dir scan order, start/stop lifecycle.
//
// Run: node _probe/probe_gap_e3.js

'use strict';

const path = require('path');
const {
    createRestartOrchestrator,
    defaultCandidateDirs,
    RESTART_FILE,
} = require(path.join(__dirname, '..', 'electron', 'restart_orchestrator.js'));

let checks = 0;
const fails = [];

function check(cond, msg) {
    checks += 1;
    if (!cond) {
        fails.push(`[${checks}] ${msg}`);
        console.error(`FAIL [${checks}] ${msg}`);
    }
}

function reqPath(dir) {
    return path.join(dir, RESTART_FILE);
}

function validPayload(extra) {
    return Object.assign({
        type: 'self_modify_restart',
        version: 1,
        reason: 'apply fix',
        checkpoint_ref: 'abc1234',
        requested_at: '2026-08-19T21:00:00',
        server_pid: 4242,
    }, extra || {});
}

// In-memory fs fake with call recording.
function makeFakeFs(initial) {
    const files = new Map(Object.entries(initial || {}));
    const calls = { exists: [], read: [], unlink: [] };
    return {
        files,
        calls,
        existsSync: (p) => { calls.exists.push(p); return files.has(p); },
        readFileSync: (p) => {
            calls.read.push(p);
            if (!files.has(p)) throw new Error(`ENOENT: ${p}`);
            return files.get(p);
        },
        unlinkSync: (p) => { calls.unlink.push(p); files.delete(p); },
    };
}

// Effect recorders: kill/spawn/health/rollback/sleep/afterCycle.
function makeEffects(opts = {}) {
    const seq = [];
    const sleepCalls = [];
    const healthResults = (opts.health || []).slice();
    const rollbackRefs = [];
    const afterResults = [];
    return {
        seq,
        sleepCalls,
        rollbackRefs,
        afterResults,
        deps: {
            killServer: async () => {
                seq.push('kill');
                if (opts.killThrows) throw new Error('kill boom');
            },
            spawnServer: async () => {
                seq.push('spawn');
                if (opts.spawnThrows) throw new Error('spawn boom');
            },
            healthCheck: async () => {
                seq.push('health');
                return healthResults.length ? healthResults.shift() : true;
            },
            gitRollback: opts.noRollback
                ? undefined
                : async (ref) => {
                    seq.push(`rollback:${ref}`);
                    rollbackRefs.push(ref);
                    if (opts.rollbackThrows) throw new Error('rollback boom');
                    return opts.rollbackResult === true;
                },
            sleep: async (ms) => { seq.push('sleep'); sleepCalls.push(ms); },
            log: () => { },
            afterCycle: opts.afterCycleThrows
                ? async () => { throw new Error('afterCycle boom'); }
                : async (r) => { afterResults.push(r); },
        },
    };
}

function makeOrch(fsx, dirs, effects, extra) {
    return createRestartOrchestrator(Object.assign({
        fs: fsx,
        dirs,
        settleMs: 123,
        pollMs: 60000,
    }, effects.deps, extra || {}));
}

async function main() {
    // ---- S1: happy path — kill -> settle -> spawn -> healthy --------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ health: [true], rollbackResult: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        check(orch.state.cycles === 1, 'S1 cycles=1');
        check(orch.state.busy === false, 'S1 busy released after cycle');
        const r = orch.state.lastResult;
        check(r && r.ok === true, 'S1 result ok=true');
        check(r && r.rolledBack === false, 'S1 no rollback on healthy restart');
        check(r && r.attempts === 1, 'S1 attempts=1');
        check(r && r.checkpointRef === 'abc1234', 'S1 checkpointRef passthrough');
        check(r && r.reason === 'apply fix', 'S1 reason passthrough');
        check(JSON.stringify(fx.seq) === JSON.stringify(['kill', 'sleep', 'spawn', 'health']),
            'S1 effect order kill->sleep->spawn->health, got: ' + JSON.stringify(fx.seq));
        check(fx.sleepCalls.length === 1 && fx.sleepCalls[0] === 123, 'S1 settle sleep uses settleMs');
        check(fx.rollbackRefs.length === 0, 'S1 gitRollback never invoked when healthy');
        check(fx.afterResults.length === 1 && fx.afterResults[0].ok === true, 'S1 afterCycle called with result');
        check(!fsx.files.has(reqPath('d1')), 'S1 request file consumed');
        check(fsx.calls.unlink.length === 1, 'S1 unlink exactly once');
    }

    // ---- S2: unhealthy -> rollback -> re-kill -> respawn -> healthy -------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ health: [false, true], rollbackResult: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === true, 'S2 recovered after rollback');
        check(r && r.rolledBack === true, 'S2 rolledBack=true');
        check(r && r.attempts === 2, 'S2 attempts=2');
        const expected = ['kill', 'sleep', 'spawn', 'health', 'rollback:abc1234',
            'kill', 'sleep', 'spawn', 'health'];
        check(JSON.stringify(fx.seq) === JSON.stringify(expected),
            'S2 order kill->spawn->health(fail)->rollback->re-kill->spawn->health, got: ' + JSON.stringify(fx.seq));
        check(fx.rollbackRefs.length === 1 && fx.rollbackRefs[0] === 'abc1234', 'S2 rollback ref passed');
        check(fx.sleepCalls.length === 2, 'S2 settle sleep twice (both respawns)');
        check(fx.afterResults.length === 1 && fx.afterResults[0].rolledBack === true, 'S2 afterCycle sees rolledBack');
    }

    // ---- S3: unhealthy + rollback returns false -> fail, no retry ---------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ health: [false, true], rollbackResult: false });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S3 ok=false when rollback fails');
        check(r && r.rolledBack === false, 'S3 rolledBack=false');
        check(r && r.attempts === 1, 'S3 attempts=1 (no retry without rollback)');
        check(r && r.reason === 'server unhealthy after restart', 'S3 failure reason');
        check(fx.seq.filter((s) => s === 'spawn').length === 1, 'S3 no second spawn');
    }

    // ---- S4: unhealthy + no checkpoint_ref -> rollback never attempted ----
    {
        const fsx = makeFakeFs({
            [reqPath('d1')]: JSON.stringify(validPayload({ checkpoint_ref: null })),
        });
        const fx = makeEffects({ health: [false], rollbackResult: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S4 ok=false');
        check(r && r.checkpointRef === null, 'S4 checkpointRef null');
        check(fx.rollbackRefs.length === 0, 'S4 gitRollback not attempted without checkpoint_ref');
        check(fx.seq.filter((s) => s === 'kill').length === 1, 'S4 single kill');
    }

    // ---- S5: kill failure -> abort before settle/spawn ---------------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ killThrows: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S5 ok=false on kill failure');
        check(r && r.attempts === 0, 'S5 attempts=0');
        check(r && typeof r.reason === 'string' && r.reason.indexOf('kill failed') === 0, 'S5 reason starts with kill failed');
        check(fx.seq.filter((s) => s === 'spawn').length === 0, 'S5 no spawn after kill failure');
        check(fx.sleepCalls.length === 0, 'S5 no settle sleep after kill failure');
    }

    // ---- S6: corrupt request file -> removed, no restart -------------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: '{not json' });
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        check(orch.state.cycles === 0, 'S6 no restart cycle for corrupt file');
        check(fx.seq.length === 0, 'S6 no effects for corrupt file');
        check(!fsx.files.has(reqPath('d1')), 'S6 corrupt file removed');
        check(fsx.calls.unlink.length === 1, 'S6 corrupt file unlinked');
        check(orch.state.lastResult === null, 'S6 lastResult stays null');
    }

    // ---- S7: non-object JSON payload -> treated as corrupt -----------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: '[1, 2]' });
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d1'], fx);
        const payload = orch.consumeRequest();
        check(payload === null, 'S7 non-object payload rejected');
        check(!fsx.files.has(reqPath('d1')), 'S7 non-object file removed');
    }

    // ---- S8: no request file -> tick is a no-op ----------------------------
    {
        const fsx = makeFakeFs({});
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        check(orch.state.cycles === 0, 'S8 no cycle without request');
        check(fx.seq.length === 0, 'S8 no effects without request');
    }

    // ---- S9: busy guard — no double cycle while one is in flight -----------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d1'], fx);
        orch.state.busy = true;
        await orch.tick();
        check(orch.state.cycles === 0, 'S9 busy tick skipped');
        check(fsx.calls.exists.length === 0, 'S9 request file not even probed while busy');
        check(fsx.files.has(reqPath('d1')), 'S9 request file preserved while busy');
        orch.state.busy = false;
        await orch.tick();
        check(orch.state.cycles === 1, 'S9 tick resumes after busy cleared');
    }

    // ---- S10: dir scan order — first dir wins, later dirs scanned ----------
    {
        const fsx = makeFakeFs({
            [reqPath('d0')]: JSON.stringify(validPayload({ reason: 'first' })),
            [reqPath('d1')]: JSON.stringify(validPayload({ reason: 'second' })),
        });
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d0', 'd1'], fx);
        const payload = orch.consumeRequest();
        check(payload && payload.reason === 'first', 'S10 first dir wins');
        check(!fsx.files.has(reqPath('d0')), 'S10 first file consumed');
        check(fsx.files.has(reqPath('d1')), 'S10 second file untouched');

        const payload2 = orch.consumeRequest();
        check(payload2 && payload2.reason === 'second', 'S10 fallback dir scanned next');
        check(orch.consumeRequest() === null, 'S10 exhausted dirs -> null');
    }

    // ---- S11: gitRollback throws -> treated as rollback failure ------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ health: [false], rollbackThrows: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S11 ok=false when rollback throws');
        check(r && r.rolledBack === false, 'S11 rolledBack=false when rollback throws');
        check(r && r.attempts === 1, 'S11 attempts=1');
    }

    // ---- S12: rollback ok but second health fails -> final failure ---------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ health: [false, false], rollbackResult: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S12 ok=false after failed post-rollback restart');
        check(r && r.rolledBack === true, 'S12 rolledBack=true reported');
        check(r && r.attempts === 2, 'S12 attempts=2');
        check(fx.seq.filter((s) => s === 'health').length === 2, 'S12 two health checks');
    }

    // ---- S13: spawn throws -> caught, unhealthy path ------------------------
    {
        const fsx = makeFakeFs({
            [reqPath('d1')]: JSON.stringify(validPayload({ checkpoint_ref: null })),
        });
        const fx = makeEffects({ spawnThrows: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S13 ok=false when spawn throws');
        check(fx.seq.filter((s) => s === 'health').length === 0, 'S13 no health check after spawn throw');
    }

    // ---- S14: afterCycle error does not wedge the orchestrator --------------
    {
        const fsx = makeFakeFs({ [reqPath('d1')]: JSON.stringify(validPayload()) });
        const fx = makeEffects({ afterCycleThrows: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        check(orch.state.busy === false, 'S14 busy released despite afterCycle error');
        check(orch.state.lastResult && orch.state.lastResult.ok === true, 'S14 result recorded');
        check(orch.state.cycles === 1, 'S14 cycle counted');
    }

    // ---- S15: start/stop lifecycle ------------------------------------------
    {
        const fsx = makeFakeFs({});
        const fx = makeEffects({});
        const orch = makeOrch(fsx, ['d1'], fx, { pollMs: 60000 });
        check(orch.state.running === false, 'S15 not running before start');
        orch.start();
        check(orch.state.running === true, 'S15 running after start');
        check(orch.state.timer !== null, 'S15 timer set after start');
        const t1 = orch.state.timer;
        orch.start();
        check(orch.state.timer === t1, 'S15 double start is idempotent');
        orch.stop();
        check(orch.state.running === false, 'S15 stopped');
        check(orch.state.timer === null, 'S15 timer cleared');
        orch.stop();
        check(orch.state.timer === null, 'S15 double stop safe');
    }

    // ---- S16: payload without checkpoint key (undefined) --------------------
    {
        const fsx = makeFakeFs({
            [reqPath('d1')]: JSON.stringify({ type: 'self_modify_restart', reason: 'bare' }),
        });
        const fx = makeEffects({ health: [false], rollbackResult: true });
        const orch = makeOrch(fsx, ['d1'], fx);
        await orch.tick();
        const r = orch.state.lastResult;
        check(r && r.ok === false, 'S16 unhealthy bare payload -> fail');
        check(fx.rollbackRefs.length === 0, 'S16 no rollback without checkpoint key');
        check(r && r.reason === 'server unhealthy after restart', 'S16 failure reason set');
    }

    // ---- S17: module surface --------------------------------------------------
    {
        check(RESTART_FILE === 'restart-request.json', 'S17 RESTART_FILE constant');
        const dirs = defaultCandidateDirs(path.join('/', 'repo'));
        check(Array.isArray(dirs) && dirs.length >= 1, 'S17 defaultCandidateDirs returns list');
        check(dirs[0] === path.join('/', 'repo', 'data'), 'S17 dev data dir first');
        if (process.env.LOCALAPPDATA) {
            check(dirs.length === 2, 'S17 packaged dir included when LOCALAPPDATA set');
            check(dirs[1] === path.join(process.env.LOCALAPPDATA, 'DAON Agent System', 'data'),
                'S17 packaged data dir path');
        }
        check(typeof createRestartOrchestrator === 'function', 'S17 factory exported');
    }

    if (fails.length > 0) {
        console.error(`GAP-E3 (node) PROBE FAILED: ${fails.length} of ${checks} checks failed`);
        process.exit(1);
    }
    console.log(`ALL GAP-E3 (node) PROBES PASSED (${checks} checks)`);
}

main().catch((e) => {
    console.error('GAP-E3 (node) PROBE CRASHED:', e && e.stack || e);
    process.exit(1);
});

// -*- coding: utf-8 -*-
// Self-Update pipeline probe: exercises createRestartOrchestrator's new
// rebuildAndSwap / restoreBackup paths under plain node with injectable fakes.
//
// Scenarios:
//   S1 plain restart (rebuild absent)  -> no rebuild call, ok, swapped=false
//   S2 rebuild:true, swap OK, healthy  -> ok, swapped=true, attempts=1
//   S3 rebuild:true, swap OK, unhealthy-> restoreBackup -> respawn healthy
//                                       -> ok, restored=true, attempts=2
//   S4 rebuild:true, swap FAILS        -> old exe kept, plain restart still ok
//   S5 rebuild:true, swap OK, unhealthy, restore FAILS, no checkpoint -> ok:false
//
// [결함④] 스왑 후 오판 방지 시나리오:
//   S6 swap OK, 첫 healthCheck 실패(느린 onefile 부팅), deep 재판정 통과
//      -> 구제됨: restore/롤백 없이 ok, deepVerdict=true
//   S7 swap OK, unhealthy, deep 재판정 실패, restore EBUSY 실패,
//      최종 deep 판정 통과 -> 새 exe 유지, ok, deepVerdict=true
//   S8 회귀: deep 재판정 실패 + restore 성공 -> 기존 복구 경로 그대로 동작
const path = require('path');
const { createRestartOrchestrator } = require(path.join(__dirname, '..', 'electron', 'restart_orchestrator.js'));

function fakeFs() {
    return {
        existsSync: () => false,
        readFileSync: () => '{}',
        unlinkSync: () => { },
    };
}

function makeOrch(opts) {
    const calls = [];
    const deps = {
        fs: fakeFs(),
        dirs: [],
        log: () => { },
        sleep: async () => { },
        killServer: async () => { calls.push('kill'); },
        spawnServer: async () => { calls.push('spawn'); },
        healthCheck: async () => (opts.healthy !== undefined ? opts.healthy : true),
        gitRollback: opts.gitRollback ? async (ref) => { calls.push('rollback:' + ref); return true; } : undefined,
        rebuildAndSwap: opts.rebuildAndSwap === null ? undefined : async () => {
            calls.push('rebuild');
            if (opts.swapThrows) throw new Error('boom-swap');
            return opts.swapResult || { swapped: true };
        },
        restoreBackup: opts.restoreBackup === null ? undefined : async () => {
            calls.push('restore');
            return !opts.restoreFails;
        },
    };
    const orch = createRestartOrchestrator(deps);
    orch._calls = calls;
    return orch;
}

async function main() {
    let pass = 0, fail = 0;
    const check = (name, cond) => {
        if (cond) { pass++; console.log(`PASS ${name}`); }
        else { fail++; console.log(`FAIL ${name}`); }
    };

    // S1: plain restart — rebuild must NOT be invoked even though dep exists.
    {
        const o = makeOrch({ healthy: true });
        const r = await o.performRestart({ reason: 'plain' });
        check('S1 ok', r.ok === true);
        check('S1 swapped=false', r.swapped === false);
        check('S1 rebuilt=false', r.rebuilt === false);
        check('S1 no rebuild call', !o._calls.includes('rebuild'));
        check('S1 attempts=1', r.attempts === 1);
    }

    // S2: rebuild requested, swap succeeds, server healthy on first try.
    {
        const o = makeOrch({ healthy: true });
        const r = await o.performRestart({ reason: 'py changed', rebuild: true });
        check('S2 ok', r.ok === true);
        check('S2 swapped=true', r.swapped === true);
        check('S2 rebuilt=true', r.rebuilt === true);
        check('S2 order kill<rebuild<spawn', o._calls.join(',') === 'kill,rebuild,spawn');
        check('S2 attempts=1', r.attempts === 1);
    }

    // S3: new exe unhealthy -> backup restored -> retry healthy (no checkpoint).
    {
        let first = true;
        const o = makeOrch({
            get healthy() { return first; },
            gitRollback: null,
        });
        // emulate: first healthCheck false, second true after restore-respawn
        o.__deps = null;
        // simpler: build manually
        const calls = [];
        const orch = createRestartOrchestrator({
            fs: fakeFs(), dirs: [], log: () => { }, sleep: async () => { },
            killServer: async () => { calls.push('kill'); },
            spawnServer: async () => { calls.push('spawn'); },
            healthCheck: async () => { calls.push('health'); return calls.filter(c => c === 'health').length >= 2; },
            rebuildAndSwap: async () => { calls.push('rebuild'); return { swapped: true }; },
            restoreBackup: async () => { calls.push('restore'); return true; },
        });
        const r = await orch.performRestart({ reason: 'bad exe', rebuild: true });
        check('S3 ok', r.ok === true);
        check('S3 restored=true', r.restored === true);
        check('S3 rolledBack=false', r.rolledBack === false);
        check('S3 attempts=2', r.attempts === 2);
        check('S3 order kill,rebuild,spawn,health,restore,kill,spawn,health',
            calls.join(',') === 'kill,rebuild,spawn,health,restore,kill,spawn,health');
    }

    // S4: rebuild/swap fails gracefully -> old exe kept, restart still completes.
    {
        const o = makeOrch({ healthy: true, swapResult: { swapped: false, reason: 'rebuild failed (old exe kept)' } });
        const r = await o.performRestart({ reason: 'rb fail', rebuild: true });
        check('S4 ok', r.ok === true);
        check('S4 swapped=false', r.swapped === false);
        check('S4 rebuilt=true(flag)', r.rebuilt === true);
        check('S4 no restore needed', !o._calls.includes('restore'));
    }

    // S5: everything fails — swap ok but unhealthy, restore fails, no checkpoint.
    {
        const o = makeOrch({ healthy: false, restoreFails: true, gitRollback: null });
        const r = await o.performRestart({ reason: 'total fail', rebuild: true });
        check('S5 ok=false', r.ok === false);
        check('S5 restored=false', r.restored === false);
        check('S5 swapped=true', r.swapped === true);
        check('S5 attempts=1(no retry possible)', r.attempts === 1);
    }

    // S6 [결함④②]: slow onefile boot — first healthCheck false, deep re-verdict
    // passes -> rescued WITHOUT touching backup/rollback.
    {
        const calls = [];
        const orch = createRestartOrchestrator({
            fs: fakeFs(), dirs: [], log: () => { }, sleep: async () => { },
            killServer: async () => { calls.push('kill'); },
            spawnServer: async () => { calls.push('spawn'); },
            healthCheck: async () => { calls.push('health'); return false; },
            deepHealthCheck: async () => { calls.push('deep'); return true; },
            rebuildAndSwap: async () => { calls.push('rebuild'); return { swapped: true }; },
            restoreBackup: async () => { calls.push('restore'); return true; },
        });
        const r = await orch.performRestart({ reason: 'slow boot', rebuild: true });
        check('S6 ok', r.ok === true);
        check('S6 deepVerdict=true', r.deepVerdict === true);
        check('S6 attempts=1', r.attempts === 1);
        check('S6 no restore call', !calls.includes('restore'));
        check('S6 order kill,rebuild,spawn,health,deep',
            calls.join(',') === 'kill,rebuild,spawn,health,deep');
    }

    // S7 [결함④③]: deep re-verdict fails, restore hits EBUSY (new exe alive),
    // final deep verdict passes -> keep the new exe, report ok.
    {
        const calls = [];
        let deepCalls = 0;
        const orch = createRestartOrchestrator({
            fs: fakeFs(), dirs: [], log: () => { }, sleep: async () => { },
            killServer: async () => { calls.push('kill'); },
            spawnServer: async () => { calls.push('spawn'); },
            healthCheck: async () => { calls.push('health'); return false; },
            deepHealthCheck: async () => { deepCalls += 1; calls.push('deep'); return deepCalls >= 2; },
            rebuildAndSwap: async () => { calls.push('rebuild'); return { swapped: true }; },
            restoreBackup: async () => { calls.push('restore'); return false; }, // EBUSY
        });
        const r = await orch.performRestart({ reason: 'ebusy rollback', rebuild: true });
        check('S7 ok', r.ok === true);
        check('S7 restored=false', r.restored === false);
        check('S7 deepVerdict=true', r.deepVerdict === true);
        check('S7 attempts=2', r.attempts === 2);
        check('S7 order kill,rebuild,spawn,health,deep,restore,deep',
            calls.join(',') === 'kill,rebuild,spawn,health,deep,restore,deep');
    }

    // S8 regression: deep re-verdict fails AND restore succeeds -> classic
    // recovery path (respawn with known-good backup) must still work.
    {
        const calls = [];
        const orch = createRestartOrchestrator({
            fs: fakeFs(), dirs: [], log: () => { }, sleep: async () => { },
            killServer: async () => { calls.push('kill'); },
            spawnServer: async () => { calls.push('spawn'); },
            healthCheck: async () => { calls.push('health'); return calls.filter(c => c === 'health').length >= 2; },
            deepHealthCheck: async () => { calls.push('deep'); return false; },
            rebuildAndSwap: async () => { calls.push('rebuild'); return { swapped: true }; },
            restoreBackup: async () => { calls.push('restore'); return true; },
        });
        const r = await orch.performRestart({ reason: 'bad exe v2', rebuild: true });
        check('S8 ok', r.ok === true);
        check('S8 restored=true', r.restored === true);
        check('S8 attempts=2', r.attempts === 2);
        check('S8 order kill,rebuild,spawn,health,deep,restore,kill,spawn,health',
            calls.join(',') === 'kill,rebuild,spawn,health,deep,restore,kill,spawn,health');
    }

    console.log(`RESULT pass=${pass} fail=${fail}`);
    process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error('PROBE-ERROR', e); process.exit(2); });

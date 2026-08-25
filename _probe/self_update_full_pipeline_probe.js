// [자가 빌드 완성 2026-08-25] 전체 파이프라인 프로브.
// rebuildAndSwap 의 5단계(미러동기화 → 백업 → 재빌드 → 카나리 → 스왑+리소스갱신)를
// 주입형 deps 로 모의 검증한다. Electron 런타임 불필요.
//
// 실행: node _probe/self_update_full_pipeline_probe.js
// 통과 조건: ALL PIPELINE PROBES PASSED + exit 0

'use strict';

const path = require('path');
const { createSelfUpdate } = require('../electron/self_update.js');

const BUILD_ROOT = 'C:/fake/build-root';
const TARGET_EXE = 'C:/fake/resources/server.exe';

function makeFakeFs() {
    const files = new Set([
        path.join(BUILD_ROOT, '_sync_build.py'),
        path.join(BUILD_ROOT, 'daon-server.spec'),
        path.join(BUILD_ROOT, 'dist', 'server.exe'),
        TARGET_EXE,
        // refreshLooseResources 소스 (existsSync 검사 대상)
        path.join(BUILD_ROOT, 'dist_new', 'api', 'api'),
        path.join(BUILD_ROOT, 'dist_new', 'static'),
        path.join(BUILD_ROOT, 'dist_new', 'index.html'),
        path.join(BUILD_ROOT, 'api', 'agents'),
        path.join(BUILD_ROOT, 'skills'),
    ]);
    const copies = [];
    const cpSyncs = [];
    return {
        files,
        copies,
        cpSyncs,
        existsSync(p) { return files.has(String(p)); },
        copyFileSync(src, dst) { copies.push([String(src), String(dst)]); },
        openSync(p) {
            if (String(p) === TARGET_EXE) return 7; // writable
            throw new Error('ENOENT');
        },
        closeSync() { },
        cpSync(src, dst, opts) { cpSyncs.push([String(src), String(dst), opts]); },
    };
}

function fakeSpawnFactory(script) {
    const calls = [];
    const { EventEmitter } = require('events');
    function spawnFn(cmd, args, opts) {
        calls.push({ cmd, args, cwd: opts && opts.cwd });
        const e = new EventEmitter();
        e.stdout = new EventEmitter();
        e.stderr = new EventEmitter();
        setTimeout(() => {
            if (script) script(e, args);
            else { e.stderr.emit('data', 'ok'); e.emit('exit', 0); }
        }, 5);
        return e;
    }
    return { spawnFn, calls };
}

async function run() {
    let failures = 0;
    function check(name, cond) {
        console.log((cond ? '[PASS] ' : '[FAIL] ') + name);
        if (!cond) failures += 1;
    }

    // ── Test 1: happy path — sync → build → canary → swap → refresh ──
    {
        const fs = makeFakeFs();
        const { spawnFn, calls } = fakeSpawnFactory();
        const su = createSelfUpdate({
            fs,
            spawnFn,
            findTargetExe: () => TARGET_EXE,
            resolveBuildRoot: () => BUILD_ROOT,
            probeHealth: async () => ({ healthy: true, pid: 123 }),
            findFreePort: async () => 8801,
            canaryPollMs: 1,
            sleep: async () => { },
        });
        const r = await su.rebuildAndSwap();
        check('T1 swapped=true', r.swapped === true);
        check('T1 refreshed=true', r.refreshed === true);
        const syncIdx = calls.findIndex((c) => String(c.args[0]).includes('_sync_build.py'));
        const pyIdx = calls.findIndex((c) => c.args.includes('daon-server.spec'));
        check('T1 sync ran before build', syncIdx !== -1 && pyIdx !== -1 && syncIdx < pyIdx);
        check('T1 pyinstaller ran', pyIdx !== -1);
        check('T1 backup made', fs.copies.some(([s, d]) => d === TARGET_EXE + '.bak' && s === TARGET_EXE));
        check('T1 swap done', fs.copies.some(([s, d]) => s === path.join(BUILD_ROOT, 'dist', 'server.exe') && d === TARGET_EXE));
        check('T1 loose resources refreshed (api)', fs.cpSyncs.some(([s]) => s.endsWith(path.join('dist_new', 'api', 'api'))));
        check('T1 loose resources refreshed (static)', fs.cpSyncs.some(([s]) => s.endsWith(path.join('dist_new', 'static'))));
        check('T1 user data NOT touched', !fs.cpSyncs.some(([, d]) => d.endsWith(path.sep + 'data')));
    }

    // ── Test 2: sync script fails → refuse to build ──
    {
        const fs = makeFakeFs();
        const { spawnFn, calls } = fakeSpawnFactory((e) => { e.stderr.emit('data', 'boom'); e.emit('exit', 1); });
        const su = createSelfUpdate({
            fs,
            spawnFn,
            findTargetExe: () => TARGET_EXE,
            resolveBuildRoot: () => BUILD_ROOT,
            probeHealth: async () => ({ healthy: true, pid: 1 }),
            findFreePort: async () => 8802,
            sleep: async () => { },
        });
        const r = await su.rebuildAndSwap();
        check('T2 refused on sync failure', r.swapped === false && /mirror sync failed/.test(r.reason || ''));
        check('T2 no pyinstaller ran', !calls.some((c) => c.args.includes('daon-server.spec')));
    }

    // ── Test 3: _sync_build.py missing → refuse early ──
    {
        const fs = makeFakeFs();
        fs.files.delete(path.join(BUILD_ROOT, '_sync_build.py'));
        const { spawnFn, calls } = fakeSpawnFactory();
        const su = createSelfUpdate({
            fs,
            spawnFn,
            findTargetExe: () => TARGET_EXE,
            resolveBuildRoot: () => BUILD_ROOT,
            sleep: async () => { },
        });
        const r = await su.rebuildAndSwap();
        check('T3 refused when sync script missing', r.swapped === false && /_sync_build.py missing/.test(r.reason || ''));
        check('T3 nothing spawned', calls.length === 0);
    }

    console.log(failures === 0 ? '\nALL PIPELINE PROBES PASSED' : `\n${failures} PROBE(S) FAILED`);
    process.exit(failures === 0 ? 0 : 1);
}

run().catch((e) => { console.error('probe crashed:', e); process.exit(1); });

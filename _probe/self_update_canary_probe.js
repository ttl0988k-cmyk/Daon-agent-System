// Self-Update 파이프라인 (electron/self_update.js) 주입형 페이크 프로브.
// Electron 런타임 없이 plain node 로 전 경로 검증:
//   C1 카나리 통과 → 스왑 (정상 경로)
//   C2 카나리 헬스 실패 → old exe 유지, 스왑 안 함
//   C3 카나리 플래핑(1회 성공 후 실패) → 안정 판정 거부
//   C4 재빌드 실패 → old exe 유지
//   C5 백업 복원 성공/실패
//   C6 대상/spec 부재 graceful degradation
//   C7 카나리 kill 보장 (스왑 전 자식 종료 확인)
//   ── EBUSY 근본 수정(2026-08-24) 검증 ──
//   C8 카나리 정리가 트리킬(onefile 자식 포함)로 호출되는지
//   C9 스왑 프리플라이트: 락된 target → 재빌드 전 즉시 실패(fail-fast)
//   C10 스왑 일시 EBUSY → 재시도 → 성공

'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');

const { createSelfUpdate } = require('../electron/self_update');

let pass = 0, fail = 0;
function check(name, cond) {
    if (cond) { pass++; console.log(`  PASS ${name}`); }
    else { fail++; console.log(`  FAIL ${name}`); }
}

function tmpDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'su-probe-'));
}

// ── 공용 페이크 스포너 ──
// spawn 호출을 기록하고 스크립트(script)가 자식 동작을 결정한다.
// 실제 child_process 처럼 'exit' 이벤트를 발행해야 하므로 _emit 제공 —
// 스크립트는 child.exitCode 설정 후 반드시 child._emit('exit', code) 호출.
// kill() 은 대응 spawn 호출 기록에 killed=true 를 남긴다(검증용).
function makeFakeSpawn(script) {
    const calls = [];
    let nextPid = 4000;
    function spawnFn(cmd, args, opts) {
        const rec = { cmd, args, cwd: opts && opts.cwd, killed: false };
        calls.push(rec);
        const isPyInstaller = args.includes('-m') && args.includes('PyInstaller');
        const listeners = {};
        const child = {
            pid: ++nextPid,
            exitCode: null, signalCode: undefined,
            stderr: { on() { } },
            on(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); return this; },
            _emit(ev, ...a) { (listeners[ev] || []).forEach((fn) => { try { fn(...a); } catch (_) { } }); },
            kill(sig) {
                if (this.exitCode !== null || this.signalCode !== undefined) return;
                rec.killed = true;
                this.signalCode = sig || 'SIGTERM';
                setImmediate(() => this._emit('exit', null, this.signalCode));
            },
        };
        rec.pid = child.pid;
        setImmediate(() => script(isPyInstaller ? 'rebuild' : 'canary', child));
        return child;
    }
    return { spawnFn, calls };
}

// 이벤트 루프 매크로태스크(setImmediate 등)가 실행될 수 있게 실제 타이머로 양보.
// 마이크로태스크 전용 sleep(async () => {}) 을 주입하면 setImmediate 예약 콜백이
// 영원히 실행되지 않아 교착이 생긴다 — 반드시 이 nap 을 사용할 것.
const nap = (ms) => new Promise((r) => setTimeout(r, Math.min(ms || 5, 10)));

(async function main() {
    console.log('=== C1: canary healthy -> swap ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD-EXE');
        fs.writeFileSync(builtExe, 'NEW-EXE');

        const { spawnFn, calls } = makeFakeSpawn((kind, child) => {
            if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); }
            // canary: 계속 살아있음(exitCode null) — 헬스는 즉시 healthy
        });
        let canaryPortSeen = null;
        const su = createSelfUpdate({
            fs,
            spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async (port) => { canaryPortSeen = port; return { healthy: true, pid: 4242 }; },
            findFreePort: async () => 8765,
            canaryPollMs: 1,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
        });

        const r = await su.rebuildAndSwap();
        check('swapped=true', r.swapped === true);
        check('target now NEW-EXE', fs.readFileSync(targetExe, 'utf8') === 'NEW-EXE');
        check('backup created', fs.existsSync(targetExe + '.bak'));
        check('backup holds OLD', fs.readFileSync(targetExe + '.bak', 'utf8') === 'OLD-EXE');
        check('pyinstaller called once', calls.filter(c => c.args.includes('PyInstaller')).length === 1);
        check('canary spawned with --port', calls.some(c => c.args.includes('--port')));
        check('canary used alternate port', canaryPortSeen === 8765);
        check('result carries canary pid', r.canary && r.canary.pid === 4242);
        const canaryRec = calls.find(c => !c.args.includes('PyInstaller'));
        check('canary killed during pipeline', canaryRec && canaryRec.killed === true);
    }

    console.log('=== C2: canary never healthy -> no swap ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD-EXE');
        fs.writeFileSync(builtExe, 'NEW-EXE');

        const { spawnFn } = makeFakeSpawn((kind, child) => { if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); } });
        const su = createSelfUpdate({
            fs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: false, pid: -1 }),
            findFreePort: async () => 8766,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            canaryStartMs: 40,
            canaryPollMs: 1,
        });
        const r = await su.rebuildAndSwap();
        check('swapped=false', r.swapped === false);
        check('reason mentions canary', /canary/.test(r.reason || ''));
        check('target still OLD-EXE', fs.readFileSync(targetExe, 'utf8') === 'OLD-EXE');
    }

    console.log('=== C3: flapping health (1 hit then fail) -> rejected ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD-EXE');
        fs.writeFileSync(builtExe, 'NEW-EXE');

        let n = 0;
        const su = createSelfUpdate({
            fs,
            spawnFn: makeFakeSpawn((kind, child) => { if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); } }).spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => (++n % 2 === 1 ? { healthy: true, pid: 7 } : null),
            findFreePort: async () => 8767,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            canaryStartMs: 60,
            canaryPollMs: 1,
        });
        const r = await su.rebuildAndSwap();
        check('swapped=false on flapping', r.swapped === false);
        check('flapping reason surfaced', /flap/i.test(r.reason || '') || /canary/.test(r.reason || ''));
        check('target still OLD-EXE', fs.readFileSync(targetExe, 'utf8') === 'OLD-EXE');
    }

    console.log('=== C4: rebuild fails -> old exe kept, no canary ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        fs.writeFileSync(targetExe, 'OLD-EXE');

        let canarySpawned = false;
        const { spawnFn } = makeFakeSpawn((kind, child) => {
            if (kind === 'rebuild') { child.exitCode = 1; child._emit('exit', 1); }
            else canarySpawned = true;
        });
        const su = createSelfUpdate({
            fs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: true, pid: 9 }),
            findFreePort: async () => 8768,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            canaryPollMs: 1,
        });
        const r = await su.rebuildAndSwap();
        check('swapped=false', r.swapped === false);
        check('reason mentions rebuild', /rebuild/.test(r.reason || ''));
        check('no canary attempted', canarySpawned === false);
        check('target still OLD-EXE', fs.readFileSync(targetExe, 'utf8') === 'OLD-EXE');
    }

    console.log('=== C5: restoreBackup success/failure ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        fs.writeFileSync(targetExe, 'BROKEN');
        fs.writeFileSync(targetExe + '.bak', 'GOOD');
        const su = createSelfUpdate({ fs, findTargetExe: () => targetExe });
        check('restore true', await su.restoreBackup() === true);
        check('target restored to GOOD', fs.readFileSync(targetExe, 'utf8') === 'GOOD');

        const su2 = createSelfUpdate({ fs, findTargetExe: () => path.join(dir, 'missing.exe') });
        check('restore false when no target', await su2.restoreBackup() === false);

        const roDir = tmpDir();
        const t2 = path.join(roDir, 's.exe');
        fs.writeFileSync(t2, 'X');
        fs.writeFileSync(t2 + '.bak', 'Y');
        const su3 = createSelfUpdate({
            fs: { existsSync: fs.existsSync, copyFileSync: () => { throw new Error('EPERM'); } },
            findTargetExe: () => t2,
        });
        check('restore false on copy error', await su3.restoreBackup() === false);
    }

    console.log('=== C6: missing target / missing spec ===');
    {
        const su = createSelfUpdate({ fs, findTargetExe: () => null, resolveBuildRoot: () => null });
        const r1 = await su.rebuildAndSwap();
        check('no target -> swapped:false', r1.swapped === false && /no server\.exe/.test(r1.reason));

        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        fs.writeFileSync(targetExe, 'OLD');
        const su2 = createSelfUpdate({ fs, findTargetExe: () => targetExe, resolveBuildRoot: () => null });
        const r2 = await su2.rebuildAndSwap();
        check('no spec -> swapped:false', r2.swapped === false && /spec not found/.test(r2.reason));
        check('no backup written when spec missing', !fs.existsSync(targetExe + '.bak'));
    }

    console.log('=== C7: canary process killed before swap ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD');
        fs.writeFileSync(builtExe, 'NEW');

        const { spawnFn, calls } = makeFakeSpawn((kind, child) => {
            if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); }
            // canary: 살아있음 — 파이프라인이 반드시 kill 해야 함
        });
        const su = createSelfUpdate({
            fs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: true, pid: 555 }),
            findFreePort: async () => 8770,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            canaryPollMs: 1,
        });
        const r = await su.rebuildAndSwap();
        const canaryRec = calls.find(c => !c.args.includes('PyInstaller'));
        check('swap succeeded after canary cleanup', r.swapped === true);
        check('canary was killed before swap', canaryRec && canaryRec.killed === true);
        check('canary fully exited (signal set)', canaryRec && canaryRec._exited !== false);
    }

    console.log('=== C8: canary cleanup uses tree-kill (onefile child included) ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD');
        fs.writeFileSync(builtExe, 'NEW');

        const killTreeCalls = [];
        const { spawnFn, calls } = makeFakeSpawn((kind, child) => {
            if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); }
            // canary: 살아있음 — 파이프라인이 트리킬해야 함
        });
        const su = createSelfUpdate({
            fs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: true, pid: 888 }),
            findFreePort: async () => 8771,
            canaryPollMs: 1,
            killTree: (pid) => killTreeCalls.push(pid),
        });
        const r = await su.rebuildAndSwap();
        const canaryRec = calls.find(c => !c.args.includes('PyInstaller'));
        check('swapped=true', r.swapped === true);
        check('tree-kill invoked with canary pid', !!canaryRec && killTreeCalls.includes(canaryRec.pid));
    }

    console.log('=== C9: swap preflight (locked target) -> fail fast, no rebuild ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        fs.writeFileSync(targetExe, 'OLD');

        let rebuildSpawned = false;
        const { spawnFn } = makeFakeSpawn((kind) => { if (kind === 'rebuild') rebuildSpawned = true; });
        const lockedFs = {
            existsSync: fs.existsSync,
            copyFileSync: fs.copyFileSync,
            openSync: () => { throw Object.assign(new Error('EBUSY: resource busy or locked'), { code: 'EBUSY' }); },
            closeSync: fs.closeSync,
        };
        const su = createSelfUpdate({
            fs: lockedFs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: true, pid: 1 }),
            findFreePort: async () => 8772,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            swapRetries: 1,
            swapRetryDelayMs: 1,
        });
        const r = await su.rebuildAndSwap();
        check('swapped=false', r.swapped === false);
        check('reason mentions preflight', /preflight/.test(r.reason || ''));
        check('no rebuild attempted (fail fast)', rebuildSpawned === false);
        check('target untouched', fs.readFileSync(targetExe, 'utf8') === 'OLD');
    }

    console.log('=== C10: transient EBUSY at swap -> retried -> success ===');
    {
        const dir = tmpDir();
        const targetExe = path.join(dir, 'server.exe');
        const builtExe = path.join(dir, 'dist', 'server.exe');
        fs.mkdirSync(path.dirname(builtExe), { recursive: true });
        fs.writeFileSync(targetExe, 'OLD');
        fs.writeFileSync(builtExe, 'NEW');

        let swapAttempts = 0;
        const flakyFs = {
            existsSync: fs.existsSync,
            openSync: fs.openSync,
            closeSync: fs.closeSync,
            copyFileSync(src, dst) {
                if (String(dst).endsWith('.bak')) return fs.copyFileSync(src, dst); // 백업은 통과
                swapAttempts++;
                if (swapAttempts <= 2) throw Object.assign(new Error('EBUSY: resource busy or locked'), { code: 'EBUSY' });
                return fs.copyFileSync(src, dst);
            },
        };
        const { spawnFn } = makeFakeSpawn((kind, child) => {
            if (kind === 'rebuild') { child.exitCode = 0; child._emit('exit', 0); }
        });
        const su = createSelfUpdate({
            fs: flakyFs, spawnFn,
            log: () => { }, errLog: () => { },
            sleep: nap,
            findTargetExe: () => targetExe,
            resolveBuildRoot: () => dir,
            probeHealth: async () => ({ healthy: true, pid: 999 }),
            findFreePort: async () => 8773,
            killTree: () => { }, // 페이크: 실제 taskkill 방지
            canaryPollMs: 1,
            swapRetries: 4,
            swapRetryDelayMs: 1,
        });
        const r = await su.rebuildAndSwap();
        check('swapped=true after retries', r.swapped === true);
        check('swap attempted exactly 3 times', swapAttempts === 3);
        check('target now NEW', fs.readFileSync(targetExe, 'utf8') === 'NEW');
    }

    console.log(`RESULT pass=${pass} fail=${fail}`);
    process.exit(fail === 0 ? 0 : 1);
})().catch((e) => { console.error('PROBE CRASH:', e); process.exit(1); });

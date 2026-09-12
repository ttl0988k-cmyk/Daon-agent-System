// @ts-check
/**
 * Self-Update Build Root 회귀 테스트
 *
 * 배경(근본 원인): Phase 3 모듈 분해 커밋(f4c1a73, 2026-09-08)에서
 * resolveBuildRoot() 이관이 누락되어, packaged 앱에서 buildRoot 가
 * `path.join(__dirname, '..')` = <앱>/resources 로 무검증 해석됐다.
 * 그곳에 daon-server.spec / _sync_build.py 가 없어 request_server_update
 * (rebuild=true) 가 2026-09-07 이후 조용히 거부됐다(restart_orchestrator 는
 * rebuild 실패를 비치명적으로 처리하므로 사용자에게 표면화되지 않음).
 *
 * 이 테스트가 고정하는 계약:
 *   G1. main.js 정적 배선 — resolveBuildRoot() 존재 + resourcesPath 후보 +
 *       spec 검증 + 검증된 buildRoot 주입(무검증 repoRoot 금지) + repoRoot 가드
 *   G2. self_update — build root 에 spec 부재 시 '빌드 전 즉시' 거부
 *   G3. self_update — _sync_build.py 부재 시 거부(stale 미러 방지)
 *   G4. self_update — 느슨한 리소스 갱신이 dist_new 미러 기준(정합화)
 *   G5. restart_orchestrator — 후보 STATE_DIR 규약 유지
 *   G6. ServerSupervisor — 재시작 루프 방어(점유자 제거 선행 + 서킷브레이커)
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { createSelfUpdate } = require('../electron/self_update');
const { defaultCandidateDirs } = require('../electron/restart_orchestrator');

const REPO = path.join(__dirname, '..');
let checks = 0;
function ok(cond, msg) {
    checks += 1;
    assert.ok(cond, msg);
}

function readSrc(rel) {
    return fs.readFileSync(path.join(REPO, rel), 'utf-8');
}

// ── G1: main.js 정적 배선 (회귀 방지) ──
function group1() {
    const src = readSrc(path.join('electron', 'main.js'));

    ok(/function\s+resolveBuildRoot\s*\(/.test(src),
        'G1: resolveBuildRoot() 정의가 복원되어야 한다');
    ok(/process\.resourcesPath/.test(src),
        'G1: packaged 후보(process.resourcesPath)를 포함해야 한다');
    ok(/fs\.existsSync\(path\.join\(c,\s*'daon-server\.spec'\)\)/.test(src),
        'G1: 후보를 daon-server.spec 존재로 검증해야 한다');
    ok(/resolveBuildRoot:\s*\(\)\s*=>\s*buildRoot/.test(src),
        'G1: 검증된 buildRoot 를 selfUpdate 에 주입해야 한다');
    ok(!/resolveBuildRoot:\s*\(\)\s*=>\s*repoRoot/.test(src),
        'G1: 무검증 repoRoot 직접 주입은 금지(회귀 방지)');

    ok(/function\s+resolveRepoRoot\s*\(/.test(src),
        'G1: git 롤백용 repoRoot 해석 함수가 분리되어야 한다');
    ok(/if\s*\(!repoRoot\)/.test(src),
        'G1: gitRollback 에 repoRoot 사전 가드가 있어야 한다');
}

// ── G2/G3: rebuildAndSwap 조기 거부 규약 ──
async function group23(tmp) {
    const targetExe = path.join(tmp, 'server.exe');
    fs.writeFileSync(targetExe, 'MZ-dummy');

    const base = {
        fs,
        log: () => { },
        errLog: () => { },
        sleep: async () => { },
        findTargetExe: () => targetExe,
        swapRetries: 0,
    };

    // G2: build root 에 spec 부재 → spec 검증에서 즉시 거부
    const noSpec = path.join(tmp, 'root-no-spec');
    fs.mkdirSync(noSpec, { recursive: true });
    const suNoSpec = createSelfUpdate({ ...base, resolveBuildRoot: () => noSpec });
    const r2 = await suNoSpec.rebuildAndSwap();
    ok(r2 && r2.swapped === false, 'G2: spec 부재 시 스왑하지 않는다');
    ok(/daon-server\.spec missing/.test(String(r2.reason)),
        `G2: 사유에 spec 부재가 명시되어야 한다 (got: ${r2.reason})`);

    // G3: spec 은 있으나 _sync_build.py 부재 → stale 미러 방지 거부
    const specOnly = path.join(tmp, 'root-spec-only');
    fs.mkdirSync(specOnly, { recursive: true });
    fs.writeFileSync(path.join(specOnly, 'daon-server.spec'), '# minimal spec');
    const suSpecOnly = createSelfUpdate({ ...base, resolveBuildRoot: () => specOnly });
    const r3 = await suSpecOnly.rebuildAndSwap();
    ok(r3 && r3.swapped === false, 'G3: _sync_build.py 부재 시 스왑하지 않는다');
    ok(/_sync_build\.py missing/.test(String(r3.reason)),
        `G3: 사유에 _sync_build.py 부재가 명시되어야 한다 (got: ${r3.reason})`);
}

// ── G4: self_update 소스 계약 ──
function group4() {
    const src = readSrc(path.join('electron', 'self_update.js'));
    ok(/daon-server\.spec missing/.test(src),
        'G4: self_update 에도 spec 이중 검증이 있어야 한다(무검증 경로 방어)');
    ok(/'dist_new\/hermes-agent'/.test(src),
        'G4: hermes-agent 를 dist_new 미러에서 갱신해야 한다');
    ok(/'dist_new\/skills'/.test(src),
        'G4: skills 를 레포 원본이 아닌 dist_new 미러에서 갱신해야 한다');
    ok(/'dist_new\/config\.yaml'/.test(src),
        'G4: config.yaml 미러 갱신이 포함되어야 한다');
    ok(!/'skills',\s*'skills'\]/.test(src),
        'G4: 레포 원본 skills 직접 복사(미러 불일치)는 제거되어야 한다');
}

// ── G5: restart_orchestrator 후보 디렉터리 규약 ──
function group5(tmp) {
    const repo = path.join(tmp, 'fake-repo');
    const dirs = defaultCandidateDirs(repo);
    ok(dirs.some((d) => d === path.join(repo, 'data')),
        'G5: dev 후보(<repoRoot>/data)가 유지되어야 한다');
    if (process.env.LOCALAPPDATA) {
        ok(dirs.some((d) => d.indexOf('DAON Agent System') !== -1),
            'G5: packaged 후보(%LOCALAPPDATA%/DAON Agent System/data)가 유지되어야 한다');
    }
}

// ── G6: ServerSupervisor 재시작 루프 방어 정적 계약 ──
function group6() {
    const src = readSrc(path.join('electron', 'src', 'ServerSupervisor.js'));

    ok(/CRASH_WINDOW_MS/.test(src), 'G6: 즉사 판정 창 상수가 있어야 한다');
    ok(/MAX_CRASH_STREAK/.test(src), 'G6: 서킷브레이커 상수가 있어야 한다');
    ok(/_scheduleAutoRespawn\s*\(/.test(src), 'G6: 자동 재시작 단일 관문이 있어야 한다');
    ok(/CIRCUIT BREAKER open/.test(src), 'G6: 서킷브레이커 개방 로그가 있어야 한다');
    ok(/Auto-restarting Python server in 2s/.test(src) === false,
        'G6: 무조건 2초 재시작(무한 루프 원인) 문구는 제거되어야 한다');

    // 재스폰 직전 잔존 포트 점유자 제거가 startPythonProcess 보다 먼저 와야 한다.
    const begin = src.indexOf('_scheduleAutoRespawn(port, cause)');
    const end = src.indexOf('// ── Port & Socket Utilities ──');
    ok(begin !== -1 && end !== -1 && end > begin, 'G6: 관문 본문 구간을 찾을 수 있어야 한다');
    const body = src.slice(begin, end);
    const iKill = body.indexOf('killPortOwner(port)');
    const iStart = body.indexOf('startPythonProcess(port)');
    ok(iKill !== -1, 'G6: 재스폰 전 killPortOwner(port) 호출이 있어야 한다');
    ok(iStart !== -1 && iKill < iStart,
        'G6: killPortOwner 가 startPythonProcess 보다 먼저 호출되어야 한다');
}

// ── G7: 패키징 자기완결 — 두 빌드 설정의 extraResources 정합 ──
function extractYmlPairs(src) {
    const pairs = [];
    const re = /-\s*from:\s*(\S+)\s*\n\s*to:\s*(\S+)/g;
    let m;
    while ((m = re.exec(src)) !== null) pairs.push([m[1], m[2]]);
    return pairs;
}

function group7() {
    const pkg = JSON.parse(readSrc('package.json'));
    const entries = (pkg.build && pkg.build.extraResources) || [];
    const toSet = new Set(entries.map((e) => e.to));

    // 자기완결 번들 계약 — 항상 검증 (electron-builder.yml 은 .gitignore 대상이므로
    // 신규 클론에는 없을 수 있다. 그 경우 package.json 만으로도 계약은 성립한다.)
    ok(entries.length > 0, 'G7: extraResources 가 정의되어야 한다');
    for (const t of ['_sync_build.py', 'daon-server.spec', 'server.py', 'tts_server.py', 'api/api', 'api/agents']) {
        ok(toSet.has(t), `G7: 재빌드 자기완결 번들 '${t}' 가 포함되어야 한다`);
    }

    // electron-builder.yml 이 존재할 때만 두 설정의 parity 를 검증한다.
    // (gitignore 대상이므로 신규 클론/CI 에서는 부재가 정상이다.)
    const ymlPath = path.join(REPO, 'electron-builder.yml');
    if (!fs.existsSync(ymlPath)) {
        console.log('  (i) electron-builder.yml 부재(.gitignore) — package.json 단독 계약으로 검증');
        return;
    }
    const ymlPairs = extractYmlPairs(readSrc('electron-builder.yml')).map((p) => p[0] + '>' + p[1]).sort();
    const pkgPairs = entries.map((e) => e.from + '>' + e.to).sort();
    ok(ymlPairs.length > 0, 'G7: electron-builder.yml extraResources 가 파싱되어야 한다');
    ok(JSON.stringify(pkgPairs) === JSON.stringify(ymlPairs),
        'G7: package.json 과 electron-builder.yml 의 extraResources 가 동일해야 한다');
}

// ── G8: _sync_build.py / spec 이 요구하는 소스 트리 번들 계약 ──
function group8() {
    const syncSrc = readSrc('_sync_build.py');
    const pkg = JSON.parse(readSrc('package.json'));
    const toSet = new Set(((pkg.build && pkg.build.extraResources) || []).map((e) => e.to));

    const dirBlock = /DIR_PAIRS\s*=\s*\[([\s\S]*?)\]/.exec(syncSrc);
    const fileBlock = /FILE_PAIRS\s*=\s*\[([\s\S]*?)\]/.exec(syncSrc);
    ok(dirBlock && fileBlock, 'G8: _sync_build.py 의 DIR_PAIRS/FILE_PAIRS 를 찾을 수 있어야 한다');

    const srcs = [];
    for (const block of [dirBlock[1], fileBlock[1]]) {
        for (const m of block.matchAll(/\('([^']+)',\s*'([^']+)'\)/g)) srcs.push(m[1]);
    }
    ok(srcs.length > 0, 'G8: 동기화 소스 목록이 비어서는 안 된다');
    // _sync_build.py 는 build root(=resources)를 cwd 로 실행되므로 소스는 그 루트에 있어야 한다.
    // api/api 처럼 중첩 경로 소스는 소스 레이아웃을 그대로 보존해 번들해야 한다.
    for (const s of srcs) {
        ok(toSet.has(s), `G8: _sync_build.py 소스 '${s}' 가 resources 루트에 번들되어야 한다`);
    }

    // spec datas 의 리터럴 'api/agents' 는 resources/api/agents 로 번들되어야 한다.
    const specSrc = readSrc('daon-server.spec');
    ok(/'api\/agents'/.test(specSrc), 'G8: spec datas 가 api/agents 소스를 요구한다');
    ok(toSet.has('api/agents'), 'G8: spec datas 요구 경로 api/agents 가 번들되어야 한다');
}

async function main() {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'daon-selfupdate-'));
    try {
        group1();
        console.log('✓ G1: main.js build root 배선 계약 확인');
        await group23(tmp);
        console.log('✓ G2/G3: rebuildAndSwap 조기 거부 규약 확인');
        group4();
        console.log('✓ G4: self_update 미러/이중검증 계약 확인');
        group5(tmp);
        console.log('✓ G5: restart_orchestrator STATE_DIR 규약 확인');
        group6();
        console.log('✓ G6: ServerSupervisor 재시작 루프 방어 계약 확인');
        group7();
        console.log('✓ G7: 패키징 자기완결 — extraResources 정합 확인');
        group8();
        console.log('✓ G8: _sync_build.py/spec 소스 트리 번들 계약 확인');

        console.log(`\nSUMMARY checks=${checks} fail=0`);
        process.exit(0);
    } finally {
        try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (_) { }
    }
}

main().catch((e) => {
    console.error('FAIL:', (e && e.message) || e);
    process.exit(1);
});

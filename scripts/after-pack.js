// after-pack.js — electron-builder afterPack hook (.cmd 삭제 함정 항구 대책).
//
// 배경: `npx electron-builder` 실행마다 dist\win-unpacked\DAON Agent System.cmd가
// 삭제되는 함정이 반복 발생했다. 바탕화면/시작 메뉴 바로가기 전부 .cmd를
// 대상으로 하므로 없으면 앱 실행이 깨진다.
//
// afterPack은 앱 디렉터리(win-unpacked) 구성 직후, NSIS 설치본 빌드 직전에
// 실행된다. 여기서 .cmd를 재생성하면 win-unpacked은 물론 설치본에도 포함된다.
// 상세: skills/System/daon-self-knowledge/SKILL.md 3절.

const fs = require('fs');
const path = require('path');

exports.default = async function afterPack(context) {
    if (context.electronPlatformName !== 'win32') {
        return;
    }
    const productName = context.packager.appInfo.productFilename;
    const cmdPath = path.join(context.appOutDir, productName + '.cmd');
    const content = [
        '@echo off',
        'rem DAON Agent System launcher',
        'set "ELECTRON_RUN_AS_NODE="',
        'start "" "%~dp0' + productName + '.exe" --remote-debugging-port=9222',
        '',
    ].join('\r\n');
    fs.writeFileSync(cmdPath, content);
    console.log('[afterPack] launcher cmd ensured: ' + cmdPath);

    // ── 자동 동기화: 빌드 직후 (설치본 + 포터블) 로 app.asar / server.exe 복사 ──
    // ⚠ 설치 폴더명 혼선 주의: productName 은 공백 "DAON Agent System" 이지만
    //   실제 설치 폴더는 하이픈/소문자 "daon-agent-system" 이다. 과거에는 공백
    //   경로만 조회해 자동동기화가 "조용히" 실패 → 배포본만 옛 버전으로 남았다.
    //   두 이름을 모두 후보로 탐색한다. (scripts/lib/daon_paths.ps1 과 동일 규칙)
    const localPrograms = path.join(
        process.env.LOCALAPPDATA || 'C:\\Users\\ttl09\\AppData\\Local', 'Programs');
    const appDirs = [
        'C:\\daon\\DAON-Portable',
        path.join(localPrograms, 'daon-agent-system'), // 실제(하이픈)
        path.join(localPrograms, productName),         // 레거시(공백)
    ];
    const srcRes = path.join(context.appOutDir, 'resources');
    // server.exe = PyInstaller 백엔드 번들(핵심 버그픽스가 들어있는 파일).
    // 느슨한 리소스(spec/store)는 지인용 빌드에서 sanitize 될 수 있어 제외한다.
    const filesToSync = ['app.asar', 'server.exe'];
    for (const appDir of appDirs) {
        const targetRes = path.join(appDir, 'resources');
        if (!fs.existsSync(targetRes)) { continue; }
        for (const fname of filesToSync) {
            const src = path.join(srcRes, fname);
            if (!fs.existsSync(src)) { continue; }
            try {
                fs.copyFileSync(src, path.join(targetRes, fname));
                console.log('[afterPack] Auto-synced ' + fname + ' -> ' + targetRes);
            } catch (e) {
                console.warn('[afterPack] Auto-sync failed for ' + fname +
                    ' @ ' + appDir + ':', e && e.message);
            }
        }
    }
};

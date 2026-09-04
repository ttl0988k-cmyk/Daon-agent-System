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

    // ── 자동 동기화: 빌드 직후 설치본 및 포터블 경로로 app.asar 자동 복사 ──
    const targets = [
        'C:\\daon\\DAON-Portable',
        path.join(process.env.LOCALAPPDATA || 'C:\\Users\\ttl09\\AppData\\Local', 'Programs', productName)
    ];
    const srcAsar = path.join(context.appOutDir, 'resources', 'app.asar');
    if (fs.existsSync(srcAsar)) {
        for (const targetDir of targets) {
            try {
                const targetRes = path.join(targetDir, 'resources');
                if (fs.existsSync(targetRes)) {
                    fs.copyFileSync(srcAsar, path.join(targetRes, 'app.asar'));
                    console.log('[afterPack] Auto-synced app.asar -> ' + targetRes);
                }
            } catch (e) {
                console.warn('[afterPack] Auto-sync failed for ' + targetDir + ':', e && e.message);
            }
        }
    }
};

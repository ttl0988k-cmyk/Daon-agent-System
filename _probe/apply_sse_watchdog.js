/**
 * [2026-08-31 SSE 끊김 감시 + 빠른 재연결] static/modules/chat.js 패치
 *
 * 구조적 문제: SSE 연결이 끊긴 채 화면이 멈추면(이벤트 미수신) 도구 카드/음성/
 * 토큰이 모두 안 보인다. 기존 복구는 idle 워치독(30초 무이벤트)에 의존했고,
 * EventSource의 자동 재연결(CONNECTING)이 장기화되면 그대로 정체됐다.
 *
 * 해결:
 *  1) SSE open/error 이벤트로 연결 상태를 추적하고,
 *  2) error 후 10초 내 open(재연결 성공)이 없으면 즉시 수동 복구 경로
 *     (_handleIdleTimeout → 백엔드 상태 확인 → 재연결/세션 복구)를 가동한다.
 *  3) 로드된 chat.js 버전을 콘솔에 출력해 사용자/에이전트가 캐시 문제를
 *     즉시 판별할 수 있게 한다.
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
    console.log(`줄바꿈 정규화: ${beforeMixed}개 정리`);
}
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const patches = [
    {
        name: '1) 파일 최상단에 버전 로그 추가 (캐시 판별용)',
        find: 'function getModelDisplayName(modelId) {',
        replace: [
            '// [2026-08-31] 로드 버전 표시 — F12 콘솔에서 캐시 문제를 즉시 판별하기 위함.',
            '// sync_to_installed.ps1이 index.html의 ?v=NN을 자동 올리므로, 콘솔의 v 번호와',
            '// index.html의 v 번호가 다르면 캐시 문제다.',
            '(function () {',
            '  try {',
            "    var _m = null;",
            "    try { _m = (document.currentScript && document.currentScript.src || '').match(/v=(\\d+)/); } catch (_) { }",
            "    console.log('[DAON] chat.js loaded (v=' + ((_m && _m[1]) || '?') + ', build=2026-08-31-sse-watchdog)');",
            '  } catch (_) { }',
            '})();',
            '',
            'function getModelDisplayName(modelId) {',
        ].join('\n'),
    },
    {
        name: '2) SSE 오류 시각 추적 변수 선언',
        find: [
            '  // 백엔드는 활성인데 EventSource 연결이 끊겼을 때 같은 stream_id로',
            '  // 재연결을 시도한 횟수 (최대 3회, plan.md Phase 2).',
            '  let _sseReconnects = 0;',
        ].join('\n'),
        replace: [
            '  // 백엔드는 활성인데 EventSource 연결이 끊겼을 때 같은 stream_id로',
            '  // 재연결을 시도한 횟수 (최대 3회, plan.md Phase 2).',
            '  let _sseReconnects = 0;',
            '  // [2026-08-31 SSE 끊김 감시] 마지막 SSE error 이벤트 시각 (0=정상).',
            '  // error 후 10초 내 open(재연결 성공)이 없으면 수동 복구를 가동한다 —',
            '  // EventSource 자동 재연결(CONNECTING)이 장기화돼 화면이 조용히 멈추는',
            '  // "도구 카드/음성/토큰 전부 미수신" 증상의 구조적 해결책.',
            '  let _sseErrorTs = 0;',
        ].join('\n'),
    },
    {
        name: '3) SSE 연결 후 open/error 추적 + 10초 감시 인터벌',
        find: [
            '    // Connect to SSE endpoint',
            '    sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);',
            '    State.currentEventSource = sse;',
            '    // Start the no-event watchdog immediately.  Previously it was only',
            '    // started after the first token/tool/reasoning event, so a backend run',
            '    // that produced no SSE event could leave the input locked forever.',
            '    resetIdleTimer();',
        ].join('\n'),
        replace: [
            '    // Connect to SSE endpoint',
            '    sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);',
            '    State.currentEventSource = sse;',
            '    // Start the no-event watchdog immediately.  Previously it was only',
            '    // started after the first token/tool/reasoning event, so a backend run',
            '    // that produced no SSE event could leave the input locked forever.',
            '    resetIdleTimer();',
            '',
            '    // ── [2026-08-31 SSE 끊김 감시] error 후 10초 내 재연결 안 되면 수동 복구 ──',
            '    // EventSource는 끊기면 CONNECTING 상태로 자동 재연결을 반복 시도하지만,',
            '    // 서버/네트워크 상황에 따라 장기화된다. 그 동안 tool/token/speak 이벤트가',
            '    // 전부 유실돼 "도구 카드도 음성도 없는" 정체 화면이 된다. error 시각을',
            '    // 기록해 두고 10초 내 open이 없으면 _handleIdleTimeout()을 즉시 호출해',
            '    // 백엔드 상태 확인 → 수동 재연결/세션 복구 경로를 강제 가동한다.',
            '    _sseErrorTs = 0;',
            "    sse.addEventListener('open', function () { _sseErrorTs = 0; });",
            "    sse.addEventListener('error', function () {",
            '      if (_sseErrorTs === 0) {',
            '        _sseErrorTs = Date.now();',
            "        console.warn('[SSE-DIAG] ⚠️ connection error — watching for reconnect (10s)');",
            '      }',
            '    });',
            '    (function _sseWatchdog() {',
            '      var _iv = setInterval(function () {',
            '        try {',
            '          if (_streamFinished || !sse) { clearInterval(_iv); return; }',
            '          if (_sseErrorTs > 0 && Date.now() - _sseErrorTs > 10000) {',
            '            clearInterval(_iv);',
            "            console.warn('[SSE-DIAG] 🔌 no reconnect within 10s — forcing recovery path');",
            '            _sseErrorTs = 0;',
            '            _idleRecoveryInFlight = false;  // 수동 복구가 막히지 않게 해제',
            '            _handleIdleTimeout();',
            '          }',
            '        } catch (_wErr) { clearInterval(_iv); }',
            '      }, 3000);',
            '    })();',
        ].join('\n'),
    },
];

let applied = 0;
for (const p of patches) {
    const findCRLF = toCRLF(p.find);
    const replaceCRLF = toCRLF(p.replace);
    const count = src.split(findCRLF).length - 1;
    if (count !== 1) {
        console.error(`[FAIL] ${p.name} — 매칭 ${count}회 (1회여야 함)`);
        process.exit(1);
    }
    src = src.replace(findCRLF, replaceCRLF);
    applied++;
    console.log(`[OK] ${p.name}`);
}

fs.writeFileSync(FILE, src, 'utf8');
console.log(`\n완료: ${applied}/${patches.length} 패치 적용 → ${FILE}`);

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

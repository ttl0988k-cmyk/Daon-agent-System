/**
 * [2026-08-31 브라우저 자동 오픈 폴링 단축] static/modules/browser_ai.js 패치
 *
 * pending_url 폴링을 5초 → 3초로 단축한다. 백업 경로(즉시 오픈 실패 시)로서
 * 반응성을 높인다. 서버 측 안전장치가 있어 단축이 안전하다:
 *  - /api/browser/status는 status 게이트(_status_gate_lock)로 중복 큐잉 방지
 *  - _ensure_browser는 _cdp_endpoint_ready() 사전 점검으로 CDP 무응답 시
 *    connect_over_cdp를 건너뛰고, 5초 쿨다운 백오프로 재시도를 제한한다.
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'browser_ai.js');
let src = fs.readFileSync(FILE, 'utf8');

const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
}
// 파일의 실제 줄바꿈 스타일을 감지해 맞춘다 (browser_ai.js는 LF 전용 파일)
const usesCRLF = (src.match(/\r\n/g) || []).length > 0;
const toEOL = (s) => usesCRLF ? s.replace(/\r?\n/g, '\r\n') : s.replace(/\r?\n/g, '\n');
console.log('파일 줄바꿈:', usesCRLF ? 'CRLF' : 'LF');

const find = toEOL([
    '  }, 5000); // 5초 폴링 — 서버 CDP 재연결 백오프(5초)와 정합. 1초 폴링은 CDP 미준비 시',
    '  // connect_over_cdp 실패를 반복시켜 서버 스레드를 소진하고 다른 API를 15초 타임아웃에 빠뜨림.',
].join('\n'));

const replace = toEOL([
    '  }, 3000); // [2026-08-31] 3초 폴링 — 즉시 오픈(SSE tool 이벤트)의 백업 경로 반응성 개선.',
    '  // 서버 측 안전장치(status 게이트 + CDP 사전 점검 + 5초 쿨다운 백오프)가 있어',
    '  // 과거 1초 폴링의 서버 스레드 소진 문제는 재발하지 않는다.',
].join('\n'));

const count = src.split(find).length - 1;
if (count !== 1) {
    console.error(`[FAIL] 폴링 주기 매칭 ${count}회 (1회여야 함)`);
    process.exit(1);
}
src = src.replace(find, replace);
fs.writeFileSync(FILE, src, 'utf8');
console.log('[OK] pending_url 폴링 5초 → 3초 단축');

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

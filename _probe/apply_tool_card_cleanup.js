/**
 * [2026-08-31 중간 답변 시 도구 카드 정리] static/modules/chat.js 패치
 *
 * 증상: 에이전트가 중간 답변을 하고 다음 도구를 사용할 때 직전 도구 그룹 카드가
 * 계속 누적돼 채팅이 지저분해짐. 작업이 완전히 끝나야 카드가 정리됨.
 * 원인: token 핸들러가 도구 그룹 카드를 "확정"할 때 참조(_toolGroupCard = null)만
 * 해제하고 DOM 요소는 그대로 두었기 때문.
 * 수정: 중간 답변 시작 시 직전 도구 카드를 DOM에서도 제거한다.
 *   - 최종 기록은 done 경로의 renderMessages가 세션 tool_calls 기반으로
 *     도구 카드를 다시 그리므로 유실되지 않는다.
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

// 안전장치: 비정상 줄바꿈 정규화 (이전과 동일한 손상 방지)
const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
    console.log(`줄바꿈 정규화: 비정상 줄바꿈 ${beforeMixed}개 정리`);
}
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const find = toCRLF([
    '      // 텍스트 토큰이 오면 현재 도구 그룹 카드를 확정 → 다음 도구 호출 시 새 그룹 시작',
    '      if (_toolGroupCard) {',
    '        _updateToolGroupHeader();',
    '        _toolGroupCard = null;',
    '        _toolGroupItems = null;',
    '        _toolGroupCount = 0;',
    '        _toolGroupDoneCount = 0;',
    '        _toolItemMap = {};',
    '      }',
].join('\n'));

const replace = toCRLF([
    '      // 텍스트 토큰이 오면 현재 도구 그룹 카드를 확정 → 다음 도구 호출 시 새 그룹 시작',
    '      if (_toolGroupCard) {',
    '        _updateToolGroupHeader();',
    '        // [2026-08-31 중간 답변 시 도구 카드 정리] 중간 답변이 시작되면 직전 도구',
    '        // 그룹 카드를 DOM에서도 제거한다. 기존에는 참조만 해제해 카드가 턴 내내',
    '        // 누적돼 채팅이 지저분해졌다. 최종 기록은 done 경로의 renderMessages가',
    '        // 세션 tool_calls 기반으로 도구 카드를 다시 그리므로 유실되지 않는다.',
    '        try { if (_toolGroupCard.parentNode) _toolGroupCard.remove(); } catch (_) { }',
    '        _toolGroupCard = null;',
    '        _toolGroupItems = null;',
    '        _toolGroupCount = 0;',
    '        _toolGroupDoneCount = 0;',
    '        _toolItemMap = {};',
    '      }',
].join('\n'));

const count = src.split(find).length - 1;
if (count !== 1) {
    console.error(`[FAIL] token 핸들러 매칭 ${count}회 (1회여야 함)`);
    process.exit(1);
}
src = src.replace(find, replace);
fs.writeFileSync(FILE, src, 'utf8');
console.log('[OK] 중간 답변 시 직전 도구 카드 DOM 제거 패치 적용');

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

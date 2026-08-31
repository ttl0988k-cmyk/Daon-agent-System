/**
 * [2026-08-31c 도구 카드 순차 누적 복원] static/modules/chat.js 패치
 *
 * 배경: 에이전트의 코드 분석으로 [2026-08-31b] 패치("새 도구 그룹 시작 시
 *   이전 도구 카드 전량 삭제")가 텍스트↔도구 교대마다 카드를 지워
 *   "도구 카드가 계속 사라지는" 증상을 만든다는 것이 확인됐다.
 *   원래 설계(_freezeAnswerSegment 주석)는 Roo Code 스타일 순차 누적:
 *   [💭 생각 카드][답변 블록 1][🔧 도구 카드][답변 블록 2]...
 *
 * 수정: 전량 삭제 라인을 제거해 순차 누적으로 복원한다.
 *   - 어제의 "지저분함"의 진짜 원인은 SSE 유실(카드 유실/잔해)이었고
 *     그것은 이미 수정됨(재연결 존중 + 10초 감시).
 *   - 멈춘 카드(0/1)의 이유도 승인 대기 표시로 설명된다.
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

const find = toCRLF([
    '        // [2026-08-31b] 새 도구 그룹 시작 시 이전 도구 카드들을 제거한다 —',
    '        // 화면에는 항상 "최신 도구 카드 1개"만 표시되어 누적(지저분함)이 없고,',
    '        // 카드는 다음 도구 시작까지 유지되므로 도구 사용도 확실히 보인다.',
    '        // 최종 전체 기록은 done 경로의 renderMessages가 세션 tool_calls 기반으로',
    '        // 다시 그리므로 유실되지 않는다.',
    "        try { box.querySelectorAll('.tool-group-card').forEach((el) => el.remove()); } catch (_) { }",
].join('\n'));

const replace = toCRLF([
    '        // [2026-08-31c] 이전 도구 카드 전량 삭제는 철회했다 — Roo Code 스타일',
    '        // 순차 누적([💭][답변1][🔧도구1][답변2][🔧도구2]...)이 원래 설계다.',
    '        // 전량 삭제는 텍스트↔도구 교대마다 카드를 지워 "카드가 계속 사라지는"',
    '        // 증상을 만들었다(에이전트 코드 분석으로 적발). 어제의 지저분함의',
    '        // 진짜 원인은 SSE 유실이었고 그것은 재연결 존중 + 10초 감시로 해결됨.',
    '        // 멈춘 카드(0/1)는 승인 대기 표시로 이유가 설명된다.',
].join('\n'));

const count = src.split(find).length - 1;
if (count !== 1) {
    console.error(`[FAIL] 전량 삭제 라인 매칭 ${count}회 (1회여야 함)`);
    process.exit(1);
}
src = src.replace(find, replace);
fs.writeFileSync(FILE, src, 'utf8');
console.log('[OK] 도구 카드 순차 누적 복원 (전량 삭제 제거)');

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

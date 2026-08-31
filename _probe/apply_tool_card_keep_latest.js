/**
 * [2026-08-31b 도구 카드 표시 방식 개선] static/modules/chat.js 패치
 *
 * 문제: 이전 패치(중간 답변 시 도구 카드 제거)가 너무 공격적이었다.
 *   토큰이 도착하는 즉시 직전 도구 카드가 지워져서, 도구 실행이 빠르면
 *   카드가 눈에 띄기도 전에 사라졌다 → "도구 카드가 아예 안 나타난다"는
 *   사용자 불만 발생.
 *
 * 개선: 제거 시점을 "새 도구 그룹 시작 시"로 옮긴다.
 *   - 도구 카드는 다음 도구가 시작될 때까지 유지 → 도구 사용이 확실히 보임
 *   - 새 도구가 시작되면 이전 카드를 제거하고 새 카드 생성 → 항상 최신 1개만
 *     표시되어 누적(지저분함) 없음
 *   - 최종 기록은 done 경로 renderMessages가 세션 tool_calls 기반으로 복원
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

// 안전장치: 비정상 줄바꿈 정규화
const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
    console.log(`줄바꿈 정규화: ${beforeMixed}개 정리`);
}
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const patches = [
    {
        name: '1) token 핸들러: 카드 즉시 제거 철회 (참조 해제만 — 카드는 다음 도구 시작까지 유지)',
        find: [
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
        ].join('\n'),
        replace: [
            '      // 텍스트 토큰이 오면 현재 도구 그룹 카드를 확정 → 다음 도구 호출 시 새 그룹 시작',
            '      if (_toolGroupCard) {',
            '        _updateToolGroupHeader();',
            '        // [2026-08-31b] 카드는 여기서 지우지 않는다 — 다음 도구가 시작될 때까지',
            '        // 유지해야 "도구 사용 흔적"이 화면에 보인다. 이전 패치가 토큰 도착 즉시',
            '        // 카드를 지워 도구 카드가 아예 안 보이는 문제를 낳았다. 누적 방지는',
            '        // _onToolEvent의 새 그룹 생성 시점에서 이전 카드를 제거하는 방식으로 처리.',
            '        _toolGroupCard = null;',
            '        _toolGroupItems = null;',
            '        _toolGroupCount = 0;',
            '        _toolGroupDoneCount = 0;',
            '        _toolItemMap = {};',
            '      }',
        ].join('\n'),
    },
    {
        name: '2) _onToolEvent: 새 도구 그룹 시작 시 이전 도구 카드 제거 (최신 1개만 표시)',
        find: [
            '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
            '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
            '      if (!_toolGroupCard) {',
            '        // 새 도구 그룹이 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '        _freezeAnswerSegment();',
            '        _toolGroupCard = document.createElement(\'details\');',
        ].join('\n'),
        replace: [
            '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
            '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
            '      if (!_toolGroupCard) {',
            '        // 새 도구 그룹이 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '        _freezeAnswerSegment();',
            '        // [2026-08-31b] 새 도구 그룹 시작 시 이전 도구 카드들을 제거한다 —',
            '        // 화면에는 항상 "최신 도구 카드 1개"만 표시되어 누적(지저분함)이 없고,',
            '        // 카드는 다음 도구 시작까지 유지되므로 도구 사용도 확실히 보인다.',
            '        // 최종 전체 기록은 done 경로의 renderMessages가 세션 tool_calls 기반으로',
            '        // 다시 그리므로 유실되지 않는다.',
            '        try { box.querySelectorAll(\'.tool-group-card\').forEach((el) => el.remove()); } catch (_) { }',
            '        _toolGroupCard = document.createElement(\'details\');',
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

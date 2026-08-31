/**
 * [2026-08-31c A방식 마무리] static/modules/chat.js 패치
 *
 * 에이전트 실측: ③(58개 부활)·중복·단일 카드 묶음 모두 해결 확인.
 * 남은 문제: "이전 턴 카드가 다음 턴까지 살아있음" — 새 싱킹이 뜰 때
 *   이전 카드가 안 지워짐 (① 미반동).
 *
 * 진짜 원인 (코드 분석):
 *   token 핸들러가 _stopReasoningTimer()로 타이머/summary만 정리하고
 *   _reasoningCard 참조를 null로 만들지 않았다. 그래서 다음 턴의 reasoning
 *   이벤트에서 if (!_reasoningCard) 분기를 스킵 → ①의 정리 코드 미실행 +
 *   이전 턴 카드에 새 싱킹 텍스트가 이어서 쌓임.
 *
 * 수정:
 *   1) token 핸들러: _reasoningCard 참조 해제 — 다음 턴 싱킹이 새 카드로 시작
 *   2) finishStream: DOM 직접 조회 정리 추가 (이중 안전장치 — 변수 참조 유실
 *      대비, cancel 경로와 동일 방식. 에이전트 제안)
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
        name: '1) token 핸들러: _reasoningCard 참조 해제 (다음 턴 싱킹이 새 카드로 시작)',
        find: [
            '      // 추론이 끝났으면 카드 제목 갱신 (경과 초 포함)',
            '      if (_reasoningCard && _reasoningTimer) {',
            "        _stopReasoningTimer('💭 생각 완료 (' + _reasoningElapsed() + '초) (클릭하여 보기)');",
            '      }',
        ].join('\n'),
        replace: [
            '      // 추론이 끝났으면 카드 제목 갱신 (경과 초 포함)',
            '      if (_reasoningCard) {',
            '        if (_reasoningTimer) {',
            "          _stopReasoningTimer('💭 생각 완료 (' + _reasoningElapsed() + '초) (클릭하여 보기)');",
            '        }',
            '        // [2026-08-31c] 참조 해제 — 다음 턴의 싱킹이 새 카드로 시작되게 한다.',
            '        // (이전 턴 카드를 재사용하면 A방식 정리(①)가 실행되지 않았었다)',
            '        _reasoningCard = null;',
            '      }',
        ].join('\n'),
    },
    {
        name: '2) finishStream: DOM 직접 조회 정리 추가 (이중 안전장치)',
        find: [
            '    try {',
            '      if (_terminalOutputCard && _terminalOutputCard.parentNode) _terminalOutputCard.remove();',
            '    } catch (_) { }',
        ].join('\n'),
        replace: [
            '    try {',
            '      if (_terminalOutputCard && _terminalOutputCard.parentNode) _terminalOutputCard.remove();',
            '    } catch (_) { }',
            '    // [2026-08-31c] DOM 직접 조회 정리 (이중 안전장치) — 변수 참조가 유실된',
            '    // 카드(클로저 소멸 등)도 확실히 제거한다. cancel 경로와 동일한 방식.',
            '    try {',
            "      box.querySelectorAll('.tool-group-card, .reasoning-card, .terminal-live-card').forEach((el) => el.remove());",
            '    } catch (_) { }',
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

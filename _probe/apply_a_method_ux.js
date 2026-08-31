/**
 * [2026-08-31c A방식 UX 구현] static/modules/chat.js 패치
 *
 * 대표님이 원하시는 UX (A방식):
 *   싱킹 말풍선 → 도구박스 → 중간 답변 → (새 싱킹이 뜨는 순간 이전 도구박스+
 *   싱킹 정리) → ... → 응답 완료 시 싱킹 말풍선과 도구박스 전부 사라짐.
 *
 * 구현 (에이전트 제안 검증 완료 — 3곳 모두 실측으로 위치 확인):
 *   ① _onReasoningEvent: 새 싱킹 카드 생성 시 이전 도구박스+싱킹카드 제거
 *   ② renderMessages: done 후 도구 카드 재렌더 블록 제거 (완료 시 카드 부활 방지)
 *   ③ renderMessages: 도구 전용 턴(텍스트 없는 assistant) 스킵 — 빈 블록(얇은
 *      라인) 방지. 도구 카드를 그리지 않으므로 도구 전용 턴은 남을 이유가 없다.
 *   ④ finishStream / cancelActiveStream의 카드 제거는 기존 구현 그대로 사용.
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
        name: '① 새 싱킹 시작 시 이전 도구박스+싱킹카드 정리',
        find: [
            '      if (!_reasoningCard) {',
            '        // 새 추론 단위가 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '        _freezeAnswerSegment();',
            '        _reasoningStartTs = Date.now();',
        ].join('\n'),
        replace: [
            '      if (!_reasoningCard) {',
            '        // 새 추론 단위가 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '        _freezeAnswerSegment();',
            '        // [2026-08-31c A방식] 새 싱킹이 뜨는 순간 이전 도구박스 + 이전 싱킹카드를',
            '        // 정리한다 — 화면에는 "현재 진행 중인 유닛"만 남는다.',
            '        try {',
            '          if (_toolGroupCard && _toolGroupCard.parentNode) _toolGroupCard.remove();',
            "          box.querySelectorAll('.tool-group-card, .reasoning-card').forEach((el) => el.remove());",
            '        } catch (_) { }',
            '        _toolGroupCard = null;',
            '        _toolGroupItems = null;',
            '        _toolGroupCount = 0;',
            '        _toolGroupDoneCount = 0;',
            '        _toolItemMap = {};',
            '        _reasoningStartTs = Date.now();',
        ].join('\n'),
    },
    {
        name: '② renderMessages: done 후 도구 카드 재렌더 블록 제거 (완료 시 카드 부활 방지)',
        find: [
            '    // Find tool calls matching this assistant message — 그룹으로 묶어 표시',
            '    if (!isUser && toolCalls) {',
            '      const msgTools = toolCalls.filter(tc => tc.assistant_msg_idx === idx);',
            '      if (msgTools.length > 0) {',
            "        const groupCard = document.createElement('details');",
            "        groupCard.className = 'tool-group-card';",
            '        const totalCount = msgTools.length;',
            '        groupCard.innerHTML = `',
            '          <summary>',
            '            <span class="tool-group-icon">🔧</span>',
            '            <span class="tool-group-label">도구 실행 완료</span>',
            '            <span class="tool-group-counter">${totalCount}</span>',
            '            <span class="tool-group-chevron">▶</span>',
            '          </summary>',
            '          <div class="tool-group-items"></div>',
            '        `;',
            "        const itemsContainer = groupCard.querySelector('.tool-group-items');",
            '        msgTools.forEach(tool => {',
            "          const item = document.createElement('div');",
            "          item.className = 'tool-group-item';",
            "          item.style.cursor = 'pointer';",
            '          item.innerHTML = `',
            '            <span class="tgi-icon">✅</span>',
            '            <span class="tgi-name">${tool.name}</span>',
            '          `;',
            '          // 클릭 시 상세 보기 토글',
            "          const detailDiv = document.createElement('div');",
            "          detailDiv.className = 'tool-card-body';",
            "          detailDiv.style.display = 'none';",
            '          detailDiv.innerHTML = `',
            '            <div>Arguments:</div>',
            '            <pre style="margin-bottom:8px;">${JSON.stringify(tool.args, null, 2)}</pre>',
            '            <div>Output Snippet:</div>',
            '            <pre>${tool.snippet}</pre>',
            '          `;',
            '          item.addEventListener(\'click\', function () {',
            "            detailDiv.style.display = detailDiv.style.display === 'none' ? 'block' : 'none';",
            '          });',
            '          item.appendChild(detailDiv);',
            '          itemsContainer.appendChild(item);',
            '        });',
            '        bubble.appendChild(groupCard);',
            '      }',
            '    }',
        ].join('\n'),
        replace: [
            '    // [2026-08-31c A방식] done 후 도구 카드 재렌더 블록 제거 —',
            '    // "응답 완료 시 싱킹 말풍선과 도구박스가 사라진다"는 UX를 위해',
            '    // 히스토리 재렌더링 시 도구 카드를 다시 그리지 않는다.',
            '    // (세션 tool_calls 데이터는 유지되므로 나중에 UX를 되돌릴 때 즉시 복구 가능)',
        ].join('\n'),
    },
    {
        name: '③ 도구 전용 턴 스킵 (빈 블록/얇은 라인 방지)',
        find: [
            '    let _toolOnlyTurn = false;',
            '    if (!isUser) {',
            '      const plain = stripThinkBlocks(msg.content);',
            '      const msgToolsPre = toolCalls ? toolCalls.filter(tc => tc.assistant_msg_idx === idx) : [];',
            '      if (!plain.trim() && msgToolsPre.length === 0) return;',
        ].join('\n'),
        replace: [
            '    let _toolOnlyTurn = false;',
            '    if (!isUser) {',
            '      const plain = stripThinkBlocks(msg.content);',
            '      // [2026-08-31c A방식] done 후 도구 카드를 그리지 않으므로 도구 전용 턴은',
            '      // 빈 블록(얇은 라인)만 남는다 — 통째로 스킵한다.',
            '      if (!plain.trim()) return;',
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

/**
 * [2026-08-31 백그라운드 작업 시각화] static/modules/chat.js 패치
 *
 * 요청: 다른 세션을 갔다가 돌아오면, 그 세션에서 에이전트가 작업한 내용이
 *   채팅창에 표시되길 원함 (모바일 앱처럼 최신 채팅이 나타나듯).
 *
 * 원인: 세션 복귀 시 _reattachSessionStream이 SSE를 재접속하지만, 그 안의
 *   tool/reasoning 리스너는 상태 표시(setChatStatus)만 하고 도구 카드/추론
 *   카드를 생성하지 않았다 → "백그라운드 작업중" 말풍선만 보임.
 *
 * 수정: _reattachSessionStream에 카드 렌더링 추가 —
 *   - tool 이벤트 → 도구 그룹 카드 생성/갱신 (started/completed, id 불일치 폴백)
 *   - reasoning 이벤트 → 추론 카드 생성/내용 누적
 *   - A방식 준수: 새 싱킹 시작 시 이전 도구박스 정리, 토큰 오면 추론 카드 정리,
 *     종료 시 전부 정리
 *   서버 큐에 쌓인 이벤트는 재접속 시 자동 재생되므로, 자리를 비운 동안의
 *   도구 호출들도 카드로 표시된다.
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
        name: '1) 백그라운드 카드 상태 변수 + 헬퍼 선언 (sse 생성 전)',
        find: [
            '  const sse = new EventSource(`/api/chat/stream?stream_id=${encodeURIComponent(streamId)}`);',
            '  State.currentEventSource = sse;',
            '  State.currentStreamId = streamId;',
        ].join('\n'),
        replace: [
            '  // ── [2026-08-31 백그라운드 작업 시각화] ──',
            '  // 복귀 후에도 작업 진행 내용(도구 카드/추론 카드)이 채팅에 표시되게 한다.',
            '  let _raToolCard = null;',
            '  let _raToolItems = null;',
            '  let _raToolCount = 0;',
            '  let _raToolDone = 0;',
            '  let _raToolMap = {};',
            '  let _raReasoningCard = null;',
            '',
            '  function _raEnsureToolCard() {',
            '    if (!_raToolCard) {',
            "      _raToolCard = document.createElement('details');",
            "      _raToolCard.className = 'tool-group-card';",
            '      _raToolCard.innerHTML = `',
            '        <summary>',
            '          <span class="tool-group-icon">🔧</span>',
            '          <span class="tool-group-label">도구 실행 중...</span>',
            '          <span class="tool-group-counter">0</span>',
            '          <span class="tool-group-spinner"></span>',
            '          <span class="tool-group-chevron">▶</span>',
            '        </summary>',
            "        <div class='tool-group-items'></div>",
            '      `;',
            "      _raToolItems = _raToolCard.querySelector('.tool-group-items');",
            '      _raToolCount = 0;',
            '      _raToolDone = 0;',
            '      _raToolMap = {};',
            '      box.insertBefore(_raToolCard, asstBubble);',
            '    }',
            '  }',
            '',
            '  const sse = new EventSource(`/api/chat/stream?stream_id=${encodeURIComponent(streamId)}`);',
            '  State.currentEventSource = sse;',
            '  State.currentStreamId = streamId;',
        ].join('\n'),
    },
    {
        name: '2) token 리스너: 토큰 오면 추론 카드 정리 (A방식)',
        find: [
            "  sse.addEventListener('token', (e) => {",
            '    try {',
            '      const d = JSON.parse(e.data);',
            "      setChatStatus('thinking', '✍️ 최종 답변 생성 중...');",
            "      if (agentStatusBubble.parentNode) agentStatusBubble.style.display = 'none';",
        ].join('\n'),
        replace: [
            "  sse.addEventListener('token', (e) => {",
            '    try {',
            '      const d = JSON.parse(e.data);',
            "      setChatStatus('thinking', '✍️ 최종 답변 생성 중...');",
            "      if (agentStatusBubble.parentNode) agentStatusBubble.style.display = 'none';",
            '      // [A방식] 토큰이 오면 추론 카드 정리 — 다음 턴 싱킹이 새 카드로 시작',
            '      if (_raReasoningCard) { try { _raReasoningCard.remove(); } catch (_) { } _raReasoningCard = null; }',
        ].join('\n'),
    },
    {
        name: '3) reasoning 리스너: 추론 카드 생성/누적 + 새 싱킹 시 이전 도구박스 정리',
        find: [
            "  sse.addEventListener('reasoning', () => {",
            "    setChatStatus('thinking', '💭 생각 중... (백그라운드 작업)');",
            '    scrollToChatBottom();',
            '  });',
            '',
            "  sse.addEventListener('tool', () => {",
            "    setChatStatus('thinking', '🔧 도구 실행 중... (백그라운드 작업)');",
            '    scrollToChatBottom();',
            '  });',
        ].join('\n'),
        replace: [
            "  sse.addEventListener('reasoning', (e) => {",
            "    setChatStatus('thinking', '💭 생각 중... (백그라운드 작업)');",
            '    try {',
            '      const d = JSON.parse(e.data);',
            '      // [A방식] 새 싱킹 단위 시작 시 이전 도구박스 정리',
            '      if (!_raReasoningCard) {',
            '        if (_raToolCard) { try { _raToolCard.remove(); } catch (_) { } _raToolCard = null; _raToolItems = null; }',
            "        _raReasoningCard = document.createElement('details');",
            "        _raReasoningCard.className = 'tool-card reasoning-card';",
            '        _raReasoningCard.innerHTML = `',
            '          <summary style="cursor:pointer; padding:6px 10px; opacity:0.75;">💭 생각 중... (백그라운드)</summary>',
            '          <div class="tool-card-body" style="display:block;">',
            '            <pre style="white-space:pre-wrap; max-height:240px; overflow:auto; opacity:0.7; font-size:12px;"></pre>',
            '          </div>',
            '        `;',
            '        box.insertBefore(_raReasoningCard, asstBubble);',
            '      }',
            "      const pre = _raReasoningCard.querySelector('pre');",
            "      if (pre) pre.textContent = (pre.textContent || '') + (d.text || '');",
            '    } catch (_) { }',
            '    scrollToChatBottom();',
            '  });',
            '',
            "  sse.addEventListener('tool', (e) => {",
            "    setChatStatus('thinking', '🔧 도구 실행 중... (백그라운드 작업)');",
            '    try {',
            '      const d = JSON.parse(e.data);',
            "      const tName = d.name || 'unknown';",
            "      const isStart = (d.event || 'tool.started') === 'tool.started';",
            "      const tid = d.tool_call_id || (tName + '_' + _raToolCount);",
            '      // [A방식] 새 도구박스 시작 시 이전 싱킹카드 정리',
            '      if (isStart && _raReasoningCard) { try { _raReasoningCard.remove(); } catch (_) { } _raReasoningCard = null; }',
            '      _raEnsureToolCard();',
            '      if (isStart) {',
            '        _raToolCount++;',
            "        const item = document.createElement('div');",
            "        item.className = 'tool-group-item';",
            '        item.innerHTML = `<span class="tgi-icon">⏳</span><span class="tgi-name">${tName}</span><span class="tgi-status">실행 중</span>`;',
            '        _raToolMap[tid] = item;',
            '        if (_raToolItems) _raToolItems.appendChild(item);',
            '      } else {',
            '        _raToolDone++;',
            '        let it = _raToolMap[tid];',
            '        if (!it) {',
            '          // id 불일치 폴백: 같은 이름의 실행 중 항목을 완료 처리 (중복 방지)',
            '          for (var _t in _raToolMap) {',
            '            const _i = _raToolMap[_t];',
            "            const _n = _i.querySelector('.tgi-name');",
            "            const _s = _i.querySelector('.tgi-status');",
            "            if (_n && _n.textContent === tName && _s && _s.textContent === '실행 중') { it = _i; break; }",
            '          }',
            '        }',
            '        if (it) {',
            "          const ic = it.querySelector('.tgi-icon');",
            "          const st = it.querySelector('.tgi-status');",
            "          if (ic) ic.textContent = '✅';",
            "          if (st) st.textContent = '완료';",
            '        } else {',
            '          _raToolCount++;',
            "          const item = document.createElement('div');",
            "          item.className = 'tool-group-item';",
            '          item.innerHTML = `<span class="tgi-icon">✅</span><span class="tgi-name">${tName}</span><span class="tgi-status">완료</span>`;',
            '          if (_raToolItems) _raToolItems.appendChild(item);',
            '        }',
            "        const label = _raToolCard.querySelector('.tool-group-label');",
            "        const counter = _raToolCard.querySelector('.tool-group-counter');",
            '        const running = _raToolCount - _raToolDone;',
            '        if (label) label.textContent = running > 0 ? `도구 실행 중... (${_raToolDone}/${_raToolCount} 완료)` : `도구 실행 완료`;',
            '        if (counter) counter.textContent = _raToolCount;',
            '      }',
            '    } catch (_) { }',
            '    scrollToChatBottom();',
            '  });',
        ].join('\n'),
    },
    {
        name: '4) finish: 종료 시 백그라운드 카드들 정리 (A방식)',
        find: [
            '    cleanupStreamState();',
            '    // [세션 동시 작업] 재접속한 스트림이 끝났으면 기록을 지워 ▶ 배지를 정리한다.',
            '    try { _forgetSessionStream(sid, streamId); renderSessionsList(); } catch (_) { }',
        ].join('\n'),
        replace: [
            '    // [A방식] 종료 시 백그라운드 카드들 정리 — done 후 renderMessages가',
            '    // 최종 답변만 렌더링하므로 카드 잔존 없이 깔끔하게 마무리된다.',
            '    try { box.querySelectorAll(\'.tool-group-card, .reasoning-card\').forEach((el) => el.remove()); } catch (_) { }',
            '    cleanupStreamState();',
            '    // [세션 동시 작업] 재접속한 스트림이 끝났으면 기록을 지워 ▶ 배지를 정리한다.',
            '    try { _forgetSessionStream(sid, streamId); renderSessionsList(); } catch (_) { }',
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

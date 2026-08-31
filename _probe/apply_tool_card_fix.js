/**
 * [2026-08-30 도구 카드 유실 수정] static/modules/chat.js 정밀 패치
 *
 * 증상: 도구 사용 출력(도구 그룹 카드)이 나왔다가 사라지고 얇은 빈 줄만 남음
 * 원인:
 *  1) SSE error 리스너가 readyState=CONNECTING(자동 재연결 시도 중)에도
 *     finishStream('error')을 호출 → 도구/추론 카드 DOM 제거 + sse.close()로
 *     자동 재연결 차단
 *  2) idle 워치독의 수동 재연결 SSE에 done/cancel/heartbeat 리스너만 부착되어
 *     재연결 후 tool/reasoning 이벤트가 화면에 반영되지 않음
 *
 * 적용 방식: CRLF 줄바꿈을 유지하는 문자열 정밀 치환 (apply_diff CRLF 손상 방지)
 * 각 치환은 원본 문자열이 정확히 1회 매칭되는지 검증한다.
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

// ── 줄바꿈 정규화 ──
// 이 파일은 과거 편집 과정에서 \r\r\r\n(CR 3개+LF)과 \r\n이 혼합된 비정상
// 줄바꿈을 가지고 있었다(2900:156). JS 파서는 \r도 줄바꿈으로 인식하므로
// 실행에는 지장이 없지만, 텍스트 도구의 정밀 매칭을 계속 깨뜨리는 원인이 된다.
// \r\r\r\n을 \r\n 하나로 직접 치환한다 (빈 줄 생성 없이).
const beforeMixed = (src.match(/\r\r\r\n/g) || []).length;
src = src.replace(/\r\r\r\n/g, '\r\n');
console.log(`줄바꿈 정규화: 비정상 CRCRCRLF ${beforeMixed}개 → CRLF 통일`);

// LF로 작성된 패턴을 파일의 CRLF에 맞춘다.
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const patches = [
    {
        name: '1) 함수 스코프에 _onToolEvent/_onReasoningEvent 선언 추가',
        find: [
            '  let streamId = null;          // SSE stream ID (try 안에서 할당)',
            '  let sse = null;               // EventSource 인스턴스 (try 안에서 할당)',
        ].join('\n'),
        replace: [
            '  let streamId = null;          // SSE stream ID (try 안에서 할당)',
            '  let sse = null;               // EventSource 인스턴스 (try 안에서 할당)',
            '  // [2026-08-30 도구 카드 유실 수정] tool/reasoning 이벤트 핸들러 본체.',
            '  // 원본 SSE와 재연결 SSE(idle 워치독 복구 경로) 양쪽에서 동일하게 사용하기',
            '  // 위해 함수 본체는 이 스코프(try 블록 바깥)에 선언하고 try 안에서 "할당"만',
            '  // 한다. 재연결된 SSE에도 이 핸들러를 부착해 도구 카드/추론 카드가 계속',
            '  // 갱신되게 한다 — 기존에는 재연결 시 터미널 이벤트만 부착돼 도구 카드가',
            '  // 멈추거나 아예 그려지지 않았다.',
            '  let _onToolEvent = null;',
            '  let _onReasoningEvent = null;',
        ].join('\n'),
    },
    {
        name: '2) 재연결된 SSE에 tool/reasoning 라이브 리스너 부착',
        find: [
            "          reconnected.addEventListener('cancel', () => finishStream('cancel_reconnected'));",
            "          reconnected.addEventListener('apperror', () => finishStream('apperror_reconnected'));",
            "          reconnected.addEventListener('heartbeat', () => { _idleExtensions = 0; resetIdleTimer(); });",
            '          sse = reconnected;',
        ].join('\n'),
        replace: [
            "          reconnected.addEventListener('cancel', () => finishStream('cancel_reconnected'));",
            "          reconnected.addEventListener('apperror', () => finishStream('apperror_reconnected'));",
            "          reconnected.addEventListener('heartbeat', () => { _idleExtensions = 0; resetIdleTimer(); });",
            '          // [2026-08-30 도구 카드 유실 수정] 재연결된 SSE에도 라이브 이벤트',
            '          // 리스너를 부착한다. 기존에는 done/cancel/heartbeat만 부착돼 재연결',
            '          // 후 tool/reasoning 이벤트가 화면에 전혀 반영되지 않았다 — "도구 실행',
            '          // 중 (0/1)" 카드가 멈추거나 도구 카드가 아예 안 그려지는 증상의',
            '          // 원인 중 하나였다.',
            "          if (typeof _onToolEvent === 'function') {",
            "            reconnected.addEventListener('tool', (ev) => {",
            '              try { _onToolEvent(JSON.parse(ev.data)); } catch (_) { }',
            '            });',
            '          }',
            "          if (typeof _onReasoningEvent === 'function') {",
            "            reconnected.addEventListener('reasoning', (ev) => {",
            '              try { _onReasoningEvent(JSON.parse(ev.data)); } catch (_) { }',
            '            });',
            '          }',
            '          sse = reconnected;',
        ].join('\n'),
    },
    {
        name: '3) reasoning 리스너 → _onReasoningEvent 함수 추출',
        find: [
            "    sse.addEventListener('reasoning', (e) => {",
            '      try {',
            '        const data = JSON.parse(e.data);',
            "        setStreamStatus('thinking', '💭 생각 중...');",
            "        _reasoningText += data.text || '';",
            '        if (!_reasoningCard) {',
            '          // 새 추론 단위가 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '          _freezeAnswerSegment();',
            '          _reasoningStartTs = Date.now();',
            "          _reasoningCard = document.createElement('details');",
            "          _reasoningCard.className = 'tool-card reasoning-card';",
            '          _reasoningCard.innerHTML = `',
            '            <summary style="cursor:pointer; padding:6px 10px; opacity:0.75;">💭 생각 중... (0초)</summary>',
            '            <div class="tool-card-body" style="display:block;">',
            '              <pre style="white-space:pre-wrap; max-height:240px; overflow:auto; opacity:0.7; font-size:12px;"></pre>',
            '            </div>',
            '          `;',
            '          // asstBubble 안에 넣으면 token 스트리밍 시 innerHTML 초기화로 사라지므로',
            '          // 버블 앞의 독립 요소로 삽입한다.',
            '          box.insertBefore(_reasoningCard, asstBubble);',
            '          // 경과 초를 1초마다 갱신 (Roo Code의 "thinking" 표시 스타일)',
            '          _reasoningTimer = setInterval(function () {',
            '            if (!_reasoningCard || _reasoningTimer === null) return;',
            "            const sum = _reasoningCard.querySelector('summary');",
            "            if (sum) sum.textContent = '💭 생각 중... (' + _reasoningElapsed() + '초)';",
            '          }, 1000);',
            '        }',
            "        const pre = _reasoningCard.querySelector('pre');",
            '        if (pre) pre.textContent = _reasoningText;',
            '        scrollToChatBottom();',
            '      } catch (err) {',
            "        console.warn('[reasoning] handler error:', err);",
            '      }',
            '      _idleExtensions = 0;',
            '      resetIdleTimer();',
            '    });',
        ].join('\n'),
        replace: [
            '    // [2026-08-30 도구 카드 유실 수정] 핸들러 본체를 _onReasoningEvent로 추출해',
            '    // 원본 SSE와 재연결 SSE 양쪽에서 재사용한다.',
            '    _onReasoningEvent = function (data) {',
            "      setStreamStatus('thinking', '💭 생각 중...');",
            "      _reasoningText += data.text || '';",
            '      if (!_reasoningCard) {',
            '        // 새 추론 단위가 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)',
            '        _freezeAnswerSegment();',
            '        _reasoningStartTs = Date.now();',
            "        _reasoningCard = document.createElement('details');",
            "        _reasoningCard.className = 'tool-card reasoning-card';",
            '        _reasoningCard.innerHTML = `',
            '          <summary style="cursor:pointer; padding:6px 10px; opacity:0.75;">💭 생각 중... (0초)</summary>',
            '          <div class="tool-card-body" style="display:block;">',
            '            <pre style="white-space:pre-wrap; max-height:240px; overflow:auto; opacity:0.7; font-size:12px;"></pre>',
            '          </div>',
            '        `;',
            '        // asstBubble 안에 넣으면 token 스트리밍 시 innerHTML 초기화로 사라지므로',
            '        // 버블 앞의 독립 요소로 삽입한다.',
            '        box.insertBefore(_reasoningCard, asstBubble);',
            '        // 경과 초를 1초마다 갱신 (Roo Code의 "thinking" 표시 스타일)',
            '        _reasoningTimer = setInterval(function () {',
            '          if (!_reasoningCard || _reasoningTimer === null) return;',
            "          const sum = _reasoningCard.querySelector('summary');",
            "          if (sum) sum.textContent = '💭 생각 중... (' + _reasoningElapsed() + '초)';",
            '        }, 1000);',
            '      }',
            "      const pre = _reasoningCard.querySelector('pre');",
            '      if (pre) pre.textContent = _reasoningText;',
            '      scrollToChatBottom();',
            '      _idleExtensions = 0;',
            '      resetIdleTimer();',
            '    };',
            '',
            "    sse.addEventListener('reasoning', (e) => {",
            '      try { _onReasoningEvent(JSON.parse(e.data)); } catch (err) { console.warn(\'[reasoning] handler error:\', err); }',
            '    });',
        ].join('\n'),
    },
    {
        name: '4a) tool 리스너 시작 → _onToolEvent 함수 할당',
        find: [
            "    sse.addEventListener('tool', (e) => {",
            '      const data = JSON.parse(e.data);',
            "      const toolName = data.name || 'unknown';",
            "      const toolEvent = data.event || 'tool.started';",
            "      const isStarted = toolEvent === 'tool.started';",
        ].join('\n'),
        replace: [
            '    // [2026-08-30 도구 카드 유실 수정] 핸들러 본체를 _onToolEvent로 추출해',
            '    // 원본 SSE와 재연결 SSE 양쪽에서 재사용한다.',
            '    _onToolEvent = function (data) {',
            "      const toolName = data.name || 'unknown';",
            "      const toolEvent = data.event || 'tool.started';",
            "      const isStarted = toolEvent === 'tool.started';",
        ].join('\n'),
    },
    {
        name: '4b) tool 리스너 끝 → 함수 종료 + 리스너 등록',
        find: [
            '      _updateToolGroupHeader();',
            '      scrollToChatBottom();',
            '      _idleExtensions = 0;',
            '      _approvalPending = false;  // 도구 재개 = 승인 처리되어 에이전트가 다시 움직임',
            '      resetIdleTimer();',
            '    });',
        ].join('\n'),
        replace: [
            '      _updateToolGroupHeader();',
            '      scrollToChatBottom();',
            '      _idleExtensions = 0;',
            '      _approvalPending = false;  // 도구 재개 = 승인 처리되어 에이전트가 다시 움직임',
            '      resetIdleTimer();',
            '    };',
            '',
            "    sse.addEventListener('tool', (e) => {",
            "      try { _onToolEvent(JSON.parse(e.data)); } catch (err) { console.error('[SSE] tool event handler error:', err); }",
            '    });',
        ].join('\n'),
    },
    {
        name: '5) 승인 대기 중 도구 카드 항목을 "승인 대기"로 표시',
        find: [
            '          try { if (window.electronAPI) window.electronAPI.setVisibility(false); } catch (_) { }',
            '          // 상단 상태 표시를 "승인 대기"로 전환해 에이전트가 멈춘 것이 아니라',
            '          // 사용자 검토를 기다리는 중임을 명확히 한다.',
            "          if (data.type === 'dangerous_command') {",
            "            setStreamStatus('thinking', '⚠️ 위험 명령 승인 대기 중...');",
            '          } else {',
            "            setStreamStatus('thinking', '🛡️ 승인 대기 중...');",
            '          }',
        ].join('\n'),
        replace: [
            '          try { if (window.electronAPI) window.electronAPI.setVisibility(false); } catch (_) { }',
            '          // 상단 상태 표시를 "승인 대기"로 전환해 에이전트가 멈춘 것이 아니라',
            '          // 사용자 검토를 기다리는 중임을 명확히 한다.',
            "          if (data.type === 'dangerous_command') {",
            "            setStreamStatus('thinking', '⚠️ 위험 명령 승인 대기 중...');",
            '          } else {',
            "            setStreamStatus('thinking', '🛡️ 승인 대기 중...');",
            '          }',
            '          // [2026-08-30] 도구 그룹 카드의 "실행 중" 항목을 "승인 대기"로 바꿔',
            '          // 카드가 (0/1)에서 멈춰 보이는 이유를 즉시 설명한다.',
            '          try {',
            '            if (_toolGroupItems) {',
            "              _toolGroupItems.querySelectorAll('.tool-group-item').forEach((it) => {",
            "                const st = it.querySelector('.tgi-status');",
            "                if (st && st.textContent === '실행 중') {",
            "                  st.textContent = '승인 대기';",
            "                  const ic = it.querySelector('.tgi-icon');",
            "                  if (ic) ic.textContent = '⏸️';",
            '                }',
            '              });',
            '            }',
            '          } catch (_) { }',
        ].join('\n'),
    },
    {
        name: '6) error 리스너: 자동 재연결(CONNECTING) 존중 + CLOSED는 워치독 위임',
        find: [
            '      // EventSource.CLOSED: 서버가 연결을 닫았거나 네트워크가 끊긴 경우 — 곧바로 finishStream',
            '      if (sse.readyState === EventSource.CLOSED) {',
            "        finishStream('sse_closed');",
            '        return;',
            '      }',
            '',
            "      finishStream('error');",
        ].join('\n'),
        replace: [
            '      // ── [2026-08-30 도구 카드 유실 수정] EventSource 자동 재연결 존중 ──',
            '      // readyState === CONNECTING(0)이면 브라우저가 자동 재연결을 시도 중이다.',
            '      // 서버 큐는 워커 스레드가 살아있는 한 이벤트를 계속 쌓으므로 재연결 후',
            '      // 이어받을 수 있다. 기존에는 이 상태에서도 finishStream()을 호출해',
            '      // (1) 도구 카드/추론 카드가 DOM에서 제거되고 (2) sse.close()로 자동',
            '      // 재연결이 차단됐다 — "도구 출력이 나왔다가 사라지고 얇은 빈 줄만 남는"',
            '      // 증상의 근본 원인이었다.',
            '      if (sse.readyState === EventSource.CONNECTING) {',
            "        console.log('[SSE-DIAG] ⏳ error but CONNECTING — EventSource auto-reconnecting, stream kept alive');",
            '        return;',
            '      }',
            '',
            '      // CLOSED(2): 재연결 불가(404 등). 즉시 종료하지 말고 idle 워치독(30초)이',
            '      // 백엔드 상태를 확인해 수동 재연결 또는 세션 복구를 시도하게 위임한다.',
            '      // (_handleIdleTimeout → /api/chat/stream/status → 재연결/복구/finishStream)',
            '      if (sse.readyState === EventSource.CLOSED) {',
            "        console.log('[SSE-DIAG] 🔌 SSE CLOSED — deferring to idle watchdog for reconnect/recovery');",
            '        resetIdleTimer();',
            '        return;',
            '      }',
            '',
            "      finishStream('error');",
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

// 검증: 문법 체크
try {
    new Function(src);
    console.log('문법 체크: OK (Function 생성 성공)');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

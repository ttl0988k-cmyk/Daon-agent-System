/**
 * [2026-08-30 도구 카드 유실 수정] 적용 결과 검증 프로브
 * static/modules/chat.js의 6개 수정 지점이 올바르게 적용됐는지 확인한다.
 */
const fs = require('fs');
const src = fs.readFileSync('static/modules/chat.js', 'utf8');
const lines = src.split('\n');

function show(label, needle, ctx = 2) {
    const idx = lines.findIndex(l => l.includes(needle));
    if (idx === -1) { console.log(`[MISS] ${label} — "${needle}" 없음`); return; }
    console.log(`\n===== ${label} (line ${idx + 1}) =====`);
    for (let i = Math.max(0, idx - ctx); i <= Math.min(lines.length - 1, idx + ctx); i++) {
        console.log((i + 1) + ': ' + lines[i].replace(/\r$/, ''));
    }
}

// 1. 함수 스코프 선언
show('선언부', 'let _onToolEvent = null;');

// 2. 재연결 SSE 라이브 리스너
show('재연결 tool 리스너', '_onToolEvent(JSON.parse(ev.data))');

// 3. reasoning 함수 추출
show('_onReasoningEvent 할당', '_onReasoningEvent = function (data) {');

// 4. tool 함수 추출
show('_onToolEvent 할당', '_onToolEvent = function (data) {');

// 5. 승인 대기 항목 표시
show('승인 대기 항목 치환', "st.textContent = '승인 대기';");

// 6. error 리스너 CONNECTING 가드
show('CONNECTING 가드', 'error but CONNECTING');

// 7. CLOSED 위임
show('CLOSED 위임', 'deferring to idle watchdog');

// 8. 원본/재연결 리스너 등록부
show('원본 tool 리스너 등록', "try { _onToolEvent(JSON.parse(e.data)); } catch (err) { console.error('[SSE] tool event handler error:', err); }");
show('원본 reasoning 리스너 등록', "try { _onReasoningEvent(JSON.parse(e.data)); } catch (err) { console.warn('[reasoning] handler error:', err); }");

console.log('\n검증 완료');

/**
 * [SSE tool 이벤트 실측 프로브]
 * 직접 /api/chat/start로 에이전트를 구동하고 /api/chat/stream SSE를 구독해
 * tool/reasoning/token/speak 이벤트가 실제로 발행되는지 관찰한다.
 *
 * 사용: node _probe/sse_tool_event_probe.js "브라우저로 example.com 을 열어줘"
 */
const http = require('http');

const HOST = '127.0.0.1';
const PORT = 9090;
const MSG = process.argv[2] || '내부 브라우저로 https://example.com 을 열어줘';
const TIMEOUT_MS = 150000;

function req(method, path, body) {
    return new Promise((resolve, reject) => {
        const data = body ? JSON.stringify(body) : null;
        const r = http.request({
            host: HOST, port: PORT, path, method,
            headers: data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}
        },
            (res) => {
                let buf = '';
                res.on('data', (c) => { buf += c; });
                res.on('end', () => {
                    try { resolve(JSON.parse(buf)); } catch (e) { resolve(buf); }
                });
            });
        r.on('error', reject);
        if (data) r.write(data);
        r.end();
    });
}

(async () => {
    console.log('=== 1) 세션 목록 조회 ===');
    const sess = await req('GET', '/api/sessions');
    const sessions = sess.sessions || [];
    if (!sessions.length) { console.error('세션 없음'); process.exit(1); }
    const target = sessions[0];
    console.log('사용 세션:', target.session_id, '| 모델:', target.model);

    console.log('\n=== 2) 채팅 시작 ===');
    const start = await req('POST', '/api/chat/start', {
        session_id: target.session_id,
        message: MSG,
        model: target.model,
        workspace: target.workspace || 'C:\\daon\\Daon agent System',
    });
    if (!start.stream_id) { console.error('start 실패:', JSON.stringify(start).substring(0, 300)); process.exit(1); }
    console.log('stream_id:', start.stream_id);

    console.log('\n=== 3) SSE 구독 — 이벤트 관찰 ===');
    const counts = {};
    const toolEvents = [];
    const started = Date.now();

    await new Promise((resolve) => {
        const r = http.get({ host: HOST, port: PORT, path: `/api/chat/stream?stream_id=${start.stream_id}` }, (res) => {
            let buf = '';
            let curEvent = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => {
                buf += chunk;
                let idx;
                while ((idx = buf.indexOf('\n')) >= 0) {
                    const line = buf.slice(0, idx).replace(/\r$/, '');
                    buf = buf.slice(idx + 1);
                    if (line.startsWith('event:')) {
                        curEvent = line.slice(6).trim();
                    } else if (line.startsWith('data:')) {
                        const ev = curEvent || 'message';
                        counts[ev] = (counts[ev] || 0) + 1;
                        if (ev === 'tool') {
                            try {
                                const d = JSON.parse(line.slice(5).trim());
                                toolEvents.push(`${d.event} ${d.name}`);
                            } catch (_) { toolEvents.push('(parse fail)'); }
                        }
                        if (ev === 'done' || ev === 'error' || ev === 'cancel' || ev === 'apperror') {
                            console.log(`[종료] ${ev} @ ${((Date.now() - started) / 1000).toFixed(1)}s`);
                            r.destroy();
                            resolve();
                        }
                        curEvent = '';
                    }
                }
            });
            res.on('end', resolve);
            res.on('error', resolve);
        });
        r.on('error', resolve);
        setTimeout(() => { console.log('[타임아웃] 150초 경과'); r.destroy(); resolve(); }, TIMEOUT_MS);
    });

    console.log('\n=== 4) 결과 ===');
    console.log('이벤트 카운트:', JSON.stringify(counts, null, 1));
    console.log('tool 이벤트 상세:', toolEvents.length ? toolEvents.join(' | ') : '(없음!)');
    const hasTool = (counts.tool || 0) > 0;
    const hasSpeak = (counts.speak || 0) > 0;
    console.log('\n판정: tool 이벤트', hasTool ? '✅ 발행됨' : '❌ 미발행', '| speak 이벤트', hasSpeak ? '✅' : '❌');
    if (counts.token && !hasTool) {
        console.log('→ 토큰은 오는데 tool 이벤트만 없음: 백엔드 on_tool 콜백/put 경로 문제 (구조적)');
    } else if (!counts.token && !hasTool) {
        console.log('→ 토큰도 없음: 에이전트 자체가 응답하지 않았거나 즉시 종료됨');
    }
})().catch((e) => { console.error('프로브 오류:', e.message); process.exit(1); });

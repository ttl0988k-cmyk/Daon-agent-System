/**
 * [검증] renderMessages의 OpenAI 형식 tool_calls 파싱이 실제 세션에서
 * 몇 개의 도구 카드를 복원하는지 시뮬레이션한다.
 */
const http = require('http');

function get(path) {
    return new Promise((resolve, reject) => {
        http.get({ host: '127.0.0.1', port: 9090, path }, (r) => {
            let b = '';
            r.on('data', (c) => b += c);
            r.on('end', () => { try { resolve(JSON.parse(b)); } catch (e) { resolve(b); } });
        }).on('error', reject);
    });
}

(async () => {
    const j = await get('/api/session?session_id=20260831_142905_2c9bb0');
    const messages = (j.session || {}).messages || [];
    console.log('세션 메시지:', messages.length, '개');

    // chat.js renderMessages에 추가한 파싱 로직과 동일
    const out = [];
    const pending = {};
    messages.forEach((m, idx) => {
        if (!m) return;
        if (m.role === 'assistant' && Array.isArray(m.tool_calls)) {
            m.tool_calls.forEach((tc) => {
                if (!tc || typeof tc !== 'object') return;
                const tid = tc.id || tc.call_id || '';
                const fname = (tc.function && tc.function.name) || tc.name || '';
                if (!tid || !fname) return;
                let args = {};
                try { args = JSON.parse((tc.function && tc.function.arguments) || '{}'); } catch (_) { }
                pending[tid] = { name: fname, args, asstIdx: idx };
            });
        } else if (m.role === 'tool') {
            const tid2 = m.tool_call_id || m.tool_use_id || '';
            const p = pending[tid2];
            if (p) {
                out.push({ name: p.name, snippet: String(m.content || '').substring(0, 60), assistant_msg_idx: p.asstIdx });
            }
        }
    });

    console.log('\n복원된 tool_calls:', out.length, '개');
    const byName = {};
    out.forEach((t) => { byName[t.name] = (byName[t.name] || 0) + 1; });
    console.log('도구별:', JSON.stringify(byName, null, 1));
    console.log('\n샘플 5개:');
    out.slice(0, 5).forEach((t, i) => console.log(`  [${i}] ${t.name} (asst_idx=${t.assistant_msg_idx}) snippet=${t.snippet.replace(/\n/g, ' ').substring(0, 50)}`));
    console.log('\n판정:', out.length > 0 ? '✅ 도구 카드 복원 가능 — done 후에도 카드가 유지됨' : '❌ 복원 불가');
})();

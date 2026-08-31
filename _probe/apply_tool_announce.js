/**
 * [2026-08-31 도구 실행 선보고] chat.js + styles.css 패치
 *
 * 요청: 에이전트가 "어떤 작업을 하겠습니다" 설명 없이 싱킹 다음 바로 도구를
 *   실행해 사용자가 혼란스럽다. 모델 성향에 의존하지 말고 확실하게.
 *
 * 해결 (Roo Code 스타일): tool.started 이벤트 도착 시 — 즉 도구 실행 직전 —
 *   채팅 스트림에 선보고 텍스트를 삽입한다:
 *     🔧 브라우저로 페이지를 엽니다...
 *   프론트엔드 로직이므로 모델이 설명을 하든 안 하든 100% 표시된다.
 *   (시스템 프롬프트의 도구 전 설명 지시는 재빌드 시 함께 활성화 — 이중 보장)
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');

function patchFile(rel, patches) {
    const FILE = path.join(ROOT, rel);
    let src = fs.readFileSync(FILE, 'utf8');
    const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
    if (beforeMixed > 0) {
        src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
        console.log(`[${rel}] 줄바꿈 정규화: ${beforeMixed}개`);
    }
    const usesCRLF = (src.match(/\r\n/g) || []).length > 0;
    const toEOL = (s) => usesCRLF ? s.replace(/\r?\n/g, '\r\n') : s.replace(/\r?\n/g, '\n');
    console.log(`[${rel}] 줄바꿈: ${usesCRLF ? 'CRLF' : 'LF'}`);

    let applied = 0;
    for (const p of patches) {
        const f = toEOL(p.find), r = toEOL(p.replace);
        const count = src.split(f).length - 1;
        if (count !== 1) {
            console.error(`[FAIL] ${rel} :: ${p.name} — 매칭 ${count}회`);
            process.exit(1);
        }
        src = src.replace(f, r);
        applied++;
        console.log(`[OK] ${rel} :: ${p.name}`);
    }
    fs.writeFileSync(FILE, src, 'utf8');
    try {
        if (rel.endsWith('.js')) new Function(src);
        console.log(`[${rel}] 문법 체크: OK`);
    } catch (e) {
        console.error(`[${rel}] 문법 체크 실패:`, e.message);
        process.exit(1);
    }
}

// ═══════════ chat.js ═══════════
patchFile('static/modules/chat.js', [
    {
        name: '1) 도구 시작 시 선보고 텍스트 삽입 (브라우저 자동 오픈 블록 앞)',
        find: [
            '      // ── 내부 브라우저 자동 오픈 (2026-08-31) ──',
        ].join('\n'),
        replace: [
            '      // ── [2026-08-31 도구 실행 선보고] ──',
            '      // "싱킹 → 도구 실행"만으로는 뭘 하는지 알 수 없다는 요청 — 도구 실행',
            '      // 직전에 한국어 선보고를 채팅 스트림에 삽입한다. 프론트엔드 로직이므로',
            '      // 모델이 설명을 하든 안 하든 100% 표시된다 (Roo Code 스타일).',
            '      if (!_isInternalMarker && isStarted && _tDesc) {',
            '        try {',
            "          const ann = document.createElement('div');",
            "          ann.className = 'tool-announce';",
            "          ann.textContent = '🔧 ' + _tDesc + '...';",
            '          box.insertBefore(ann, asstBubble);',
            '        } catch (_) { }',
            '      }',
            '',
            '      // ── 내부 브라우저 자동 오픈 (2026-08-31) ──',
        ].join('\n'),
    },
]);

// ═══════════ styles.css ═══════════
patchFile('static/styles.css', [
    {
        name: '2) .tool-announce 스타일 (선보고 텍스트)',
        find: [
            '    .tool-group-item .tgi-desc {',
        ].join('\n'),
        replace: [
            '    /* [2026-08-31] 도구 실행 선보고 텍스트 — Roo Code 스타일 */',
            '    .tool-announce {',
            '      align-self: flex-start;',
            '      max-width: 90%;',
            '      padding: 4px 10px;',
            '      margin: 4px 0;',
            '      font-size: 11px;',
            '      color: var(--text2);',
            '      background: rgba(245, 158, 11, 0.08);',
            '      border-left: 3px solid rgba(245, 158, 11, 0.55);',
            '      border-radius: 4px;',
            '    }',
            '',
            '    .tool-group-item .tgi-desc {',
        ].join('\n'),
    },
]);

console.log('\n모든 패치 적용 완료');

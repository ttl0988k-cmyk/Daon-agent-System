/**
 * [2026-08-31 얇은 빈 줄의 최종 원인 — flex 찌그러짐 수정] static/styles.css 패치
 *
 * 에이전트 실측 (CDP로 검증):
 *   #chatMessages는 display:flex + flex-direction:column 컨테이너.
 *   .tool-group-card에 overflow:hidden이 있으면 CSS 명세상 flex item의
 *   automatic minimum size가 적용되지 않아(min-height 0) 채팅이 길어져
 *   스크롤이 찬 순간 카드가 내용 0 + 테두리 2px 선으로 찌그러든다.
 *   실측: 카드 높이 2px → flex-shrink:0 주입 시 37px 복원.
 *
 * 수정: .tool-group-card / .terminal-live-card / .reasoning-card에
 *   flex-shrink: 0 추가 (찌그러짐 방지).
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'styles.css');
let src = fs.readFileSync(FILE, 'utf8');

const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
}
const usesCRLF = (src.match(/\r\n/g) || []).length > 0;
const toEOL = (s) => usesCRLF ? s.replace(/\r?\n/g, '\r\n') : s.replace(/\r?\n/g, '\n');
console.log('styles.css 줄바꿈:', usesCRLF ? 'CRLF' : 'LF');

const patches = [
    {
        name: '1) .tool-group-card에 flex-shrink: 0',
        find: toEOL([
            '    .tool-group-card {',
            '      border: 1px solid var(--border);',
            '      border-radius: 8px;',
            '      margin: 6px 0;',
            '      overflow: hidden;',
            '      background: var(--bg2);',
            '    }',
        ].join('\n')),
        replace: toEOL([
            '    .tool-group-card {',
            '      border: 1px solid var(--border);',
            '      border-radius: 8px;',
            '      margin: 6px 0;',
            '      overflow: hidden;',
            '      background: var(--bg2);',
            '      /* [2026-08-31] flex column 컨테이너(#chatMessages)에서 overflow:hidden이',
            '         있으면 automatic minimum size가 0이 되어 카드가 2px 선으로 찌그러든다.',
            '         flex-shrink: 0으로 찌그러짐 방지 (에이전트 CDP 실측: 2px → 37px 복원). */',
            '      flex-shrink: 0;',
            '    }',
        ].join('\n')),
    },
];

// .terminal-live-card / .reasoning-card 규칙을 동적으로 찾아 flex-shrink 추가
function addFlexShrinkToRule(src, selector) {
    // selector { ... } 블록을 찾아 flex-shrink: 0이 없으면 추가
    const re = new RegExp('(' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{)([^}]*)\\}', 'g');
    let modified = 0;
    src = src.replace(re, (match, head, body) => {
        if (body.includes('flex-shrink')) return match;
        modified++;
        return head + body.replace(/(\r?\n)(\s*)$/, '$1$2') + '\n      flex-shrink: 0;\n    }';
    });
    return { src, modified };
}

// .terminal-live-card 찾기
const tlcMatch = src.match(/\.terminal-live-card\s*\{[^}]*\}/);
if (tlcMatch) {
    if (!tlcMatch[0].includes('flex-shrink')) {
        const newRule = tlcMatch[0].replace(/\}\s*$/, '      flex-shrink: 0;\n    }');
        src = src.replace(tlcMatch[0], newRule);
        console.log('[OK] .terminal-live-card에 flex-shrink: 0 추가');
    } else {
        console.log('[SKIP] .terminal-live-card 이미 flex-shrink 있음');
    }
} else {
    console.log('[INFO] .terminal-live-card 규칙 없음');
}

// .reasoning-card 찾기 (tool-card reasoning-card 또는 단독)
const rcMatch = src.match(/\.reasoning-card\s*\{[^}]*\}/);
if (rcMatch) {
    if (!rcMatch[0].includes('flex-shrink')) {
        const newRule = rcMatch[0].replace(/\}\s*$/, '      flex-shrink: 0;\n    }');
        src = src.replace(rcMatch[0], newRule);
        console.log('[OK] .reasoning-card에 flex-shrink: 0 추가');
    } else {
        console.log('[SKIP] .reasoning-card 이미 flex-shrink 있음');
    }
} else {
    console.log('[INFO] .reasoning-card 규칙 없음');
}

let applied = 0;
for (const p of patches) {
    const f = toEOL(p.find), r = toEOL(p.replace);
    const count = src.split(f).length - 1;
    if (count !== 1) {
        console.error(`[FAIL] ${p.name} — 매칭 ${count}회`);
        process.exit(1);
    }
    src = src.replace(f, r);
    applied++;
    console.log(`[OK] ${p.name}`);
}

fs.writeFileSync(FILE, src, 'utf8');
console.log(`\n완료: ${applied + 2} 패치 적용 → ${FILE}`);

/**
 * [2026-08-31 선보고 고도화] static/modules/chat.js 패치
 *
 * 요청: "브라우저로 페이지를 엽니다" 같은 간단 보고 말고,
 *   "어떤 작업을 위해 브라우저를 사용합니다", "이건 이렇게 작업 하겠습니다"
 *   같은 작업 내용 설명이 필요하다.
 *
 * 해결: 선보고에 도구 인자(args)를 포함한다 — 백엔드가 tool 이벤트에
 *   이미 args(URL, 파일경로, 명령어 등)를 담아 보내고 있다.
 *   예: "🔧 브라우저로 페이지를 엽니다 → https://www.figma.com/design/..."
 *       "🔧 터미널 명령을 실행합니다 → git status"
 *   "무엇을 위해, 무엇을 대상으로"가 즉시 보인다.
 *   (모델의 작업 의도 설명은 시스템 프롬프트 지시 — 재빌드 시 활성화)
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
        name: '1) args 요약 추출 헬퍼 추가 (_toolDescKo 옆)',
        find: toCRLF([
            "  function _toolDescKo(name) { return _TOOL_DESC_KO[name] || ''; }",
        ].join('\n')),
        replace: toCRLF([
            "  function _toolDescKo(name) { return _TOOL_DESC_KO[name] || ''; }",
            '  // [2026-08-31] 도구 인자 요약 — "무엇을 대상으로"를 선보고에 표시',
            '  function _argsSummary(args) {',
            "    if (!args || typeof args !== 'object') return '';",
            "    var keys = ['url', 'path', 'file_path', 'command', 'query', 'pattern', 'task', 'code', 'name'];",
            '    for (var i = 0; i < keys.length; i++) {',
            '      var v = args[keys[i]];',
            '      if (v) {',
            '        var s2 = String(v).replace(/\\s+/g, \' \').trim();',
            "        return s2.substring(0, 90) + (s2.length > 90 ? '…' : '');",
            '      }',
            '    }',
            '    for (var k in args) {',
            "      if (typeof args[k] === 'string' && args[k]) {",
            '        var s3 = args[k].replace(/\\s+/g, \' \').trim();',
            "        return s3.substring(0, 90) + (s3.length > 90 ? '…' : '');",
            '      }',
            '    }',
            "    return '';",
            '  }',
        ].join('\n')),
    },
    {
        name: '2) 선보고에 args 요약 포함 ("설명 → 대상" 형식)',
        find: toCRLF([
            '      if (!_isInternalMarker && isStarted && _tDesc) {',
            '        try {',
            "          const ann = document.createElement('div');",
            "          ann.className = 'tool-announce';",
            "          ann.textContent = '🔧 ' + _tDesc + '...';",
            '          box.insertBefore(ann, asstBubble);',
            '        } catch (_) { }',
            '      }',
        ].join('\n')),
        replace: toCRLF([
            '      if (!_isInternalMarker && isStarted) {',
            '        try {',
            "          const ann = document.createElement('div');",
            "          ann.className = 'tool-announce';",
            '          var _argSum = _argsSummary(data.args);',
            "          ann.textContent = '🔧 ' + (_tDesc || toolName) + (_argSum ? ' → ' + _argSum : '') + '...';",
            '          box.insertBefore(ann, asstBubble);',
            '        } catch (_) { }',
            '      }',
        ].join('\n')),
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

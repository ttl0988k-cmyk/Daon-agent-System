/**
 * [2026-08-31 도구 카드 한국어 설명 표시] chat.js + styles.css 패치
 *
 * 요청: 에이전트가 "어떤 작업을 하겠습니다" 설명 없이 싱킹 다음 바로 도구를
 *   실행해 사용자가 혼란스럽다.
 *
 * 해결 (2단):
 *   1) [즉시 효과] 도구 카드 항목에 한국어 설명을 표시한다 — 서버의 음성
 *      안내용 한국어 맵(_TOOL_SPEAK_MAP)과 동일한 문구를 프론트에 두고,
 *      도구 이름 옆에 "무엇을 하는 중인지" 표시.
 *   2) [재빌드 시] 시스템 프롬프트에 "도구 호출 전 한 문장 설명" 지시 추가
 *      (streaming.py — 별도 커밋).
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

// 도구별 한국어 설명 맵 (streaming.py _TOOL_SPEAK_MAP과 정합)
const TOOL_DESC_MAP = [
    "  // [2026-08-31] 도구별 한국어 설명 — 도구 카드에 '무엇을 하는 중인지' 표시",
    '  var _TOOL_DESC_KO = {',
    "    'browser_navigate': '브라우저로 페이지를 엽니다',",
    "    'browser_snapshot': '브라우저 화면을 분석합니다',",
    "    'browser_click': '브라우저 요소를 클릭합니다',",
    "    'browser_type': '브라우저에 텍스트를 입력합니다',",
    "    'browser_scroll': '브라우저 화면을 스크롤합니다',",
    "    'browser_press': '브라우저에 키를 입력합니다',",
    "    'browser_console': '브라우저 콘솔을 확인합니다',",
    "    'browser_cdp': '브라우저를 CDP로 제어합니다',",
    "    'browser_tabs': '브라우저 탭을 관리합니다',",
    "    'browser_switch_tab': '브라우저 탭을 전환합니다',",
    "    'browser_get_images': '브라우저에서 이미지를 추출합니다',",
    "    'terminal': '터미널 명령을 실행합니다',",
    "    'execute_command': '명령을 실행합니다',",
    "    'execute_code': '코드를 실행합니다',",
    "    'read_file': '파일을 읽습니다',",
    "    'write_file': '파일을 생성/수정합니다',",
    "    'patch': '코드를 수정합니다',",
    "    'apply_diff': '코드 변경사항을 적용합니다',",
    "    'search_files': '파일을 검색합니다',",
    "    'web_search': '웹을 검색합니다',",
    "    'skill_view': '스킬 정보를 확인합니다',",
    "    'skill_manage': '스킬을 관리합니다',",
    "    'query_patches': '패치 기록을 확인합니다',",
    "    'register_patch': '패치를 등록합니다',",
    "    'todo': '작업 계획을 관리합니다',",
    "    'memory': '메모리를 검색/저장합니다',",
    "    'clarify': '사용자에게 확인합니다',",
    "    'delegate_task': '하위 작업을 위임합니다',",
    "    'image_generate': '이미지를 생성합니다',",
    "    'video_generate': '영상을 생성합니다',",
    "    'vision_analyze': '이미지를 분석합니다',",
    "    'text_to_speech': '음성을 생성합니다',",
    "  };",
    "  function _toolDescKo(name) { return _TOOL_DESC_KO[name] || ''; }",
].join('\n');

// ═══════════ chat.js ═══════════
patchFile('static/modules/chat.js', [
    {
        name: '1) 도구별 한국어 설명 맵 선언 (메인 스트림 스코프)',
        find: [
            '      // [2026-08-31] _thinking은 내부 추론 마커다 — 도구 카드에 노출하지 않는다',
            '      // (카운트는 started/completed 쌍을 맞춰야 하므로 유지하고 항목만 숨긴다)',
            "      var _isInternalMarker = (toolName === '_thinking');",
        ].join('\n'),
        replace: [
            '      // [2026-08-31] _thinking은 내부 추론 마커다 — 도구 카드에 노출하지 않는다',
            '      // (카운트는 started/completed 쌍을 맞춰야 하므로 유지하고 항목만 숨긴다)',
            "      var _isInternalMarker = (toolName === '_thinking');",
            '      var _tDesc = _toolDescKo(toolName);',
        ].join('\n'),
    },
    {
        name: '2) started 항목에 한국어 설명 표시',
        find: [
            '        if (!_isInternalMarker) {',
            "        const item = document.createElement('div');",
            "        item.className = 'tool-group-item';",
            '        item.innerHTML = `',
            '          <span class="tgi-icon">⏳</span>',
            '          <span class="tgi-name">${toolName}</span>',
            '          <span class="tgi-status">실행 중</span>',
            '        `;',
        ].join('\n'),
        replace: [
            '        if (!_isInternalMarker) {',
            "        const item = document.createElement('div');",
            "        item.className = 'tool-group-item';",
            '        item.innerHTML = `',
            '          <span class="tgi-icon">⏳</span>',
            '          <span class="tgi-name">${toolName}</span>',
            '          <span class="tgi-desc">${_tDesc}</span>',
            '          <span class="tgi-status">실행 중</span>',
            '        `;',
        ].join('\n'),
    },
    {
        name: '3) completed-only 항목에도 한국어 설명 표시',
        find: [
            '        } else if (!_isInternalMarker) {',
            '          // started 없이 completed만 온 경우 — 항목 새로 추가',
            '          _toolGroupCount++;',
            "          const item = document.createElement('div');",
            "          item.className = 'tool-group-item';",
            '          item.innerHTML = `',
            '            <span class="tgi-icon">✅</span>',
            '            <span class="tgi-name">${toolName}</span>',
            '            <span class="tgi-status">완료</span>',
            '          `;',
            '          if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '        }',
        ].join('\n'),
        replace: [
            '        } else if (!_isInternalMarker) {',
            '          // started 없이 completed만 온 경우 — 항목 새로 추가',
            '          _toolGroupCount++;',
            "          const item = document.createElement('div');",
            "          item.className = 'tool-group-item';",
            '          item.innerHTML = `',
            '            <span class="tgi-icon">✅</span>',
            '            <span class="tgi-name">${toolName}</span>',
            '            <span class="tgi-desc">${_tDesc}</span>',
            '            <span class="tgi-status">완료</span>',
            '          `;',
            '          if (_toolGroupItems) _toolGroupItems.appendChild(item);',
            '        }',
        ].join('\n'),
    },
    {
        name: '4) 맵 + 헬퍼를 함수 스코프에 선언',
        find: [
            "  let incomingText = '';",
        ].join('\n'),
        replace: [
            '  let incomingText = \';',
            '',
            '  // ── [2026-08-31] 도구별 한국어 설명 맵 — 도구 카드에 표시 ──',
            '  var _TOOL_DESC_KO = {',
            "    'browser_navigate': '브라우저로 페이지를 엽니다',",
            "    'browser_snapshot': '브라우저 화면을 분석합니다',",
            "    'browser_click': '브라우저 요소를 클릭합니다',",
            "    'browser_type': '브라우저에 텍스트를 입력합니다',",
            "    'browser_scroll': '브라우저 화면을 스크롤합니다',",
            "    'browser_press': '브라우저에 키를 입력합니다',",
            "    'browser_console': '브라우저 콘솔을 확인합니다',",
            "    'browser_cdp': '브라우저를 CDP로 제어합니다',",
            "    'browser_tabs': '브라우저 탭을 관리합니다',",
            "    'browser_switch_tab': '브라우저 탭을 전환합니다',",
            "    'browser_get_images': '브라우저에서 이미지를 추출합니다',",
            "    'terminal': '터미널 명령을 실행합니다',",
            "    'execute_command': '명령을 실행합니다',",
            "    'execute_code': '코드를 실행합니다',",
            "    'read_file': '파일을 읽습니다',",
            "    'write_file': '파일을 생성/수정합니다',",
            "    'patch': '코드를 수정합니다',",
            "    'apply_diff': '코드 변경사항을 적용합니다',",
            "    'search_files': '파일을 검색합니다',",
            "    'web_search': '웹을 검색합니다',",
            "    'skill_view': '스킬 정보를 확인합니다',",
            "    'skill_manage': '스킬을 관리합니다',",
            "    'query_patches': '패치 기록을 확인합니다',",
            "    'register_patch': '패치를 등록합니다',",
            "    'todo': '작업 계획을 관리합니다',",
            "    'memory': '메모리를 검색/저장합니다',",
            "    'clarify': '사용자에게 확인합니다',",
            "    'delegate_task': '하위 작업을 위임합니다',",
            "    'image_generate': '이미지를 생성합니다',",
            "    'video_generate': '영상을 생성합니다',",
            "    'vision_analyze': '이미지를 분석합니다',",
            "    'text_to_speech': '음성을 생성합니다',",
            '  };',
            '  function _toolDescKo(name) { return _TOOL_DESC_KO[name] || \'\'; }',
        ].join('\n'),
    },
]);

// ═══════════ styles.css: .tgi-desc 스타일 ═══════════
patchFile('static/styles.css', [
    {
        name: '5) .tgi-desc 스타일 추가 (도구 설명 텍스트)',
        find: [
            '    .tool-group-card summary .tool-group-counter {',
        ].join('\n'),
        replace: [
            '    .tool-group-item .tgi-desc {',
            '      flex: 1;',
            '      font-size: 10px;',
            '      color: var(--muted);',
            '      white-space: nowrap;',
            '      overflow: hidden;',
            '      text-overflow: ellipsis;',
            '      text-align: left;',
            '    }',
            '',
            '    .tool-group-card summary .tool-group-counter {',
        ].join('\n'),
    },
]);

console.log('\n모든 패치 적용 완료');

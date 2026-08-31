/**
 * [2026-08-31 내부 브라우저 자동 오픈] static/modules/chat.js 패치
 *
 * 문제: 에이전트가 browser_navigate 등을 호출해도 내부 브라우저 뷰가 즉시
 *   열리지 않는다. 기존 자동 오픈은 프론트의 5초 폴링(pending_url)에 의존하는데,
 *   그 사이 에이전트는 서버에서 "탭 없음" 실패 응답을 받아 버린다.
 *
 * 해결: tool.started SSE는 도구 실행 시작 즉시 도착하므로, 여기서 내부
 *   브라우저 뷰를 먼저 열어준다. Electron TabManager.navigate()는 탭이
 *   없으면 자동 생성하므로, 뷰를 열고 URL을 넘기면 탭까지 만들어진다.
 *   그 결과 서버 워커의 CDP 연결이 성공할 확률이 크게 올라가고, 설령
 *   첫 시도가 실패해도 뷰가 열려 있는 상태라 재시도가 바로 성공한다.
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'static', 'modules', 'chat.js');
let src = fs.readFileSync(FILE, 'utf8');

// 안전장치: 비정상 줄바꿈 정규화
const beforeMixed = (src.match(/\r\r+\n/g) || []).length;
if (beforeMixed > 0) {
    src = src.replace(/\r\n/g, '\n').replace(/\r+/g, '').replace(/\n/g, '\r\n');
    console.log(`줄바꿈 정규화: ${beforeMixed}개 정리`);
}
const toCRLF = (s) => s.replace(/\r?\n/g, '\r\n');

const find = toCRLF([
    '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
    '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
    '      if (!_toolGroupCard) {',
].join('\n'));

const replace = toCRLF([
    '      // ── 내부 브라우저 자동 오픈 (2026-08-31) ──',
    '      // 에이전트가 browser_* 도구를 호출하면 5초 pending_url 폴링을 기다리지',
    '      // 않고 즉시 내부 브라우저 뷰를 연다. 기존 구조에서는 서버가 "탭 없음"을',
    '      // 반환하고 프론트 폴링이 뷰를 열 때까지(최대 5초) 에이전트가 실패 응답을',
    '      // 받는 문제가 있었다. tool.started SSE는 즉시 도착하므로 여기서 뷰를',
    '      // 먼저 열면 Electron TabManager.navigate()가 탭을 자동 생성하고, 서버',
    '      // 워커의 CDP 연결이 성공할 확률이 크게 올라간다.',
    '      if (isStarted && toolName.indexOf(\'browser_\') === 0 && window.electronAPI) {',
    '        try {',
    '          var _bvOpen = (typeof _browserViewVisible !== \'undefined\') ? _browserViewVisible : false;',
    '          if (!_bvOpen && typeof toggleBrowserView === \'function\') {',
    '            toggleBrowserView();',
    '          } else if (typeof syncElectronBrowserBounds === \'function\') {',
    '            syncElectronBrowserBounds();',
    '          }',
    '          if (toolName === \'browser_navigate\') {',
    '            var _bUrl = (data.args && (data.args.url || data.args.target_url)) || \'\';',
    '            if (_bUrl && typeof browserGoToAddress === \'function\') {',
    '              var _bInput = document.getElementById(\'browserCanvasUrlInput\') || document.getElementById(\'browserUrlInput\');',
    '              if (_bInput) _bInput.value = _bUrl;',
    '              browserGoToAddress();',
    '            }',
    '          }',
    '        } catch (_bErr) {',
    '          console.warn(\'[Chat→Browser] auto-open failed:\', _bErr);',
    '        }',
    '      }',
    '',
    '      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──',
    '      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)',
    '      if (!_toolGroupCard) {',
].join('\n'));

const count = src.split(find).length - 1;
if (count !== 1) {
    console.error(`[FAIL] 삽입 지점 매칭 ${count}회 (1회여야 함)`);
    process.exit(1);
}
src = src.replace(find, replace);
fs.writeFileSync(FILE, src, 'utf8');
console.log('[OK] browser_* 도구 시작 시 내부 브라우저 즉시 오픈 패치 적용');

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

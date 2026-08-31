/**
 * [2026-08-31 채팅창 이미지 붙여넣기] static/modules/chat.js 패치
 *
 * 요청: 채팅 창에 이미지 복사-붙여넣기(Ctrl+V)가 안 된다.
 * 원인: promptInput(textarea)에 paste 이벤트 핸들러가 없었다 —
 *   파일 선택 버튼과 드래그&드롭만 구현되어 있었음.
 * 수정: paste 이벤트에서 클립보드의 이미지 파일을 감지해 기존 첨부
 *   파이프라인(addFiles → State.pendingFiles)으로 넣는다.
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

const find = toCRLF([
    '  // 📎 File Attachment Bindings',
    "  const fileInput = $('fileInput');",
    "  const attachBtn = $('attachBtn');",
    '  if (attachBtn && fileInput) {',
    '    attachBtn.onclick = () => fileInput.click();',
    '    fileInput.onchange = (e) => {',
    '      addFiles(e.target.files);',
    "      fileInput.value = '';",
    '    };',
    '  }',
].join('\n'));

const replace = toCRLF([
    '  // 📎 File Attachment Bindings',
    "  const fileInput = $('fileInput');",
    "  const attachBtn = $('attachBtn');",
    '  if (attachBtn && fileInput) {',
    '    attachBtn.onclick = () => fileInput.click();',
    '    fileInput.onchange = (e) => {',
    '      addFiles(e.target.files);',
    "      fileInput.value = '';",
    '    };',
    '  }',
    '',
    '  // 🖼️ Image Paste Bindings (2026-08-31) — 채팅 입력창에 이미지 Ctrl+V 지원',
    '  // 클립보드의 이미지 파일을 감지해 기존 첨부 파이프라인(addFiles)으로 넣는다.',
    '  // 파일 선택 버튼/드래그&드롭과 동일한 경로라 업로드·전송 흐름이 그대로 재사용된다.',
    '  if (promptInput) {',
    "    promptInput.addEventListener('paste', (e) => {",
    '      try {',
    '        const items = e.clipboardData && e.clipboardData.items;',
    '        if (!items) return;',
    '        const imgFiles = [];',
    '        for (const item of items) {',
    "          if (item.kind === 'file') {",
    '            const f = item.getAsFile();',
    "            if (f && f.type && f.type.indexOf('image/') === 0) imgFiles.push(f);",
    '          }',
    '        }',
    '        if (imgFiles.length > 0) {',
    '          e.preventDefault();  // 이미지면 기본 붙여넣기(깨진 텍스트) 방지',
    '          addFiles(imgFiles);',
    "          if (typeof showToast === 'function') showToast('🖼️ 이미지 ' + imgFiles.length + '개가 첨부되었습니다.');",
    '        }',
    '      } catch (_pErr) {',
    "        console.warn('[paste] image paste failed:', _pErr);",
    '      }',
    '    });',
    '  }',
].join('\n'));

const count = src.split(find).length - 1;
if (count !== 1) {
    console.error(`[FAIL] 삽입 지점 매칭 ${count}회 (1회여야 함)`);
    process.exit(1);
}
src = src.replace(find, replace);
fs.writeFileSync(FILE, src, 'utf8');
console.log('[OK] 채팅창 이미지 붙여넣기(Ctrl+V) 패치 적용');

try {
    new Function(src);
    console.log('문법 체크: OK');
} catch (e) {
    console.error('문법 체크 실패:', e.message);
    process.exit(1);
}

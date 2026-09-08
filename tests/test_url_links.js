// @ts-check
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

console.log('--- Testing URL Autolinking & Markdown Parser ---');

const ROOT = path.resolve(__dirname, '..');
const coreCode = fs.readFileSync(path.join(ROOT, 'static', 'modules', 'core.js'), 'utf8');
const explorerCode = fs.readFileSync(path.join(ROOT, 'static', 'modules', 'explorer.js'), 'utf8');

const mockElement = () => ({
  addEventListener: () => {},
  removeEventListener: () => {},
  innerHTML: '',
  style: {},
});

const sandbox = {
  addEventListener: () => {},
  removeEventListener: () => {},
  window: {},
  document: {
    getElementById: () => mockElement(),
    querySelector: () => mockElement(),
    querySelectorAll: () => [],
    createElement: () => mockElement(),
    addEventListener: () => {},
    removeEventListener: () => {},
    body: mockElement(),
  },
  $: (id) => sandbox.document.getElementById(id),
  esc: (s) => (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
  State: { sessions: [], activeSessionId: null },
  console: console,
  localStorage: { getItem: () => null, setItem: () => {} },
};
sandbox.window = sandbox;
const context = vm.createContext(sandbox);

// Run core.js to load renderMd
vm.runInContext(coreCode, context);
assert.strictEqual(typeof sandbox.renderMd, 'function', 'renderMd must be defined in core.js');

// 1. Test Bare URL with protocol
const out1 = sandbox.renderMd('방문하세요: https://github.com/daon-ai/system 입니다.');
assert.ok(out1.includes('<a href="https://github.com/daon-ai/system" target="_blank" rel="noopener noreferrer" class="md-link">https://github.com/daon-ai/system</a>'), `Failed bare URL: ${out1}`);
console.log('✓ Bare URL with protocol successfully converted to <a> tag.');

// 2. Test Bare URL ending in punctuation
const out2 = sandbox.renderMd('사이트는 https://naver.com.');
assert.ok(out2.includes('<a href="https://naver.com" target="_blank" rel="noopener noreferrer" class="md-link">https://naver.com</a>.'), `Failed trailing period URL: ${out2}`);
console.log('✓ Bare URL with trailing period preserves period outside <a>.');

// 3. Test Bare URL in parentheses
const out3 = sandbox.renderMd('확인 (https://google.com/test)');
assert.ok(out3.includes('(<a href="https://google.com/test" target="_blank" rel="noopener noreferrer" class="md-link">https://google.com/test</a>)'), `Failed URL in parens: ${out3}`);
console.log('✓ Bare URL in parentheses preserves parens outside <a>.');

// 4. Test Autolink <https://...>
const out4 = sandbox.renderMd('링크: <https://example.com>');
assert.ok(out4.includes('<a href="https://example.com" target="_blank" rel="noopener noreferrer" class="md-link">https://example.com</a>'), `Failed autolink: ${out4}`);
console.log('✓ Markdown autolink <url> successfully converted.');

// 5. Test standard markdown link [text](url)
const out5 = sandbox.renderMd('구글 바로가기: [Google](https://google.com)');
assert.ok(out5.includes('<a href="https://google.com" target="_blank" rel="noopener noreferrer" class="md-link">Google</a>'), `Failed markdown link: ${out5}`);
console.log('✓ Standard markdown link [text](url) converted without double-linking.');

// 6. Test code blocks: URLs inside code must NOT be converted to <a>
const out6 = sandbox.renderMd('```bash\ncurl https://api.daon.ai\n```');
assert.ok(!out6.includes('<a href='), `URL inside code block was converted: ${out6}`);
assert.ok(out6.includes('curl https://api.daon.ai'), `Code block content corrupted: ${out6}`);
console.log('✓ Code blocks preserve raw URLs without converting to <a>.');

// 7. Test inline code: URLs inside `code` must NOT be converted to <a>
const out7 = sandbox.renderMd('실행 명령어: `https://test.com`');
assert.ok(!out7.includes('<a href='), `URL inside inline code was converted: ${out7}`);
assert.ok(out7.includes('<code class="md-inline">https://test.com</code>'), `Inline code corrupted: ${out7}`);
console.log('✓ Inline code preserves raw URLs without converting to <a>.');

// 8. Test bare www URL
const out8 = sandbox.renderMd('포털: www.daum.net 바로가기');
assert.ok(out8.includes('<a href="https://www.daum.net" target="_blank" rel="noopener noreferrer" class="md-link">www.daum.net</a>'), `Failed bare www: ${out8}`);
console.log('✓ Bare www URL converted to <a> with https:// prefix.');

// 9. Test formatUserMessageContent in explorer.js
vm.runInContext(explorerCode, context);
assert.strictEqual(typeof sandbox.formatUserMessageContent, 'function', 'formatUserMessageContent must be defined');

const userOut = sandbox.formatUserMessageContent('이 링크 확인해줘 https://github.com/foo/bar', 'sess-123');
assert.ok(userOut.includes('<a href="https://github.com/foo/bar" target="_blank" rel="noopener noreferrer" class="md-link">https://github.com/foo/bar</a>'), `Failed user message linkify: ${userOut}`);
console.log('✓ formatUserMessageContent converts URLs to <a> links in user messages.');

console.log('\n========================================');
console.log('URL Autolinking Unit Tests: ALL PASSED!');
console.log('========================================');

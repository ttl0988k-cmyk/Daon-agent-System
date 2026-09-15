#!/usr/bin/env node
/**
 * scripts/verify_scroll_anchoring.js
 *
 * 채팅 자동 스크롤 앵커링 동작 검증 (실제 static/modules/chat.js 코드를 로드해 실행).
 *
 * 검증 시나리오
 *  [1] 하단에 붙어 있을 때(_chatPinned=true) 신규 콘텐츠 → 하단으로 따라간다
 *  [2] 사용자가 위로 스크롤하면(_chatPinned=false) 신규 콘텐츠 → 위치가 유지된다
 *  [3] 위로 올리면 '맨 아래로' 버튼이 노출되고, 쌓인 신규 메시지 수가 표시된다
 *  [4] 버튼 클릭(forceStickChatBottom) → 하단 고정 복귀 + 버튼 숨김
 *  [5] renderMessages() 전체 재렌더 → 읽던 위치가 보존된다 (scrollTop 0 리셋 방지)
 *  [6] sendPrompt 경로(forceStickChatBottom) → 읽는 중이어도 하단 고정 복원
 *
 * 종료코드: 0=전부 통과, 1=실패 존재
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const CHAT_JS = path.join(ROOT, 'static', 'modules', 'chat.js');

let pass = 0;
const failures = [];

function check(name, cond, detail) {
    if (cond) { pass++; console.log('  PASS  ' + name); }
    else { failures.push(name); console.log('  FAIL  ' + name + (detail ? '  -> ' + detail : '')); }
}

// ── 최소 DOM 셰임 ────────────────────────────────────────────────────────────
function makeEl(tag) {
    const el = {
        tagName: (tag || 'div').toUpperCase(),
        children: [],
        parentNode: null,
        style: {},
        dataset: {},
        className: '',
        id: '',
        _text: '',
        _html: '',
        scrollTop: 0,
        scrollHeight: 1000,
        clientHeight: 400,
        clientWidth: 400,
        _listeners: {},
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
            contains(c) { return this._s.has(c); },
        },
        get textContent() { return this._text; },
        set textContent(v) { this._text = String(v); },
        get innerHTML() { return this._html; },
        set innerHTML(v) { this._html = String(v); if (v === '') { this.children.length = 0; } },
        appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
        insertBefore(c) { c.parentNode = this; this.children.unshift(c); return c; },
        remove() {
            if (this.parentNode) {
                const i = this.parentNode.children.indexOf(this);
                if (i >= 0) this.parentNode.children.splice(i, 1);
            }
            this.parentNode = null;
        },
        addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
        removeEventListener() { },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        getAttribute() { return null; },
        setAttribute() { },
        blur() { },
        fire(type, ev) { (this._listeners[type] || []).forEach(fn => fn(ev || {})); },
    };
    return el;
}

const registry = {};
const chatBox = makeEl('div'); chatBox.id = 'chatMessages';
const debateBox = makeEl('div'); debateBox.id = 'debateMessages'; debateBox.style.display = 'none';
const chatModeContent = makeEl('div'); chatModeContent.id = 'chatModeContent';
chatModeContent.appendChild(chatBox);
chatModeContent.appendChild(debateBox);
registry['chatMessages'] = chatBox;
registry['debateMessages'] = debateBox;
registry['chatModeContent'] = chatModeContent;

// 동적으로 append 된 요소까지 id 로 찾을 수 있도록 DOM 트리를 순회한다.
function findById(root, id) {
    if (!root) return null;
    if (root.id === id) return root;
    for (const c of (root.children || [])) {
        const hit = findById(c, id);
        if (hit) return hit;
    }
    return null;
}

const documentShim = {
    currentScript: { src: '/static/modules/chat.js?v=55' },
    getElementById(id) {
        return registry[id] || findById(chatModeContent, id) || null;
    },
    createElement(tag) { return makeEl(tag); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() { },
    body: makeEl('body'),
};

const sandbox = {
    console,
    document: documentShim,
    setTimeout: (fn) => { fn(); return 0; },   // 타이머 즉시 실행(결정적 검증)
    clearTimeout() { },
    setInterval: () => 0,
    clearInterval() { },
    requestAnimationFrame: (fn) => { fn(); return 0; },
    localStorage: { getItem: () => null, setItem() { }, removeItem() { } },
    JSON, Math, Date, String, Number, Array, Object, RegExp, Error,
    navigator: { userAgent: 'node' },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.window.electronAPI = undefined;
sandbox.addEventListener = () => { };

// chat.js 가 참조하는 전역 의존성 스텁
sandbox.$ = (id) => registry[id] || null;
sandbox.State = { activeSessionId: 'sess-1', currentStreamId: null, sessions: [], models: [] };
sandbox.api = async () => ({});
sandbox.renderMd = (t) => String(t || '');
sandbox.showToast = () => { };
sandbox.formatUserMessageContent = (t) => String(t || '');
sandbox.setChatStatus = () => { };
sandbox.stripThinkBlocks = (t) => String(t || '');
sandbox.resetIdleTimer = () => { };
sandbox.cleanupStreamState = () => { };
sandbox._isInternalNudgeMessage = () => false;
sandbox.renderTabs = () => { };
sandbox.refreshFileTree = async () => { };
sandbox.showCanvas = () => { };
sandbox.syncApprovalModeUI = () => { };
sandbox.setApprovalMode = () => { };
sandbox.showConfirmModal = async () => true;

const CODE = fs.readFileSync(CHAT_JS, 'utf8');
vm.createContext(sandbox);

console.log('[1] chat.js 로드 및 스크롤 API 노출');
try {
    vm.runInContext(CODE, sandbox, { filename: 'chat.js' });
} catch (e) {
    console.log('  FAIL  chat.js 로드 실패 -> ' + e.message);
    process.exit(1);
}
check('scrollToChatBottom 정의', typeof sandbox.scrollToChatBottom === 'function');
check('forceStickChatBottom 정의', typeof sandbox.forceStickChatBottom === 'function');
check('_isChatNearBottom 정의', typeof sandbox._isChatNearBottom === 'function');

// 리스너/버튼 초기화
sandbox.scrollToChatBottom();
const btn = documentShim.getElementById('chatScrollDownBtn');
check('맨 아래로 버튼이 DOM에 생성됨', !!btn, 'btn=' + btn);
check('스크롤 리스너가 chatMessages 에 바인딩됨',
    (chatBox._listeners['scroll'] || []).length > 0 &&
    (chatBox._listeners['wheel'] || []).length > 0,
    'scroll=' + (chatBox._listeners['scroll'] || []).length + ' wheel=' + (chatBox._listeners['wheel'] || []).length);

function setScroll(top, height, client) {
    chatBox.scrollHeight = height || 1000;
    chatBox.clientHeight = client || 400;
    chatBox.scrollTop = top;
    chatBox.fire('scroll');
}

console.log('\n[2] 하단 고정 상태: 신규 콘텐츠가 오면 하단으로 따라간다');
setScroll(600, 1000, 400);            // 1000-600-400 = 0 → 하단
sandbox.scrollToChatBottom();
check('하단 위치에서 scrollTop == scrollHeight(하단 이동)', chatBox.scrollTop === 1000,
    'scrollTop=' + chatBox.scrollTop);
check('버튼 숨김(visible 미부여)', !btn.classList.contains('visible'));

console.log('\n[3] 사용자가 위로 스크롤: 자동 스크롤이 멈추고 위치가 유지된다');
setScroll(100, 1000, 400);            // 1000-100-400 = 500 → 하단 아님
check('pinned 해제됨(_chatPinned=false)', sandbox._chatPinned === false);
chatBox.scrollHeight = 1400;          // 에이전트가 토큰을 더 쌓음
sandbox.scrollToChatBottom();
check('읽던 위치 유지(끌어내리지 않음)', chatBox.scrollTop === 100, 'scrollTop=' + chatBox.scrollTop);
check('버튼 노출됨', btn.classList.contains('visible'));

console.log('\n[4] 쌓인 신규 메시지 수 표시');
check('미확인 카운트 누적(_chatMissedCount=1)', sandbox._chatMissedCount === 1,
    'count=' + sandbox._chatMissedCount);
sandbox.scrollToChatBottom();
check('2회째 누적(_chatMissedCount=2)', sandbox._chatMissedCount === 2,
    'count=' + sandbox._chatMissedCount);
check('버튼 라벨에 새 메시지 수 표기', /2/.test(btn.textContent), 'label=' + btn.textContent);
check('has-new 강조 클래스', btn.classList.contains('has-new'));

console.log('\n[5] "맨 아래로" 버튼 클릭 → 하단 고정 복귀');
chatBox.scrollHeight = 1400;
btn.onclick();
check('하단으로 이동', chatBox.scrollTop === 1400, 'scrollTop=' + chatBox.scrollTop);
check('pinned 복원', sandbox._chatPinned === true);
check('버튼 숨김', !btn.classList.contains('visible'));
check('카운트 초기화', sandbox._chatMissedCount === 0);

console.log('\n[6] renderMessages 전체 재렌더 시 읽던 위치 보존');
const messages = [
    { role: 'user', content: '질문 1' },
    { role: 'assistant', content: '답변 1' },
];
sandbox.renderMessages(messages, []);
setScroll(150, 1000, 400);             // 사용자가 위쪽을 읽는 중
sandbox.renderMessages(messages, []);
check('재렌더 후에도 scrollTop 유지(0으로 리셋되지 않음)', chatBox.scrollTop === 150,
    'scrollTop=' + chatBox.scrollTop);

console.log('\n[7] 하단 고정 상태에서 재렌더 → 하단으로 따라간다');
setScroll(600, 1000, 400);
sandbox.renderMessages(messages, []);
check('재렌더 후 하단 이동', chatBox.scrollTop === chatBox.scrollHeight,
    'scrollTop=' + chatBox.scrollTop + ' height=' + chatBox.scrollHeight);

console.log('\n[8] forceStickChatBottom (메시지 전송 경로): 읽는 중이어도 하단 고정');
setScroll(120, 1000, 400);
check('사전조건: paused', sandbox._chatPinned === false);
chatBox.scrollHeight = 1600;
sandbox.forceStickChatBottom();
check('하단 고정 복원', sandbox._chatPinned === true);
check('하단으로 이동', chatBox.scrollTop === 1600, 'scrollTop=' + chatBox.scrollTop);

console.log('\n[9] 토론 모드(debateMessages)는 기존대로 항상 하단 유지');
debateBox.style.display = 'block';
debateBox.scrollHeight = 900;
debateBox.scrollTop = 50;
sandbox.scrollToChatBottom();
check('debateBox 는 항상 하단으로 이동', debateBox.scrollTop === 900,
    'scrollTop=' + debateBox.scrollTop);
debateBox.style.display = 'none';

console.log('\n=== RESULT ===');
console.log('PASS: ' + pass + '   FAIL: ' + failures.length);
if (failures.length) {
    failures.forEach(f => console.log('  - ' + f));
    process.exit(1);
}
process.exit(0);

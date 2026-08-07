/**
 * roo_chat.js — Roo React ChatView 임베드 제어 모듈.
 *
 * 역할:
 *   1. #rooChatFrame(iframe)과 기존 채팅 UI(#chatMessages/#chatInputArea) 간 전환
 *   2. iframe 내부(parent-bridge.ts)에서 postMessage로 올라오는 하네스 연동
 *      이벤트(agent_log, harness_tool)를 기존 harness.js 함수로 라우팅
 *
 * 설계 원칙:
 *   - DAON shell / 기존 chat.js / harness.js 는 수정하지 않는다.
 *   - iframe은 same-origin(/webview/)이므로 /api/* SSE·REST가 그대로 동작한다.
 *   - 전환 상태는 localStorage에 영속화한다.
 */

(function () {
    var PREF_KEY = 'daonRooChatEnabled';
    var ROO_URL = '/webview/';
    var _rooEnabled = false;
    var _frameLoaded = false;

    function _el(id) { return document.getElementById(id); }

    function _legacyEls() {
        // 기존 채팅 UI 구성 요소 (Roo UI 활성 시 숨김 처리 대상)
        return [_el('chatMessages'), _el('chatInputArea'), _el('cancelStreamBtn')];
    }

    function _applyVisibility() {
        var frame = _el('rooChatFrame');
        if (!frame) return;
        if (_rooEnabled) {
            if (!_frameLoaded) {
                frame.src = ROO_URL;
                _frameLoaded = true;
            }
            frame.style.display = 'block';
            _legacyEls().forEach(function (el) { if (el) el.style.display = 'none'; });
        } else {
            frame.style.display = 'none';
            _legacyEls().forEach(function (el) { if (el) el.style.display = ''; });
            // 기존 렌더링 복원 (세션 히스토리가 iframe 안에서만 갱신됐을 수 있으므로)
            try {
                if (typeof renderMessages === 'function' && typeof State !== 'undefined' && State.activeSessionId) {
                    var activeSess = (State.sessions || []).find(function (x) { return x.session_id === State.activeSessionId; });
                    if (activeSess) {
                        var normalMessages = (activeSess.messages || []).filter(function (msg) { return !msg.sender; });
                        renderMessages(normalMessages, activeSess.tool_calls);
                    }
                }
            } catch (e) { console.warn('[roo_chat] restore legacy render failed:', e); }
        }
        var btn = _el('rooChatToggleBtn');
        if (btn) {
            btn.classList.toggle('active', _rooEnabled);
            btn.textContent = _rooEnabled ? '🧪 Roo ●' : '🧪 Roo';
        }
    }

    /** 채팅 탭 상단 토글 버튼 핸들러 (index.html onclick) */
    window.toggleRooChatUI = function toggleRooChatUI() {
        _rooEnabled = !_rooEnabled;
        try { localStorage.setItem(PREF_KEY, _rooEnabled ? '1' : '0'); } catch (e) { }
        _applyVisibility();
        // Roo UI로 전환 시 기존 스트림이 돌고 있으면 취소 (이중 스트림 방지)
        if (_rooEnabled) {
            try {
                if (typeof State !== 'undefined' && State.isStreaming && typeof cancelActiveStream === 'function') {
                    cancelActiveStream();
                }
            } catch (e) { console.warn('[roo_chat] cancel legacy stream failed:', e); }
        }
    };

    /** iframe → 부모(DAON shell) 이벤트 라우팅 */
    window.addEventListener('message', function (ev) {
        var d = ev.data;
        if (!d || d.source !== 'daon-webview') return;

        if (d.type === 'agent_log') {
            // 기존 chat.js의 agent_log 리스너와 동일한 라우팅
            try {
                var logType = d.status === 'error' ? 'error'
                    : (d.status === 'done' || d.status === 'completed' || d.status === 'success') ? 'success'
                        : 'info';
                if (typeof appendCardLog === 'function') appendCardLog(d.agent_id || 'harness', d.content || '', logType);
                if (typeof updateCardStatus === 'function') updateCardStatus(d.agent_id || 'harness', d.status || 'running');
            } catch (err) { console.error('[roo_chat] agent_log routing failed:', err); }
        } else if (d.type === 'harness_tool') {
            // execute_dynamic_harness 시작 → 하네스 탭 전환, 완료 → 채팅 복귀
            try {
                if (d.started) {
                    if (typeof cleanupHarnessState === 'function') cleanupHarnessState();
                    var hc = _el('harnessConsole');
                    if (hc) hc.innerHTML = '';
                    if (typeof switchMode === 'function') switchMode('harness');
                    if (typeof logToConsole === 'function') {
                        logToConsole('🚀 채팅 에이전트(Roo UI)가 다이나믹 하네스를 실행합니다', 'info');
                        if (d.task) logToConsole('📋 작업: ' + d.task, 'info');
                    }
                } else {
                    if (typeof logToConsole === 'function') {
                        logToConsole('✅ 다이나믹 하네스 실행 완료 — 채팅에서 최종 보고를 확인하세요', 'success');
                    }
                    if (typeof switchMode === 'function') switchMode('chat');
                }
            } catch (err) { console.error('[roo_chat] harness_tool routing failed:', err); }
        }
    });

    /** 초기화: 저장된 설정 복원 (DOM 준비 후) */
    function _init() {
        try { _rooEnabled = localStorage.getItem(PREF_KEY) === '1'; } catch (e) { }
        _applyVisibility();
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _init);
    } else {
        _init();
    }
})();

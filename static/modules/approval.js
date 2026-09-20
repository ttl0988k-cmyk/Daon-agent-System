/**
 * approval.js - Inline Approval Cards & Choice Cards for DAON IDE
 *
 * Provides Roo Code-style inline approval cards that appear inside
 * the chat message stream (#chatMessages) or harness console (#harnessConsole).
 *
 * Fixes applied:
 * - [B] plan.md(is_plan) 승인 시 원본 diffActiveBar를 호출하지 않아 이중 UI 제거
 * - [C] is_plan 승인/거절 시 apply-preview / reject-preview 호출 스킵 + 잔존 bar 숨김
 * - [D] SSE 이벤트를 놓쳐도 복구하도록 /api/approval/pending 폴링 추가
 */

function showInlineApproval(data, container) {
    // [A] 위험 명령 pending 데이터는 status 필드가 없을 수 있어 type으로도 판별
    // auto_approved(자동 승인)는 type이 없어도(is_plan 하네스 등) 읽기 전용 완료 카드를 그린다.
    if (!data || (data.status !== 'pending' && data.type !== 'dangerous_command' && data.status !== 'auto_approved')) return;
    var currentActiveSid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
    var sid = data.session_id || currentActiveSid;
    // [세션 격리] 백그라운드 세션의 승인 데이터가 현재 활성 창(#approvalSlot)에 오염되지 않도록 차단
    if (data.session_id && currentActiveSid && data.session_id !== currentActiveSid) {
        return;
    }
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    // ── [E] 자동 승인(auto_approved): 버튼 없는 읽기 전용 완료 카드 ──
    // 백엔드가 45초 무응답 후 자동 승인했으므로 인터랙티브 승인 UI를 다시 그리면
    // 안 된다. 기존 pending 카드가 있다면 완료 카드로 교체한다.
    if (data.status === 'auto_approved') {
        var autoMsg = data.message || ('응답 없음 — 45초 후 자동 승인되었습니다.');
        var autoCard = document.createElement('div');
        autoCard.className = 'inline-approval-card resolved auto-approved';
        autoCard.id = 'inlineApprovalCard';
        if (sid) autoCard.setAttribute('data-session-id', sid);
        autoCard.innerHTML =
            '<div class="inline-approval-card-inner">'
            + '<div class="inline-approval-card-header">'
            + '<span class="inline-approval-card-icon">✅</span>'
            + '<span class="inline-approval-card-title">자동 승인됨</span>'
            + '</div>'
            + '<div class="inline-approval-card-body" style="color:var(--success);">'
            + _escInlineApproval(autoMsg)
            + '</div>'
            + '</div>';
        var _exAuto = document.getElementById('inlineApprovalCard');
        if (_exAuto) _exAuto.remove();
        container.appendChild(autoCard);
        _showApprovalSlotIfIsSlot(container);
        _syncWebviewForApprovalCard();
        _scrollContainerToBottom(container);
        _scrollChatToBottom();
        // 완료 카드는 잠시 후 자동 제거 (다음 승인/폴링에 지장 없도록)
        setTimeout(function () {
            var _c = document.getElementById('inlineApprovalCard');
            if (_c && _c.classList.contains('auto-approved')) _c.remove();
            _hideApprovalSlotIfEmpty();
        }, 6000);
        return;
    }
    if (!sid) return;
    var lc = data.line_changes || {};
    var file = data.path || '';
    var added = lc.added || 0;
    var removed = lc.removed || 0;
    var isPlan = data.is_plan || false;
    var isSkillSave = data.type === 'skill_save';
    var isDangerous = data.type === 'dangerous_command';
    var previewId = data.preview_id || '';
    var card = document.createElement('div');
    card.className = 'inline-approval-card';
    card.id = 'inlineApprovalCard';
    card.setAttribute('data-preview-id', previewId);
    card.setAttribute('data-session-id', sid);
    card.setAttribute('data-is-plan', isPlan ? '1' : '');
    card.setAttribute('data-kind', isDangerous ? 'dangerous_command' : (isSkillSave ? 'skill_save' : 'architect'));
    // [G] 모든 승인 요청은 동일한 심플 박스 헤더(⚠ 승인 필요)로 통일.
    var icon = '\u26A0\uFE0F';
    var title = '승인 필요';
    var body;
    if (isSkillSave) {
        body = '\u{1F4BE} 작업을 스킬로 저장할까요?<br><span style="color:var(--text2);font-size:12px;">'
            + _escInlineApproval(data.message || ('\'' + (data.task || 'Unknown').slice(0, 60) + '\' 실행 결과를 재사용 가능한 스킬로 저장합니다.')) + '</span>';
    } else if (isDangerous) {
        body = '<div style="margin-bottom:6px;">명령 실행을 허용할까요?</div>'
            + (data.description ? '<div style="margin-bottom:6px;color:var(--text2);font-size:12px;">' + _escInlineApproval(data.description) + '</div>' : '')
            + '<pre style="margin:0;padding:8px;background:rgba(0,0,0,0.25);border:1px solid rgba(255,255,255,0.1);border-radius:6px;font-size:12px;white-space:pre-wrap;word-break:break-all;">'
            + _escInlineApproval(data.command || '') + '</pre>';
    } else if (isPlan) {
        body = '\u{1F4CB} 실행 계획을 검토하고 승인해주세요.<br><span style="color:var(--text2);font-size:12px;">'
            + _escInlineApproval(data.message || '') + '</span>';
    } else {
        body = '\u{1F4C4} 파일 변경을 승인할까요?<br><code>' + _escInlineApproval(file || 'unknown') + '</code> '
            + '<span style="color:var(--success)">+' + added + '</span> '
            + '<span style="color:var(--danger)">-' + removed + '</span>';
    }
    var actionsHtml;
    // [G] 모든 승인(위험 명령 포함)은 동일한 심플 박스: [승인] [거부] 두 버튼만.
    // 위험 명령은 승인 → choice 'once', 거부 → choice 'deny' 로 /api/approval/respond 에 전달.
    if (isDangerous) {
        actionsHtml =
            '<button class="ia-approve-btn" data-choice="once" onclick="handleInlineApproval(true, this)">승인</button>'
            + '<button class="ia-reject-btn" data-choice="deny" onclick="handleInlineApproval(false, this)">거부</button>';
    } else {
        actionsHtml =
            '<button class="ia-approve-btn" onclick="handleInlineApproval(true, this)">승인</button>'
            + '<button class="ia-reject-btn" onclick="handleInlineApproval(false, this)">거부</button>';
    }
    card.innerHTML =
        '<div class="inline-approval-card-inner">'
        + '<div class="inline-approval-card-header">'
        + '<span class="inline-approval-card-icon">' + icon + '</span>'
        + '<span class="inline-approval-card-title">' + title + '</span>'
        + '</div>'
        + '<div class="inline-approval-card-body">' + body + '</div>'
        + '<div class="inline-approval-card-actions">' + actionsHtml + '</div>'
        + '</div>';
    var existing = document.getElementById('inlineApprovalCard');
    if (existing) existing.remove();
    container.appendChild(card);
    _showApprovalSlotIfIsSlot(container);
    // [웹뷰 클릭 가로채기 근본 수정] 카드가 그려지는 순간 웹뷰를 숨긴다.
    // SSE/폴링 어느 경로로 이 함수에 도달하든 반드시 실행된다.
    _syncWebviewForApprovalCard();
    _scrollContainerToBottom(container);
    _scrollChatToBottom();
}

// ── [F] Dedicated approval slot: chat bottom (#approvalSlot) ──
// The approval card is rendered in a separate container outside of #chatMessages.
// renderMessages()'s innerHTML clearing (done/recovery/session switch) can't
// wipe the card, so the approve/reject buttons always stay alive.
function _getApprovalSlot() {
    var slot = document.getElementById('approvalSlot');
    if (slot) return slot;
    // Fallback for old index.html without the slot: create it dynamically right after chatMessages.
    var box = document.getElementById('chatMessages');
    if (!box || !box.parentNode) return null;
    slot = document.createElement('div');
    slot.id = 'approvalSlot';
    slot.style.display = 'none';
    box.parentNode.insertBefore(slot, box.nextSibling);
    return slot;
}
function _showApprovalSlotIfIsSlot(container) {
    if (!container || container.id !== 'approvalSlot') return;
    container.style.display = 'block';
    // [버튼 안 먹힘 근본 수정] 슬롯은 chatModeContent 안에 있다. 하네스 모드가
    // 활성이라 chatModeContent 가 display:none 이면 슬롯도 함께 숨겨져
    // 승인 카드가 보이지 않는다. 승인이 뜨는 순간 chat 모드로 강제 전환해
    // 슬롯(그리고 버튼)이 반드시 보이도록 한다.
    var chatContent = document.getElementById('chatModeContent');
    if (chatContent && chatContent.style.display === 'none') {
        if (typeof switchMode === 'function') {
            try { switchMode('chat'); } catch (e) { chatContent.style.display = 'block'; }
        } else {
            chatContent.style.display = 'block';
        }
    }
}
function _scrollChatToBottom() {
    // [2026-09-15] 사용자가 위쪽을 읽는 중이면 승인 카드로 인해 끌어내리지 않는다.
    // chat.js 의 전역 스크롤 앵커링(_chatPinned / scrollToChatBottom)을 사용한다.
    if (typeof scrollToChatBottom === 'function') { scrollToChatBottom(); return; }
    var box = document.getElementById('chatMessages');
    if (box) box.scrollTop = box.scrollHeight;
}
function _hideApprovalSlotIfEmpty() {
    var slot = document.getElementById('approvalSlot');
    if (slot && !slot.querySelector('.inline-approval-card')) slot.style.display = 'none';
    // [웹뷰 클릭 가로채기 근본 수정] 승인 카드가 하나도 남지 않으면 승인 대기 중
    // 숨겼던 Electron 웹뷰(WebContentsView)를 복원한다. 카드 생명주기의 "복원"
    // 단일 지점 — 카드가 사라지는 모든 경로(승인/거절/자동승인/타임아웃)가
    // 이 함수를 거치므로 복원 누락이 없다.
    _restoreWebviewIfNoApprovalCard();
}

// 승인 카드가 화면에 하나라도 있으면 웹뷰를 숨기고, 없으면 복원한다.
// Electron 의 WebContentsView 는 HTML DOM 이 아니라 OS 레이어라서 승인 카드
// 위를 덮으면 클릭을 가로챈다(실측: respond 요청 0건 → 45초 자동 승인).
// SSE/폴링/하네스 어느 경로로 카드가 뜨든 이 두 함수가 반드시 호출되므로
// 경로별 분기 없이 한 곳에서 불변식을 유지한다.
function _syncWebviewForApprovalCard() {
    var card = document.querySelector('.inline-approval-card');
    try {
        if (!window.electronAPI) return;
        if (card) window.electronAPI.setVisibility(false);
        else window.electronAPI.setVisibility(true);
    } catch (_) { }
}
function _restoreWebviewIfNoApprovalCard() {
    if (document.querySelector('.inline-approval-card')) return;
    try { if (window.electronAPI) window.electronAPI.setVisibility(true); } catch (_) { }
}

async function handleInlineApproval(approved, btnEl) {
    var card = btnEl ? btnEl.closest('.inline-approval-card') : null;
    if (!card) return;
    // 이중 클릭/중복 처리 방지 (같은 카드는 한 번만 처리)
    if (card.getAttribute('data-busy') === '1') return;
    card.setAttribute('data-busy', '1');
    var sid = card.getAttribute('data-session-id');
    var previewId = card.getAttribute('data-preview-id');
    var isPlan = card.getAttribute('data-is-plan') === '1';
    var kind = card.getAttribute('data-kind') || 'architect';
    var actions = card.querySelector('.inline-approval-card-actions');
    if (actions) {
        actions.innerHTML = '<span style="color:var(--text2);font-size:12px;padding:8px;">처리 중...</span>';
    }
    // 스킬 저장 승인: 전용 엔드포인트 사용 (Architect approve/reject 경로로 가면 스킬 추출이 시작되지 않음)
    if (kind === 'skill_save') {
        try {
            await api(approved ? '/api/approval/skill-save/approve' : '/api/approval/skill-save/reject', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid })
            });
            card.outerHTML = approved
                ? '<div class="inline-approval-card resolved approved"><div class="inline-approval-card-inner"><span style="color:var(--success)">✅ 스킬로 저장 중...</span></div></div>'
                : '<div class="inline-approval-card resolved rejected"><div class="inline-approval-card-inner"><span style="color:var(--text2)">❌ 스킬 저장 안 함</span></div></div>';
        } catch (err) {
            console.error('[InlineApproval] skill-save error:', err);
            if (actions && card.isConnected) {
                actions.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval(err.message || '') + '</span>';
            }
            card.removeAttribute('data-busy');
        }
        // [2026-08-27] 승인 대기 중 숨겼던 웹 뷰 복원
        try { if (window.electronAPI) window.electronAPI.setVisibility(true); } catch (_) { }
        setTimeout(function () {
            var resolved = document.querySelector('.inline-approval-card.resolved');
            if (resolved) resolved.remove();
            _hideApprovalSlotIfEmpty();
        }, 5000);
        return;
    }
    // [A] 위험 명령 승인: Architect diff 플로우가 아니라 /api/approval/respond 로 choice 전달 (once|session|always|deny)
    if (kind === 'dangerous_command') {
        var choice = (btnEl && btnEl.getAttribute && btnEl.getAttribute('data-choice')) || (approved ? 'once' : 'deny');
        try {
            await api('/api/approval/respond', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid, choice: choice })
            });
            card.outerHTML = approved
                ? '<div class="inline-approval-card resolved approved"><div class="inline-approval-card-inner"><span style="color:var(--success)">✅ 명령 승인됨 (' + _escInlineApproval(choice) + ')</span></div></div>'
                : '<div class="inline-approval-card resolved rejected"><div class="inline-approval-card-inner"><span style="color:var(--danger)">❌ 명령 거절됨</span></div></div>';
            try { if (typeof _approvalPending !== 'undefined') _approvalPending = false; } catch (e) { }
        } catch (err) {
            console.error('[InlineApproval] respond error:', err);
            // [버튼 안 먹힘 보강] outerHTML 교체로 card 가 DOM 에서 분리됐을 수 있으므로
            // isConnected 를 확인한 뒤에만 actions 를 갱신한다 (죽은 노드 참조 방지).
            if (actions && card.isConnected) {
                actions.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval(err.message || '') + '</span>';
                card.removeAttribute('data-busy'); // 일시 실패 시 재시도 허용
            }
        }
        // [2026-08-27] 승인 대기 중 숨겼던 웹 뷰 복원
        try { if (window.electronAPI) window.electronAPI.setVisibility(true); } catch (_) { }
        setTimeout(function () {
            var resolved = document.querySelector('.inline-approval-card.resolved');
            if (resolved) resolved.remove();
            _hideApprovalSlotIfEmpty();
        }, 5000);
        return;
    }
    try {
        if (approved) {
            var apprRes = await api('/api/approval/approve', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid, preview_id: previewId, reviewer: 'user' })
            });
            // [C] plan.md 승인은 diff 적용 대상이 아니므로 apply-preview 스킵
            if (previewId && apprRes.ok && !isPlan) {
                try {
                    await api('/api/file/apply-preview', {
                        method: 'POST',
                        timeout: 30000,
                        body: JSON.stringify({ session_id: sid, preview_id: previewId })
                    });
                } catch (e) { console.warn('[InlineApproval] Apply-preview failed:', e); }
            }
            card.outerHTML = '<div class="inline-approval-card resolved approved">'
                + '<div class="inline-approval-card-inner">'
                + '<span style="color:var(--success)">\u2705 승인됨</span>'
                + '</div></div>';
            if (typeof refreshFileTree === 'function') {
                refreshFileTree().catch(function () { });
            }
        } else {
            await api('/api/approval/reject', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid, reason: 'User rejected via inline card' })
            });
            // [C] plan.md 거절은 reject-preview 스킵
            if (previewId && !isPlan) {
                try {
                    await api('/api/file/reject-preview', {
                        method: 'POST',
                        body: JSON.stringify({ session_id: sid, preview_id: previewId })
                    });
                } catch (e) { console.warn('[InlineApproval] Reject-preview failed:', e); }
            }
            card.outerHTML = '<div class="inline-approval-card resolved rejected">'
                + '<div class="inline-approval-card-inner">'
                + '<span style="color:var(--danger)">\u274C 거절됨</span>'
                + '</div></div>';
        }
    } catch (err) {
        console.error('[InlineApproval] Error:', err);
        var errMsg = (err && err.message) || '';
        if (/no pending/i.test(errMsg)) {
            // 서버 측 승인이 이미 처리된 경우(타임아웃/중복 처리) — 카드를 우아하게 정리
            if (card.isConnected) {
                card.outerHTML = '<div class="inline-approval-card resolved approved"><div class="inline-approval-card-inner"><span style="color:var(--text2)">⌛ 이미 처리된 승인 요청입니다</span></div></div>';
            }
        } else if (actions && card.isConnected) {
            actions.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval(errMsg) + '</span>';
            card.removeAttribute('data-busy'); // 일시 실패 시 재시도 허용
        }
    }
    // [C] 혹시 남아있을 수 있는 diff 패널 상단 bar 숨김
    var leftoverBar = document.getElementById('diffActiveBar');
    if (leftoverBar) leftoverBar.style.display = 'none';
    if (typeof _resetApprovalButtons === 'function') _resetApprovalButtons();
    // [2026-08-27] 승인 대기 중 숨겼던 웹 뷰 복원
    try { if (window.electronAPI) window.electronAPI.setVisibility(true); } catch (_) { }
    setTimeout(function () {
        var resolved = document.querySelector('.inline-approval-card.resolved');
        if (resolved) resolved.remove();
        _hideApprovalSlotIfEmpty();
    }, 5000);
}

function showChoiceCard(question, choices, container) {
    if (!question || !choices || !choices.length) return;
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    var card = document.createElement('div');
    card.className = 'inline-choice-card';
    var headerHTML = '<div class="inline-choice-card-header">'
        + '<span class="inline-choice-card-icon">\u{1F914}</span>'
        + '<span class="inline-choice-card-title">' + _escInlineApproval(question) + '</span>'
        + '</div>';
    card.innerHTML = headerHTML;
    var choicesWrap = document.createElement('div');
    choicesWrap.className = 'inline-choice-card-choices';
    choices.forEach(function (choice) {
        var btn = document.createElement('button');
        btn.className = 'ic-choice-btn';
        btn.textContent = choice.text;
        btn.addEventListener('click', function () {
            handleChoiceClick(choice.text, choice.mode || '', btn);
        });
        choicesWrap.appendChild(btn);
    });
    card.appendChild(choicesWrap);
    container.appendChild(card);
    _scrollContainerToBottom(container);
}

function handleChoiceClick(text, mode, btnEl) {
    var card = btnEl.closest('.inline-choice-card');
    if (card) {
        var allBtns = card.querySelectorAll('.ic-choice-btn');
        allBtns.forEach(function (b) { b.disabled = true; b.style.opacity = '0.5'; });
        btnEl.style.opacity = '1';
        btnEl.style.background = 'var(--accent)';
        btnEl.style.color = '#fff';
        btnEl.style.borderColor = 'var(--accent)';
        btnEl.textContent = '\u2705 ' + btnEl.textContent;
        var indicator = document.createElement('div');
        indicator.className = 'inline-choice-selected';
        indicator.style.cssText = 'font-size:11px;color:var(--text2);padding:6px 0 0 0;';
        indicator.textContent = '선택됨: ' + text;
        card.appendChild(indicator);
    }
    var promptInput = document.getElementById('promptInput');
    if (promptInput) {
        promptInput.value = text;
        promptInput.style.height = 'auto';
        promptInput.style.height = promptInput.scrollHeight + 'px';
        promptInput.focus();
        if (typeof switchMode === 'function') {
            if (mode && typeof switchAgentMode === 'function') {
                switchAgentMode(mode);
            } else {
                switchMode('chat');
            }
        }
    }
}

function _escInlineApproval(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _scrollContainerToBottom(container) {
    if (!container) return;
    // 승인 슬롯이 아닌 채팅 컨테이너라면 읽기 중 스크롤을 보존한다.
    if (container.id === 'chatMessages' && typeof scrollToChatBottom === 'function') {
        scrollToChatBottom();
        return;
    }
    container.scrollTop = container.scrollHeight;
}

var _origShowApprovalBanner = (typeof _showApprovalBanner === 'function') ? _showApprovalBanner : null;
_showApprovalBanner = function (data) {
    if (!data) return;
    var curSid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
    // [세션 격리] 다른 세션의 이벤트인 경우 토스트 알림만 띄우고 현재 활성 창에는 카드를 그리지 않음
    if (data.session_id && curSid && data.session_id !== curSid) {
        if (typeof _showToast === 'function') {
            try {
                var sessObj = (typeof State !== 'undefined' && State.sessions) ? State.sessions.find(function (s) { return s.session_id === data.session_id; }) : null;
                var sessName = (sessObj && sessObj.title) ? sessObj.title : '다른 세션';
                _showToast('⚠ [' + sessName + '] 세션에서 승인 요청이 대기 중입니다.');
            } catch (e) { }
        }
        return;
    }
    // ── [H] 자율 실행 모드(autonomous): 승인 박스를 표시하지 않고 백엔드 승인 API로
    // 자동 응답(once/approve)해 에이전트를 계속 진행시킨다. 버튼 숨김(display:none)이
    // 아니라 백엔드 승인 시스템을 그대로 통과시키는 방식이다. ──
    if (data.status !== 'auto_approved' && getApprovalMode() === 'autonomous') {
        _autoRespondApproval(data);
        return;
    }
    // 하네스 동적 승인({message, actions, onAction} 형태): 액션 버튼 인라인 카드로 렌더.
    // (diff 승인으로 취급하면 아무것도 표시되지 않아 하네스 승인이 불가능해진다.)
    if (data.actions && data.actions.length && typeof data.onAction === 'function') {
        var hContainer = _resolveApprovalContainer();
        if (hContainer) showHarnessApprovalCard(data, hContainer);
        if (typeof _showToast === 'function') { try { _showToast('⚠ 작업 승인이 필요합니다.'); } catch (e) { } }
        return;
    }
    // auto_approved(자동 승인)는 type이 없어도(is_plan 하네스 등) 배너로 알린다.
    if (data.status !== 'pending' && data.type !== 'dangerous_command' && data.status !== 'auto_approved') return;
    // [F] 승인 이벤트로 상단 diff 바(diffActiveBar)를 다시 띄우지도, 미리보기를 등록하지도 않는다.
    // registerDiffPreview 호출이 화려한 글로우의 상단 바(✓ 적용/✕ 거절/👁 상세 보기)를
    // 부활시키는 원인이었다. 모든 승인은 채팅 아래 전용 슬롯의 인라인 카드로 통일한다.
    var container = _resolveApprovalContainer(data);
    if (container) showInlineApproval(data, container);
    if (typeof _showToast === 'function') {
        try {
            // [E] 자동 승인: "승인 필요"가 아니라 "자동 승인됨"을 알린다.
            if (data.status === 'auto_approved') _showToast('✅ 응답 없음 — 자동 승인됨');
            else if (data.type === 'dangerous_command') _showToast('⚠ 위험 명령 승인이 필요합니다.');
            else if (data.is_plan) _showToast('⚠ 실행 계획 승인이 필요합니다.');
            else _showToast('⚠ 승인이 필요합니다: ' + (data.path || ''));
        } catch (e) { }
    }
};

// ── 하네스 동적 승인 인라인 카드 ──
// harness.js의 {message, actions, onAction} 형태 승인을 인라인 카드로 렌더링한다.
function showHarnessApprovalCard(data, container) {
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    var currentActiveSid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
    var sid = data.session_id || currentActiveSid;
    if (data.session_id && currentActiveSid && data.session_id !== currentActiveSid) {
        return;
    }
    var existing = document.getElementById('inlineApprovalCard');
    if (existing) existing.remove();
    var card = document.createElement('div');
    card.className = 'inline-approval-card';
    card.id = 'inlineApprovalCard';
    if (sid) card.setAttribute('data-session-id', sid);
    card.setAttribute('data-kind', 'harness');
    var actionsHtml = data.actions.map(function (a) {
        var val = (typeof a === 'string') ? a : (a.action || a.label || String(a));
        var label = (typeof a === 'string') ? a : (a.label || a.action || String(a));
        var cls = /approve|accept|continue|proceed|yes|승인|계속|진행/i.test(val) ? 'ia-approve-btn' : 'ia-reject-btn';
        return '<button class="' + cls + '" data-action="' + _escInlineApproval(val) + '">' + _escInlineApproval(label) + '</button>';
    }).join('');
    // [G] 다이나믹 하네스 승인도 동일한 심플 박스 헤더(⚠ 승인 필요)로 통일.
    card.innerHTML =
        '<div class="inline-approval-card-inner">'
        + '<div class="inline-approval-card-header">'
        + '<span class="inline-approval-card-icon">\u26A0\uFE0F</span>'
        + '<span class="inline-approval-card-title">승인 필요</span>'
        + '</div>'
        + '<div class="inline-approval-card-body">' + _escInlineApproval(data.message || '작업을 계속하려면 승인해 주세요.') + '</div>'
        + '<div class="inline-approval-card-actions">' + actionsHtml + '</div>'
        + '</div>';
    card.querySelectorAll('button[data-action]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (card.getAttribute('data-busy') === '1') return;
            card.setAttribute('data-busy', '1');
            var action = btn.getAttribute('data-action');
            var wrap = card.querySelector('.inline-approval-card-actions');
            if (wrap) wrap.innerHTML = '<span style="color:var(--text2);font-size:12px;padding:8px;">처리 중...</span>';
            Promise.resolve()
                .then(function () { return data.onAction(action); })
                .then(function () {
                    if (!card.isConnected) return;
                    card.removeAttribute('id');
                    card.outerHTML = '<div class="inline-approval-card resolved approved"><div class="inline-approval-card-inner"><span style="color:var(--success)">✅ 응답 완료 (' + _escInlineApproval(action) + ')</span></div></div>';
                    setTimeout(function () {
                        var resolved = document.querySelector('.inline-approval-card.resolved');
                        if (resolved) resolved.remove();
                        _hideApprovalSlotIfEmpty();
                    }, 5000);
                })
                .catch(function (err) {
                    console.error('[HarnessApproval] onAction error:', err);
                    card.removeAttribute('data-busy');
                    if (card.isConnected && wrap) {
                        wrap.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval((err && err.message) || '') + '</span>';
                    }
                });
        });
    });
    container.appendChild(card);
    _showApprovalSlotIfIsSlot(container);
    // [웹뷰 클릭 가로채기 근본 수정] 하네스 동적 승인 카드도 그려지는 순간
    // 웹뷰를 숨긴다. 카드를 그리는 세 함수(showInlineApproval 2분기 +
    // showHarnessApprovalCard) 모두가 이 불변식을 지켜야 경로 누락이 없다.
    _syncWebviewForApprovalCard();
    _scrollContainerToBottom(container);
    _scrollChatToBottom();
}

// ── [D] Approval 폴링: SSE 이벤트를 놓쳐도 복구 ──
var _approvalPollTimer = null;
function _resolveApprovalContainer(data) {
    // ── [F] 모든 승인은 전용 슬롯(#approvalSlot)으로 통일 렌더 ──
    // 슬롯은 #chatMessages 밖에 있으므로 renderMessages()의 innerHTML 초기화로
    // 카드가 사라지는 일이 없고, 승인/거절 버튼이 항상 동작한다.
    return _getApprovalSlot();
}
async function _pollApprovalOnce() {
    try {
        // 탭/창이 숨겨져 있으면 폴링 스킵 (로그 폭주 방지)
        if (document.hidden) return;
        var sid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
        if (!sid) return;
        // [세션 격리 & 버튼 안 먹힘 근본 수정]
        // 화면에 표시된 카드가 다른 세션의 것이라면 즉시 치우고 슬롯을 숨긴 뒤 현재 세션 폴링 진행
        var existing = document.getElementById('inlineApprovalCard');
        if (existing && existing.isConnected && existing.offsetParent !== null) {
            var existingSid = existing.getAttribute('data-session-id');
            if (existingSid && existingSid !== sid) {
                existing.remove();
                _hideApprovalSlotIfEmpty();
            } else {
                return;
            }
        }
        var res = await api('/api/approval/pending?session_id=' + encodeURIComponent(sid), { method: 'GET' });
        // [A] CLI 위험 명령 pending 데이터는 status 필드가 없으므로 type으로도 판별
        if (res && res.has_pending && res.pending && (res.pending.status === 'pending' || res.pending.type === 'dangerous_command')) {
            // [H] 자율 실행 모드: 폴링으로 발견한 pending도 자동 응답으로 통과
            if (getApprovalMode() === 'autonomous') {
                _autoRespondApproval(res.pending);
                return;
            }
            var container = _resolveApprovalContainer(res.pending);
            if (container) showInlineApproval(res.pending, container);
        }
    } catch (e) { /* 폴링 실패는 조용히 무시 */ }
}

// ── [세션 전환 시 승인 슬롯 동기화] ──
// 세션이 바뀔 때 이전 세션의 카드를 즉시 지우고 새 세션의 승인 대기 여부를 조회한다.
function syncApprovalSlotForSession(sid) {
    var targetSid = sid || ((typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null);
    var card = document.getElementById('inlineApprovalCard');
    if (card) {
        var cardSid = card.getAttribute('data-session-id');
        if (cardSid && targetSid && cardSid !== targetSid) {
            card.remove();
            _hideApprovalSlotIfEmpty();
        }
    }
    if (targetSid) {
        _pollApprovalOnce();
    }
}
if (typeof window !== 'undefined') {
    window.syncApprovalSlotForSession = syncApprovalSlotForSession;
}

function ensureApprovalPolling() {
    if (_approvalPollTimer) return;
    _approvalPollTimer = setInterval(_pollApprovalOnce, 2000);
}
ensureApprovalPolling();

// ── [H] 실행 방식(approvalMode): 세션 단위 플래그 ─────────────────────────────
//   normal     → 기존 방식: #approvalSlot 에 심플 승인 박스 표시, 사용자 클릭 대기
//   autonomous → 승인 요청 발생 시 백엔드 승인 API 에 자동으로 'once'/approve 응답
//                후 에이전트 계속 진행. 백엔드 승인 시스템은 그대로 유지되고
//                UX 만 바뀐다 (버튼 숨김 ≠ 승인 요구사항 제거).
var _approvalModeWarned = {};
function _approvalModeKey() {
    var sid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
    return 'daon_approval_mode_' + (sid || 'default');
}
function getApprovalMode() {
    try {
        var v = localStorage.getItem(_approvalModeKey());
        return v === 'autonomous' ? 'autonomous' : 'normal';
    } catch (e) { return 'normal'; }
}
function setApprovalMode(mode, silent) {
    var next = (mode === 'autonomous') ? 'autonomous' : 'normal';
    try { localStorage.setItem(_approvalModeKey(), next); } catch (e) { }
    syncApprovalModeUI();
    // 자율 실행 진입 시 세션당 1회만 경고 안내
    if (next === 'autonomous' && !silent) {
        var sid = (typeof State !== 'undefined') ? (State.activeSessionId || 'default') : 'default';
        if (!_approvalModeWarned[sid]) {
            _approvalModeWarned[sid] = true;
            if (typeof _showToast === 'function') {
                try { _showToast('⚠️ 자율 실행 모드에서는 위험 명령을 포함한 승인 요청을 자동 승인합니다.'); } catch (e) { }
            }
        }
    }
}
function toggleApprovalMode() {
    setApprovalMode(getApprovalMode() === 'autonomous' ? 'normal' : 'autonomous');
}
function syncApprovalModeUI() {
    var btn = document.getElementById('approvalModeToggle');
    if (!btn) return;
    var auto = getApprovalMode() === 'autonomous';
    btn.textContent = auto ? '🤖 자율 실행' : '🛡️ 일반 실행';
    btn.title = auto
        ? '자율 실행 모드: 승인 요청(위험 명령 포함)을 자동으로 승인합니다. 클릭하면 일반 실행으로 전환.'
        : '일반 실행 모드: 위험 작업마다 확인합니다. 클릭하면 자율 실행으로 전환.';
    // 두 모드를 강하게 시각 구분한다 (일반=파랑 보호 톤 / 자율=주황 경고 톤).
    // 배경 틴트 + 테두리 + 굵기(+자율은 발광)로 상태를 전달하고, 글자는 항상
    // 밝은 본문색(var(--text))으로 유지해 어두운/밝은 테마 모두에서 가독성을 보장한다.
    if (auto) {
        btn.classList.add('mode-autonomous');
        btn.classList.remove('mode-normal');
        btn.style.background = 'rgba(230, 126, 34, 0.20)';
        btn.style.borderColor = '#e67e22';
        btn.style.fontWeight = '700';
        btn.style.boxShadow = '0 0 6px rgba(230, 126, 34, 0.45)';
    } else {
        btn.classList.add('mode-normal');
        btn.classList.remove('mode-autonomous');
        btn.style.background = 'rgba(59, 130, 246, 0.16)';
        btn.style.borderColor = '#3b82f6';
        btn.style.fontWeight = '600';
        btn.style.boxShadow = 'none';
    }
    btn.style.color = 'var(--text, #e2e2f2)';
}
// [H] 자율 실행 모드에서 승인 요청을 백엔드 승인 API 로 자동 통과시킨다.
async function _autoRespondApproval(data) {
    if (!data) return;
    var sid = data.session_id || ((typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null);
    // 하네스 동적 승인({actions, onAction}): approve 계열 액션을 골라 onAction 호출
    if (data.actions && data.actions.length && typeof data.onAction === 'function') {
        var pick = null;
        for (var i = 0; i < data.actions.length; i++) {
            var a = data.actions[i];
            var val = (typeof a === 'string') ? a : (a.action || a.label || String(a));
            if (/approve|accept|continue|proceed|yes|승인|계속|진행/i.test(val)) { pick = val; break; }
        }
        if (!pick) {
            pick = (typeof data.actions[0] === 'string') ? data.actions[0] : (data.actions[0].action || data.actions[0].label || String(data.actions[0]));
        }
        try {
            await data.onAction(pick);
            if (typeof _showToast === 'function') { try { _showToast('🤖 자율 실행: 작업 승인 자동 응답 (' + pick + ')'); } catch (e) { } }
        } catch (e) {
            console.error('[AutoApproval] harness onAction error:', e);
        }
        return;
    }
    if (!sid) return;
    try {
        if (data.type === 'dangerous_command') {
            // 위험 명령: 'once' 자동 응답 → 에이전트 계속
            await api('/api/approval/respond', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid, choice: 'once' })
            });
        } else if (data.type === 'skill_save') {
            await api('/api/approval/skill-save/approve', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid })
            });
        } else {
            // architect 파일 변경 / is_plan(실행 계획): approve (+ 파일 변경이면 apply-preview)
            var apprRes = await api('/api/approval/approve', {
                method: 'POST',
                body: JSON.stringify({ session_id: sid, preview_id: data.preview_id || '', reviewer: 'autonomous' })
            });
            if (data.preview_id && apprRes && apprRes.ok && !data.is_plan) {
                try {
                    await api('/api/file/apply-preview', {
                        method: 'POST',
                        timeout: 30000,
                        body: JSON.stringify({ session_id: sid, preview_id: data.preview_id })
                    });
                    if (typeof refreshFileTree === 'function') refreshFileTree().catch(function () { });
                } catch (e) { console.warn('[AutoApproval] apply-preview failed:', e); }
            }
        }
        if (typeof _showToast === 'function') { try { _showToast('🤖 자율 실행: 승인 자동 통과'); } catch (e) { } }
    } catch (e) {
        console.error('[AutoApproval] error:', e);
        // 자동 응답 실패 시 안전 폴백: 일반 모드처럼 승인 박스를 표시한다.
        if (typeof _showToast === 'function') { try { _showToast('⚠ 자동 승인 실패 — 승인 박스를 표시합니다.'); } catch (ee) { } }
        var container = _resolveApprovalContainer(data);
        if (container) showInlineApproval(data, container);
    }
}

// ── [버튼 안 먹힘 최종 안전망] 문서 레벨 이벤트 위임 ──────────────────────────
// 승인/거절 버튼은 인라인 onclick="handleInlineApproval(...)" 을 쓴다. 그런데
// 카드가 재렌더/재배치되거나 인라인 핸들러가 어떤 이유로든 죽으면 클릭이
// 유실된다("승인/거절 버튼이 안 먹히는" 문제). 문서 레벨에서 캡처 단계로
// 위임하면, 카드가 어디로 옮겨지든 버튼 클릭을 반드시 잡아낸다.
// 인라인 핸들러가 이미 처리한 경우(data-busy=1)는 중복 실행하지 않는다.
(function _installApprovalDelegation() {
    if (typeof document === 'undefined') return;
    if (window.__daonApprovalDelegationInstalled) return;
    window.__daonApprovalDelegationInstalled = true;
    document.addEventListener('click', function (ev) {
        var btn = ev.target && ev.target.closest
            ? ev.target.closest('.ia-approve-btn, .ia-reject-btn')
            : null;
        if (!btn) return;
        var card = btn.closest('.inline-approval-card');
        if (!card) return;
        // 하네스 동적 승인 카드(data-action 버튼)는 자체 addEventListener 로
        // data.onAction 을 호출한다. 여기서 가로채면 안 되므로 건너뛴다.
        if (btn.hasAttribute('data-action')) return;
        // 인라인 onclick 이 이미 처리 중이면 건너뛴다 (이중 실행 방지).
        if (card.getAttribute('data-busy') === '1') return;
        var approved = btn.classList.contains('ia-approve-btn');
        // 인라인 핸들러가 정상 동작하면 여기서는 아무 것도 하지 않는다.
        // (인라인 핸들러가 먼저 실행되어 data-busy=1 을 세팅하므로 위에서 걸러진다.)
        // 인라인 핸들러가 죽어 이 시점에 도달한 경우에만 폴백 실행한다.
        if (typeof handleInlineApproval === 'function') {
            try { handleInlineApproval(approved, btn); } catch (e) {
                console.error('[ApprovalDelegation] fallback error:', e);
            }
        }
    }, true);
})();

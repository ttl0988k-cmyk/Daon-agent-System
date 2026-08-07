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
    if (!data || (data.status !== 'pending' && data.type !== 'dangerous_command')) return;
    if (typeof container === 'string') container = document.getElementById(container);
    if (!container) return;
    var sid = (typeof State !== 'undefined') ? (State.sessionId || State.activeSessionId) : null;
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
    card.setAttribute('data-kind', isDangerous ? 'dangerous_command' : 'architect');
    var icon, title, body;
    if (isSkillSave) {
        icon = '\u{1F4BE}';
        title = '작업을 스킬로 저장할까요?';
        body = data.message || ('\'' + (data.task || 'Unknown').slice(0, 60) + '\' 실행 결과를 재사용 가능한 스킬로 저장합니다.');
    } else if (isDangerous) {
        icon = '\u26A0\uFE0F';
        title = '위험한 명령 - 승인 필요';
        body = '<div style="margin-bottom:6px;">' + _escInlineApproval(data.description || data.message || '') + '</div>'
            + '<pre style="margin:0;padding:8px;background:rgba(0,0,0,0.25);border:1px solid rgba(255,255,255,0.1);border-radius:6px;font-size:12px;white-space:pre-wrap;word-break:break-all;">'
            + _escInlineApproval(data.command || '') + '</pre>';
    } else if (isPlan) {
        icon = '\u{1F4CB}';
        title = '실행 계획 승인';
        body = data.message || '실행 계획을 검토하고 승인해주세요.';
    } else {
        icon = '\u{1F4C4}';
        title = '파일 변경 승인 필요';
        body = '<code>' + _escInlineApproval(file || 'unknown') + '</code> '
            + '<span style="color:var(--success)">+' + added + '</span> '
            + '<span style="color:var(--danger)">-' + removed + '</span>';
    }
    var actionsHtml;
    if (isDangerous) {
        // [A] 위험 명령: once|session|always|deny 선택지 제공
        actionsHtml =
            '<button class="ia-approve-btn" data-choice="once" onclick="handleInlineApproval(true, this)">승인 (1회)</button>'
            + '<button class="ia-approve-btn" data-choice="session" onclick="handleInlineApproval(true, this)">세션 동안 승인</button>'
            + '<button class="ia-approve-btn" data-choice="always" onclick="handleInlineApproval(true, this)">항상 승인</button>'
            + '<button class="ia-reject-btn" data-choice="deny" onclick="handleInlineApproval(false, this)">거절</button>';
    } else {
        actionsHtml =
            '<button class="ia-approve-btn" onclick="handleInlineApproval(true, this)">승인</button>'
            + '<button class="ia-reject-btn" onclick="handleInlineApproval(false, this)">거절</button>';
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
    _scrollContainerToBottom(container);
}

async function handleInlineApproval(approved, btnEl) {
    var card = btnEl.closest('.inline-approval-card');
    if (!card) return;
    var sid = card.getAttribute('data-session-id');
    var previewId = card.getAttribute('data-preview-id');
    var isPlan = card.getAttribute('data-is-plan') === '1';
    var kind = card.getAttribute('data-kind') || 'architect';
    var actions = card.querySelector('.inline-approval-card-actions');
    if (actions) {
        actions.innerHTML = '<span style="color:var(--text2);font-size:12px;padding:8px;">처리 중...</span>';
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
            if (actions) {
                actions.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval(err.message || '') + '</span>';
            }
        }
        setTimeout(function () {
            var resolved = document.querySelector('.inline-approval-card.resolved');
            if (resolved) resolved.remove();
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
        if (actions) {
            actions.innerHTML = '<span style="color:var(--danger);font-size:12px;padding:8px;">오류: ' + _escInlineApproval(err.message || '') + '</span>';
        }
    }
    // [C] 혹시 남아있을 수 있는 diff 패널 상단 bar 숨김
    var leftoverBar = document.getElementById('diffActiveBar');
    if (leftoverBar) leftoverBar.style.display = 'none';
    if (typeof _resetApprovalButtons === 'function') _resetApprovalButtons();
    setTimeout(function () {
        var resolved = document.querySelector('.inline-approval-card.resolved');
        if (resolved) resolved.remove();
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
    container.scrollTop = container.scrollHeight;
}

var _origShowApprovalBanner = (typeof _showApprovalBanner === 'function') ? _showApprovalBanner : null;
_showApprovalBanner = function (data) {
    if (!data || (data.status !== 'pending' && data.type !== 'dangerous_command')) return;
    // 상단 diff 바(diffActiveBar)는 더 이상 표시하지 않는다. 모든 승인(파일 변경/계획/위험 명령)은
    // 채팅 하단 인라인 카드로 통일한다. 미리보기 등록만 유지해 클라이언트 상태 일관성을 지킨다.
    if (data.preview_id && typeof registerDiffPreview === 'function') {
        try {
            registerDiffPreview({
                preview_id: data.preview_id,
                session_id: data.session_id || ((typeof State !== 'undefined') ? (State.sessionId || State.activeSessionId) : null),
                path: data.path,
                line_changes: data.line_changes,
                source_agent: data.source_agent || 'architect',
                approval_required: true
            });
        } catch (e) { }
    }
    var container = _resolveApprovalContainer();
    if (container) showInlineApproval(data, container);
    if (typeof _showToast === 'function') {
        try {
            if (data.type === 'dangerous_command') _showToast('⚠ 위험 명령 승인이 필요합니다.');
            else if (data.is_plan) _showToast('⚠ 실행 계획 승인이 필요합니다.');
            else _showToast('⚠ 승인이 필요합니다: ' + (data.path || ''));
        } catch (e) { }
    }
};

// ── [D] Approval 폴링: SSE 이벤트를 놓쳐도 복구 ──
var _approvalPollTimer = null;
function _resolveApprovalContainer() {
    var chatContent = document.getElementById('chatModeContent');
    var harnessContent = document.getElementById('harnessModeContent');
    var isHarnessVisible = harnessContent && harnessContent.style.display !== 'none'
        && (!chatContent || chatContent.style.display === 'none');
    if (isHarnessVisible) return document.getElementById('harnessConsole');
    return document.getElementById('chatMessages');
}
async function _pollApprovalOnce() {
    try {
        var sid = (typeof State !== 'undefined') ? (State.sessionId || State.activeSessionId) : null;
        if (!sid) return;
        // 이미 카드가 표시되어 있으면 중복 표시 방지
        if (document.getElementById('inlineApprovalCard')) return;
        var res = await api('/api/approval/pending?session_id=' + encodeURIComponent(sid), { method: 'GET' });
        // [A] CLI 위험 명령 pending 데이터는 status 필드가 없으므로 type으로도 판별
        if (res && res.has_pending && res.pending && (res.pending.status === 'pending' || res.pending.type === 'dangerous_command')) {
            var container = _resolveApprovalContainer();
            if (container) showInlineApproval(res.pending, container);
        }
    } catch (e) { /* 폴링 실패는 조용히 무시 */ }
}
function ensureApprovalPolling() {
    if (_approvalPollTimer) return;
    _approvalPollTimer = setInterval(_pollApprovalOnce, 2000);
}
ensureApprovalPolling();

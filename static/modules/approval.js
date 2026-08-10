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
        _scrollContainerToBottom(container);
        // 완료 카드는 잠시 후 자동 제거 (다음 승인/폴링에 지장 없도록)
        setTimeout(function () {
            var _c = document.getElementById('inlineApprovalCard');
            if (_c && _c.classList.contains('auto-approved')) _c.remove();
        }, 6000);
        return;
    }
    // 이벤트가 담은 session_id 우선 (컨텍스트 압축으로 세션이 회전된 뒤에도 정확)
    var sid = data.session_id || ((typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null);
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
        setTimeout(function () {
            var resolved = document.querySelector('.inline-approval-card.resolved');
            if (resolved) resolved.remove();
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
    if (!data) return;
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
    // 상단 diff 바(diffActiveBar)는 더 이상 표시하지 않는다. 모든 승인(파일 변경/계획/위험 명령)은
    // 채팅 하단 인라인 카드로 통일한다. 미리보기 등록만 유지해 클라이언트 상태 일관성을 지킨다.
    if (data.preview_id && typeof registerDiffPreview === 'function') {
        try {
            registerDiffPreview({
                preview_id: data.preview_id,
                session_id: data.session_id || ((typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null),
                path: data.path,
                line_changes: data.line_changes,
                source_agent: data.source_agent || 'architect',
                approval_required: true
            });
        } catch (e) { }
    }
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
    var existing = document.getElementById('inlineApprovalCard');
    if (existing) existing.remove();
    var card = document.createElement('div');
    card.className = 'inline-approval-card';
    card.id = 'inlineApprovalCard';
    card.setAttribute('data-kind', 'harness');
    var actionsHtml = data.actions.map(function (a) {
        var val = (typeof a === 'string') ? a : (a.action || a.label || String(a));
        var label = (typeof a === 'string') ? a : (a.label || a.action || String(a));
        var cls = /approve|accept|continue|proceed|yes|승인|계속|진행/i.test(val) ? 'ia-approve-btn' : 'ia-reject-btn';
        return '<button class="' + cls + '" data-action="' + _escInlineApproval(val) + '">' + _escInlineApproval(label) + '</button>';
    }).join('');
    card.innerHTML =
        '<div class="inline-approval-card-inner">'
        + '<div class="inline-approval-card-header">'
        + '<span class="inline-approval-card-icon">\u{1F6A7}</span>'
        + '<span class="inline-approval-card-title">작업 승인 필요</span>'
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
    _scrollContainerToBottom(container);
}

// ── [D] Approval 폴링: SSE 이벤트를 놓쳐도 복구 ──
var _approvalPollTimer = null;
function _resolveApprovalContainer(data) {
    // ── chat 스트림 승인(위험 명령)은 항상 chatMessages로 렌더 ──
    // harness 모드가 활성 상태여도 chat 스트림에서 온 위험 명령 승인은
    // 사용자가 보고 있는(또는 봐야 하는) chat 화면의 인라인 카드여야 한다.
    // harnessConsole에 붙으면 숨겨진 컨테이너에 렌더되어 "승인 창이 안 뜬다"
    // 버그가 된다.
    if (data && data.type === 'dangerous_command') {
        var chatBox = document.getElementById('chatMessages');
        if (chatBox) return chatBox;
    }
    var chatContent = document.getElementById('chatModeContent');
    var harnessContent = document.getElementById('harnessModeContent');
    var isHarnessVisible = harnessContent && harnessContent.style.display !== 'none'
        && (!chatContent || chatContent.style.display === 'none');
    // ── [E] 하네스 모드가 보이는 동안 skill_save / is_plan 은 harnessConsole 로 렌더 ──
    // 다이나믹 하네스 완료 후 뜨는 '스킬로 저장할까요' 팝업과 실행 계획 승인 카드는
    // 채팅창이 아니라 하네스 창에서 보여야 한다. skill_save 는 언제나 하네스 산출물이고,
    // is_plan(plan.md) 은 하네스 모드 실행 시 하네스 계획이므로 하네스 창이 적절하다.
    // (dangerous_command 만 chat 스트림 경유이므로 chatMessages 를 유지한다.)
    if (isHarnessVisible && data && (data.type === 'skill_save' || data.is_plan)) {
        return document.getElementById('harnessConsole');
    }
    if (isHarnessVisible) return document.getElementById('harnessConsole');
    return document.getElementById('chatMessages');
}
async function _pollApprovalOnce() {
    try {
        var sid = (typeof State !== 'undefined') ? (State.activeSessionId || State.sessionId) : null;
        if (!sid) return;
        // 이미 카드가 표시되어 있으면 중복 표시 방지
        if (document.getElementById('inlineApprovalCard')) return;
        var res = await api('/api/approval/pending?session_id=' + encodeURIComponent(sid), { method: 'GET' });
        // [A] CLI 위험 명령 pending 데이터는 status 필드가 없으므로 type으로도 판별
        if (res && res.has_pending && res.pending && (res.pending.status === 'pending' || res.pending.type === 'dangerous_command')) {
            var container = _resolveApprovalContainer(res.pending);
            if (container) showInlineApproval(res.pending, container);
        }
    } catch (e) { /* 폴링 실패는 조용히 무시 */ }
}
function ensureApprovalPolling() {
    if (_approvalPollTimer) return;
    _approvalPollTimer = setInterval(_pollApprovalOnce, 2000);
}
ensureApprovalPolling();

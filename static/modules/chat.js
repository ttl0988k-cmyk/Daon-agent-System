function getModelDisplayName(modelId) {
  if (!modelId) return '알 수 없음';
  // Friendly names for common models
  const friendly = {
    'anthropic/claude-sonnet-4': 'Claude Sonnet 4',
    'anthropic/claude-opus-4': 'Claude Opus 4',
    'anthropic/claude-haiku-4': 'Claude Haiku 4',
    'anthropic/claude-3.5-sonnet': 'Claude 3.5 Sonnet',
    'openai/gpt-4o': 'GPT-4o',
    'openai/gpt-4o-mini': 'GPT-4o Mini',
    'openai/gpt-4-turbo': 'GPT-4 Turbo',
    'deepseek-v4-pro': 'DeepSeek V4 Pro',
    'deepseek-v3': 'DeepSeek V3',
  };
  if (friendly[modelId]) return friendly[modelId];
  // Fallback: capitalize and replace hyphens/underscores
  return modelId.split('/').pop().replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

async function loadInitialData() {
  try {
    // 1. Models
    const modelsData = await api('/api/models');
    State.models = modelsData.groups || [];
    populateModelSelect();

    // 1b. Profiles
    const profData = await api('/api/profiles');
    State.profiles = profData.profiles;
    State.activeProfileName = profData.active;
    populateProfileSelect();

    // 2. Settings
    State.settings = await api('/api/settings');
    State.activeModelId = (State.settings || {}).default_model || '';
    if ($('modelSelect')) $('modelSelect').value = State.activeModelId;

    // 2b. Modes (Roo-style)
    loadModes();

    // 3. Sessions
    const sessData = await api('/api/sessions');
    State.sessions = sessData.sessions;
    renderSessionsList();

    if (State.sessions.length > 0) {
      await selectSession(State.sessions[0].session_id);
    } else {
      await createNewSession();
    }

    // 앱 시작 시 설정 모달 자동 오픈 (프로바이더/모델 설정 유도)
    // 프로바이더 미설정 시 설정창 자동 열기
    // API 키가 설정된 프로바이더가 하나도 없거나 local만 있는 경우 설정창 자동 표시
    // 💡 앱 시작 시 프로바이더 설정창 바로 열기
    setTimeout(() => { openSettingsModal(); }, 300);

    // 4. Preload Skills & MCP data in background (non-blocking)
    //    사이드바 메뉴를 클릭하기 전에 데이터를 미리 로드하여 패널 전환 시 즉시 표시
    loadSkills().catch(e => console.warn('[preload] skills:', e));
    refreshMcpServers().catch(e => console.warn('[preload] mcp servers:', e));
    loadMcpPresets().catch(e => console.warn('[preload] mcp presets:', e));
  } catch (e) {
    console.error("Init load failed:", e);
  }
}

function populateModelSelect() {
  const sel = $('modelSelect');
  const modalSel = $('settingsDefaultModel');
  if (sel) sel.innerHTML = '';
  if (modalSel) modalSel.innerHTML = '';

  (State.models || []).forEach(g => {
    const optgroup = document.createElement('optgroup');
    optgroup.label = g.provider;

    g.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.label;
      if (m.type) opt.setAttribute('data-type', m.type);
      optgroup.appendChild(opt);
    });

    if (sel) sel.appendChild(optgroup.cloneNode(true));
    if (modalSel) modalSel.appendChild(optgroup.cloneNode(true));
  });
}

function populateProfileSelect() {
  const sel = $('agentProfileSelect');
  const rightSel = $('rightAgentProfileSelect');

  if (sel) {
    sel.innerHTML = '';
    State.profiles.forEach(p => {
      const isAct = p.name === State.activeProfileName ? 'selected' : '';
      const opt = `<option value="${p.name}" ${isAct}>${p.name}${p.is_default ? ' (default)' : ''}</option>`;
      sel.insertAdjacentHTML('beforeend', opt);
    });
  }

  if (rightSel) {
    rightSel.innerHTML = '';
    State.profiles.forEach(p => {
      const isAct = p.name === State.activeProfileName ? 'selected' : '';
      const opt = `<option value="${p.name}" ${isAct}>${p.name}${p.is_default ? ' (default)' : ''}</option>`;
      rightSel.insertAdjacentHTML('beforeend', opt);
    });
  }
}
function renderSessionsList() {
  const list = $('sessionsList');
  list.innerHTML = '';
  State.sessions.forEach(s => {
    const activeClass = s.session_id === State.activeSessionId ? 'active' : '';
    // [세션 동시 작업] 백그라운드에서 실행 중인 세션은 ▶ 배지로 표시해
    // "다른 세션 작업이 돌아가는 중"임을 한눈에 알린다.
    const runningBadge = (State.sessionStreams && State.sessionStreams[s.session_id])
      ? '<span class="session-running-badge" title="이 세션에서 에이전트가 작업 중입니다">▶</span>'
      : '';
    const item = document.createElement('div');
    item.className = `session-item ${activeClass}`;
    item.dataset.sid = s.session_id;
    item.onclick = () => selectSession(s.session_id);

    item.innerHTML = `
      <div class="session-title-container">
        <span class="session-icon">💬</span>
        <span class="session-title" id="title-text-${s.session_id}">${s.title}</span>
        ${runningBadge}
      </div>
      <div class="session-actions">
        <button class="icon-btn edit-sess-btn" onclick="renameSessionPrompt(event, '${s.session_id}', '${s.title}')">✏</button>
        <button class="icon-btn delete-sess-btn" onclick="deleteSession(event, '${s.session_id}')">🗑</button>
      </div>
    `;
    list.appendChild(item);
  });
}

// ── [세션 동시 작업] 유틸 ─────────────────────────────────────────────────────
// 스트림 기록: sendPrompt 성공 경로와 재접속 경로가 함께 사용한다.
function _rememberSessionStream(sid, streamId) {
  if (!sid || !streamId) return;
  State.sessionStreams[sid] = streamId;
}

function _forgetSessionStream(sid, streamId) {
  if (!sid) return;
  // 같은 스트림일 때만 지운다 — 새 메시지가 이미 같은 세션의 기록을
  // 덮어쓴 뒤 늦게 도착한 종료 이벤트가 새 기록을 지우는 것을 방지.
  if (!streamId || State.sessionStreams[sid] === streamId) {
    delete State.sessionStreams[sid];
  }
}

// 세션 복귀 재접속: 백그라운드로 돌린 스트림에 SSE를 다시 붙인다.
// 서버 큐에는 끊긴 동안 발행된 이벤트가 남아 있으므로(15초 heartbeat 유지),
// 재접속 연결이 이후 이벤트와 done을 정상 수신한다. 라이브 중간 이벤트는
// 유실될 수 있지만 done이 전체 결과를 렌더링하므로 최종 상태는 항상 옳다.
async function _reattachSessionStream(sid, streamId) {
  console.log('[SessionStream] 🔌 reattaching to stream', streamId, 'for session', sid);
  setChatStatus('thinking', '작업 진행 중... (백그라운드 작업 재접속)');
  $('sendPromptBtn').disabled = true;
  $('cancelStreamBtn').style.display = 'block';

  const box = $('chatMessages');
  let asstBubble = document.createElement('div');
  asstBubble.className = 'message-bubble assistant';
  asstBubble.innerHTML = '<span class="cursor">|</span>';
  box.appendChild(asstBubble);

  const agentStatusBubble = document.createElement('div');
  agentStatusBubble.className = 'agent-status-bubble thinking';
  agentStatusBubble.textContent = '⏳ 백그라운드 작업 진행 중...';
  box.insertBefore(agentStatusBubble, asstBubble);
  scrollToChatBottom();

  let finished = false;
  function finish(reason) {
    if (finished) return;
    finished = true;
    try { if (agentStatusBubble && agentStatusBubble.parentNode) agentStatusBubble.remove(); } catch (_) { }
    try {
      if (asstBubble) {
        const cur = asstBubble.querySelector('.cursor');
        if (cur) cur.remove();
        const txt = (asstBubble.textContent || '').trim();
        if (!txt && !asstBubble.querySelector('img, video, .text-muted, .text-danger') && asstBubble.parentNode) asstBubble.remove();
      }
    } catch (_) { }
    cleanupStreamState();
    // [세션 동시 작업] 재접속한 스트림이 끝났으면 기록을 지워 ▶ 배지를 정리한다.
    try { _forgetSessionStream(sid, streamId); renderSessionsList(); } catch (_) { }
    console.log('[SessionStream] finished:', reason);
  }

  const sse = new EventSource(`/api/chat/stream?stream_id=${encodeURIComponent(streamId)}`);
  State.currentEventSource = sse;
  State.currentStreamId = streamId;

  sse.addEventListener('token', (e) => {
    try {
      const d = JSON.parse(e.data);
      setChatStatus('thinking', '✍️ 최종 답변 생성 중...');
      if (agentStatusBubble.parentNode) agentStatusBubble.style.display = 'none';
      asstBubble._txt = (asstBubble._txt || '') + (d.text || '');
      asstBubble.innerHTML = renderMd(asstBubble._txt);
      scrollToChatBottom();
    } catch (_) { }
  });

  sse.addEventListener('reasoning', () => {
    setChatStatus('thinking', '💭 생각 중... (백그라운드 작업)');
    scrollToChatBottom();
  });

  sse.addEventListener('tool', () => {
    setChatStatus('thinking', '🔧 도구 실행 중... (백그라운드 작업)');
    scrollToChatBottom();
  });

  sse.addEventListener('heartbeat', () => { /* keep-alive */ });

  sse.addEventListener('approval', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data && data.status === 'pending') {
        setChatStatus('thinking', data.type === 'dangerous_command'
          ? '⚠️ 위험 명령 승인 대기 중...'
          : '🛡️ 승인 대기 중...');
        if (typeof _showApprovalBanner === 'function') _showApprovalBanner(data);
      }
    } catch (_) { }
  });

  sse.addEventListener('notice', (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d && d.message && asstBubble.parentNode) {
        const note = document.createElement('div');
        note.className = 'text-muted';
        note.style.cssText = 'margin-top:8px;font-size:12px;';
        note.textContent = 'ℹ️ ' + d.message;
        box.insertBefore(note, asstBubble);
      }
    } catch (_) { }
  });

  sse.addEventListener('done', (e) => {
    finish('done');
    try {
      const data = JSON.parse(e.data);
      if (data && data.session && data.session.messages) {
        renderMessages(data.session.messages, data.session.tool_calls);
        const localSess = State.sessions.find(x => x.session_id === sid);
        if (localSess) localSess.title = data.session.title;
        renderSessionsList();
        refreshFileTree();
      }
    } catch (err) { console.error('[SessionStream] done handler error:', err); }
  });

  sse.addEventListener('cancel', () => finish('cancel'));

  sse.addEventListener('apperror', (e) => {
    finish('apperror');
    let msg = '알 수 없는 오류';
    try { const d = JSON.parse(e.data || '{}'); msg = d.message || msg; } catch (_) { }
    if (asstBubble && asstBubble.parentNode) {
      asstBubble.insertAdjacentHTML('beforeend',
        `<div class="text-danger" style="margin-top:8px;">[오류: ${msg}]</div>`);
    }
  });
  // API 호출 실패(404/503 등) 중계 — 에이전트 내부 재시도 루프가 계속
  // 진행 중이므로 스트림을 끊지 않고(finish 금지) 경고만 표시한다.
  sse.addEventListener('apierror', (e) => {
    let msg = '알 수 없는 오류';
    try { const d = JSON.parse(e.data || '{}'); msg = d.message || msg; } catch (_) { }
    if (asstBubble && asstBubble.parentNode) {
      asstBubble.insertAdjacentHTML('beforeend',
        `<div style="margin-top:8px;font-size:12px;color:#e67e22;">⚠️ [API 오류] ${msg} — 재시도 중...</div>`);
    }
  });

  sse.addEventListener('error', () => {
    // EventSource 자동 재연결에 맡긴다. 서버 큐가 살아있는 동안에는
    // 재연결이 성공하고, 완전히 사라졌으면 아래 복구가 마무리한다.
    if (sse.readyState === EventSource.CLOSED && !finished) {
      // 스트림이 이미 끝났는데(404) done 캐시를 놓친 경우 — 세션을 다시
      // 불러 최종 결과를 복구한다.
      api(`/api/session?session_id=${encodeURIComponent(sid)}`).then((res) => {
        const sess = res && res.session;
        if (sess && sess.messages && sess.messages.length) {
          const lastMsg = sess.messages[sess.messages.length - 1];
          if (lastMsg && lastMsg.role === 'assistant') {
            renderMessages(sess.messages, sess.tool_calls);
            const localSess = State.sessions.find(x => x.session_id === sid);
            if (localSess) localSess.title = sess.title;
            renderSessionsList();
          }
        }
        finish('sse_closed_recovered');
      }).catch(() => finish('sse_closed'));
    }
  });
}

// ── Re-entrancy guard for selectSession ──
let _selectSessionLock = false;

async function selectSession(sid) {
  if (!sid) return;
  // Prevent concurrent selectSession calls from cascading
  if (_selectSessionLock) {
    // Bugfix #2: show user feedback instead of silently ignoring
    console.warn('[selectSession] Already switching session, ignoring duplicate call for:', sid);
    showToast('세션 전환 중입니다. 잠시만 기다려주세요.', 1500);
    return;
  }
  if (State.activeSessionId === sid) return;

  _selectSessionLock = true;
  const previousSessionId = State.activeSessionId;

  // Bugfix #2: show loading indicator so the user knows work is happening
  setChatStatus('thinking', '세션 로드 중...');

  try {
    // ── Phase 1: Fetch new session data FIRST (before any state changes) ──
    const res = await api(`/api/session?session_id=${encodeURIComponent(sid)}`);
    const session = res.session;

    // ── Phase 2: Only after API success, commit state changes ──
    State.activeSessionId = sid;

    // [세션 동시 작업] 현재 세션에 실행 중 스트림이 있으면 백그라운드로 넘긴다.
    // SSE 연결과 감시 타이머만 정리하고 백엔드 작업은 계속 진행된다.
    // (_suspendActiveStream은 sendPrompt의 finishStream 경로에서 설정됨)
    if (typeof State._suspendActiveStream === 'function') {
      try { State._suspendActiveStream(); } catch (_) { }
      State._suspendActiveStream = null;
    } else {
      // 실행 중 스트림이 없는 일반 전환 — 기존대로 SSE만 끊는다.
      cleanupStreamState();
    }

    // Render active selection in session list
    renderSessionsList();

    State.activeWorkspacePath = session.workspace;
    State.activeModelId = session.model;
    if ($('modelSelect')) $('modelSelect').value = State.activeModelId;

    // Load session mode
    loadSessionMode();

    // [H] 세션 전환 시 실행 방식(approvalMode) 토글 UI를 해당 세션 플래그에 동기화
    if (typeof syncApprovalModeUI === 'function') { try { syncApprovalModeUI(); } catch (_) { } }

    // Clear tabs & reload file tree
    State.openTabs = [];
    State.activeTabIndex = -1;
    State.expandedDirs.clear();
    renderTabs();
    showCanvas('welcome');

    await refreshFileTree();
    renderMessages(session.messages, session.tool_calls);

    // [세션 동시 작업] 복귀한 세션에 실행 중 스트림이 있으면 SSE 재접속해
    // 실제 진행 상태를 표시한다. 백엔드는 계속 돌고 있었으므로 이후 이벤트와
    // done을 그대로 수신한다. 없으면 기존대로 '대기 중'.
    const resumedStreamId = State.sessionStreams[sid];
    if (resumedStreamId) {
      let stillActive = false;
      try {
        const st = await api(`/api/chat/stream/status?stream_id=${encodeURIComponent(resumedStreamId)}`);
        stillActive = !!(st && st.active);
      } catch (_) { /* 조회 실패 시 비활성으로 간주 */ }
      if (stillActive) {
        await _reattachSessionStream(sid, resumedStreamId);
        return; // finally에서 _selectSessionLock 해제됨
      }
      // 이미 끝난 스트림 — 기록 정리 후 최종 결과가 반영된 세션 데이터로 렌더링
      _forgetSessionStream(sid, resumedStreamId);
      try {
        const fresh = await api(`/api/session?session_id=${encodeURIComponent(sid)}`);
        if (fresh && fresh.session && fresh.session.messages) {
          renderMessages(fresh.session.messages, fresh.session.tool_calls);
          const localSess = State.sessions.find(x => x.session_id === sid);
          if (localSess && fresh.session.title) localSess.title = fresh.session.title;
        }
      } catch (_) { }
      renderSessionsList();
    }
    setChatStatus('idle', '대기 중');
  } catch (e) {
    console.error("Session load failed:", e);
    // ── CRITICAL FIX: Do NOT recursively cascade to other sessions.
    // If a session fails to load, revert to the previous valid state
    // instead of triggering a domino effect of recursive failures.
    showToast("세션 로드 실패: " + (e.message || '알 수 없는 오류'));
    setChatStatus('idle', '대기 중');

    // Revert activeSessionId to previous state (or clear if it was set prematurely)
    if (State.activeSessionId === sid) {
      State.activeSessionId = previousSessionId;
    }

    // Re-render the session list to reflect the current (unchanged) active session
    renderSessionsList();

    // If there was no previous session (edge case), try to find any valid one
    if (!State.activeSessionId && State.sessions.length > 0) {
      // Only try the first remaining session; if it also fails, stop (no recursion)
      State.activeSessionId = null; // reset to force selectSession to proceed
      _selectSessionLock = false;   // release lock before the single retry
      await selectSession(State.sessions[0].session_id);
      return; // lock already released and re-acquired by the recursive call
    }
  } finally {
    _selectSessionLock = false;
  }
}

async function createNewSession() {
  try {
    const res = await api('/api/session/new', {
      method: 'POST',
      body: { workspace: State.activeWorkspacePath, model: State.activeModelId }
    });
    State.sessions.unshift(res.session);
    await selectSession(res.session.session_id);
  } catch (e) {
    console.error("New session failed:", e);
  }
}

async function renameSessionPrompt(e, sid, oldTitle) {
  e.stopPropagation();
  const newTitle = await showInputModal("대화 제목 변경", "새 제목을 입력하세요", oldTitle);
  if (!newTitle || newTitle.trim() === '') return;

  try {
    await api('/api/session/rename', {
      method: 'POST',
      body: { session_id: sid, title: newTitle.trim() }
    });
    const s = State.sessions.find(x => x.session_id === sid);
    if (s) s.title = newTitle.trim();
    renderSessionsList();
  } catch (e) {
    showToast("이름 변경 실패: " + e.message);
  }
}

async function deleteSession(e, sid) {
  e.stopPropagation();
  if (!(await showConfirmModal("이 세션을 정말 삭제하시겠습니까?", "세션 삭제"))) return;

  // Cancel any active agent stream before deleting to prevent
  // backend thread crashes trying to save to a deleted session.
  // Fire-and-forget — do NOT await, so the delete proceeds immediately
  // even if the cancel API is slow.
  if (State.activeSessionId === sid && State.currentStreamId) {
    const streamToCancel = State.currentStreamId;
    // Close EventSource immediately (client-side), then tell server async
    if (State.currentEventSource) {
      State.currentEventSource.close();
      State.currentEventSource = null;
    }
    State.currentStreamId = null;
    api('/api/chat/cancel', {
      method: 'POST',
      body: { stream_id: streamToCancel }
    }).catch(() => { /* fire-and-forget */ });
    // Reset UI state that cleanupStreamState would normally handle
    setChatStatus('idle', '대기 중');
    $('sendPromptBtn').disabled = false;
    $('cancelStreamBtn').style.display = 'none';
  }

  try {
    await api('/api/session/delete', {
      method: 'POST',
      body: { session_id: sid }
    });
    State.sessions = State.sessions.filter(s => s.session_id !== sid);

    if (State.activeSessionId === sid) {
      State.activeSessionId = null;
      if (State.sessions.length > 0) {
        await selectSession(State.sessions[0].session_id);
      } else {
        await createNewSession();
      }
    } else {
      renderSessionsList();
    }
  } catch (e) {
    showToast("삭제 실패: " + e.message);
  }
}

async function clearChatHistory() {
  if (!State.activeSessionId) return;
  if (!(await showConfirmModal("이 세션의 모든 메시지 기록을 지우시겠습니까?", "기록 삭제"))) return;

  try {
    await api('/api/session/clear', {
      method: 'POST',
      body: { session_id: State.activeSessionId }
    });
    renderMessages([], []);
  } catch (e) {
    showToast("기록 삭제 실패: " + e.message);
  }
}
// ── 내부 제어 nudging 메시지 판별 ──
// 에이전트가 도구 호출 없이 멈췄을 때 백엔드(hermes-agent)가 role:user로 주입하는
// 루프 제어 프롬프트와 컨텍스트 압축 요약은 사용자에게 보여선 안 되므로
// 렌더링에서 제외한다. 압축 요약은 세션 히스토리에 role:user 메시지로
// 저장되어 done/복구 렌더링 시 챗창에 그대로 노출될 수 있다.
function _isInternalNudgeMessage(content) {
  if (!content) return false;
  const sigs = ['[System: Continue now', '단문 확인 메시지만', '[시스템 안내:', '[CONTEXT COMPACTION'];
  return sigs.some(s => content.includes(s));
}

// ── Chat Engine (SSE integration) ──
//  태그 제거 — 백엔드는 trajectory 보존을 위해 메시지에 think 블록을
// 원문 그대로 저장하므로, 렌더링 시점에서도 제거해야 챗창에 노출되지 않는다.
function stripThinkBlocks(text) {
  if (!text || typeof text !== 'string') return text || '';
  return text.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '').trim();
}

function renderMessages(messages, toolCalls) {
  const box = $('chatMessages');
  // ── 진행 중 승인/선택 카드 보존 ──
  // innerHTML 초기화로 pending 승인 카드가 사라지면 승인 버튼이 영구 유실된다
  // ("승인/거절 버튼이 안 먹히는" 문제의 주원인). 렌더 후 다시 붙인다.
  const preservedCards = [];
  try {
    const approvalCard = document.getElementById('inlineApprovalCard');
    if (approvalCard && approvalCard.parentNode === box) {
      approvalCard.remove();
      // 현재 세션의 카드만 보존 (세션 전환 시 이전 세션 카드는 제거)
      if (approvalCard.getAttribute('data-session-id') === State.activeSessionId) {
        preservedCards.push(approvalCard);
      }
    }
    // 미응답 선택 카드(ask_followup_question)는 스트림 진행 중 렌더링에서만 보존
    if (State.currentStreamId) {
      box.querySelectorAll('.inline-choice-card').forEach((c) => {
        if (!c.querySelector('.inline-choice-selected')) { c.remove(); preservedCards.push(c); }
      });
    }
  } catch (_) { }
  box.innerHTML = '';

  messages.forEach((msg, idx) => {
    if (!msg || !msg.role || msg.role === 'tool') return;
    // 내부 제어 nudging 메시지(role:user 주입)는 채팅에 노출하지 않음
    if (msg.role === 'user' && _isInternalNudgeMessage(msg.content)) return;
    const isUser = msg.role === 'user';
    // ── 빈 assistant 버블 스킵 (얇은 빈 줄 아티팩트 방지) ──
    // 도구 전용 턴은 content가 비어 있거나 think 블록뿐인 assistant 메시지를 남긴다.
    // 이걸 버블로 렌더링하면 테두리만 있는 얇은 빈 줄로 보인다.
    let _toolOnlyTurn = false;
    if (!isUser) {
      const plain = stripThinkBlocks(msg.content);
      const msgToolsPre = toolCalls ? toolCalls.filter(tc => tc.assistant_msg_idx === idx) : [];
      if (!plain.trim() && msgToolsPre.length === 0) return;
      // 도구 전용 턴(텍스트 없이 도구 호출만): 빈 텍스트 버블 대신
      // 도구 카드만 가진 작업 단위 블록으로 렌더링 (Roo Code 스타일 분리).
      // 배경/테두리를 제거해 얇은 선 잔상처럼 보이지 않게 한다.
      _toolOnlyTurn = true;
    }
    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${isUser ? 'user' : 'assistant'}`;
    if (_toolOnlyTurn) bubble.classList.add('tool-only-turn');

    // Style judge message differently
    if (msg.sender && msg.sender.includes('판사')) {
      bubble.style.border = '2px solid var(--accent)';
      bubble.style.background = 'rgba(233, 69, 96, 0.05)';
      bubble.style.maxWidth = '95%';
    }

    let html = isUser ? formatUserMessageContent(msg.content, State.activeSessionId) : renderMd(stripThinkBlocks(msg.content));
    if (msg.sender) {
      const senderHtml = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${msg.sender}</div>`;
      html = senderHtml + html;
    }
    bubble.innerHTML = html;

    // Find tool calls matching this assistant message — 그룹으로 묶어 표시
    if (!isUser && toolCalls) {
      const msgTools = toolCalls.filter(tc => tc.assistant_msg_idx === idx);
      if (msgTools.length > 0) {
        const groupCard = document.createElement('details');
        groupCard.className = 'tool-group-card';
        const totalCount = msgTools.length;
        groupCard.innerHTML = `
          <summary>
            <span class="tool-group-icon">🔧</span>
            <span class="tool-group-label">도구 실행 완료</span>
            <span class="tool-group-counter">${totalCount}</span>
            <span class="tool-group-chevron">▶</span>
          </summary>
          <div class="tool-group-items"></div>
        `;
        const itemsContainer = groupCard.querySelector('.tool-group-items');
        msgTools.forEach(tool => {
          const item = document.createElement('div');
          item.className = 'tool-group-item';
          item.style.cursor = 'pointer';
          item.innerHTML = `
            <span class="tgi-icon">✅</span>
            <span class="tgi-name">${tool.name}</span>
          `;
          // 클릭 시 상세 보기 토글
          const detailDiv = document.createElement('div');
          detailDiv.className = 'tool-card-body';
          detailDiv.style.display = 'none';
          detailDiv.innerHTML = `
            <div>Arguments:</div>
            <pre style="margin-bottom:8px;">${JSON.stringify(tool.args, null, 2)}</pre>
            <div>Output Snippet:</div>
            <pre>${tool.snippet}</pre>
          `;
          item.addEventListener('click', function () {
            detailDiv.style.display = detailDiv.style.display === 'none' ? 'block' : 'none';
          });
          item.appendChild(detailDiv);
          itemsContainer.appendChild(item);
        });
        bubble.appendChild(groupCard);
      }
    }

    box.appendChild(bubble);
  });
  // 보존한 카드(승인/선택)를 맨 아래에 복원 — 재렌더링 후에도 승인 버튼 유지
  preservedCards.forEach((c) => box.appendChild(c));
  scrollToChatBottom();
}

function toggleToolCard(headerEl) {
  const body = headerEl.nextElementSibling;
  const icon = headerEl.children[1];
  if (body.style.display === 'none') {
    body.style.display = 'block';
    icon.textContent = '▼';
  } else {
    body.style.display = 'none';
    icon.textContent = '▶';
  }
}

function scrollToChatBottom() {
  const chatBox = $('chatMessages');
  const debateBox = $('debateMessages');

  setTimeout(() => {
    if (chatBox && chatBox.style.display !== 'none') {
      chatBox.scrollTop = chatBox.scrollHeight;
    }
    if (debateBox && debateBox.style.display !== 'none') {
      debateBox.scrollTop = debateBox.scrollHeight;
    }
  }, 30);
}
async function sendPrompt() {
  // Bugfix #1: prevent duplicate sends while stream is already active
  if ($('sendPromptBtn').disabled) return;

  const input = $('promptInput');
  const text = input.value.trim();

  // 1. Upload attachments first if any
  let uploaded = [];
  try {
    if (State.pendingFiles && State.pendingFiles.length > 0) {
      setChatStatus('thinking', '파일 업로드 중...');
      uploaded = await uploadPendingFiles();
    }
  } catch (err) {
    showToast("파일 업로드 실패: " + err.message);
    setChatStatus('idle', '대기 중');
    return;
  }

  if (!text && uploaded.length === 0) return;
  if (!State.activeSessionId) return;

  // ── 환영/마법사 카드 제거: 첫 유효 전송 시 일회성으로 치움 (일반 대화는 영향 없음) ──
  if (typeof window._dismissBeginnerWelcome === 'function') {
    try { window._dismissBeginnerWelcome(); } catch (_) { }
  }

  // Clear input
  input.value = '';
  input.style.height = 'auto';

  // Add temporary user message to UI
  const box = $('chatMessages');
  const userBubble = document.createElement('div');
  userBubble.className = 'message-bubble user';

  let displayText = text;
  if (uploaded.length > 0) {
    if (!displayText) {
      displayText = `${uploaded.length}개 파일 업로드됨: ${uploaded.join(', ')}`;
    } else {
      displayText = `${text}\n\n[첨부 파일: ${uploaded.join(', ')}]`;
    }
  }
  userBubble.innerHTML = formatUserMessageContent(displayText, State.activeSessionId);
  box.appendChild(userBubble);
  scrollToChatBottom();

  // ── Auto mode switching ──
  // Before executing, let the mode system auto-switch based on intent detection.
  // Manual clicks on suggestion buttons always take priority (handled inside
  // applyAutoModeForSend via _pendingSuggestedMode). Low-confidence / errors keep
  // the current mode and never block the send.
  if (typeof applyAutoModeForSend === 'function') {
    try { await applyAutoModeForSend(displayText); } catch (_) { }
  }

  // ── [H] 자율 실행 키워드 감지: "이번 작업은 끝까지 알아서 해" 류의 요청이면 ──
  // 이 세션의 실행 방식을 autonomous 로 전환한다. (승인 요청 발생 시 자동 once 응답)
  if (typeof setApprovalMode === 'function' && displayText) {
    try {
      if (/알아서|끝까지|혼자서?|골\s*명령어|자율\s*실행|승인\s*없이|확인\s*없이|그냥\s*다\s*해|알아서\s*해/.test(displayText)) {
        setApprovalMode('autonomous');
      }
    } catch (_) { }
  }

  // Execute agent stream with the (possibly auto-switched) active mode.
  await _executeAgentStream(displayText, uploaded);
}

/**
 * Extracted SSE streaming logic — called after mode cards or directly.
 */
async function _executeAgentStream(displayText, uploaded) {
  // Bugfix #1: prevent duplicate streams. If a stream is already active,
  // ignore the new send entirely. This covers the case where Enter key
  // fires sendPrompt() while a stream is still in progress (button
  // disabled check in sendPrompt is not sufficient — e.g. race between
  // keydown and button state).
  if (State.currentStreamId) {
    console.warn('[chat] Stream already active (id=%s), ignoring new send', State.currentStreamId);
    return;
  }

  // Set UI state to active
  State._userCancelledStream = false;
  // "thinking" is the broad transport state; use explicit wording here so
  // it is not confused with the separate 💭 reasoning card below.
  setChatStatus('thinking', '에이전트 작업 시작 중...');
  $('sendPromptBtn').disabled = true;
  $('cancelStreamBtn').style.display = 'block';
  // [I] 취소 버튼이 높이를 차지해 chatMessages 가시 영역이 줄어든다.
  // 방금 보낸 메시지의 마지막 줄이 버튼 뒤로 숨지 않도록 즉시 재스크롤.
  scrollToChatBottom();

  // Create stream target assistant bubble
  const box = $('chatMessages');
  // 작업 단위 분리를 위해 스트림 중 새 버블로 교체 가능해야 하므로 let으로 선언
  let asstBubble = document.createElement('div');
  asstBubble.className = 'message-bubble assistant';
  asstBubble.innerHTML = '<span class="cursor">|</span>';
  box.appendChild(asstBubble);

  // Keep the agent's transient state visible in the chat stream as well as in
  // the compact header status.  The legacy DAON shell does not have Roo's
  // separate status message component, so this small independent element is
  // the equivalent without replacing the existing UI.
  const agentStatusBubble = document.createElement('div');
  agentStatusBubble.className = 'agent-status-bubble thinking';
  box.insertBefore(agentStatusBubble, asstBubble);
  scrollToChatBottom();

  // ── 경과 시간 카운터 (대표님 요청 2026-08-24) ──
  // 상태 말풍선(⏳ 시작 중 / 💭 생각 중 / 🔧 도구 실행 중 / ✍️ 최종 답변 생성 중)
  // 뒤에 흐르는 초를 실시간 표시해 "서버가 죽었는지 계속 작업 중인지" 즉시
  // 구별할 수 있게 한다. finishStream()이 유일한 종료 진입점이므로 여기서 정리.
  const _statusStartTs = Date.now();
  let _statusTimer = null;
  let _statusBaseText = '';

  function _statusElapsed() {
    return Math.max(0, Math.floor((Date.now() - _statusStartTs) / 1000));
  }

  function _renderStatusText() {
    if (!agentStatusBubble) return;
    agentStatusBubble.textContent = _statusBaseText
      ? `${_statusBaseText} (${_statusElapsed()}초)`
      : '';
  }

  _statusBaseText = '⏳ 에이전트 작업 시작 중...';
  _renderStatusText();
  // 1초 간격으로 말풍선의 경과 초를 갱신한다.
  _statusTimer = setInterval(function () {
    if (!agentStatusBubble || !agentStatusBubble.parentNode) return;
    _renderStatusText();
  }, 1000);

  function setStreamStatus(status, text) {
    setChatStatus(status, text);
    if (agentStatusBubble && agentStatusBubble.parentNode) {
      agentStatusBubble.className = `agent-status-bubble ${status}`;
      _statusBaseText = text || '';
      _renderStatusText();
      agentStatusBubble.style.display = text ? '' : 'none';
    }
  }

  let incomingText = '';

  // ── finishStream: 단일 진입점 — 모든 스트림 종료 경로를 이곳으로 통합 ──
  // dedup guard: 두 번 이상 호출되더라도 cleanupStreamState()는 한 번만 실행
  let _streamFinished = false;
  let _idleTimer = null;
  let _startWatchdog = null;
  // 실행 중인 도구 수. 도구가 돌아가는 동안에는 idle timer를 완화한다
  // (완전 중단이 아님 — tool.completed 이벤트 유실 상황 대비).
  let _activeTools = 0;
  // 도구 실행 억제 시작 시점 (비정상적으로 긴 억제 상한 처리용)
  let _toolSuppressStart = 0;

  // 연속 idle 연장 횟수. 백엔드 스트림이 아직 활성(추론 단계/긴 도구 실행)이면
  // 타이머를 재가동해 계속 대기한다. 무한 대기를 막기 위해 상한을 둔다
  // (30초 × 40회 = 최대 20분).
  let _idleExtensions = 0;
  let _idleRecoveryInFlight = false;
  // idle 워치독의 백엔드 상태 조회가 연속 실패한 횟수. 상태 조회 실패는
  // 백엔드 종료를 의미하지 않으므로(plan.md Cause B) 몇 번은 연장해서 더
  // 기다리고, 반복 실패 시에만 복구 경로로 진행한다.
  let _statusCheckFailures = 0;
  // 백엔드는 활성인데 EventSource 연결이 끊겼을 때 같은 stream_id로
  // 재연결을 시도한 횟수 (최대 3회, plan.md Phase 2).
  let _sseReconnects = 0;

  // ── 블록 스코프 주의 (중요) ──
  // finishStream() / _handleIdleTimeout() 등 종료 경로 함수들은 아래 try 블록
  // "바깥"에 선언되어 있다. try 블록 안에서 let/const로 선언된 변수는 이
  // 함수들에서 보이지 않고, 읽는 순간 ReferenceError가 발생해 finishStream()이
  // 중도에 사망 → cleanupStreamState()가 실행되지 않아 챗창이 영구 잠기는
  // 문제(전송 버튼 비활성, 상태 표시 멈춤)의 원인이었다.
  // 따라서 종료 경로 함수가 참조하는 모든 상태 변수는 이곳 함수 최상위에서
  // 선언하고 try 블록 안에서는 "할당"만 한다.
  let streamId = null;          // SSE stream ID (try 안에서 할당)
  let sse = null;               // EventSource 인스턴스 (try 안에서 할당)
  // [세션 동시 작업] 이 스트림이 속한 세션(전송 시점의 활성 세션).
  // 세션 이동 후 늦게 도착하는 비동기 복구 콜백이 다른 세션 화면에
  // 결과를 덮어쓰지 않도록 소유권 확인에 사용한다.
  const ownerSid = State.activeSessionId;
  let _reasoningCard = null;
  let _reasoningText = '';
  let _reasoningStartTs = 0;
  let _reasoningTimer = null;
  let _toolGroupCard = null;      // <details> 컨테이너 요소
  let _toolGroupItems = null;     // 도구 항목 리스트 컨테이너
  let _toolGroupCount = 0;        // 총 도구 이벤트 수 (started 기준)
  let _toolGroupDoneCount = 0;    // 완료된 도구 수
  let _toolItemMap = {};          // tool_call_id -> 항목 DOM 요소 매핑
  // 터미널 실시간 출력 카드. finishStream()이 참조하므로 함수 최상위에 선언
  // (try 블록 안에 두면 종료 경로에서 ReferenceError → 정리 생략 → 카드가
  //  빈 줄로 남는 문제의 원인이 된다).
  let _terminalOutputCard = null;
  let _terminalOutputText = '';
  // 승인(위험 명령/Architect 변경) 대기 여부. 승인 대기 중에는 이벤트가
  // 오지 않아도 idle 워치독이 스트림을 종료하면 안 된다 — 백엔드는 사용자
  // 승인 응답을 기다리며 블로킹 중이기 때문이다 (최대 5분).
  let _approvalPending = false;

  function _reasoningElapsed() {
    return Math.max(0, Math.floor((Date.now() - _reasoningStartTs) / 1000));
  }

  function _stopReasoningTimer(finalLabel) {
    if (_reasoningTimer) {
      clearInterval(_reasoningTimer);
      _reasoningTimer = null;
    }
    if (_reasoningCard) {
      const sum = _reasoningCard.querySelector('summary');
      if (sum) sum.textContent = finalLabel || ('💭 생각 완료 (' + _reasoningElapsed() + '초) (클릭하여 보기)');
    }
  }

  // ── Roo Code 스타일 작업 단위 분리 (대표님 요청 2026-08-24) ──
  // 지금까지 스트리밍된 답변을 "완료된 블록"으로 확정하고 새 빈 버블을 만들어
  // 다음 단위(추론/도구 이후 답변)를 받는다. 결과적으로 스트림이
  // [💭 생각 카드][답변 블록 1][🔧 도구 카드][답변 블록 2] 처럼 작업 하나마다
  // 끊어져 표시되고, 빈 블록(얇은 선 잔상)이 남지 않는다.
  let _answerSegments = [];
  function _freezeAnswerSegment() {
    try {
      if (!asstBubble || !asstBubble.parentNode) return;
      const segText = (incomingText || '').trim();
      if (!segText) return; // 빈 세그먼트는 확정하지 않는다 — 잔상 방지 핵심
      const cur = asstBubble.querySelector('.cursor');
      if (cur) cur.remove();
      asstBubble.classList.add('answer-segment');
      _answerSegments.push(asstBubble);
      incomingText = ''; // 다음 단위부터 새로 누적 (중복 방지)
      const nb = document.createElement('div');
      nb.className = 'message-bubble assistant';
      nb.innerHTML = '<span class="cursor">|</span>';
      // 기존 버블 위치 "뒤"에 새 버블을 붙여 스트림 순서를 유지한다
      box.insertBefore(nb, asstBubble.nextSibling);
      // 살아있는 상태 표시 말풍선은 항상 현재(마지막) 활성 블록 바로 위에 위치
      if (agentStatusBubble && agentStatusBubble.parentNode) {
        box.insertBefore(agentStatusBubble, nb);
      }
      asstBubble = nb;
    } catch (_) { }
  }

  function _updateToolGroupHeader() {
    if (!_toolGroupCard) return;
    const label = _toolGroupCard.querySelector('.tool-group-label');
    const counter = _toolGroupCard.querySelector('.tool-group-counter');
    const spinner = _toolGroupCard.querySelector('.tool-group-spinner');
    const running = _toolGroupCount - _toolGroupDoneCount;
    if (label) {
      label.textContent = running > 0
        ? `도구 실행 중... (${_toolGroupDoneCount}/${_toolGroupCount} 완료)`
        : `도구 실행 완료`;
    }
    if (counter) counter.textContent = _toolGroupCount;
    if (spinner) spinner.style.display = running > 0 ? '' : 'none';
  }

  function resetIdleTimer() {
    clearTimeout(_idleTimer);
    // 도구 실행 중에도 무응답 감시를 "완전 중단"하지 않고 30초 워치독을
    // 유지한다. 이전처럼 중단하면 tool.completed 이벤트가 유실됐을 때
    // 워치독이 영원히 꺼져 챗창이 영구 잠긴다. _handleIdleTimeout()이 백엔드
    // 상태를 확인하므로 정상적인 긴 도구 실행은 조기 종료되지 않는다.
    if (_activeTools > 0) {
      if (!_toolSuppressStart) _toolSuppressStart = Date.now();
      // 비정상적으로 긴 억제(5분 초과): 카운트 손상(이벤트 유실)으로 간주.
      if (Date.now() - _toolSuppressStart > 300000) {
        console.warn('[SSE-DIAG] tool-active suppression exceeded 5min — resetting _activeTools');
        _activeTools = 0;
        _toolSuppressStart = 0;
      }
    }
    if (_activeTools === 0) _toolSuppressStart = 0;
    // 30초: 도구 완료 → LLM 재호출 → 첫 토큰(TTFT) 대기 시간을 커버.
    // 이전 2초는 LLM API 첫 토큰이 3~15초 걸리는 경우 스트림을 조기 종료시켰음.
    _idleTimer = setTimeout(function () {
      if (_streamFinished) return;
      _handleIdleTimeout();
    }, 30000);
  }

  // ── idle 타임아웃 처리: 강제 종료 전 백엔드 상태 확인 ──
  // 추론(reasoning) 단계에서는 텍스트 토큰이 오지 않아 30초 무응답이 정상일
  // 수 있다. 백엔드 스트림이 아직 활성이면 타이머를 재가동해 계속 기다리고,
  // 이미 종료됐다면(done 이벤트 누락) 세션을 fetch해 결과를 복구 렌더링한다.
  // 이것이 "도구 사용 후 결과 보고가 안 보이는" 문제의 주 복구 경로다.
  async function _handleIdleTimeout() {
    if (_streamFinished || _idleRecoveryInFlight) return;
    _idleRecoveryInFlight = true;
    try {
      // 0) 승인 대기 중에는 이벤트가 없어도 종료하지 않는다.
      // 백엔드가 사용자 승인 응답을 기다리며 블로킹 중(최대 5분)이다.
      // 승인 카드 자체가 타이머를 리셋하지만, 카드 유실 시의 안전장치.
      if (_approvalPending && _idleExtensions < 40) {
        _idleExtensions++;
        console.log('[SSE-DIAG] ⏳ idle timeout but approval pending — extending wait (%d/40)', _idleExtensions);
        setChatStatus('thinking', '승인 대기 중... (승인 여부를 선택해주세요)');
        _idleRecoveryInFlight = false;
        resetIdleTimer();
        return;
      }

      // 1) 백엔드 스트림 활성 여부 확인
      let active = false;
      let statusCheckOk = true;
      try {
        const st = await api(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`);
        active = !!(st && st.active);
      } catch (_) {
        // 상태 조회 실패 ≠ 백엔드 종료 (plan.md Cause B). 일시적인 네트워크
        // 글리치에 곧바로 사망 선언하지 않고 몇 번은 연장해서 더 기다린다.
        statusCheckOk = false;
      }

      if (!statusCheckOk && _idleExtensions < 40 && _statusCheckFailures < 3) {
        _statusCheckFailures++;
        _idleExtensions++;
        console.warn('[SSE-DIAG] ⏳ status check failed (%d/3) — extending wait instead of declaring dead', _statusCheckFailures);
        setChatStatus('thinking', '응답 대기 중... (연결 상태 확인)');
        _idleRecoveryInFlight = false;
        resetIdleTimer();
        return;
      }

      // 1-1) 백엔드는 활성인데 EventSource가 끊긴 경우 — 같은 stream_id로
      // 재연결 (최대 3회). 끊겨 있던 동안 발행된 이벤트는 서버 큐에 남아
      // 있으므로 재연결한 연결이 done 이벤트를 정상 수신할 수 있다.
      // 재연결된 소스에는 최소한의 터미널 이벤트 리스너만 부착한다
      // (라이브 토큰은 유실되지만 done이 전체 결과를 렌더링한다).
      if (active && sse && sse.readyState === EventSource.CLOSED && _sseReconnects < 3) {
        _sseReconnects++;
        console.warn('[SSE-DIAG] 🔌 SSE closed while backend active — reconnecting (%d/3)', _sseReconnects);
        try {
          const reconnected = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
          reconnected.addEventListener('done', (ev) => {
            try {
              const d = JSON.parse(ev.data);
              finishStream('done_reconnected');
              if (d && d.session && d.session.messages) {
                renderMessages(d.session.messages, d.session.tool_calls);
                const localSess = State.sessions.find(function (x) { return x.session_id === State.activeSessionId; });
                if (localSess) localSess.title = d.session.title;
                renderSessionsList();
                refreshFileTree();
              }
            } catch (innerErr) {
              console.error('[SSE] reconnect done handler error:', innerErr);
              finishStream('done_reconnected');
            }
          });
          reconnected.addEventListener('cancel', () => finishStream('cancel_reconnected'));
          reconnected.addEventListener('apperror', () => finishStream('apperror_reconnected'));
          reconnected.addEventListener('heartbeat', () => { _idleExtensions = 0; resetIdleTimer(); });
          sse = reconnected;
          State.currentEventSource = reconnected;
          _idleExtensions = 0;
          _idleRecoveryInFlight = false;
          resetIdleTimer();
          return;
        } catch (reErr) {
          console.error('[SSE-DIAG] SSE reconnect failed:', reErr);
        }
      }

      if (active && _idleExtensions < 40) {
        // 백엔드가 아직 실행 중(추론/긴 작업) — 계속 대기
        _idleExtensions++;
        console.log('[SSE-DIAG] ⏳ idle timeout but backend active — extending wait (%d/40)', _idleExtensions);
        setChatStatus('thinking', '응답 생성 중... (대기)');
        _idleRecoveryInFlight = false;
        resetIdleTimer();
        return;
      }

      // 2) 백엔드 종료(또는 연장 상한 도달) — 세션에서 결과 복구
      console.log('[SSE-DIAG] 🔍 idle timeout — recovering session result');
      let recovered = false;
      try {
        const sessRes = await api('/api/sessions');
        const sessions = sessRes.sessions || [];
        const found = sessions.find(function (s) { return s.session_id === State.activeSessionId; });
        if (found && found.messages && found.messages.length > 0) {
          const lastMsg = found.messages[found.messages.length - 1];
          if (lastMsg.role === 'assistant') {
            renderMessages(found.messages, found.tool_calls);
            const localSess = State.sessions.find(function (x) { return x.session_id === State.activeSessionId; });
            if (localSess) localSess.title = found.title;
            renderSessionsList();
            refreshFileTree();
            recovered = true;
          }
        }
      } catch (recErr) {
        console.error('[SSE] idle-timeout recovery failed:', recErr);
      }
      if (!recovered && asstBubble && asstBubble.parentNode) {
        asstBubble.insertAdjacentHTML('beforeend',
          '<div class="text-muted" style="margin-top:8px;">[응답 대기 시간 초과 — 세션을 다시 열면 결과를 확인할 수 있습니다]</div>');
      }
      finishStream('idle_timeout');
    } finally {
      _idleRecoveryInFlight = false;
    }
  }

  function finishStream(reason) {
    if (_streamFinished) return;
    _streamFinished = true;
    _approvalPending = false;
    clearTimeout(_idleTimer);
    clearTimeout(_startWatchdog);
    // 상태 말풍선의 경과 초 카운터 정지 (대표님 요청 2026-08-24)
    try { if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; } } catch (_) { }
    // "생각 중" 카드의 경과 초 카운터가 돌고 있으면 정지
    try { _stopReasoningTimer(); } catch (_) { }
    console.log('[SSE-DIAG] 🏁 finishStream called, reason=', reason);
    // [세션 동시 작업] 종료 사유별 스트림 기록 정리.
    // 'session_switch'는 세션 이동으로 '감시'만 일시정지한 것이다 — 백엔드
    // 작업은 계속되므로 기록을 남겨 두고, 복귀 시 /api/chat/stream/status로
    // 확인해 재접속한다. 그 외(done/cancel/error/워치독 등)는 실제 종료이므로
    // 기록을 지워 사이드바 ▶ 배지를 제거한다.
    if (reason !== 'session_switch') {
      try { _forgetSessionStream(ownerSid, streamId); renderSessionsList(); } catch (_) { }
    }
    // 어떤 경로로든 이 스트림의 일시정지 훅은 무효화한다.
    // (정상 종료 후 남은 훅이 다음 세션 전환에서 헛돌지 않게 한다)
    try { State._suspendActiveStream = null; } catch (_) { }
    // 도구 그룹 카드가 남아있으면 최종 상태로 갱신.
    // try/catch로 감싸 DOM 이상으로도 아래 cleanupStreamState()가
    // 건너뛰어지지 않게 한다 (과거 ReferenceError로 영구 잠김 발생).
    try {
      if (_toolGroupCard) {
        _updateToolGroupHeader();
        _toolGroupCard = null;
        _toolGroupItems = null;
        _toolGroupCount = 0;
        _toolGroupDoneCount = 0;
        _toolItemMap = {};
      }
    } catch (_) { }
    // ── 잔여 live 요소 정리 (얇은 줄/생각 중 필 아티팩트 방지) ──
    // agentStatusBubble("💭 생각 중..." 등)은 일시 표시용 — 스트림 종료 시 항상 제거.
    try {
      if (agentStatusBubble && agentStatusBubble.parentNode) agentStatusBubble.remove();
    } catch (_) { }
    // 추론 카드 / 도구 그룹 카드 / 터미널 live 카드 등 transient 카드도 DOM에서
    // 제거한다. done 경로는 renderMessages(innerHTML 초기화)가 정리해주지만,
    // cancel/error/sse_closed 경로는 renderMessages가 호출되지 않아 이 카드들이
    // 얇은 빈 줄(또는 접힌 카드 헤더)로 남는 문제가 있었다.
    try {
      if (_reasoningCard && _reasoningCard.parentNode) _reasoningCard.remove();
    } catch (_) { }
    try {
      if (_toolGroupCard && _toolGroupCard.parentNode) _toolGroupCard.remove();
    } catch (_) { }
    try {
      if (_terminalOutputCard && _terminalOutputCard.parentNode) _terminalOutputCard.remove();
    } catch (_) { }
    // 확정 답변 세그먼트 추적 해제. 요소 자체는 DOM에 유지한다 — done 경로는
    // renderMessages가 전체 재렌더링으로 정리하고, cancel 등 비-done 경로에서는
    // 지금까지 받은 부분 답변이 화면에 남아 "중지해도 내용이 보존된다"는 안전감을 준다.
    try { _answerSegments = []; } catch (_) { }
    try {
      if (asstBubble) {
        const _cur = asstBubble.querySelector('.cursor');
        if (_cur) _cur.remove();
        // 뒤에서 피드백 문구를 붙이지 않는 종료 사유라면, 빈 버블을 통째로 제거
        const _noFeedback = (reason === 'done' || reason === 'sse_closed' || reason === 'done_reconnected' || reason === 'cancel_reconnected' || reason === 'apperror_reconnected');
        if (_noFeedback && asstBubble.parentNode) {
          const _txt = (asstBubble.textContent || '').trim();
          const _hasMedia = asstBubble.querySelector('img, video, .tool-group-card, .text-muted, .text-danger');
          if (!_txt && !_hasMedia) asstBubble.remove();
        }
      }
    } catch (_) { }
    try { if (sse) sse.close(); } catch (_) { }
    cleanupStreamState();
  }

  try {
    // ── Collect open editor tabs info for agent context ──
    const openTabs = [];
    if (State.openTabs && State.openTabs.length > 0) {
      for (const tab of State.openTabs) {
        openTabs.push({
          path: tab.path,
          name: tab.name,
          mode: tab.mode,
          active: (State.openTabs.indexOf(tab) === State.activeTabIndex)
        });
      }
    }

    // Initiate chat run in backend with attachments list
    const planningMode = $('planningModeToggle') ? $('planningModeToggle').checked : false;
    // #33 fix: increase timeout to 45s for /api/chat/start. When the system is busy
    // (e.g. browser operations in progress, previous stream cancel cleanup, session
    // save I/O), the backend handler may need more than the default 15s to respond.
    // /api/chat/start normally returns immediately, but if the server is
    // overloaded the request can remain pending before a stream_id exists.
    // In that window the normal cancel button cannot call the backend and the
    // input would stay disabled forever.  Fail closed and restore the UI.
    _startWatchdog = setTimeout(function () {
      if (_streamFinished) return;
      console.warn('[SSE-DIAG] chat/start watchdog expired; restoring input UI');
      finishStream('start_watchdog');
      if (asstBubble && asstBubble.parentNode) {
        asstBubble.insertAdjacentHTML('beforeend',
          '<div class="text-danger" style="margin-top:8px;">[서버 응답이 지연되어 입력을 다시 활성화했습니다]</div>');
      }
      // 백엔드가 busy여서 /api/chat/start가 늦게 응답하고 이미 스트림을
      // 시작한 경우, 늦게 저장된 assistant 응답을 놓치지 않도록 잠시 후
      // 세션 결과를 자동 복구해 렌더링한다. ("서버응답 없음" 재발 방지)
      setTimeout(function () {
        if (!State.activeSessionId) return;
        // [세션 동시 작업] 소유권 가드 — 워치독 복구 대기 중 세션이 바뀌었으면
        // 이전 세션의 메시지를 현재 화면에 덮어쓰지 않는다.
        if (State.activeSessionId !== ownerSid) return;
        api('/api/sessions').then(function (sessRes) {
          var sessions = (sessRes && sessRes.sessions) || [];
          var found = sessions.find(function (s) { return s.session_id === State.activeSessionId; });
          if (found && found.messages && found.messages.length) {
            var lastMsg = found.messages[found.messages.length - 1];
            if (lastMsg && lastMsg.role === 'assistant' && lastMsg.content) {
              renderMessages(found.messages, found.tool_calls);
            }
          }
        }).catch(function () { });
      }, 5000);
    }, 60000);

    const startRes = await api('/api/chat/start', {
      method: 'POST',
      timeout: 45000,
      body: {
        session_id: State.activeSessionId,
        message: displayText,
        model: State.activeModelId,
        workspace: State.activeWorkspacePath,
        attachments: uploaded.length > 0 ? uploaded : undefined,
        planning_mode: planningMode,
        open_tabs: openTabs,
        media_options: buildMediaOptions()
      }
    });

    clearTimeout(_startWatchdog);
    _startWatchdog = null;

    // The watchdog may have released the UI while the HTTP request was still
    // completing.  Do not attach a new SSE stream in that stale request.
    if (_streamFinished) {
      if (startRes && startRes.stream_id) {
        api('/api/chat/cancel', {
          method: 'POST',
          body: { stream_id: startRes.stream_id }
        }).catch(() => { });
      }
      return;
    }

    streamId = startRes.stream_id;
    State.currentStreamId = streamId;

    // [세션 동시 작업] 실행 중 스트림을 세션별로 기록해 ▶ 배지를 표시하고,
    // 사용자가 다른 세션으로 이동했다가 돌아와도 재접속할 수 있게 한다.
    _rememberSessionStream(ownerSid, streamId);
    try { renderSessionsList(); } catch (_) { }
    // 일시정지 훅: selectSession()이 세션 전환 직전에 호출한다.
    // finishStream('session_switch')은 SSE·타이머만 정리하고 백엔드 작업과
    // 위 기록은 유지된다 — "세션 이동 중에도 작업 지속"의 핵심 경로다.
    State._suspendActiveStream = function () { finishStream('session_switch'); };

    // Connect to SSE endpoint
    sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
    State.currentEventSource = sse;
    // Start the no-event watchdog immediately.  Previously it was only
    // started after the first token/tool/reasoning event, so a backend run
    // that produced no SSE event could leave the input locked forever.
    resetIdleTimer();

    // ── 추론(reasoning) 스트림: 별도 접이식 박스 표시 ──
    // 추론 단계에서는 token 이벤트가 오지 않아 idle timer가 스트림을 조기
    // 종료하던 문제가 있었다. reasoning 이벤트가 타이머를 계속 갱신한다.
    // Roo Code 스타일로 경과 초를 함께 표시한다 ("💭 생각 중... (Ns)").
    // ※ _reasoning* 상태 변수와 _reasoningElapsed()/_stopReasoningTimer()는
    //   블록 스코프 수정(finishStream 잠김 방지)을 위해 함수 최상위에서 선언.
    // ── 도구 실행 그룹 카드: 반복 도구 호출을 하나의 접이식 카드로 묶음 ──
    // ※ _toolGroup* 상태 변수와 _updateToolGroupHeader()도 함수 최상위에서 선언.

    sse.addEventListener('reasoning', (e) => {
      try {
        const data = JSON.parse(e.data);
        setStreamStatus('thinking', '💭 생각 중...');
        _reasoningText += data.text || '';
        if (!_reasoningCard) {
          // 새 추론 단위가 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)
          _freezeAnswerSegment();
          _reasoningStartTs = Date.now();
          _reasoningCard = document.createElement('details');
          _reasoningCard.className = 'tool-card reasoning-card';
          _reasoningCard.innerHTML = `
            <summary style="cursor:pointer; padding:6px 10px; opacity:0.75;">💭 생각 중... (0초)</summary>
            <div class="tool-card-body" style="display:block;">
              <pre style="white-space:pre-wrap; max-height:240px; overflow:auto; opacity:0.7; font-size:12px;"></pre>
            </div>
          `;
          // asstBubble 안에 넣으면 token 스트리밍 시 innerHTML 초기화로 사라지므로
          // 버블 앞의 독립 요소로 삽입한다.
          box.insertBefore(_reasoningCard, asstBubble);
          // 경과 초를 1초마다 갱신 (Roo Code의 "thinking" 표시 스타일)
          _reasoningTimer = setInterval(function () {
            if (!_reasoningCard || _reasoningTimer === null) return;
            const sum = _reasoningCard.querySelector('summary');
            if (sum) sum.textContent = '💭 생각 중... (' + _reasoningElapsed() + '초)';
          }, 1000);
        }
        const pre = _reasoningCard.querySelector('pre');
        if (pre) pre.textContent = _reasoningText;
        scrollToChatBottom();
      } catch (err) {
        console.warn('[reasoning] handler error:', err);
      }
      _idleExtensions = 0;
      resetIdleTimer();
    });

    sse.addEventListener('token', (e) => {
      const data = JSON.parse(e.data);
      setStreamStatus('thinking', '✍️ 최종 답변 생성 중...');
      incomingText += data.text;
      asstBubble.innerHTML = renderMd(incomingText);
      // 추론이 끝났으면 카드 제목 갱신 (경과 초 포함)
      if (_reasoningCard && _reasoningTimer) {
        _stopReasoningTimer('💭 생각 완료 (' + _reasoningElapsed() + '초) (클릭하여 보기)');
      }
      // 텍스트 토큰이 오면 현재 도구 그룹 카드를 확정 → 다음 도구 호출 시 새 그룹 시작
      if (_toolGroupCard) {
        _updateToolGroupHeader();
        _toolGroupCard = null;
        _toolGroupItems = null;
        _toolGroupCount = 0;
        _toolGroupDoneCount = 0;
        _toolItemMap = {};
      }
      scrollToChatBottom();
      _idleExtensions = 0;
      _statusCheckFailures = 0;
      _approvalPending = false;  // 토큰 재개 = 승인 처리되어 에이전트가 다시 움직임
      resetIdleTimer();
    });

    // ── Image/Video generation result ─────────────────────────────────────
    sse.addEventListener('media_result', (e) => {
      const data = JSON.parse(e.data);
      if (data.type === 'image' && data.images) {
        data.images.forEach(img => {
          const src = img.b64_json ? `data:image/png;base64,${img.b64_json}` : img.url;
          if (src) {
            incomingText += `\n\n![Generated Image](${src})\n`;
          }
        });
        if (data.revised_prompt) incomingText += `\n*Prompt: ${data.revised_prompt}*\n`;
      } else if (data.type === 'video' && data.video_url) {
        incomingText += `\n\n<video controls src="${data.video_url}" style="max-width:100%;border-radius:8px;"></video>\n`;
      }
      asstBubble.innerHTML = renderMd(incomingText);
      scrollToChatBottom();
      resetIdleTimer();
    });

    // ── Real-time terminal output streaming ──────────────────────────────
    // (_terminalOutputCard/_terminalOutputText 는 함수 최상위에 선언됨)

    sse.addEventListener('terminal_output', (e) => {
      const data = JSON.parse(e.data);
      _terminalOutputText += data.text || '';

      if (!_terminalOutputCard) {
        _terminalOutputCard = document.createElement('div');
        _terminalOutputCard.className = 'tool-card terminal-live-card';
        _terminalOutputCard.innerHTML = `
          <div class="tool-card-header">
            <span>Terminal: ${esc(data.tool === 'terminal' ? 'Running...' : data.tool)}</span>
            <span class="terminal-live-indicator">●</span>
          </div>
          <div class="tool-card-body" style="display:block;">
            <pre class="terminal-live-output"></pre>
          </div>
        `;
        // Keep this outside the token bubble.  Each token replaces
        // asstBubble.innerHTML, which otherwise silently removes the live
        // terminal card on the next streamed token.
        box.insertBefore(_terminalOutputCard, asstBubble);
      }

      var outputPre = _terminalOutputCard.querySelector('.terminal-live-output');
      if (outputPre) {
        outputPre.textContent = _terminalOutputText;
      }
      scrollToChatBottom();
      resetIdleTimer();
    });

    // ── heartbeat: 백엔드 keep-alive (미디어 생성 등 장기 작업 중 주기 발행) ──
    // streaming.py가 "프론트엔드가 heartbeat 리스너에서 resetIdleTimer()를
    // 호출한다"는 전제로 발행하지만 그동안 리스너가 없어, 토큰/도구 이벤트가
    // 없는 장기 작업 중 30초 idle 워치독이 불필요하게 firing했다.
    sse.addEventListener('heartbeat', () => {
      _idleExtensions = 0;
      _statusCheckFailures = 0;
      resetIdleTimer();
    });

    // ── notice: 서버 안내 (이전 작업 자동 취소 등) ──
    // chat_routes.py가 새 메시지 발송으로 이전 진행 중 스트림을 취소했을 때
    // 발행한다 (plan.md Cause D). 사용자에게 조용히 안내한다.
    sse.addEventListener('notice', (e) => {
      try {
        const data = JSON.parse(e.data);
        const msg = (data && data.message) || '';
        if (msg && asstBubble && asstBubble.parentNode) {
          const note = document.createElement('div');
          note.className = 'text-muted';
          note.style.cssText = 'margin-top:8px;font-size:12px;';
          note.textContent = 'ℹ️ ' + msg;
          // 토큰 스트리밍이 버블 innerHTML을 덮어쓰므로 버블 앞 독립 요소로 삽입
          box.insertBefore(note, asstBubble);
        }
      } catch (_) { }
      resetIdleTimer();
    });

    // ── 토론 전용 실시간 스트리밍 감지 ──
    let debateBubbles = {}; // sender -> element mapping
    let debateTexts = {};   // sender -> text string

    sse.addEventListener('debate_token', (e) => {
      const data = JSON.parse(e.data);
      const sender = data.sender;
      const text = data.text;

      if (!debateBubbles[sender]) {
        // Create new debate bubble
        const box = $('chatMessages');
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant';

        // style judge bubble
        if (sender.includes('판사')) {
          bubble.style.border = '2px solid var(--accent)';
          bubble.style.background = 'rgba(233, 69, 96, 0.05)';
          bubble.style.maxWidth = '95%';
        }

        box.appendChild(bubble);
        debateBubbles[sender] = bubble;
        debateTexts[sender] = '';
      }

      debateTexts[sender] += text;

      // Render sender badge + markdown
      const badge = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${sender}</div>`;
      debateBubbles[sender].innerHTML = badge + renderMd(debateTexts[sender]);
      scrollToChatBottom();
      resetIdleTimer();
    });

sse.addEventListener('debate_status', (e) => {
      const data = JSON.parse(e.data);
      const statusText = $('debateStatusText');
      if (statusText) {
        statusText.textContent = data.text;
      }
      const nextBtn = $('debateNextBtn');
      if (nextBtn) {
        if (data.waiting_next) {
          nextBtn.style.display = '';
          nextBtn.disabled = false;
        } else if (data.round && !data.waiting_next && !data.completed) {
          nextBtn.style.display = 'none';
        }
      }
      resetIdleTimer();
    });

    // Health badge UI: 🟢 ok / 🔴 failed(N)
    let _debateHealthBadges = {};
    sse.addEventListener('debate_health', (e) => {
      try {
        const data = JSON.parse(e.data);
        const bar = $('debateHealthBar');
        if (!bar) return;
        bar.innerHTML = '';
        (data.models || []).forEach((m) => {
          const span = document.createElement('span');
          span.style.cssText = 'display:inline-flex; align-items:center; gap:4px; padding:2px 8px; margin-right:6px; border-radius:10px; font-size:11px; border:1px solid ' + (m.status === 'ok' ? 'rgba(80,200,120,0.4)' : 'rgba(255,80,80,0.6)') + '; background:' + (m.status === 'ok' ? 'rgba(80,200,120,0.1)' : 'rgba(255,80,80,0.15)') + ';';
          span.title = m.status === 'ok' ? '응답 정상' : (`실패: ${m.reason || 'unknown'}`);
          span.innerHTML = (m.status === 'ok' ? '🟢 ' : '🔴 ') + m.label + (m.status === 'failed' && m.reason ? ` (HTTP ${m.reason})` : '');
          bar.appendChild(span);
        });
        bar.style.display = '';
      } catch (err) { /* ignore */ }
      resetIdleTimer();
    });

    // Partial-failed notice: bubble with reason
    sse.addEventListener('debate_partial_failed', (e) => {
      try {
        const data = JSON.parse(e.data);
        const box = $('chatMessages');
        if (!box) return;
        const b = document.createElement('div');
        b.className = 'debate-failed-notice';
        b.style.cssText = 'margin:8px 0; padding:10px 14px; border-left:4px solid #ff5050; background:rgba(255,80,80,0.08); border-radius:6px; font-size:13px; color:#ff9999;';
        b.innerHTML = `<strong>${data.sender}</strong> 응답 실패 — HTTP ${data.reason || 'unknown'}. 이 모델은 이번 ${data.round === 1 ? '라운드' : '라운드'}에서 제외됩니다.`;
        box.appendChild(b);
        scrollToChatBottom();
      } catch (err) { /* ignore */ }
      resetIdleTimer();
    });

    sse.addEventListener('debate_message_done', (e) => {
      delete debateBubbles[data.sender];
      delete debateTexts[data.sender];
    });

    sse.addEventListener('debate_health', (e) => {
      try {
        const data = JSON.parse(e.data);
        const models = data.models || [];
        const badgeRow = document.createElement('div');
        badgeRow.id = 'debateHealthRow';
        badgeRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;padding:8px;background:rgba(127,127,127,0.08);border-radius:6px;border:1px solid var(--border);';
        badgeRow.innerHTML = '<strong style="color:var(--accent);">모델 상태:</strong> ' + models.map(m => {
          const isOk = m.status === 'ok';
          const dot = isOk ? '🟢' : '🔴';
          const reason = m.reason ? ` <span style="color:#999;font-size:0.85em;">(${m.reason})</span>` : '';
          return `<span style="padding:4px 8px;border-radius:4px;background:${isOk ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.18)'};font-weight:500;">${dot} ${m.label}${reason}</span>`;
        }).join('');
        const old = document.getElementById('debateHealthRow');
        if (old) old.remove();
        const chatBox = $('chatMessages');
        if (chatBox) chatBox.appendChild(badgeRow);
        scrollToChatBottom();
      } catch (err) { console.error('debate_health parse err', err); }
    });

    sse.addEventListener('debate_partial_failed', (e) => {
      try {
        const data = JSON.parse(e.data);
        const sender = data.sender || 'Unknown';
        const reason = data.reason || 'unknown';
        const round = data.round || '?';
        const failBox = document.createElement('div');
        failBox.style.cssText = 'margin:8px 0;padding:10px 14px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.4);border-left:4px solid #ef4444;border-radius:4px;color:#fca5a5;';
        failBox.innerHTML = `<strong>⚠️ ${sender}</strong> <span style="color:#999;">r${round}·HTTP ${reason}</span> — 이 모델 응답은 사용 불가. 다른 모델과 회의는 계속 진행됩니다.`;
        const chatBox = $('chatMessages');
        if (chatBox) chatBox.appendChild(failBox);
        scrollToChatBottom();
      } catch (err) { console.error('debate_partial_failed parse err', err); }
    });

    sse.addEventListener('tool', (e) => {
      const data = JSON.parse(e.data);
      const toolName = data.name || 'unknown';
      const toolEvent = data.event || 'tool.started';
      const isStarted = toolEvent === 'tool.started';
      setStreamStatus('tool', isStarted
        ? `도구 실행 중: ${toolName}...`
        : `도구 실행 완료: ${toolName}`);

      // ── 도구 실행 상태 추적: idle timer 조기 종료 방지 ──
      // tool.started → 실행 중 카운트 증가 (idle timer 억제)
      // tool.completed → 카운트 감소 후 idle timer 재가동
      if (isStarted) {
        _activeTools++;
      } else {
        if (_activeTools > 0) _activeTools--;
        if (_activeTools === 0) {
          // 모든 도구가 끝나면 챗창 상태를 즉시 "응답 생성 중"으로 복귀시킨다.
          // 다음 토큰이 올 때까지 상태표시가 "도구 완료"에 머물러
          // 원상복구가 늦어 보이는 문제를 방지한다.
          setStreamStatus('thinking', '✍️ 최종 답변 생성 중...');
        } else {
          setStreamStatus('tool', '✅ 도구 완료 — 다음 작업 준비 중...');
        }
      }

      // ── 채팅 → 다이나믹 하네스 연동 ──
      // 채팅 에이전트가 execute_dynamic_harness 도구를 호출하면 자동으로
      // 하네스 탭으로 전환한다. 실행 로그는 agent_log SSE 이벤트로 들어와
      // 하네스 콘솔의 에이전트 노드 카드에 라우팅된다(채팅 버블에는 렌더링 안 함).
      // 하네스는 채팅 경로에서 동기적으로 실행되므로 /api/dynamic/run 폴링이
      // 없다. 완료 이벤트의 preview(formatted_output)를 하네스 콘솔에 직접
      // 결과 보고 박스로 렌더링해 "완료 보고가 안 뜬다" 문제를 해결한다.
      if (toolName === 'execute_dynamic_harness') {
        if (isStarted) {
          try {
            if (typeof cleanupHarnessState === 'function') cleanupHarnessState();
            const _hc = (typeof $ === 'function') ? $('harnessConsole') : null;
            if (_hc) _hc.innerHTML = '';
            if (typeof switchMode === 'function') switchMode('harness');
            const _task = (data.args && data.args.task) || '';
            if (typeof logToConsole === 'function') {
              logToConsole('🚀 채팅 에이전트가 다이나믹 하네스를 실행합니다', 'info');
              if (_task) logToConsole(`📋 작업: ${_task}`, 'info');
            }
          } catch (_dhErr) {
            console.error('[Chat→Harness] switch failed:', _dhErr);
          }
        } else {
          // ── 완료: 하네스 창에 최종 결과 보고 박스를 직접 렌더 ──
          try {
            const _consoleEl = (typeof $ === 'function') ? $('harnessConsole') : null;
            if (_consoleEl) {
              const _report = (data.preview && data.preview.formatted_output)
                ? data.preview.formatted_output
                : (typeof data.preview === 'string' ? data.preview : '');
              const _resultEl = document.createElement('div');
              _resultEl.className = 'harness-result-output';
              if (_report && _report.trim()) {
                _resultEl.innerHTML = '<div class="harness-result-header">📄 최종 결과물</div>' + renderMd(_report);
              } else {
                _resultEl.innerHTML = '<div class="harness-result-header">✅ 작업 완료</div>' +
                  '<div class="harness-result-empty">채팅 에이전트가 실행한 다이나믹 하네스가 완료되었습니다. 최종 보고는 위 채팅에서 확인할 수 있습니다.</div>';
              }
              _consoleEl.appendChild(_resultEl);
              if (typeof scrollToHarnessBottom === 'function') scrollToHarnessBottom();
            }
            if (typeof logToConsole === 'function') {
              logToConsole('✅ 다이나믹 하네스 실행 완료', 'success');
            }
          } catch (_dhErr2) {
            console.error('[Chat→Harness] report render failed:', _dhErr2);
          }
        }
      }

      // ── ask_followup_question: render choice cards inline ──
      if (toolName === 'ask_followup_question' && isStarted && data.args) {
        const question = data.args.question || '';
        let choices = data.args.follow_up || data.args.options || [];
        // Defensive: if backend still serialized the array as a truncated string,
        // try to recover it via JSON.parse so choice buttons still render.
        if (!Array.isArray(choices) && typeof choices === 'string') {
          try { const parsed = JSON.parse(choices); if (Array.isArray(parsed)) choices = parsed; } catch (_) { }
        }
        if (question && Array.isArray(choices) && choices.length > 0 && typeof showChoiceCard === 'function') {
          // Convert choice objects [{text, mode}] to the format showChoiceCard expects
          const mappedChoices = choices.map(function (c) {
            if (typeof c === 'string') return { text: c, mode: '' };
            return { text: c.text || c.label || String(c), mode: c.mode || '' };
          });
          showChoiceCard(question, mappedChoices, box);
        }
      }

      // ── 도구 그룹 카드: 반복 호출을 하나의 접이식 카드로 묶음 ──
      // 그룹 카드가 없으면 새로 생성 (reasoning 카드와 같이 box에 독립 삽입)
      if (!_toolGroupCard) {
        // 새 도구 그룹이 시작되면 진행 중이던 답변을 별도 블록으로 확정 (Roo 스타일 분리)
        _freezeAnswerSegment();
        _toolGroupCard = document.createElement('details');
        _toolGroupCard.className = 'tool-group-card';
        _toolGroupCard.innerHTML = `
          <summary>
            <span class="tool-group-icon">🔧</span>
            <span class="tool-group-label">도구 실행 중...</span>
            <span class="tool-group-counter">0</span>
            <span class="tool-group-spinner"></span>
            <span class="tool-group-chevron">▶</span>
          </summary>
          <div class="tool-group-items"></div>
        `;
        _toolGroupItems = _toolGroupCard.querySelector('.tool-group-items');
        _toolGroupCount = 0;
        _toolGroupDoneCount = 0;
        _toolItemMap = {};
        box.insertBefore(_toolGroupCard, asstBubble);
      }

      // 도구 항목 ID (started/completed 매칭용)
      const toolCallId = data.tool_call_id || (toolName + '_' + _toolGroupCount);

      if (isStarted) {
        _toolGroupCount++;
        // 새 항목 추가
        const item = document.createElement('div');
        item.className = 'tool-group-item';
        item.innerHTML = `
          <span class="tgi-icon">⏳</span>
          <span class="tgi-name">${toolName}</span>
          <span class="tgi-status">실행 중</span>
        `;
        _toolItemMap[toolCallId] = item;
        if (_toolGroupItems) _toolGroupItems.appendChild(item);
      } else {
        // completed: 기존 항목을 찾아 상태 업데이트
        _toolGroupDoneCount++;
        const existingItem = _toolItemMap[toolCallId];
        if (existingItem) {
          const icon = existingItem.querySelector('.tgi-icon');
          const status = existingItem.querySelector('.tgi-status');
          if (icon) icon.textContent = '✅';
          if (status) status.textContent = '완료';
        } else {
          // started 없이 completed만 온 경우 — 항목 새로 추가
          _toolGroupCount++;
          const item = document.createElement('div');
          item.className = 'tool-group-item';
          item.innerHTML = `
            <span class="tgi-icon">✅</span>
            <span class="tgi-name">${toolName}</span>
            <span class="tgi-status">완료</span>
          `;
          if (_toolGroupItems) _toolGroupItems.appendChild(item);
        }
      }

      _updateToolGroupHeader();
      scrollToChatBottom();
      _idleExtensions = 0;
      _approvalPending = false;  // 도구 재개 = 승인 처리되어 에이전트가 다시 움직임
      resetIdleTimer();
    });

    // ── 채팅 → 다이나믹 하네스 실시간 로그 라우팅 ──
    // execute_dynamic_harness 도구가 실행 중일 때 백엔드가 emit하는 agent_log
    // 이벤트({agent_id, content, status})를 하네스 콘솔의 에이전트 노드 카드로
    // 라우팅한다. 채팅 버블에는 렌더링하지 않는다(하네스 탭에서 확인).
    sse.addEventListener('agent_log', (e) => {
      try {
        const data = JSON.parse(e.data);
        const agentId = data.agent_id || 'harness';
        const content = data.content || '';
        const status = data.status || 'running';
        const logType = status === 'error' ? 'error'
          : (status === 'done' || status === 'completed' || status === 'success') ? 'success'
            : 'info';
        if (typeof appendCardLog === 'function') {
          appendCardLog(agentId, content, logType);
        }
        if (typeof updateCardStatus === 'function') {
          updateCardStatus(agentId, status);
        }
      } catch (err) {
        console.error('[Chat→Harness] agent_log routing failed:', err);
      }
    });

    // Monaco Editor UX를 위한 파일 편집 이벤트 리스너
    // tool.started 시점: 파일이 아직 디스크에 쓰이지 않았으므로 디스크에서 읽지 않고
    // args.content로 즉시 에디터 탭을 생성/전환한다.
    sse.addEventListener('file_edit', async (e) => {
      const data = JSON.parse(e.data);
      const filePath = data.args?.path || data.args?.file_path;
      console.log('[MonacoEditorUX] Received file_edit event:', data.name, filePath);

      if ((data.name === 'write_file' || data.name === 'patch') && filePath) {
        try {
          const content = data.args.content || data.args.new_content || '';
          // 1. 디스크 읽기 없이 content로 즉시 탭 생성/전환
          if (typeof createTabWithContent === 'function') {
            createTabWithContent(filePath, content);
          } else {
            const existingIdx = State.openTabs.findIndex(t => t.path === filePath);
            if (existingIdx !== -1) { switchTab(existingIdx); }
            else if (typeof openFileInTab === 'function') { await openFileInTab(filePath); }
          }

          // 2. 파일 트리 갱신
          if (typeof refreshFileTree === 'function') {
            refreshFileTree().catch(() => { });
          }
        } catch (err) {
          console.error('[MonacoEditorUX] Error handling file_edit:', err);
        }
      }
    });

    // tool.completed 시점: 디스크에 실제 쓰인 내용을 읽어 에디터에 확정 반영한다.
    // (patch 도구는 부분 수정이므로 디스크의 최종 내용이 정답이다.)
    sse.addEventListener('file_edit_done', async (e) => {
      try {
        const data = JSON.parse(e.data);
        const filePath = data.path;
        console.log('[MonacoEditorUX] Received file_edit_done event:', data.name, filePath);
        if (!filePath) return;

        if (typeof createTabWithContent === 'function') {
          createTabWithContent(filePath, data.content || '');
        }
        if (typeof refreshFileTree === 'function') {
          refreshFileTree().catch(() => { });
        }
      } catch (err) {
        console.error('[MonacoEditorUX] Error handling file_edit_done:', err);
      }
    });

    // ── Diff Preview SSE (AI → Preview Panel)
    // The server already registered the preview in _diff_previews and sends the
    // full payload (preview_id, original_full, new_full, line_changes). Register
    // it directly on the client. Re-posting a reconstructed SEARCH/REPLACE diff
    // to /api/file/preview-diff raced with the agent's own file write (409) and
    // left the apply/reject/view buttons dead while the bar stayed visible.
    sse.addEventListener('diff_preview', (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('[Streaming→DiffPreview] Received diff_preview event:', data.path, data.preview_id);
        if (data.preview_id && typeof registerDiffPreview === 'function') {
          registerDiffPreview(data);
        } else if (typeof previewAIDiff === 'function') {
          previewAIDiff(data.session_id, data.path,
            data.original_full && data.new_full
              ? `<<<<<<< SEARCH\n:start_line:1\n-------\n${data.original_full}\n=======\n${data.new_full}\n>>>>>>> REPLACE`
              : `<<<<<<< SEARCH\n-------\n${data.original_snippet || ''}\n=======\n${data.new_snippet || ''}\n>>>>>>> REPLACE`,
            data.source_agent || 'unknown'
          );
        }
      } catch (err) {
        console.error('[Streaming→DiffPreview] Error handling diff_preview:', err);
      }
    });

    // ── Approval SSE (Architect 변경 승인 + 위험 명령 승인)
    sse.addEventListener('approval', (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('[Streaming→Approval] Received approval event:', data.status, data.type || '', data.path || data.command || '');
        if (data && data.status === 'auto_approved') {
          // ── [E] 자동 승인(auto_approved): 대기 플래그 해제 + 완료 카드 ──
          // 백엔드가 45초 무응답 후 자동 승인했다. pending 블록과 달리 대기 상태로
          // 두면 안 되고, 승인 대기 중이던 상태를 즉시 해제해 에이전트가 계속 진행
          // 중임을 표시한다. 카드 자체는 _showApprovalBanner → showInlineApproval 이
          // 읽기 전용 "✅ 자동 승인됨" 카드로 교체한다.
          _approvalPending = false;
          _idleExtensions = 0;
          if (data.type === 'dangerous_command') {
          // [2026-08-27] restore the web view hidden during approval wait
            setStreamStatus('thinking', '⏱️ 응답 없음 — 자동 승인됨');
          } else {
            setStreamStatus('thinking', '⏱️ 자동 승인됨');
          }
          // 완료 카드도 승인 카드와 같은 컨테이너 가시성 규칙을 따른다.
          if (data.type === 'dangerous_command' && typeof switchMode === 'function') {
            try {
              const _cc = document.getElementById('chatModeContent');
              const _hc = document.getElementById('harnessModeContent');
              if (_cc && _hc && _cc.style.display === 'none') {
                switchMode('chat');
              }
            } catch (_swErr) { /* 무시 */ }
          }
        }
        if (data && data.status === 'pending') {
          // 승인 대기 표시 — idle 워치독이 스트림을 종료하지 않게 유예한다.
          // (백엔드는 사용자 응답을 기다리며 블로킹 중)
          _approvalPending = true;
          _idleExtensions = 0;
          // [2026-08-27] WebContentsView covers the chat/approval card area and
          // steals clicks (measured: 0 respond requests -> 45s auto-approve).
          // Hide the web view while approval is pending so the card is on top.
          try { if (window.electronAPI) window.electronAPI.setVisibility(false); } catch (_) { }
          // 상단 상태 표시를 "승인 대기"로 전환해 에이전트가 멈춘 것이 아니라
          // 사용자 검토를 기다리는 중임을 명확히 한다.
          if (data.type === 'dangerous_command') {
            setStreamStatus('thinking', '⚠️ 위험 명령 승인 대기 중...');
          } else {
            setStreamStatus('thinking', '🛡️ 승인 대기 중...');
          }
          // ── 승인 카드 가시성 보장 ──
          // 승인 카드는 chat 스트림의 일부로 chatMessages에 렌더된다. 그런데
          // 사용자가 harness 모드에 머문 상태에서 chat 스트림이 위험 명령을
          // 실행하면 _resolveApprovalContainer()가 harnessConsole을 반환해
          // 카드가 숨겨진 컨테이너에 붙어 보이지 않게 된다 ("승인 대기 중인데
          // 승인 창이 안 뜬다" 원인). chat 모드로 강제 전환해 카드를 노출한다.
          if (data.type === 'dangerous_command' && typeof switchMode === 'function') {
            try {
              const _cc = document.getElementById('chatModeContent');
              const _hc = document.getElementById('harnessModeContent');
              if (_cc && _hc && _cc.style.display === 'none') {
                switchMode('chat');
              }
            } catch (_swErr) { /* 무시 */ }
          }
        }
        if (typeof _showApprovalBanner === 'function') {
          _showApprovalBanner(data);
        }
      } catch (err) {
        console.error('[Streaming→Approval] Error handling approval:', err);
      }
      resetIdleTimer();
    });

    sse.addEventListener('model_info', (e) => {
      const data = JSON.parse(e.data);
      State._lastModelInfo = data;
    });

    sse.addEventListener('model_fallback', (e) => {
      const data = JSON.parse(e.data);
    });

    // ── Agent Voice Output: speak SSE 이벤트 수신 → SpeechSynthesis ──
    sse.addEventListener('speak', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data && data.text && typeof speak === 'function') {
          speak(data.text);
        }
      } catch (err) {
        console.warn('[Speak] Failed to process speak event:', err);
      }
    });

    // ── Context Compression: session ID rotated — update frontend state ──
    // When context exceeds threshold, the agent creates a new session and the
    // backend renames the session file. We must update State.activeSessionId
    // so subsequent messages are sent to the correct (new) session.
    sse.addEventListener('compressed', (e) => {
      try {
        const data = JSON.parse(e.data);
        const oldSid = data.old_session_id;
        const newSid = data.new_session_id;
        console.log('[SSE] Context compressed: session', oldSid, '→', newSid);

        // Update the active session ID so subsequent messages use the new session
        if (newSid && State.activeSessionId === oldSid) {
          State.activeSessionId = newSid;
          console.log('[SSE] Updated State.activeSessionId to:', newSid);
        }

        // Update session list if the session is tracked locally
        const localSess = State.sessions.find(x => x.session_id === oldSid);
        if (localSess) {
          localSess.session_id = newSid;
        }

        // Show user notification about context compression
        if (asstBubble && asstBubble.parentNode) {
          asstBubble.insertAdjacentHTML('beforeend',
            '<div class="text-muted" style="margin-top:8px;font-size:12px;">' +
            (data.message || 'Context auto-compressed to continue the conversation') +
            '</div>');
        }
      } catch (err) {
        console.error('[SSE] compressed handler error:', err);
      }
    });

    sse.addEventListener('done', (e) => {
      console.log('[SSE-DIAG] ✅ done event received');
      finishStream('done');

      try {
        const data = JSON.parse(e.data);

        // Render the final completed message and update lists
        renderMessages(data.session.messages, data.session.tool_calls);

        // Add model attribution to the last assistant bubble
        if (State._lastModelInfo) {
          const mi = State._lastModelInfo;
          const messagesEl = $('chatMessages');
          if (messagesEl) {
            const asstBubbles = messagesEl.querySelectorAll('.message-bubble.assistant');
            if (asstBubbles.length > 0) {
              const lastBubble = asstBubbles[asstBubbles.length - 1];
              const requested = mi.requested || 'unknown';
              const actual = mi.actual || 'unknown';
              const sameModel = requested === actual;
              const modelLabel = getModelDisplayName(actual);
              const attributionHtml = sameModel
                ? `<div class="model-attribution">🤖 ${modelLabel}</div>`
                : `<div class="model-attribution model-attribution--fallback">🤖 ${modelLabel} <span class="model-attribution-note">(요청: ${getModelDisplayName(requested)})</span></div>`;
              lastBubble.insertAdjacentHTML('beforeend', attributionHtml);
            }
          }
          State._lastModelInfo = null;
        }

        // Update session title locally
        const localSess = State.sessions.find(x => x.session_id === State.activeSessionId);
        if (localSess) localSess.title = data.session.title;
        renderSessionsList();

        // Refresh tree since agent might have modified workspace files
        refreshFileTree();

        // If any active tab is open, reload it
        var activeTab = null;
        try { activeTab = getActiveTab(); } catch (_) { /* getActiveTab may not be defined */ }
        if (activeTab) {
          var tabIdx = State.activeTabIndex;
          State.openTabs = State.openTabs.filter(function (_, i) { return i !== tabIdx; });
          openFileInTab(activeTab.path);
        }
      } catch (innerErr) {
        console.error('[SSE] done handler error:', innerErr);
        // cleanupStreamState already called above — button is re-enabled
      }
    });

    sse.addEventListener('cancel', () => {
      console.log('[SSE-DIAG] ⚠️ cancel event received');
      State._userCancelledStream = false;
      finishStream('cancel');
      if (asstBubble && asstBubble.parentNode) {
        asstBubble.insertAdjacentHTML('beforeend', '<div class="text-danger" style="margin-top:8px;">[실행 취소됨]</div>');
      }
    });

    sse.addEventListener('error', (e) => {
      // Log detailed error info for debugging
      console.log('[SSE-DIAG] ❌ error event received, readyState=', sse.readyState);
      console.error('[SSE] Stream error event:', e);
      console.error('[SSE] readyState:', sse.readyState, '(0=CONNECTING, 1=OPEN, 2=CLOSED)');
      console.error('[SSE] streamId:', streamId);
      if (e.data) {
        try { console.error('[SSE] error data:', JSON.parse(e.data)); } catch (_) { console.error('[SSE] error raw data:', e.data); }
      }
      if (e.target && e.target.readyState !== undefined) {
        console.error('[SSE] target readyState:', e.target.readyState);
      }

      // 사용자가 취소 버튼을 누른 직후의 연결 종료/재연결 실패는 오류가 아닌
      // 정상 취소로 처리한다. "스트림 오류/연결 끊김"으로 보이는 것을 방지.
      if (State._userCancelledStream) {
        console.log('[SSE-DIAG] error after user cancel — treating as clean cancel');
        State._userCancelledStream = false;
        finishStream('user_cancel');
        if (asstBubble && asstBubble.parentNode) {
          asstBubble.insertAdjacentHTML('beforeend', '<div class="text-danger" style="margin-top:8px;">[실행 취소됨]</div>');
        }
        return;
      }

      // EventSource.CLOSED: 서버가 연결을 닫았거나 네트워크가 끊긴 경우 — 곧바로 finishStream
      if (sse.readyState === EventSource.CLOSED) {
        finishStream('sse_closed');
        return;
      }

      finishStream('error');

      // Attempt recovery: the server may have already completed the run and
      // saved messages to the session. Fetch the session data and render if
      // the server finished. This covers the case where EventSource
      // auto-reconnects after the stream is already gone from STREAMS.
      var recovered = false;
      (async function _recoverSession() {
        try {
          var sessRes = await api('/api/sessions');
          var sessions = sessRes.sessions || [];
          var found = sessions.find(function (s) { return s.session_id === State.activeSessionId; });
          if (found && found.messages && found.messages.length > 0) {
            var lastMsg = found.messages[found.messages.length - 1];
            // Only recover if the last message is an assistant response (not the user's prompt alone)
            if (lastMsg.role === 'assistant' || found.messages.length > 1) {
              console.log('[SSE] Recovered session data after stream error, rendering messages.');
              renderMessages(found.messages, found.tool_calls);
              // Update session title
              var localSess = State.sessions.find(function (x) { return x.session_id === State.activeSessionId; });
              if (localSess) localSess.title = found.title;
              renderSessionsList();
              refreshFileTree();
              recovered = true;
            }
          }
        } catch (recoveryErr) {
          console.error('[SSE] Session recovery attempt failed:', recoveryErr);
        }
        // Only update UI if we have a valid asstBubble element still in the DOM
        // If page was unloaded or asstBubble was removed, skip UI update
        if (!recovered) {
          var errorMsg = '[스트림 오류 발생]';
          if (e.data) {
            try {
              var errData = JSON.parse(e.data);
              errorMsg = '[스트림 오류: ' + (errData.message || errData.error || '알 수 없음') + ']';
            } catch (_) {
              errorMsg = '[스트림 오류: ' + (String(e.data).substring(0, 100)) + ']';
            }
          }
          // Safely update UI only if asstBubble is still in the DOM
          if (asstBubble && asstBubble.parentNode) {
            asstBubble.insertAdjacentHTML('beforeend', '<div class="text-danger" style="margin-top:8px;">' + errorMsg + '</div>');
          } else {
            console.warn('[SSE] asstBubble removed from DOM, skipping error message display');
          }
        } else {
          // Remove the error placeholder bubble if recovery succeeded and bubble is still in DOM
          if (asstBubble && asstBubble.parentNode) {
            asstBubble.remove();
          }
        }
      })();
    });

    sse.addEventListener('apperror', (e) => {
      console.log('[SSE-DIAG] 💥 apperror event received');
      // Always release the UI first.  Malformed error payloads must never
      // prevent finishStream() from re-enabling the composer.
      finishStream('apperror');
      let data = {};
      try { data = JSON.parse(e.data || '{}'); } catch (_) { data = { message: e.data || '알 수 없는 오류' }; }
      if (asstBubble && asstBubble.parentNode) {
        asstBubble.insertAdjacentHTML('beforeend', `<div class="text-danger" style="margin-top:8px;">[오류: ${data.message || '알 수 없는 오류'}]</div>`);
      }
    });
    // API 호출 실패(404/503 등) 중계 — 에이전트 내부 재시도 루프가 계속
    // 진행 중이므로 스트림을 끊지 않고(finishStream 금지) 경고만 표시한다.
    sse.addEventListener('apierror', (e) => {
      console.log('[SSE-DIAG] ⚠️ apierror event received');
      let data = {};
      try { data = JSON.parse(e.data || '{}'); } catch (_) { data = { message: e.data || '알 수 없는 오류' }; }
      if (asstBubble && asstBubble.parentNode) {
        const warn = document.createElement('div');
        warn.style.cssText = 'margin-top:8px;font-size:12px;color:#e67e22;';
        warn.textContent = '⚠️ [API 오류] ' + (data.message || '알 수 없는 오류') + ' — 재시도 중...';
        asstBubble.appendChild(warn);
      }
    });

  } catch (err) {
    console.log('[SSE-DIAG] ❌ catch block (start run failed):', err.message);
    finishStream('start_failed');
    // [2026-08-27 압축 리네임 경합 폴백] 도구 실행 중 취소하면 컨텍스트 압축이
    // 세션 파일을 리네임하고 compressed 이벤트(new_session_id)가 유실될 수 있다.
    // 이 경우 옧 session_id로 /api/chat/start가 404 Session not found를 반환한다.
    // 세션 목록을 재로드해 가장 최근 세션으로 자동 전환해 복구를 시도한다.
    if (/Session not found/i.test(String(err.message))) {
      try {
        const sessRes = await api('/api/sessions');
        const sessions = (sessRes && sessRes.sessions) || [];
        if (sessions.length) {
          const latest = sessions.slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))[0];
          State.sessions = sessions;
          State.activeSessionId = latest.session_id;
          if (typeof renderSessionsList === 'function') renderSessionsList();
          if (typeof selectSession === 'function') { await selectSession(latest.session_id); }
          if (asstBubble && asstBubble.parentNode) {
            asstBubble.innerHTML = '<div class="text-warning" style="margin-top:8px;">[세션이 압축 갱신되어 최근 세션으로 전환했습니다 — 메시지를 다시 보내주세요]</div>';
          }
          return;
        }
      } catch (recoveryErr) {
        console.warn('[SSE-DIAG] session recovery failed:', recoveryErr);
      }
    }
    asstBubble.innerHTML = `<div class="text-danger">[Failed to start run: ${err.message}]</div>`;
  }
}

async function cancelActiveStream() {
  if (!State.currentStreamId) return;
  // 사용자 취소 표시 — 이후 SSE error/재연결 이벤트가 "에이전트 연결 끊김"
  // 오류로 표시되지 않도록 한다 (error 핸들러에서 확인).
  State._userCancelledStream = true;
  try {
    await api('/api/chat/cancel', {
      method: 'POST',
      body: {
        stream_id: State.currentStreamId,
        // session_id를 함께 보내야 streaming.cancel_stream()이
        // _force_release_session_lock에서 역방향 조회(실패 가능) 대신
        // 락을 직접 해제할 수 있다. 취소 직후 새 메시지가
        // '이전 작업이 아직 종료되지 않았습니다'로 거부되는 것을 방지.
        session_id: State.activeSessionId || ''
      }
    });
  } catch (e) {
    console.error("Cancel failed:", e);
  }
  // 취소 버튼 경로는 SSE를 즉시 닫으므로(cleanupStreamState) 블록 클로저의
  // finishStream()이 실행되기 전에 transient 카드가 남을 수 있다. DOM에서
  // 직접 찾아 제거해 얇은 빈 줄/접힌 카드 헤더가 남지 않게 한다.
  try {
    const box = $('chatMessages');
    if (box) {
      box.querySelectorAll('.reasoning-card, .tool-group-card, .terminal-live-card').forEach((el) => el.remove());
      // 빈 assistant 버블(커서만 남은)도 제거
      box.querySelectorAll('.message-bubble.assistant').forEach((el) => {
        if (!(el.textContent || '').trim() && !el.querySelector('img, video, .text-muted, .text-danger')) el.remove();
      });
    }
  } catch (_) { }
  cleanupStreamState();
}

function cleanupStreamState() {
  console.log('[SSE-DIAG] 🧹 cleanupStreamState called');
  // Cleanup is called from several asynchronous SSE/error paths.  Each UI
  // operation must be isolated so one missing/replaced DOM node cannot leave
  // the composer permanently disabled.
  try {
    if (State.currentEventSource) State.currentEventSource.close();
  } catch (err) {
    console.warn('[SSE-DIAG] EventSource close failed:', err);
  } finally {
    State.currentEventSource = null;
    State.currentStreamId = null;
  }
  try { setChatStatus('idle', '대기 중'); } catch (err) { console.warn('[SSE-DIAG] status reset failed:', err); }
  try {
    const sendBtn = $('sendPromptBtn');
    if (sendBtn) sendBtn.disabled = false;
  } catch (err) { console.warn('[SSE-DIAG] send button reset failed:', err); }
  try {
    const cancelBtn = $('cancelStreamBtn');
    if (cancelBtn) cancelBtn.style.display = 'none';
  } catch (err) { console.warn('[SSE-DIAG] cancel button reset failed:', err); }
  // [I] 취소 버튼이 사라지며 가시 영역이 다시 늘어나면 마지막 메시지가
  // 완전히 보이도록 재스크롤.
  try { scrollToChatBottom(); } catch (err) { }
}

function setChatStatus(status, text) {
  const ind = $('statusIndicator');
  const statusText = $('statusText');
  if (ind) ind.className = `status-indicator ${status}`;
  if (statusText) statusText.textContent = text;
}
// ── Modals Setup ──

// 프로바이더 미설정 안내 배너
function showNoProviderBanner() {
  const existing = document.getElementById('noProviderBanner');
  if (existing) return;
  const banner = document.createElement('div');
  banner.id = 'noProviderBanner';
  banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:linear-gradient(90deg,#ff6b35,#f7931e);color:white;padding:10px 16px;text-align:center;font-size:13px;font-weight:600;box-shadow:0 2px 8px rgba(0,0,0,0.3);';
  banner.innerHTML = `⚠️ 연동된 AI 모델(API 키)이 없습니다. 설정에서 API 키를 입력해 주세요. <button onclick="document.getElementById('noProviderBanner').remove()" style="margin-left:12px;background:rgba(255,255,255,0.3);border:none;color:white;padding:2px 8px;border-radius:4px;cursor:pointer;">닫기</button>`;
  document.body.insertBefore(banner, document.body.firstChild);
}

function openSettingsModal() {
  const settings = State.settings || {};
  if ($('settingsDefaultModel')) $('settingsDefaultModel').value = settings.default_model || '';
  if ($('settingsShowCli')) $('settingsShowCli').checked = settings.show_cli_sessions || false;
  $('settingsModal').style.display = 'flex';
  // Load provider management
  if (typeof loadProviderManagement === 'function') {
    loadProviderManagement();
  }
}

function closeSettingsModal() {
  $('settingsModal').style.display = 'none';
  // Reset provider form
  if (typeof hideAddProviderForm === 'function') {
    hideAddProviderForm();
  }
}

async function saveSettings() {
  const model = $('settingsDefaultModel').value;
  const showCli = $('settingsShowCli').checked;

  // Bugfix #3: auto-save pending provider form data before saving general settings.
  // The user may have filled in the provider form and clicked "설정 저장"
  // instead of the separate "💾 제공자 저장" button.
  const providerForm = $('settingsAddProviderForm');
  if (providerForm && providerForm.style.display !== 'none') {
    const providerName = ($('settingsProviderName') || {}).value.trim();
    const providerKey = ($('settingsProviderKey') || {}).value.trim();
    // Only save if the form has meaningful data (name + key are required)
    if (providerName && providerKey) {
      try {
        const providerUrl = ($('settingsProviderUrl') || {}).value.trim();
        await api('/api/providers/add', {
          method: 'POST',
          body: { name: providerName, api_key: providerKey, base_url: providerUrl }
        });
      } catch (providerErr) {
        // Provider save failed — show a warning but still save the rest
        showToast('제공자 저장 실패: ' + providerErr.message);
      }
    }
  }

  try {
    State.settings = await api('/api/settings', {
      method: 'POST',
      body: { default_model: model, show_cli_sessions: showCli }
    });
    closeSettingsModal();
    showToast("설정이 저장되었습니다.");
    // Refresh model select to pick up any newly added providers
    if (typeof loadProviderManagement === 'function') {
      loadProviderManagement().catch(() => { });
    }
    if (typeof refreshAllModelSelects === 'function') {
      refreshAllModelSelects().catch(() => { });
    }
  } catch (e) {
    showToast("설정 저장 실패: " + e.message);
  }
}
async function handleModelChange(newModelId) {
  State.activeModelId = newModelId;
  if ($('modelSelect')) $('modelSelect').value = newModelId;
  updateMediaOptionsPanel(newModelId);

  if (State.activeSessionId) {
    try {
      await api('/api/session/update', {
        method: 'POST',
        body: {
          session_id: State.activeSessionId,
          model: newModelId
        }
      });
      // Update local sessions cache
      const s = State.sessions.find(x => x.session_id === State.activeSessionId);
      if (s) s.model = newModelId;
    } catch (e) {
      console.error("Failed to update session model:", e);
    }
  }
}

// ── 미디어 생성 옵션 패널 ──────────────────────────────────────────
function getSelectedModelType(modelId) {
  const sel = $('modelSelect');
  if (sel) {
    const opt = Array.from(sel.options).find(o => o.value === modelId);
    if (opt && opt.getAttribute('data-type')) return opt.getAttribute('data-type');
  }
  for (const g of (State.models || [])) {
    for (const m of (g.models || [])) {
      if (m.id === modelId && m.type) return m.type;
    }
  }
  return 'chat';
}

function updateMediaOptionsPanel(modelId) {
  const panel = $('mediaOptionsPanel');
  if (!panel) return;
  const type = getSelectedModelType(modelId);
  if (type === 'image' || type === 'video') {
    panel.style.display = '';
    const isVideo = type === 'video';
    if ($('mediaOptionsIcon')) $('mediaOptionsIcon').textContent = isVideo ? '🎬' : '🎨';
    if ($('mediaOptionsLabel')) $('mediaOptionsLabel').textContent = isVideo ? '영상 생성 옵션' : '이미지 생성 옵션';
    const countField = $('mediaCountField');
    if (countField) countField.style.display = isVideo ? 'none' : '';
  } else {
    panel.style.display = 'none';
  }
}

function buildMediaOptions() {
  const type = getSelectedModelType(State.activeModelId);
  if (type !== 'image' && type !== 'video') return undefined;
  const opts = {};
  const sizeSel = $('mediaSizeSelect');
  if (sizeSel && sizeSel.value) opts.size = sizeSel.value;
  if (type === 'image') {
    const countInput = $('mediaCountInput');
    if (countInput) {
      const n = parseInt(countInput.value, 10);
      if (n >= 1) opts.n = n;
    }
  }
  return opts;
}

async function switchAgentProfile(name) {
  try {
    const res = await api('/api/profile/switch', {
      method: 'POST',
      body: { name }
    });
    State.activeProfileName = res.active;
    showToast(`에이전트 프로필 전환 완료: ${res.active}`);

    // Sync dropdown values
    if ($('agentProfileSelect')) $('agentProfileSelect').value = res.active;
    if ($('rightAgentProfileSelect')) $('rightAgentProfileSelect').value = res.active;

    // If a default model was returned, apply it
    if (res.default_model) {
      const bestMatch = findBestModelMatch(res.default_model);
      if (bestMatch) {
        await handleModelChange(bestMatch);
      }
    }

    // Refresh sessions list because the profile directory controls files/memory
    const sessData = await api('/api/sessions');
    State.sessions = sessData.sessions;
    renderSessionsList();

    if (State.sessions.length > 0) {
      await selectSession(State.sessions[0].session_id);
    } else {
      await createNewSession();
    }
  } catch (e) {
    showToast("프로필 전환 실패: " + e.message);
    if ($('agentProfileSelect')) {
      $('agentProfileSelect').value = State.activeProfileName;
    }
    if ($('rightAgentProfileSelect')) {
      $('rightAgentProfileSelect').value = State.activeProfileName;
    }
  }
}

function findBestModelMatch(modelId) {
  if (!modelId) return null;
  const flatModels = [];
  (State.models || []).forEach(g => {
    if (g.models && Array.isArray(g.models)) {
      flatModels.push(...g.models);
    }
  });
  const exact = flatModels.find(m => m.id === modelId);
  if (exact) return exact.id;
  const parts = modelId.split('/');
  const base = (parts[parts.length - 1] || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const partial = flatModels.find(m => m.id.toLowerCase().replace(/[^a-z0-9]/g, '').includes(base));
  if (partial) return partial.id;
  return null;
}

// ── Event Listeners Binding ──
function setupEventListeners() {
  // New session button
  $('newSessionBtn').onclick = createNewSession;

  // Model select change
  $('modelSelect').onchange = (e) => handleModelChange(e.target.value);

  // Agent profile change
  if ($('agentProfileSelect')) {
    $('agentProfileSelect').onchange = (e) => switchAgentProfile(e.target.value);
  }

  // Collapsible default tasks toggle
  const tasksHeader = $('defaultTasksHeader');
  if (tasksHeader) {
    tasksHeader.onclick = () => {
      const content = $('defaultTasksContent');
      if (content) {
        content.classList.toggle('collapsed');
        tasksHeader.classList.toggle('collapsed');
      }
    };
  }

  // Default tasks template click handlers
  document.querySelectorAll('.task-btn').forEach(btn => {
    btn.onclick = () => {
      const templateKey = btn.dataset.template;
      const templates = {
        'workspace-summary': '현재 작업공간을 빠르게 훑고, 중요한 파일/폴더와 지금 바로 할 수 있는 작업 5가지를 요약해줘.',
        'note-draft': '이 대화나 현재 작업을 바탕으로 바로 저장 가능한 Obsidian 노트 초안을 만들어줘. frontmatter와 읽기 좋은 구조를 포함해줘.',
        'blog-post': '이 주제를 바탕으로 블로그 포스트 또는 /posting 초안 방향을 잡아줘. 핵심 논지, 구조, 시각화 아이디어까지 제안해줘.',
        'schedule-task': '이 작업을 나중에 자동으로 반복하려면 어떤 cron job 이 좋은지 제안하고, 바로 만들 수 있게 초안을 작성해줘.'
      };
      const text = templates[templateKey] || '';
      if (!text) return;

      const promptInput = $('promptInput');
      if (promptInput) {
        promptInput.value = text;
        promptInput.style.height = 'auto';
        promptInput.style.height = `${promptInput.scrollHeight}px`;
        promptInput.focus();
        showToast('기본 작업 템플릿이 입력창에 자동 완성되었습니다.');
      }
    };
  });

  // Panel Toggles
  $('toggleLeftBtn').onclick = () => {
    State.leftPanelVisible = !State.leftPanelVisible;
    localStorage.setItem('daon_left_panel_visible', State.leftPanelVisible);
    window.updateLayout();
  };
  $('toggleExplorerBtn').onclick = () => {
    State.explorerVisible = !State.explorerVisible;
    localStorage.setItem('daon_explorer_visible', State.explorerVisible);
    window.updateLayout();
  };
  $('toggleRightBtn').onclick = () => {
    State.rightPanelVisible = !State.rightPanelVisible;
    localStorage.setItem('daon_right_panel_visible', State.rightPanelVisible);
    window.updateLayout();
  };

  // Folder open buttons
  $('openFolderBtn').onclick = selectWorkspacePathNative;
  $('welcomeOpenFolderBtn').onclick = selectWorkspacePathNative;

  // File explorer header actions
  $('newFileBtn').onclick = createNewFilePrompt;
  $('newDirBtn').onclick = createNewDirPrompt;
  $('openFileBtn').onclick = openFilePrompt;
  $('refreshExplorerBtn').onclick = refreshFileTree;

  // Editor header actions
  $('saveFileBtn').onclick = saveCurrentFile;
  $('deleteFileBtn').onclick = deleteCurrentFile;
  if ($('previewHtmlBtn')) {
    $('previewHtmlBtn').onclick = toggleHtmlPreview;
  }

  // Settings buttons
  $('settingsBtn').onclick = openSettingsModal;
  $('closeSettingsBtn').onclick = closeSettingsModal;
  $('saveSettingsBtn').onclick = saveSettings;

  // Chat buttons
  $('clearChatBtn').onclick = clearChatHistory;
  $('sendPromptBtn').onclick = sendPrompt;
  $('cancelStreamBtn').onclick = cancelActiveStream;

  // Mode switcher tabs
  $('modeChatBtn').onclick = () => switchMode('chat');
  $('modeHarnessBtn').onclick = () => switchMode('harness');

  // Harness actions
  $('runHarnessBtn').onclick = runDynamicHarness;
  $('cancelHarnessBtn').onclick = cancelHarness;

  // Textarea dynamic expand and enter trigger
  const promptInput = $('promptInput');
  promptInput.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // Bugfix #1: guard against Enter sending while a stream is already active.
      // Even though sendPrompt() checks the button disabled state, this
      // provides an early exit and avoids any race between keydown and the
      // button state toggle.
      if (State.currentStreamId || $('sendPromptBtn').disabled) return;
      sendPrompt();
    }
  };
  promptInput.oninput = () => {
    promptInput.style.height = 'auto';
    promptInput.style.height = `${promptInput.scrollHeight}px`;
    scrollToChatBottom();
  };

  const harnessInput = $('harnessInput');
  harnessInput.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      runDynamicHarness();
    }
  };
  harnessInput.oninput = () => {
    harnessInput.style.height = 'auto';
    harnessInput.style.height = `${harnessInput.scrollHeight}px`;
  };

  // 📎 File Attachment Bindings
  const fileInput = $('fileInput');
  const attachBtn = $('attachBtn');
  if (attachBtn && fileInput) {
    attachBtn.onclick = () => fileInput.click();
    fileInput.onchange = (e) => {
      addFiles(e.target.files);
      fileInput.value = '';
    };
  }

  // 📦 Drag & Drop Bindings on Chat Input Area (expanded to Right Panel)
  const rightPanel = document.querySelector('.right-panel');
  if (rightPanel) {
    const inputArea = document.querySelector('.chat-input-area');
    const dropHint = document.getElementById('dropHint');
    const messagesEl = document.getElementById('messages');

    ['dragenter', 'dragover'].forEach(eventName => {
      rightPanel.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (inputArea) inputArea.classList.add('drag-over');
        if (dropHint) dropHint.classList.add('show');
        if (messagesEl) messagesEl.classList.add('drag-over');
      }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
      rightPanel.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (inputArea) inputArea.classList.remove('drag-over');
        if (dropHint) dropHint.classList.remove('show');
        if (messagesEl) messagesEl.classList.remove('drag-over');
      }, false);
    });

    rightPanel.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const files = dt.files;
      if (files && files.length > 0) {
        addFiles(files);
      }
    }, false);
  }

  // Voice input (Whisper)
  initVoiceInput();

  // ── Bug fix: Ensure cleanupStreamState is called on page unload ──
  // This prevents the send button from being permanently disabled when:
  // 1. User navigates away during an active stream
  // 2. User refreshes the page during a stream
  // 3. Network connection drops without triggering an error event
  window.addEventListener('beforeunload', () => {
    if (State.currentEventSource) {
      State.currentEventSource.close();
      State.currentEventSource = null;
    }
    // Force-enable the send button regardless of current state
    const sendBtn = $('sendPromptBtn');
    if (sendBtn) sendBtn.disabled = false;
    const cancelBtn = $('cancelStreamBtn');
    if (cancelBtn) cancelBtn.style.display = 'none';
    State.currentStreamId = null;
    setChatStatus('idle', '대기 중');
  });
}

// ── ⚖️ 전문가 토론 및 다자간 회의 모드 프론트엔드 연동 ──

let debateIsActive = false;
let debateAutoTimer = null; // waiting_next 자동진행 타이머(수동 클릭/새 상태 수신 시 취소)
let currentDebateType = 'debate'; // 'debate' | 'meeting'
let currentDebatePlanText = '';

function selectDebateType(type) {
  currentDebateType = type;
  const debateBtn = $('debateTypeDebateBtn');
  const meetingBtn = $('debateTypeMeetingBtn');
  const meetingRow = $('meetingOptionsRow');
  const submitBtn = $('startDebateSubmitBtn');
  const label = $('debateModelSelectLabel');
  const topicLabel = $('debateTopicLabel');

  if (type === 'meeting') {
    if (debateBtn) {
      debateBtn.className = 'cron-btn';
      debateBtn.style.fontWeight = 'normal';
    }
    if (meetingBtn) {
      meetingBtn.className = 'cron-btn run';
      meetingBtn.style.fontWeight = '600';
    }
    if (meetingRow) meetingRow.style.display = 'flex';
    if (submitBtn) submitBtn.textContent = '👥 회의 시작';
    if (label) label.textContent = '👥 회의 참여 패널 모델 선택 (최소 2개 이상):';
    if (topicLabel) topicLabel.textContent = '회의 아젠다/주제:';
  } else {
    if (debateBtn) {
      debateBtn.className = 'cron-btn run';
      debateBtn.style.fontWeight = '600';
    }
    if (meetingBtn) {
      meetingBtn.className = 'cron-btn';
      meetingBtn.style.fontWeight = 'normal';
    }
    if (meetingRow) meetingRow.style.display = 'none';
    if (submitBtn) submitBtn.textContent = '⚖️ 토론 시작';
    if (label) label.textContent = '⚖️ 토론 참여 모델 선택 (최소 2개 이상):';
    if (topicLabel) topicLabel.textContent = '토론 주제:';
  }
}

function toggleDebateModeUI(show) {
  const chatInput = $('chatInputArea');
  const debateSetup = $('debateSetupArea');
  const debateControl = $('debateControlArea');
  const chatMessages = $('chatMessages');
  const debateMessages = $('debateMessages');

  if (show) {
    chatInput.style.display = 'none';
    if (chatMessages) chatMessages.style.display = 'none';
    if (debateMessages) debateMessages.style.display = 'flex';

    if (debateIsActive) {
      debateControl.style.display = 'flex';
      debateSetup.style.display = 'none';
    } else {
      debateSetup.style.display = 'flex';
      debateControl.style.display = 'none';
      populateDebateModels();
      selectDebateType(currentDebateType);
    }
  } else {
    chatInput.style.display = 'flex';
    if (chatMessages) chatMessages.style.display = 'flex';
    if (debateMessages) debateMessages.style.display = 'none';

    debateSetup.style.display = 'none';
    debateControl.style.display = 'none';

    // Refresh normal chat messages to ensure they are pristine
    if (State.activeSessionId) {
      const activeSess = State.sessions.find(x => x.session_id === State.activeSessionId);
      if (activeSess) {
        // filter out any debate messages from the normal chat view if they were saved in session
        const normalMessages = (activeSess.messages || []).filter(msg => !msg.sender);
        renderMessages(normalMessages, activeSess.tool_calls || []);
      }
    }
  }
}

function populateDebateModels() {
  const container = $('debateModelCheckboxes');
  const modSelect = $('debateModeratorSelect');
  if (!container) return;
  container.innerHTML = '';
  if (modSelect) modSelect.innerHTML = '';

  const flatModels = [];
  (State.models || []).forEach(g => {
    if (g.models && Array.isArray(g.models)) {
      flatModels.push(...g.models);
    }
  });

  if (flatModels.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:4px;">사용 가능한 모델이 없습니다.</div>';
    return;
  }

  flatModels.forEach((m, idx) => {
    const label = document.createElement('label');
    label.style = "display:flex; align-items:center; gap:4px; font-size:11px; color:var(--text); cursor:pointer; margin-right:8px; margin-bottom:4px; user-select:none;";

    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = m.id;
    input.className = 'debate-model-checkbox';
    input.style = "cursor:pointer;";

    // Auto-check first few or common models for convenience
    const idLower = m.id.toLowerCase();
    if (idLower.includes('deepseek-v3') || idLower.includes('claude-3.5-sonnet') || idLower.includes('gpt-4o-mini') || idx < 3) {
      input.checked = true;
    }

    label.appendChild(input);
    label.appendChild(document.createTextNode(m.label || m.id));
    container.appendChild(label);

    if (modSelect) {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.label || m.id;
      if (idLower.includes('claude-3.5-sonnet') || idLower.includes('gpt-4o') || idx === 0) {
        opt.selected = true;
      }
      modSelect.appendChild(opt);
    }
  });
}

function sendDebatePlanToHarness(customPlan) {
  const plan = customPlan || currentDebatePlanText;
  if (!plan) {
    showToast('추천 실행 계획안이 없습니다.');
    return;
  }

  if (typeof switchMode === 'function') {
    switchMode('harness');
  }

  const hInput = $('harnessInput');
  if (hInput) {
    hInput.value = plan;
    hInput.focus();
  }
  showToast('⚡ 다이나믹 하네스에 실행 계획안이 설정되었습니다.');
}

async function startDebateWorkflow() {
  const topicInput = $('debateTopicInput');
  const topic = topicInput ? topicInput.value.trim() : '';
  if (!topic) {
    showToast(currentDebateType === 'meeting' ? '회의 아젠다를 입력해 주세요.' : '토론 주제를 입력해 주세요.');
    return;
  }

  // Collect checked models
  const checkboxes = document.querySelectorAll('.debate-model-checkbox');
  const selectedModels = [];
  checkboxes.forEach(cb => {
    if (cb.checked) selectedModels.push(cb.value);
  });

  if (selectedModels.length < 2) {
    showToast('최소 2개 이상의 모델을 선택해 주세요.');
    return;
  }

  const maxTurns = parseInt($('debateMaxTurnsSelect')?.value || '8', 10);
  const moderatorModel = $('debateModeratorSelect')?.value;

  // UI state change to active
  debateIsActive = true;
  if (debateAutoTimer) { clearTimeout(debateAutoTimer); debateAutoTimer = null; }
  currentDebatePlanText = '';
  $('debateSetupArea').style.display = 'none';
  $('debateControlArea').style.display = 'flex';
  $('debateStatusText').textContent = currentDebateType === 'meeting' ? '👥 회의 준비 중...' : '⚖️ 토론 준비 중...';
  $('debateNextBtn').style.display = 'none';

  // Clear debate messages and render initial banner
  const box = $('debateMessages');
  if (box) box.innerHTML = '';

  const userBubble = document.createElement('div');
  userBubble.className = 'message-bubble user';
  const badgeTitle = currentDebateType === 'meeting' ? '👥 다자간 회의 시작' : '⚖️ 전문가 토론 시작';
  userBubble.innerHTML = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${badgeTitle}</div><strong>주제:</strong> ${topic}`;
  box.appendChild(userBubble);
  scrollToChatBottom();

  try {
    const res = await api('/api/debate/start', {
      method: 'POST',
      body: {
        session_id: State.activeSessionId,
        topic: topic,
        models: selectedModels,
        mode: currentDebateType,
        max_turns: maxTurns,
        moderator_model: moderatorModel
      }
    });

    if (!res.ok) {
      showToast('시작 실패: ' + (res.message || ''));
      cancelDebateWorkflow();
      return;
    }

    const streamId = res.stream_id;
    bindDebateStream(streamId);

  } catch (err) {
    showToast('토론/회의를 시작할 수 없습니다: ' + err.message);
    cancelDebateWorkflow();
  }
}

function bindDebateStream(streamId) {
  State.currentStreamId = streamId;

  if (State.currentEventSource) {
    State.currentEventSource.close();
  }

  const sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
  State.currentEventSource = sse;

  const box = $('debateMessages');
  let debateBubbles = {};
  let debateTexts = {};
  let debateStreamFinished = false;

  sse.addEventListener('heartbeat', (e) => {
    // Connection keep-alive
  });

  sse.addEventListener('moderator_pick', (e) => {
    const data = JSON.parse(e.data);
    const card = document.createElement('div');
    card.className = 'moderator-pick-card';
    card.style = "margin: 8px 0; padding: 10px 14px; background: linear-gradient(135deg, rgba(233, 69, 96, 0.08), rgba(15, 52, 96, 0.15)); border: 1px solid rgba(233, 69, 96, 0.3); border-radius: 8px; font-size: 12px;";
    card.innerHTML = `
      <div style="font-weight: 700; color: var(--accent); margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center;">
        <span>🎙️ 사회자 지목 [턴 ${data.turn}/${data.max_turns}] → <strong>${data.speaker}</strong></span>
        <span style="font-size: 10px; opacity: 0.75; font-weight: normal;">${data.reason || ''}</span>
      </div>
      <div style="color: var(--text); padding-left: 4px; border-left: 2px solid var(--accent); margin-top: 6px;">
        <strong>질문/요청:</strong> ${data.question}
      </div>
    `;
    box.appendChild(card);
    scrollToChatBottom();
  });

  sse.addEventListener('debate_token', (e) => {
    const data = JSON.parse(e.data);
    const sender = data.sender;
    const text = data.text;

    if (!debateBubbles[sender]) {
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble assistant';
      if (sender.includes('판사')) {
        bubble.style.border = '2px solid var(--accent)';
        bubble.style.background = 'rgba(233, 69, 96, 0.06)';
        bubble.style.maxWidth = '96%';
      }
      box.appendChild(bubble);
      debateBubbles[sender] = bubble;
      debateTexts[sender] = '';
    }

    debateTexts[sender] += text;
    const badge = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${sender}</div>`;
    debateBubbles[sender].innerHTML = badge + renderMd(debateTexts[sender]);

    if (sender.includes('판사')) {
      currentDebatePlanText = debateTexts[sender];
    }
    scrollToChatBottom();
  });

  let lastDebateCompleted = false;
  sse.addEventListener('debate_status', (e) => {
    const data = JSON.parse(e.data);
    lastDebateCompleted = !!data.completed;
    $('debateStatusText').textContent = data.text;

    if (data.completed) {
      $('debateNextBtn').style.display = 'none';
      // Append Harness Execution Bridge button if Judge verdict is finished
      if (currentDebatePlanText) {
        const bridgeCard = document.createElement('div');
        bridgeCard.style = "margin-top: 12px; padding: 10px; background: var(--bg2); border: 1px solid var(--border2); border-radius: 8px; text-align: center;";
        bridgeCard.innerHTML = `
          <div style="font-size: 11px; color: var(--text2); margin-bottom: 6px;">💡 판결 추천 계획안을 다이나믹 하네스에 전달하여 즉시 실행할 수 있습니다.</div>
          <button class="cron-btn run" onclick="sendDebatePlanToHarness()" style="padding: 6px 14px; font-size: 12px; font-weight: 600;">⚡ 다이나믹 하네스로 계획 실행</button>
        `;
        box.appendChild(bridgeCard);
        scrollToChatBottom();
      }
    } else if (data.waiting_next) {
      // [2026-08-26 수정] 자동 모드에서도 수동 진행 버튼을 항상 표시한다.
      // 기존엔 display:none으로 숨겨 타이머가 실패하면 복구 수단이 없었다.
      if (debateAutoTimer) { clearTimeout(debateAutoTimer); debateAutoTimer = null; }
      const isAuto = $('debateAutoAdvanceToggle')?.checked;
      if (isAuto && !data.completed) {
        $('debateStatusText').textContent = data.text + ' (⚡ 자동 진행 중...)';
        $('debateNextBtn').textContent = '▶ 지금 진행';
        $('debateNextBtn').style.display = 'block';
        debateAutoTimer = setTimeout(() => {
          debateAutoTimer = null;
          if (debateIsActive) {
            proceedDebateRound();
          }
        }, 1200);
      } else {
        $('debateNextBtn').style.display = 'block';
        if (data.text.includes('1라운드')) {
          $('debateNextBtn').textContent = '▶ 2라운드(반박) 진행';
        } else if (data.text.includes('2라운드')) {
          $('debateNextBtn').textContent = '⚖️ 최종 판결 요청';
        } else if (data.text.includes('턴')) {
          $('debateNextBtn').textContent = '▶ 다음 발언/판결 진행';
        } else {
          $('debateNextBtn').textContent = '▶ 다음 단계 진행';
        }
      }
    } else {
      $('debateNextBtn').style.display = 'none';
    }
  });

  sse.addEventListener('debate_message_done', (e) => {
    const data = JSON.parse(e.data);
    delete debateBubbles[data.sender];
    delete debateTexts[data.sender];
  });

  sse.addEventListener('done', (e) => {
    const data = JSON.parse(e.data);
    debateStreamFinished = true;
    if (debateAutoTimer) { clearTimeout(debateAutoTimer); debateAutoTimer = null; }
    sse.close();
    State.currentEventSource = null;
    State.currentStreamId = null;

    const sessIdx = State.sessions.findIndex(x => x.session_id === data.session.session_id);
    if (sessIdx !== -1) {
      State.sessions[sessIdx] = data.session;
    }

    // [2026-08-26 수정] '완료' 문자열 매칭 금지 — 대기 상태 텍스트('[N/M턴 완료] 다음 발언...')
    // 에도 '완료'가 포함되어 waiting_next 대기 중인데 종결 처리되는 버그.
    // 서버가 보낸 debate_status.completed 플래그로만 판정한다.
    if (lastDebateCompleted) {
      debateIsActive = false;
    }
  });

  sse.addEventListener('error', (e) => {
    if (debateStreamFinished || State.currentEventSource !== sse) {
      sse.close();
      return;
    }
    sse.close();
    showToast('토론/회의 스트리밍 연결이 종료되었습니다.');
  });
}

async function proceedDebateRound() {
  if (!State.activeSessionId) return;
  // 수동 클릭 시 대기 중인 자동진행 타이머를 취소해 이중 진행을 방지한다.
  if (debateAutoTimer) { clearTimeout(debateAutoTimer); debateAutoTimer = null; }
  $('debateNextBtn').style.display = 'none';
  $('debateStatusText').textContent = '다음 데이터를 생성 요청 중...';

  try {
    const res = await api('/api/debate/next', {
      method: 'POST',
      body: { session_id: State.activeSessionId }
    });

    if (!res.ok) {
      showToast('다음 단계 진행 실패: ' + (res.message || ''));
      return;
    }

    const streamId = res.stream_id;
    bindDebateStream(streamId);

  } catch (err) {
    showToast('다음 단계를 진행할 수 없습니다: ' + err.message);
  }
}

async function cancelDebateWorkflow() {
  if (!State.activeSessionId) return;
  try {
    await api('/api/debate/cancel', {
      method: 'POST',
      body: { session_id: State.activeSessionId }
    });
  } catch (e) {
    console.error('Cancel debate failed:', e);
  }

  debateIsActive = false;
  if (State.currentEventSource) {
    State.currentEventSource.close();
    State.currentEventSource = null;
  }
  State.currentStreamId = null;
  toggleDebateModeUI(false);

  // Reload session to restore stable message list
  const activeSess = State.sessions.find(x => x.session_id === State.activeSessionId);
  if (activeSess) {
    selectSession(activeSess.session_id);
  }
}


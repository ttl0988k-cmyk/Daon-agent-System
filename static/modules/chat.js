function getModelDisplayName(modelId) {
  if (!modelId) return 'Unknown';
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
    State.activeModelId = State.settings.default_model || '';
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
    openSettingsModal();
  } catch (e) {
    console.error("Init load failed:", e);
  }
}

function populateModelSelect() {
  const sel = $('modelSelect');
  const modalSel = $('settingsDefaultModel');
  if (sel) sel.innerHTML = '';
  if (modalSel) modalSel.innerHTML = '';

  State.models.forEach(g => {
    const optgroup = document.createElement('optgroup');
    optgroup.label = g.provider;

    g.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = m.label;
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
    const item = document.createElement('div');
    item.className = `session-item ${activeClass}`;
    item.dataset.sid = s.session_id;
    item.onclick = () => selectSession(s.session_id);

    item.innerHTML = `
      <div class="session-title-container">
        <span class="session-icon">💬</span>
        <span class="session-title" id="title-text-${s.session_id}">${s.title}</span>
      </div>
      <div class="session-actions">
        <button class="icon-btn edit-sess-btn" onclick="renameSessionPrompt(event, '${s.session_id}', '${s.title}')">✏</button>
        <button class="icon-btn delete-sess-btn" onclick="deleteSession(event, '${s.session_id}')">🗑</button>
      </div>
    `;
    list.appendChild(item);
  });
}

async function selectSession(sid) {
  if (State.activeSessionId === sid) return;
  State.activeSessionId = sid;

  // Render active selection
  renderSessionsList();

  // Disconnect frontend SSE only — do NOT cancel backend agent work.
  // The agent continues running in the background and saves results to the session.
  cleanupStreamState();

  try {
    const res = await api(`/api/session?session_id=${encodeURIComponent(sid)}`);
    const session = res.session;
    State.activeWorkspacePath = session.workspace;
    State.activeModelId = session.model;
    if ($('modelSelect')) $('modelSelect').value = State.activeModelId;

    // Load session mode
    loadSessionMode();

    // Clear tabs & reload file tree
    State.openTabs = [];
    State.activeTabIndex = -1;
    State.expandedDirs.clear();
    renderTabs();
    showCanvas('welcome');

    await refreshFileTree();
    renderMessages(session.messages, session.tool_calls);
  } catch (e) {
    console.error("Session load failed:", e);
    // Clean up invalid session from State and UI list
    State.sessions = State.sessions.filter(x => x.session_id !== sid);
    renderSessionsList();
    if (State.activeSessionId === sid) {
      State.activeSessionId = null;
    }
    if (State.sessions.length > 0) {
      await selectSession(State.sessions[0].session_id);
    } else {
      await createNewSession();
    }
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
  const newTitle = prompt("Enter new title for the chat:", oldTitle);
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
    showToast("Rename failed: " + e.message);
  }
}

async function deleteSession(e, sid) {
  e.stopPropagation();
  if (!confirm("Are you sure you want to delete this session?")) return;

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
    showToast("Delete failed: " + e.message);
  }
}

async function clearChatHistory() {
  if (!State.activeSessionId) return;
  if (!confirm("Clear all message history in this session?")) return;

  try {
    await api('/api/session/clear', {
      method: 'POST',
      body: { session_id: State.activeSessionId }
    });
    renderMessages([], []);
  } catch (e) {
    showToast("Clear history failed: " + e.message);
  }
}
// ── Chat Engine (SSE integration) ──
function renderMessages(messages, toolCalls) {
  const box = $('chatMessages');
  box.innerHTML = '';

  messages.forEach((msg, idx) => {
    if (!msg || !msg.role || msg.role === 'tool') return;
    const isUser = msg.role === 'user';
    const bubble = document.createElement('div');
    bubble.className = `message-bubble ${isUser ? 'user' : 'assistant'}`;

    // Style judge message differently
    if (msg.sender && msg.sender.includes('판사')) {
      bubble.style.border = '2px solid var(--accent)';
      bubble.style.background = 'rgba(233, 69, 96, 0.05)';
      bubble.style.maxWidth = '95%';
    }

    let html = isUser ? formatUserMessageContent(msg.content, State.activeSessionId) : renderMd(msg.content);
    if (msg.sender) {
      const senderHtml = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${msg.sender}</div>`;
      html = senderHtml + html;
    }
    bubble.innerHTML = html;

    // Find tool calls matching this assistant message
    if (!isUser && toolCalls) {
      const msgTools = toolCalls.filter(tc => tc.assistant_msg_idx === idx);
      msgTools.forEach(tool => {
        const card = document.createElement('div');
        card.className = 'tool-card';
        card.innerHTML = `
          <div class="tool-card-header" onclick="toggleToolCard(this)">
            <span>Tool Run: ${tool.name}</span>
            <span>▶</span>
          </div>
          <div class="tool-card-body" style="display:none;">
            <div>Arguments:</div>
            <pre style="margin-bottom:8px;">${JSON.stringify(tool.args, null, 2)}</pre>
            <div>Output Snippet:</div>
            <pre>${tool.snippet}</pre>
          </div>
        `;
        bubble.appendChild(card);
      });
    }

    box.appendChild(bubble);
  });
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
  const input = $('promptInput');
  const text = input.value.trim();

  // 1. Upload attachments first if any
  let uploaded = [];
  try {
    if (State.pendingFiles && State.pendingFiles.length > 0) {
      setChatStatus('thinking', 'Uploading files...');
      uploaded = await uploadPendingFiles();
    }
  } catch (err) {
    showToast("File upload failed: " + err.message);
    setChatStatus('idle', 'Idle');
    return;
  }

  if (!text && uploaded.length === 0) return;
  if (!State.activeSessionId) return;

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
      displayText = `Uploaded ${uploaded.length} file(s): ${uploaded.join(', ')}`;
    } else {
      displayText = `${text}\n\n[Attached files: ${uploaded.join(', ')}]`;
    }
  }
  userBubble.innerHTML = formatUserMessageContent(displayText, State.activeSessionId);
  box.appendChild(userBubble);
  scrollToChatBottom();

  // Execute agent stream directly — mode suggestions are handled by the agent via ask_followup_question / choice cards
  await _executeAgentStream(displayText, uploaded);
}

/**
 * Extracted SSE streaming logic — called after mode cards or directly.
 */
async function _executeAgentStream(displayText, uploaded) {
  // Set UI state to active
  setChatStatus('thinking', 'Thinking...');
  $('sendPromptBtn').disabled = true;
  $('cancelStreamBtn').style.display = 'block';

  // Create stream target assistant bubble
  const box = $('chatMessages');
  const asstBubble = document.createElement('div');
  asstBubble.className = 'message-bubble assistant';
  asstBubble.innerHTML = '<span class="cursor">|</span>';
  box.appendChild(asstBubble);

  let incomingText = '';

  try {
    // Initiate chat run in backend with attachments list
    const planningMode = $('planningModeToggle') ? $('planningModeToggle').checked : false;
    const startRes = await api('/api/chat/start', {
      method: 'POST',
      body: {
        session_id: State.activeSessionId,
        message: displayText,
        model: State.activeModelId,
        workspace: State.activeWorkspacePath,
        attachments: uploaded.length > 0 ? uploaded : undefined,
        planning_mode: planningMode
      }
    });

    const streamId = startRes.stream_id;
    State.currentStreamId = streamId;

    // Connect to SSE endpoint
    const sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
    State.currentEventSource = sse;

    sse.addEventListener('token', (e) => {
      const data = JSON.parse(e.data);
      incomingText += data.text;
      asstBubble.innerHTML = renderMd(incomingText);
      scrollToChatBottom();
    });

    // ── Real-time terminal output streaming ──────────────────────────────
    let _terminalOutputCard = null;
    let _terminalOutputText = '';

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
        asstBubble.appendChild(_terminalOutputCard);
      }

      var outputPre = _terminalOutputCard.querySelector('.terminal-live-output');
      if (outputPre) {
        outputPre.textContent = _terminalOutputText;
      }
      scrollToChatBottom();
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
          nextBtn.style.display = 'block';
        } else {
          nextBtn.style.display = 'none';
        }
      }
    });

    sse.addEventListener('debate_message_done', (e) => {
      const data = JSON.parse(e.data);
      // clean mapping for next round if same sender speaks again
      delete debateBubbles[data.sender];
      delete debateTexts[data.sender];
    });

    sse.addEventListener('tool', (e) => {
      const data = JSON.parse(e.data);
      const toolName = data.name || 'unknown';
      const toolEvent = data.event || 'tool.started';
      const isStarted = toolEvent === 'tool.started';
      setChatStatus('tool', `${isStarted ? 'Running' : 'Completed'} tool: ${toolName}...`);
      logToConsole(`${isStarted ? 'Running' : 'Completed'} tool: ${toolName}...`, 'info');

      // ── ask_followup_question: render choice cards inline ──
      if (toolName === 'ask_followup_question' && isStarted && data.args) {
        const question = data.args.question || '';
        const choices = data.args.follow_up || data.args.options || [];
        if (question && Array.isArray(choices) && choices.length > 0 && typeof showChoiceCard === 'function') {
          // Convert choice objects [{text, mode}] to the format showChoiceCard expects
          const mappedChoices = choices.map(function (c) {
            if (typeof c === 'string') return { text: c, mode: '' };
            return { text: c.text || c.label || String(c), mode: c.mode || '' };
          });
          showChoiceCard(question, mappedChoices, box);
        }
      }

      // Inline tool card showing tool run progress
      const card = document.createElement('div');
      card.className = 'tool-card';
      card.innerHTML = `
        <div class="tool-card-header" onclick="toggleToolCard(this)">
          <span>Tool ${isStarted ? 'Running' : 'Completed'}: ${toolName}</span>
          <span>▶</span>
        </div>
        <div class="tool-card-body" style="display:none;">
          <pre>${data.preview || ''}</pre>
        </div>
      `;
      asstBubble.appendChild(card);
      scrollToChatBottom();
    });

    // Monaco Editor UX를 위한 파일 편집 이벤트 리스너
    sse.addEventListener('file_edit', async (e) => {
      const data = JSON.parse(e.data);
      const filePath = data.args?.path;
      console.log('[MonacoEditorUX] Received file_edit event:', data.name, filePath);

      if (data.name === 'write_file' || data.name === 'patch') {
        try {
          // 1. 파일을 에디터 탭에 먼저 열기 (없으면 열고, 있으면 전환)
          const existingIdx = State.openTabs.findIndex(t => t.path === filePath);
          if (existingIdx !== -1) {
            switchTab(existingIdx);
          } else if (typeof openFileInTab === 'function') {
            await openFileInTab(filePath);
          }

          // 2. Monaco Editor UX로 내용 적용 (탭이 열린 후)
          if (window.MonacoEditorUX && window.MonacoEditorUX.applyAIResponse) {
            // 탭이 열리는 동안 잠시 대기
            await new Promise(r => setTimeout(r, 200));
            MonacoEditorUX.applyAIResponse({
              provider: 'hermes',
              content: data.args.content || '',
              action: 'edit',
              path: filePath
            });
          }

          // 3. 파일 트리 갱신
          if (typeof refreshFileTree === 'function') {
            refreshFileTree().catch(() => { });
          }
        } catch (err) {
          console.error('[MonacoEditorUX] Error handling file_edit:', err);
        }
      }
    });

    // ── Diff Preview SSE (AI → Preview Panel)
    sse.addEventListener('diff_preview', (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('[Streaming→DiffPreview] Received diff_preview event:', data.path, data.preview_id);
        if (typeof previewAIDiff === 'function') {
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

    // ── Approval SSE (Architect mode approval required)
    sse.addEventListener('approval', (e) => {
      try {
        const data = JSON.parse(e.data);
        console.log('[Streaming→Approval] Received approval event:', data.status, data.path);
        if (typeof _showApprovalBanner === 'function') {
          _showApprovalBanner(data);
        }
      } catch (err) {
        console.error('[Streaming→Approval] Error handling approval:', err);
      }
    });

    sse.addEventListener('model_info', (e) => {
      const data = JSON.parse(e.data);
      State._lastModelInfo = data;
    });

    sse.addEventListener('model_fallback', (e) => {
      const data = JSON.parse(e.data);
      logToConsole(data.message || 'Model fallback occurred.', 'warning');
    });

    sse.addEventListener('done', (e) => {
      const data = JSON.parse(e.data);
      sse.close();
      cleanupStreamState();
      logToConsole('Agent run completed successfully.', 'success');

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
      const activeTab = getActiveTab();
      if (activeTab) {
        // Simple tab reload logic
        const tabIdx = State.activeTabIndex;
        State.openTabs = State.openTabs.filter((_, i) => i !== tabIdx);
        openFileInTab(activeTab.path);
      }
    });

    sse.addEventListener('cancel', () => {
      sse.close();
      cleanupStreamState();
      logToConsole('Agent run cancelled by user.', 'warning');
      asstBubble.insertAdjacentHTML('beforeend', '<div class="text-danger" style="margin-top:8px;">[Run Cancelled]</div>');
    });

    sse.addEventListener('error', (e) => {
      sse.close();
      cleanupStreamState();
      logToConsole('Agent run stream error occurred.', 'error');
      asstBubble.insertAdjacentHTML('beforeend', '<div class="text-danger" style="margin-top:8px;">[Stream error occurred]</div>');
    });

    sse.addEventListener('apperror', (e) => {
      const data = JSON.parse(e.data);
      sse.close();
      cleanupStreamState();
      logToConsole(`Agent run error: ${data.message}`, 'error');
      asstBubble.insertAdjacentHTML('beforeend', `<div class="text-danger" style="margin-top:8px;">[Error: ${data.message}]</div>`);
    });

  } catch (err) {
    cleanupStreamState();
    asstBubble.innerHTML = `<div class="text-danger">[Failed to start run: ${err.message}]</div>`;
  }
}

async function cancelActiveStream() {
  if (!State.currentStreamId) return;
  try {
    await api('/api/chat/cancel', {
      method: 'POST',
      body: { stream_id: State.currentStreamId }
    });
  } catch (e) {
    console.error("Cancel failed:", e);
  }
  cleanupStreamState();
}

function cleanupStreamState() {
  if (State.currentEventSource) {
    State.currentEventSource.close();
    State.currentEventSource = null;
  }
  State.currentStreamId = null;
  setChatStatus('idle', 'Idle');
  $('sendPromptBtn').disabled = false;
  $('cancelStreamBtn').style.display = 'none';
}

function setChatStatus(status, text) {
  const ind = $('statusIndicator');
  ind.className = `status-indicator ${status}`;
  $('statusText').textContent = text;
}
// ── Modals Setup ──
function openSettingsModal() {
  $('settingsDefaultModel').value = State.settings.default_model || '';
  $('settingsShowCli').checked = State.settings.show_cli_sessions || false;
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
  try {
    State.settings = await api('/api/settings', {
      method: 'POST',
      body: { default_model: model, show_cli_sessions: showCli }
    });
    closeSettingsModal();
    showToast("Settings saved successfully.");
  } catch (e) {
    showToast("Save settings failed: " + e.message);
  }
}
async function handleModelChange(newModelId) {
  State.activeModelId = newModelId;
  if ($('modelSelect')) $('modelSelect').value = newModelId;

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
    showToast("Failed to switch profile: " + e.message);
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
  State.models.forEach(g => {
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
}

// ── ⚖️ 전문가 토론 모드 프론트엔드 연동 ──

let debateIsActive = false;

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
        const normalMessages = activeSess.messages.filter(msg => !msg.sender);
        renderMessages(normalMessages, activeSess.tool_calls);
      }
    }
  }
}

function populateDebateModels() {
  const container = $('debateModelCheckboxes');
  if (!container) return;
  container.innerHTML = '';

  const flatModels = [];
  State.models.forEach(g => {
    if (g.models && Array.isArray(g.models)) {
      flatModels.push(...g.models);
    }
  });

  if (flatModels.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:4px;">사용 가능한 모델이 없습니다.</div>';
    return;
  }

  flatModels.forEach(m => {
    const label = document.createElement('label');
    label.style = "display:flex; align-items:center; gap:4px; font-size:11px; color:var(--text); cursor:pointer; margin-right:8px; margin-bottom:4px; user-select:none;";

    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = m.id;
    input.className = 'debate-model-checkbox';
    input.style = "cursor:pointer;";

    // Auto-check common models for convenience
    const idLower = m.id.toLowerCase();
    if (idLower.includes('deepseek-v3') || idLower.includes('claude-3.5-sonnet') || idLower.includes('gpt-4o-mini')) {
      input.checked = true;
    }

    label.appendChild(input);
    label.appendChild(document.createTextNode(m.label));
    container.appendChild(label);
  });
}

async function startDebateWorkflow() {
  const topicInput = $('debateTopicInput');
  const topic = topicInput ? topicInput.value.trim() : '';
  if (!topic) {
    showToast('토론 주제를 입력해 주세요.');
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

  // UI state change to active
  debateIsActive = true;
  $('debateSetupArea').style.display = 'none';
  $('debateControlArea').style.display = 'flex';
  $('debateStatusText').textContent = '토론 준비 중...';
  $('debateNextBtn').style.display = 'none';

  // Target debateMessages instead of chatMessages and clear previous debate
  const box = $('debateMessages');
  if (box) box.innerHTML = '';

  const userBubble = document.createElement('div');
  userBubble.className = 'message-bubble user';
  userBubble.innerHTML = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">⚖️ 토론 시작</div><strong>주제:</strong> ${topic}`;
  box.appendChild(userBubble);
  scrollToChatBottom();

  try {
    const res = await api('/api/debate/start', {
      method: 'POST',
      body: {
        session_id: State.activeSessionId,
        topic: topic,
        models: selectedModels
      }
    });

    if (!res.ok) {
      showToast('토론 시작 실패: ' + (res.message || ''));
      cancelDebateWorkflow();
      return;
    }

    const streamId = res.stream_id;
    State.currentStreamId = streamId;

    // Connect to SSE stream
    const sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
    State.currentEventSource = sse;

    // Re-bind token, debate_token, and other listeners dynamically to this stream
    let debateBubbles = {};
    let debateTexts = {};

    sse.addEventListener('debate_token', (e) => {
      const data = JSON.parse(e.data);
      const sender = data.sender;
      const text = data.text;

      if (!debateBubbles[sender]) {
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant';
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
      const badge = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${sender}</div>`;
      debateBubbles[sender].innerHTML = badge + renderMd(debateTexts[sender]);
      scrollToChatBottom();
    });

    sse.addEventListener('debate_status', (e) => {
      const data = JSON.parse(e.data);
      $('debateStatusText').textContent = data.text;
      if (data.waiting_next) {
        $('debateNextBtn').style.display = 'block';
        if (data.text.includes('1라운드')) {
          $('debateNextBtn').textContent = '▶ 2라운드(반박) 진행';
        } else if (data.text.includes('2라운드')) {
          $('debateNextBtn').textContent = '⚖️ 최종 판결 요청';
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
      sse.close();
      State.currentEventSource = null;
      State.currentStreamId = null;

      // Update session data in State but do NOT render in chatMessages
      const sessIdx = State.sessions.findIndex(x => x.session_id === data.session.session_id);
      if (sessIdx !== -1) {
        State.sessions[sessIdx] = data.session;
      }

      // If completed all rounds
      if ($('debateStatusText').textContent.includes('판결 완료')) {
        debateIsActive = false;
        // Keep the debate window visible so they can review the judge outcome.
        // The user can exit manually by clicking "일반대화" button.
      }
    });

    sse.addEventListener('error', (e) => {
      sse.close();
      showToast('토론 스트리밍 에러가 발생했습니다.');
      cancelDebateWorkflow();
    });

  } catch (err) {
    showToast('토론을 시작할 수 없습니다: ' + err.message);
    cancelDebateWorkflow();
  }
}

async function proceedDebateRound() {
  if (!State.activeSessionId) return;
  $('debateNextBtn').style.display = 'none';
  $('debateStatusText').textContent = '다음 라운드 데이터를 요청 중...';

  try {
    const res = await api('/api/debate/next', {
      method: 'POST',
      body: { session_id: State.activeSessionId }
    });

    if (!res.ok) {
      showToast('다음 라운드 시작 실패: ' + (res.message || ''));
      return;
    }

    const streamId = res.stream_id;
    State.currentStreamId = streamId;

    // Connect to SSE stream
    const sse = new EventSource(`/api/chat/stream?stream_id=${streamId}`);
    State.currentEventSource = sse;

    const box = $('debateMessages');
    let debateBubbles = {};
    let debateTexts = {};

    sse.addEventListener('debate_token', (e) => {
      const data = JSON.parse(e.data);
      const sender = data.sender;
      const text = data.text;

      if (!debateBubbles[sender]) {
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant';
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
      const badge = `<div class="model-attribution" style="margin-bottom: 6px; font-weight: bold; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 4px;">${sender}</div>`;
      debateBubbles[sender].innerHTML = badge + renderMd(debateTexts[sender]);
      scrollToChatBottom();
    });

    sse.addEventListener('debate_status', (e) => {
      const data = JSON.parse(e.data);
      $('debateStatusText').textContent = data.text;
      if (data.waiting_next) {
        $('debateNextBtn').style.display = 'block';
        if (data.text.includes('1라운드')) {
          $('debateNextBtn').textContent = '▶ 2라운드(반박) 진행';
        } else if (data.text.includes('2라운드')) {
          $('debateNextBtn').textContent = '⚖️ 최종 판결 요청';
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
      sse.close();
      State.currentEventSource = null;
      State.currentStreamId = null;

      const sessIdx = State.sessions.findIndex(x => x.session_id === data.session.session_id);
      if (sessIdx !== -1) {
        State.sessions[sessIdx] = data.session;
      }

      if ($('debateStatusText').textContent.includes('판결 완료')) {
        debateIsActive = false;
      }
    });

    sse.addEventListener('error', (e) => {
      sse.close();
      showToast('토론 스트리밍 에러가 발생했습니다.');
      cancelDebateWorkflow();
    });

  } catch (err) {
    showToast('다음 라운드를 진행할 수 없습니다: ' + err.message);
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


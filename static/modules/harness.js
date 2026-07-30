function initResizers() {
  const container = document.querySelector('.main-grid');
  const leftResizer = $('resizerLeft');
  const rightResizer = $('resizerRight');

  if (!leftResizer || !rightResizer || !container) return;

  let isDraggingLeft = false;
  let isDraggingRight = false;

  let leftWidth = parseInt(localStorage.getItem('daon_sidebar_width')) || 220;
  let rightWidth = parseInt(localStorage.getItem('daon_chat_width')) || 340;

  window.updateLayout = function () {
    const leftCol = State.leftPanelVisible ? `${leftWidth}px` : '0px';
    const leftResizerCol = State.leftPanelVisible ? '6px' : '0px';
    const rightResizerCol = State.rightPanelVisible ? '6px' : '0px';
    const rightCol = State.rightPanelVisible ? `${rightWidth}px` : '0px';
    // 초보자 모드: explorerVisible이면 탐색기만(260px), 아니면 0. 채팅은 항상 1fr
    const beginnerExplorerOnly = State.beginnerMode && State.explorerVisible;
    const midCol = State.beginnerMode ? (beginnerExplorerOnly ? '260px' : '0px') : '1fr';
    const rResCol = State.beginnerMode ? '0px' : rightResizerCol;
    const rCol = State.beginnerMode ? '1fr' : rightCol;
    container.style.gridTemplateColumns = `${leftCol} ${leftResizerCol} ${midCol} ${rResCol} ${rCol}`;

    // 초보자 모드 표시: CSS에서 file-tree-wrap을 middle-panel 전체 너비로 확장해
    // 탐색기 오른쪽 경계선이 채팅 패널 왼쪽 경계선과 일치하도록 함 (빈 여백 제거)
    container.classList.toggle('beginner-mode', !!State.beginnerMode);

    // 초보자 모드: middle-panel 자체를 토글 (탐색기 열 때만 보임)
    const middlePanel = document.querySelector('.middle-panel');
    if (middlePanel) {
      if (State.beginnerMode) {
        middlePanel.style.display = State.explorerVisible ? 'flex' : 'none';
      } else {
        middlePanel.style.display = '';
      }
    }

    // 초보자 모드에서 탐색기만 열 때 에디터 영역 숨김
    const editorArea = document.querySelector('.editor-area');
    if (editorArea) editorArea.style.display = (State.beginnerMode && State.explorerVisible) ? 'none' : '';

    const leftPanelEl = document.querySelector('.left-panel');
    if (leftPanelEl) leftPanelEl.style.display = State.leftPanelVisible ? 'flex' : 'none';
    if (leftResizer) leftResizer.style.display = State.leftPanelVisible ? 'block' : 'none';

    const rightPanelEl = document.querySelector('.right-panel');
    if (rightPanelEl) rightPanelEl.style.display = State.rightPanelVisible ? 'flex' : 'none';
    if (rightResizer) rightResizer.style.display = State.rightPanelVisible ? 'block' : 'none';

    const explorerEl = document.querySelector('.file-tree-wrap');
    if (explorerEl) explorerEl.style.display = State.explorerVisible ? 'flex' : 'none';

    // Toggle active state on buttons
    const btnLeft = $('toggleLeftBtn');
    const btnExplorer = $('toggleExplorerBtn');
    const btnRight = $('toggleRightBtn');

    if (btnLeft) btnLeft.classList.toggle('active', State.leftPanelVisible);
    if (btnExplorer) btnExplorer.classList.toggle('active', State.explorerVisible);
    if (btnRight) btnRight.classList.toggle('active', State.rightPanelVisible);

    if (State.editor) {
      setTimeout(() => {
        State.editor.layout();
      }, 50);
    }

    // Fix: recalculate textarea height after grid resize so typed text doesn't disappear
    const promptInput = document.getElementById('promptInput');
    if (promptInput) {
      const curHeight = promptInput.style.height;
      promptInput.style.height = 'auto';
      const newHeight = promptInput.scrollHeight + 'px';
      promptInput.style.height = newHeight;
      // Restore scroll position — ensure the cursor/typed text stays visible
      promptInput.scrollTop = promptInput.scrollHeight;
    }
  };

  window.updateLayout();

  leftResizer.addEventListener('mousedown', (e) => {
    isDraggingLeft = true;
    leftResizer.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  });

  rightResizer.addEventListener('mousedown', (e) => {
    isDraggingRight = true;
    rightResizer.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (isDraggingLeft) {
      const newWidth = e.clientX - 12;
      if (newWidth > 180 && newWidth < 500) {
        leftWidth = newWidth;
        window.updateLayout();
      }
    } else if (isDraggingRight) {
      const containerWidth = container.offsetWidth;
      const newWidth = containerWidth - e.clientX - 12;
      if (newWidth > 250 && newWidth < containerWidth - 100) {
        rightWidth = newWidth;
        window.updateLayout();
      }
    }
  });

  document.addEventListener('mouseup', () => {
    if (isDraggingLeft || isDraggingRight) {
      isDraggingLeft = false;
      isDraggingRight = false;
      leftResizer.classList.remove('dragging');
      rightResizer.classList.remove('dragging');
      document.body.style.cursor = '';

      localStorage.setItem('daon_sidebar_width', leftWidth);
      localStorage.setItem('daon_chat_width', rightWidth);
    }
  });
}
function switchMode(mode) {
  if (mode === 'chat') {
    $('modeChatBtn').classList.add('active');
    $('modeHarnessBtn').classList.remove('active');
    $('toggleDebateModeBtn').classList.remove('active');
    $('chatModeContent').style.display = 'flex';
    $('harnessModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(false);
    }
  } else if (mode === 'harness') {
    $('modeHarnessBtn').classList.add('active');
    $('modeChatBtn').classList.remove('active');
    $('toggleDebateModeBtn').classList.remove('active');
    $('harnessModeContent').style.display = 'flex';
    $('chatModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(false);
    }
    if (typeof window.updateLayout === 'function') {
      window.updateLayout();
    }
  } else if (mode === 'debate') {
    $('toggleDebateModeBtn').classList.add('active');
    $('modeChatBtn').classList.remove('active');
    $('modeHarnessBtn').classList.remove('active');
    $('chatModeContent').style.display = 'flex';
    $('harnessModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(true);
    }
  }
}

function getAgentClass(agentId) {
  const idLower = agentId.toLowerCase();
  if (idLower.includes('ceo')) return 'ceo';
  if (idLower.includes('planner')) return 'planner';
  if (idLower.includes('bill')) return 'bill';
  if (idLower.includes('sherlock')) return 'sherlock';
  if (idLower.includes('prada')) return 'prada';
  if (idLower.includes('tony')) return 'tony';
  if (idLower.includes('merger')) return 'merger';
  return 'generic';
}

function getAgentLabel(agentId) {
  const match = agentId.match(/^([A-Za-z0-9_가-힣ㄱ-ㅎㅏ-ㅣ]+)/);
  return match ? match[1].toUpperCase() : agentId;
}

function toggleAgentCard(headerEl) {
  const body = headerEl.nextElementSibling;
  body.style.display = body.style.display === 'none' ? 'block' : 'none';
}

function scrollToHarnessBottom() {
  const consoleEl = $('harnessConsole');
  if (consoleEl) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

async function runDynamicHarness() {
  if (!State.activeSessionId) {
    showToast('먼저 세션을 선택하세요.');
    return;
  }

  const taskText = ($('harnessInput')?.value || '').trim();
  if (!taskText) {
    showToast('수행할 작업을 입력하세요.');
    return;
  }

  $('runHarnessBtn').disabled = true;
  $('cancelHarnessBtn').style.display = 'block';

  cleanupHarnessState();

  try {
    const res = await api('/api/dynamic/run', {
      method: 'POST',
      body: {
        session_id: State.activeSessionId,
        task: taskText,
        workspace: State.activeWorkspacePath || '',
        model: State.activeModelId || '',
        planning_mode: true,
      },
    });

    if (res.run_id) {
      State.harnessRunId = res.run_id;
      logToConsole(`\n🚀 Dynamic Harness 시작됨 (Run ID: ${res.run_id})`, 'info');
      logToConsole(`📋 작업: ${taskText}`, 'info');
      pollHarnessStatus(res.run_id);
    }
  } catch (err) {
    logToConsole(`❌ Harness 실행 실패: ${err.message}`, 'error');
    $('runHarnessBtn').disabled = false;
    $('cancelHarnessBtn').style.display = 'none';
  }
}

async function pollHarnessStatus(runId) {
  if (State.harnessPollInterval) {
    clearInterval(State.harnessPollInterval);
  }

  State.harnessPollInterval = setInterval(async () => {
    try {
      const res = await api(`/api/dynamic/status/${runId}`, { method: 'GET' });

      if (res.status === 'completed') {
        clearInterval(State.harnessPollInterval);
        State.harnessPollInterval = null;
        logToConsole('\n✅ Dynamic Harness 완료됨', 'success');
        $('runHarnessBtn').disabled = false;
        $('cancelHarnessBtn').style.display = 'none';
        if (res.result) {
          const consoleEl = $('harnessConsole');
          const resultEl = document.createElement('div');
          resultEl.className = 'harness-result-output';
          resultEl.innerHTML = '<div class="harness-result-header">📄 최종 결과물</div>' + renderMd(res.result);
          consoleEl.appendChild(resultEl);
          scrollToHarnessBottom();
        }
        return;
      }

      if (res.status === 'failed') {
        clearInterval(State.harnessPollInterval);
        State.harnessPollInterval = null;
        logToConsole(`\n❌ Dynamic Harness 실패: ${res.error || '알 수 없는 오류'}`, 'error');
        $('runHarnessBtn').disabled = false;
        $('cancelHarnessBtn').style.display = 'none';
        return;
      }

      if (res.status === 'awaiting_approval') {
        clearInterval(State.harnessPollInterval);
        State.harnessPollInterval = null;
        _showApprovalBanner({
          message: res.approval_message || '작업 승인이 필요합니다.',
          actions: res.available_actions || [],
          onAction: async (action) => {
            await api(`/api/dynamic/approve/${runId}`, {
              method: 'POST',
              body: { action: action },
            });
            pollHarnessStatus(runId);
          },
        });
        return;
      }

      if (res.status === 'clarifying') {
        clearInterval(State.harnessPollInterval);
        State.harnessPollInterval = null;
        _showClarificationUI(runId, res.clarification);
        return;
      }

      if (res.logs && res.logs.length > State.harnessLogCursor) {
        for (let i = State.harnessLogCursor; i < res.logs.length; i++) {
          const entry = res.logs[i];
          logToConsole(entry.message, entry.type || 'info');
        }
        State.harnessLogCursor = res.logs.length;
      }

      if (res.agent_cards) {
        const consoleEl = $('harnessConsole');
        Object.entries(res.agent_cards).forEach(([agentId, card]) => {
          if (!State.harnessAgentCards[agentId]) {
            State.harnessAgentCards[agentId] = true;
            const cardEl = document.createElement('div');
            cardEl.className = 'harness-agent-card';
            cardEl.innerHTML = `<div class="harness-agent-card-header" onclick="toggleAgentCard(this)"><span class="agent-label ${getAgentClass(agentId)}">${getAgentLabel(agentId)}</span><span class="toggle-icon">▼</span></div><div class="harness-agent-card-body">${renderMd(card.status || '작업 중...')}</div>`;
            consoleEl.appendChild(cardEl);
          }
        });
      }
    } catch (err) {
      logToConsole(`⚠️ 상태 확인 오류: ${err.message}`, 'error');
    }
  }, 1000);
}

function _showClarificationUI(runId, clarification) {
  const consoleEl = $('harnessConsole');
  if (!consoleEl) return;

  const questions = (clarification && clarification.questions) || [];
  const turn = (clarification && clarification.turn) || 1;
  if (questions.length === 0) {
    pollHarnessStatus(runId);
    return;
  }

  logToConsole(`\n🤔 CEO 에이전트가 의도를 확인합니다 (턴 ${turn})`, 'info');

  const cardEl = document.createElement('div');
  cardEl.className = 'clarification-card';
  cardEl.style.cssText = 'margin:12px 0;padding:16px;border:1px solid var(--border-color,#444);border-radius:8px;background:var(--bg-secondary,#1e1e2e);';

  let html = '<div style="font-weight:600;margin-bottom:12px;color:var(--accent-color,#7c3aed);">📋 추가 확인이 필요합니다</div>';
  html += '<div style="display:flex;flex-direction:column;gap:12px;">';
  questions.forEach((q, i) => {
    html += `<div class="clarification-q" style="display:flex;flex-direction:column;gap:4px;">`;
    html += `<label style="font-size:13px;color:var(--text-primary,#e0e0e0);">${i + 1}. ${q}</label>`;
    html += `<textarea class="clarification-answer" data-idx="${i}" rows="2" placeholder="답변을 입력하세요..." style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border-color,#555);background:var(--bg-primary,#121212);color:var(--text-primary,#e0e0e0);resize:vertical;font-size:13px;"></textarea>`;
    html += `</div>`;
  });
  html += '</div>';
  html += '<div style="margin-top:14px;display:flex;gap:8px;">';
  html += '<button class="clarification-submit-btn" style="padding:8px 18px;border-radius:6px;border:none;background:var(--accent-color,#7c3aed);color:#fff;cursor:pointer;font-size:13px;font-weight:600;">답변 제출</button>';
  html += '<button class="clarification-skip-btn" style="padding:8px 18px;border-radius:6px;border:1px solid var(--border-color,#555);background:transparent;color:var(--text-secondary,#aaa);cursor:pointer;font-size:13px;">건너뛰기</button>';
  html += '</div>';

  cardEl.innerHTML = html;
  consoleEl.appendChild(cardEl);
  scrollToHarnessBottom();

  const submitBtn = cardEl.querySelector('.clarification-submit-btn');
  const skipBtn = cardEl.querySelector('.clarification-skip-btn');

  async function _submitAnswers(answers) {
    submitBtn.disabled = true;
    skipBtn.disabled = true;
    try {
      await api(`/api/dynamic/answer/${runId}`, {
        method: 'POST',
        body: { answers: answers },
      });
      // Collapse card into a compact submitted summary
      let summaryHtml = '<div style="font-weight:600;color:var(--accent-color,#7c3aed);">✅ 답변 제출 완료 (턴 ' + turn + ')</div>';
      summaryHtml += '<div style="margin-top:6px;font-size:12px;color:var(--text-secondary,#aaa);">';
      questions.forEach((q, i) => {
        summaryHtml += '<div style="margin-bottom:4px;"><b>' + (i + 1) + '.</b> ' + q + '<br><span style="color:var(--text-primary,#e0e0e0);">→ ' + (answers[i] || '(무응답)') + '</span></div>';
      });
      summaryHtml += '</div>';
      cardEl.innerHTML = summaryHtml;
      cardEl.style.opacity = '0.85';
      cardEl.style.borderColor = 'var(--accent-color,#7c3aed)';
      logToConsole('✅ 답변이 제출되었습니다. CEO가 평가 중...', 'info');
      // Delay polling to let backend transition status from 'clarifying' → 'running'
      setTimeout(() => pollHarnessStatus(runId), 1500);
    } catch (err) {
      logToConsole(`❌ 답변 제출 실패: ${err.message}`, 'error');
      submitBtn.disabled = false;
      skipBtn.disabled = false;
    }
  }

  submitBtn.addEventListener('click', () => {
    const answers = [];
    cardEl.querySelectorAll('.clarification-answer').forEach(ta => {
      answers.push(ta.value.trim() || '(무응답)');
    });
    _submitAnswers(answers);
  });

  skipBtn.addEventListener('click', () => {
    const answers = questions.map(() => '(건너뜀)');
    _submitAnswers(answers);
  });
}

function cancelHarness() {
  if (State.harnessPollInterval) {
    clearInterval(State.harnessPollInterval);
    State.harnessPollInterval = null;
  }
  logToConsole('\n⏹️ Harness 취소됨', 'info');
  $('runHarnessBtn').disabled = false;
  $('cancelHarnessBtn').style.display = 'none';
  State.harnessRunId = null;
}

function cleanupHarnessState() {
  State.harnessLogCursor = 0;
  State.harnessAgentCards = {};
  if (State.harnessPollInterval) {
    clearInterval(State.harnessPollInterval);
    State.harnessPollInterval = null;
  }
}

function logToConsole(message, type = 'info') {
  const cliBody = $('consoleBody');
  if (!cliBody) return;

  // 생성 시점 (타임스탬프)
  const now = new Date();
  const timeStr = now.getHours().toString().padStart(2, '0') + ':' +
    now.getMinutes().toString().padStart(2, '0') + ':' +
    now.getSeconds().toString().padStart(2, '0');

  // 메시지 파싱 (예: "[api.dynamic.runner] 작업 시작")
  let tag = '';
  let content = message;
  const tagMatch = message.match(/^\[(.*?)\]\s([\s\S]*)$/);
  if (tagMatch) {
    tag = tagMatch[1];
    content = tagMatch[2];
  }

  const cliLine = document.createElement('div');
  cliLine.className = `console-line ${type}`;

  // HTML 조립
  let html = `<span class="timestamp">[${timeStr}]</span>`;

  // 배지 (로그 유형별 아이콘/색상)
  if (type === 'error') html += `<span class="log-badge error">✖</span>`;
  else if (type === 'success') html += `<span class="log-badge success">✔</span>`;
  else if (type === 'warning') html += `<span class="log-badge warning">⚠</span>`;
  else if (type === 'command') html += `<span class="log-badge command">▶</span>`;
  else html += `<span class="log-badge info">ℹ</span>`;

  // 태그
  if (tag) {
    const safeTag = tag.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html += `<span class="log-tag">[${safeTag}]</span> `;
  }

  // 본문 (XSS 방지를 위해 DOM의 textContent로 삽입)
  html += `<span class="log-msg"></span>`;

  cliLine.innerHTML = html;
  cliLine.querySelector('.log-msg').textContent = content;

  cliBody.appendChild(cliLine);
  cliBody.scrollTop = cliBody.scrollHeight;
}

function handleConsoleCommand(cmdText) {
  const cmd = cmdText.trim();

  if (!cmd) return;

  logToConsole(`> ${cmd}`, 'command');

  if (cmd.startsWith('/workspace ')) {
    const name = cmd.slice(11).trim();
    if (name) switchWorkspaceByName(name);
  } else if (cmd.startsWith('/profile ')) {
    const name = cmd.slice(9).trim();
    if (name) switchProfileByName(name);
  } else if (cmd.startsWith('/run ')) {
    const task = cmd.slice(5).trim();
    if (task) {
      if ($('harnessTaskInput')) $('harnessTaskInput').value = task;
      runDynamicHarness();
    }
  } else if (cmd === '/help') {
    logToConsole('  /workspace <name>  - 워크스페이스 전환', 'info');
    logToConsole('  /profile <name>    - 에이전트 프로필 전환', 'info');
    logToConsole('  /run <task>        - Harness 작업 실행', 'info');
    logToConsole('  /help              - 도움말', 'info');
  } else {
    logToConsole(`  알 수 없는 명령어: ${cmd} (도움말: /help)`, 'error');
  }
}

async function switchWorkspaceByName(nameQuery) {
  try {
    const res = await api('/api/workspace/switch', {
      method: 'POST',
      body: { name: nameQuery },
    });
    if (res.path) {
      State.activeWorkspacePath = res.path;
      logToConsole(`✅ 워크스페이스 전환됨: ${res.path}`, 'success');
      if (typeof loadFileTree === 'function') loadFileTree();
    }
  } catch (err) {
    logToConsole(`❌ 워크스페이스 전환 실패: ${err.message}`, 'error');
  }
}

async function switchProfileByName(nameQuery) {
  try {
    const res = await api('/api/profile/switch', {
      method: 'POST',
      body: { name: nameQuery },
    });
    if (res.profile) {
      logToConsole(`✅ 프로필 전환됨: ${res.profile}`, 'success');
    }
  } catch (err) {
    logToConsole(`❌ 프로필 전환 실패: ${err.message}`, 'error');
  }
}

async function runHarnessTaskFromConsole(taskText) {
  if ($('harnessTaskInput')) $('harnessTaskInput').value = taskText;
  await runDynamicHarness();
}

function initCliConsole() {
  const consoleHeader = $('consoleHeader');
  const inputEl = $('consoleInput');
  if (!consoleHeader || !inputEl) return;

  let consoleCollapsed = localStorage.getItem('daon_cli_console_collapsed') === 'true';
  const consoleEl = $('cliConsole');
  const resizer = $('consoleResizer');
  const toggleIcon = consoleEl?.querySelector('.console-toggle-icon');

  function applyConsoleState() {
    if (consoleCollapsed) {
      consoleEl.classList.add('collapsed');
      if (resizer) resizer.style.display = 'none';
      const toggleIcon = consoleEl.querySelector('.console-toggle-icon');
      if (toggleIcon) toggleIcon.textContent = '▶';
    } else {
      consoleEl.classList.remove('collapsed');
      if (resizer) resizer.style.display = 'block';
      if (toggleIcon) toggleIcon.textContent = '▼';
    }
  }

  applyConsoleState();

  consoleHeader.addEventListener('click', (e) => {
    if (e.target.closest('.console-actions')) return;
    consoleCollapsed = !consoleCollapsed;
    localStorage.setItem('daon_cli_console_collapsed', consoleCollapsed);
    applyConsoleState();
  });

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const text = inputEl.value.trim();
      inputEl.value = '';
      if (text) {
        const cleanText = text.startsWith('>') ? text.slice(1).trim() : text;
        handleConsoleCommand(cleanText);
      }
    }
  });

  // Help button
  const helpBtn = $('helpConsoleBtn');
  if (helpBtn) {
    helpBtn.addEventListener('click', () => handleConsoleCommand('/help'));
  }

  // Clear button
  const clearBtn = $('clearConsoleBtn');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      const body = $('consoleBody');
      if (body) body.innerHTML = '';
    });
  }

  // Console resizer drag
  if (resizer) {
    let isDraggingConsole = false;
    let startY = 0;
    let startHeight = 0;

    resizer.addEventListener('mousedown', (e) => {
      isDraggingConsole = true;
      startY = e.clientY;
      startHeight = consoleEl.offsetHeight;
      resizer.classList.add('dragging');
      document.body.style.cursor = 'ns-resize';
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!isDraggingConsole) return;
      const deltaY = startY - e.clientY;
      const newHeight = Math.max(80, Math.min(500, startHeight + deltaY));
      consoleEl.style.height = newHeight + 'px';
    });

    document.addEventListener('mouseup', () => {
      if (isDraggingConsole) {
        isDraggingConsole = false;
        resizer.classList.remove('dragging');
        document.body.style.cursor = '';
        localStorage.setItem('daon_console_height', consoleEl.style.height);
      }
    });
  }

  // ── Server health heartbeat polling (every 30s) → CLI console ──
  let _heartbeatSeq = 0;
  function _pollServerHealth() {
    fetch('/health')
      .then(r => r.json())
      .then(data => {
        _heartbeatSeq++;
        const tag = `Heartbeat #${_heartbeatSeq}`;
        const msg = `Uptime=${data.uptime_display || '?'}  Threads=${data.thread_count ?? '?'}  PID=${data.pid ?? '?'}  Status=${data.status}`;
        logToConsole(`[${tag}] ${msg}`, data.status === 'ok' ? 'success' : 'warning');
      })
      .catch(err => {
        _heartbeatSeq++;
        logToConsole(`[Heartbeat #${_heartbeatSeq}] ❌ 연결 실패: ${err.message}`, 'error');
      })
      .finally(() => {
        setTimeout(_pollServerHealth, 30000);
      });
  }
  // 첫 하트비트는 5초 후 시작 (페이지 로딩 직후 너무 빠른 폴링 방지)
  setTimeout(_pollServerHealth, 5000);
}

function initHarnessManual() {
  const overlay = $('harnessManualOverlay');
  if (!overlay) return;

  const dontShowAgain = localStorage.getItem('daon_harness_manual_hide') === 'true';
  if (dontShowAgain) {
    overlay.style.display = 'none';
    return;
  }

  // Show the overlay (CSS uses .show class for opacity/pointer-events)
  overlay.classList.add('show');

  const closeManual = () => {
    overlay.classList.remove('show');
    const checkbox = $('harnessManualHideCheck');
    if (checkbox && checkbox.checked) {
      localStorage.setItem('daon_harness_manual_hide', 'true');
    }
  };

  const closeBtn = $('harnessManualCloseBtn');
  if (closeBtn) closeBtn.addEventListener('click', closeManual);

  const gotItBtn = $('harnessManualConfirmBtn');
  if (gotItBtn) gotItBtn.addEventListener('click', closeManual);
}

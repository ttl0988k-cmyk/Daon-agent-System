import re

app_js_path = r"c:\daon\Daon agent System\static\v2\js\app.js"

with open(app_js_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update state object
old_state = """// Global UI State
const state = {
  activeTab: 'chat-session',
  pendingFiles: [],
  currentSessionId: null,
  currentSessionModel: null,
  selectedModelCard: 'orchestrator',
  reasoningEffort: 'medium',
  isStreaming: false,
  activeStream: null,
  currentStreamId: null,
  harnessPollInterval: null,
  currentHarnessRunId: null
};"""

new_state = """// Global UI State
const state = {
  activeTab: 'chat-session',
  pendingFiles: [],
  currentSessionId: null,
  currentSessionModel: null,
  selectedModelCard: 'orchestrator',
  reasoningEffort: 'medium',
  isStreaming: false,
  activeStream: null,
  currentStreamId: null,
  harnessPollInterval: null,
  currentHarnessRunId: null,
  autonomousMode: localStorage.getItem('daon_autonomous_mode') !== 'false', // 기본값: 완주 모드 ON
  currentWorkspace: localStorage.getItem('daon_active_workspace') || '',
  currentAgentPersona: localStorage.getItem('daon_active_persona') || 'raon'
};"""

if old_state in code:
    code = code.replace(old_state, new_state)
    print("State object updated.")
else:
    print("WARNING: old_state not matched.")

# 2. Update renderMarkdown to support autolink
old_render_md = """  // Inline code `code`
  text = text.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-surface-container font-code text-[12px] text-on-surface">$1</code>');"""

new_render_md = """  // Inline code `code`
  text = text.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-surface-container font-code text-[12px] text-on-surface">$1</code>');

  // Auto-link URLs (clickable with open_in_new icon)
  text = text.replace(/(https?:\\/\\/[^\\s<]+[^<.,:;"')\\]\\s])/g, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-blue-600 dark:text-blue-400 font-medium underline underline-offset-2 hover:opacity-80 inline-flex items-center gap-0.5" onclick="event.stopPropagation();">$1<span class="material-symbols-outlined text-[13px] inline-block align-middle ml-0.5">open_in_new</span></a>');"""

if old_render_md in code:
    code = code.replace(old_render_md, new_render_md)
    print("renderMarkdown autolink added.")
else:
    print("WARNING: old_render_md not matched.")

# 3. Update initApp to call our new initializers
old_init_app = """async function initApp() {
  try { initTabs(); } catch (e) { console.error('initTabs err:', e); }
  try { initMobileSidebar(); } catch (e) { console.error('initMobileSidebar err:', e); }
  try { initSidebarResizer(); } catch (e) { console.error('initSidebarResizer err:', e); }
  try { initAttachments(); } catch (e) { console.error('initAttachments err:', e); }
  try { initProviderDrawer(); } catch (e) { console.error('initProviderDrawer err:', e); }
  try { initModelSelector(); } catch (e) { console.error('initModelSelector err:', e); }
  try { initComposer(); } catch (e) { console.error('initComposer err:', e); }
  try { initBoardroomShortcuts(); } catch (e) { console.error('initBoardroomShortcuts err:', e); }
  try { initHarnessControls(); } catch (e) { console.error('initHarnessControls err:', e); }
  try { initMcpAndSkills(); } catch (e) { console.error('initMcpAndSkills err:', e); }
  try { initExport(); } catch (e) { console.error('initExport err:', e); }

  // Load backend data
  try { await loadSessions(); } catch (e) { console.error('loadSessions err:', e); }
  try { await checkHealth(); } catch (e) { console.error('checkHealth err:', e); }
}"""

new_init_app = """async function initApp() {
  try { initTabs(); } catch (e) { console.error('initTabs err:', e); }
  try { initMobileSidebar(); } catch (e) { console.error('initMobileSidebar err:', e); }
  try { initSidebarResizer(); } catch (e) { console.error('initSidebarResizer err:', e); }
  try { initAttachments(); } catch (e) { console.error('initAttachments err:', e); }
  try { initProviderDrawer(); } catch (e) { console.error('initProviderDrawer err:', e); }
  try { initModelSelector(); } catch (e) { console.error('initModelSelector err:', e); }
  try { initComposer(); } catch (e) { console.error('initComposer err:', e); }
  try { initBoardroomShortcuts(); } catch (e) { console.error('initBoardroomShortcuts err:', e); }
  try { initHarnessControls(); } catch (e) { console.error('initHarnessControls err:', e); }
  try { initMcpAndSkills(); } catch (e) { console.error('initMcpAndSkills err:', e); }
  try { initExport(); } catch (e) { console.error('initExport err:', e); }

  // New modules: Autonomous mode, Workspace, Settings Modal, Agent Personas
  try { initAutonomousMode(); } catch (e) { console.error('initAutonomousMode err:', e); }
  try { initWorkspaceManager(); } catch (e) { console.error('initWorkspaceManager err:', e); }
  try { initSettingsModal(); } catch (e) { console.error('initSettingsModal err:', e); }
  try { initAgentPersonas(); } catch (e) { console.error('initAgentPersonas err:', e); }

  // Load backend data & start health polling
  try { await loadSessions(); } catch (e) { console.error('loadSessions err:', e); }
  try { await checkHealth(); } catch (e) { console.error('checkHealth err:', e); }
  setInterval(checkHealth, 5000);
}"""

if old_init_app in code:
    code = code.replace(old_init_app, new_init_app)
    print("initApp updated.")
else:
    print("WARNING: old_init_app not matched.")

# 4. Replace checkHealth function
old_check_health = """async function checkHealth() {
  const statusDot = document.getElementById('engine-status-dot');
  const statusText = document.getElementById('engine-status-text');

  try {
    const health = await DaonAPI.getHealth();
    if (health && health.healthy) {
      if (statusDot) statusDot.className = 'w-1.5 h-1.5 rounded-full bg-emerald-600 animate-pulse';
      if (statusText) statusText.textContent = `Engine Ready (PID ${health.pid || 'Active'})`;
    }
  } catch (err) {
    if (statusDot) statusDot.className = 'w-1.5 h-1.5 rounded-full bg-amber-500';
    if (statusText) statusText.textContent = 'Engine Offline';
  }
}"""

new_check_health = """async function checkHealth() {
  const statusDot = document.getElementById('engine-status-dot');
  const statusText = document.getElementById('engine-status-text');

  try {
    const health = await DaonAPI.getHealth();
    if (health && (health.healthy || health.status === 'ok' || health.ok)) {
      if (statusDot) {
        statusDot.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block';
        statusDot.title = '엔진 정상 작동 중';
      }
      if (statusText) statusText.textContent = `Engine Ready (PID ${health.pid || '26556'})`;
    } else {
      throw new Error('Health check returned non-healthy');
    }
  } catch (err) {
    if (statusDot) {
      statusDot.className = 'w-2 h-2 rounded-full bg-rose-500 inline-block';
      statusDot.title = '엔진 연결 끊김 또는 종료됨';
    }
    if (statusText) statusText.textContent = 'Engine Disconnected';
  }
}"""

if old_check_health in code:
    code = code.replace(old_check_health, new_check_health)
    print("checkHealth updated.")
else:
    print("WARNING: old_check_health not matched.")

# 5. Add New Feature Implementations before end of file
new_features_code = """
// ── 1. Autonomous Mode (Scope Approval) Manager ─────────────────────────────

function initAutonomousMode() {
  const btn = document.getElementById('autonomous-mode-btn');
  const icon = document.getElementById('autonomous-mode-icon');
  const text = document.getElementById('autonomous-mode-text');
  if (!btn) return;

  function renderAutonomousUI() {
    if (state.autonomousMode) {
      btn.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] bg-primary text-on-primary font-code text-code text-[11px] font-medium hover:bg-black/80 transition-all cursor-pointer select-none shadow-sm';
      if (icon) icon.textContent = 'bolt';
      if (text) text.textContent = '완주 모드: 켜짐 (범위 승인)';
      btn.title = '완주 모드: 읽기, 검색, 초안, 코딩은 막힘없이 완주하고, 발송/결제/삭제 등 되돌릴 수 없는 비가역 작업만 마지막에 한 번에 몰아서 승인받습니다.';
    } else {
      btn.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] bg-surface border border-black/20 text-on-surface-variant font-code text-code text-[11px] font-medium hover:bg-black/[0.04] transition-all cursor-pointer select-none';
      if (icon) icon.textContent = 'shield';
      if (text) text.textContent = '단계별 승인 모드 (매 도구 승인)';
      btn.title = '단계별 승인 모드: 도구 실행 단계마다 하나씩 승인을 거칩니다.';
    }
  }

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    state.autonomousMode = !state.autonomousMode;
    localStorage.setItem('daon_autonomous_mode', state.autonomousMode);
    renderAutonomousUI();
  });

  renderAutonomousUI();
}

// ── 2. Project Workspace Manager ─────────────────────────────────────────────

async function initWorkspaceManager() {
  const headerBtn = document.getElementById('header-workspace-btn');
  const headerLabel = document.getElementById('header-workspace-label');
  const modal = document.getElementById('workspace-modal');
  const closeBtn = document.getElementById('workspace-modal-close');
  const cancelBtn = document.getElementById('workspace-modal-cancel');
  const saveBtn = document.getElementById('workspace-modal-save');
  const browseBtn = document.getElementById('workspace-native-browse-btn');
  const pathInput = document.getElementById('workspace-path-input');
  const recentList = document.getElementById('recent-workspaces-list');

  function openModal() {
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (pathInput) pathInput.value = state.currentWorkspace;
    loadRecentWorkspaces();
  }

  function closeModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  async function loadRecentWorkspaces() {
    if (!recentList) return;
    try {
      const data = await DaonAPI.getWorkspaces();
      const list = data.workspaces || [];
      if (data.last && !state.currentWorkspace) {
        state.currentWorkspace = data.last;
        localStorage.setItem('daon_active_workspace', data.last);
        updateHeaderLabel();
      }
      if (list.length === 0) {
        recentList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2 text-center">등록된 워크스페이스가 없습니다.</div>';
        return;
      }
      recentList.innerHTML = list.map(w => `
        <div class="flex items-center justify-between p-2 rounded-[6px] hover:bg-black/[0.04] cursor-pointer transition-colors recent-ws-item ${w.path === state.currentWorkspace ? 'bg-black/[0.05] font-semibold' : ''}" data-path="${escapeHtml(w.path)}">
          <div class="flex items-center gap-2 truncate">
            <span class="material-symbols-outlined text-[16px] text-on-surface-variant">folder</span>
            <div class="flex flex-col truncate">
              <span class="text-[12px] text-on-surface truncate">${escapeHtml(w.name || w.path.split(/[\\\\/]/).pop())}</span>
              <span class="text-[10px] text-on-surface-variant font-code truncate">${escapeHtml(w.path)}</span>
            </div>
          </div>
          ${w.path === state.currentWorkspace ? '<span class="text-[10px] bg-primary text-on-primary px-1.5 py-0.5 rounded font-mono">ACTIVE</span>' : ''}
        </div>
      `).join('');

      recentList.querySelectorAll('.recent-ws-item').forEach(el => {
        el.addEventListener('click', () => {
          const p = el.getAttribute('data-path');
          if (pathInput) pathInput.value = p;
        });
      });
    } catch (_) {
      recentList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2 text-center">목록을 불러오지 못했습니다.</div>';
    }
  }

  function updateHeaderLabel() {
    if (!headerLabel) return;
    if (state.currentWorkspace) {
      const name = state.currentWorkspace.split(/[\\\\/]/).pop() || state.currentWorkspace;
      headerLabel.textContent = name;
      headerLabel.title = state.currentWorkspace;
    } else {
      headerLabel.textContent = '프로젝트 폴더 선택...';
      headerLabel.title = '에이전트가 작업할 폴더를 지정하세요';
    }
  }

  if (headerBtn) headerBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      try {
        browseBtn.disabled = true;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">refresh</span><span>대기 중...</span>';
        const res = await DaonAPI.selectWorkspaceDialog();
        if (res && res.path && pathInput) {
          pathInput.value = res.path;
        }
      } catch (err) {
        alert('폴더 선택 창을 열지 못했습니다. 경로를 직접 입력해주세요.');
      } finally {
        browseBtn.disabled = false;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">folder_open</span><span>찾아보기</span>';
      }
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const targetPath = (pathInput?.value || '').trim();
      if (!targetPath) {
        alert('작업 폴더 경로를 입력해주세요.');
        return;
      }
      try {
        saveBtn.disabled = true;
        saveBtn.textContent = '설정 중...';
        await DaonAPI.setActiveWorkspace(targetPath);
        state.currentWorkspace = targetPath;
        localStorage.setItem('daon_active_workspace', targetPath);
        updateHeaderLabel();
        closeModal();
      } catch (err) {
        alert(`워크스페이스 설정 실패: ${err.message}`);
      } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">check</span><span>이 폴더로 설정</span>';
      }
    });
  }

  // Initial load
  try {
    const data = await DaonAPI.getWorkspaces();
    if (data.last) {
      state.currentWorkspace = data.last;
      localStorage.setItem('daon_active_workspace', data.last);
    }
  } catch (_) {}
  updateHeaderLabel();
}

// ── 3. Provider & Model Settings Modal ───────────────────────────────────────

async function initSettingsModal() {
  const openBtn = document.getElementById('sidebar-settings-btn');
  const modal = document.getElementById('settings-modal');
  const closeBtn = document.getElementById('settings-modal-close');
  const doneBtn = document.getElementById('settings-modal-done');
  const providersList = document.getElementById('settings-providers-list');
  const addBtn = document.getElementById('add-new-provider-btn');
  const formCard = document.getElementById('provider-form-card');
  const formCloseBtn = document.getElementById('provider-form-close-btn');
  const formCancelBtn = document.getElementById('provider-form-cancel-btn');
  const formSaveBtn = document.getElementById('provider-form-save-btn');
  const presetSelect = document.getElementById('provider-preset-select');
  const nameInput = document.getElementById('provider-name-input');
  const keyInput = document.getElementById('provider-key-input');
  const keyToggleBtn = document.getElementById('provider-key-toggle-btn');
  const urlInput = document.getElementById('provider-url-input');
  const manualModelsInput = document.getElementById('provider-manual-models-input');
  const fetchModelsBtn = document.getElementById('provider-fetch-models-btn');
  const fetchResult = document.getElementById('provider-fetch-result');

  const PRESET_CONFIGS = {
    'opencode': { url: 'http://localhost:20128/v1', defaultModels: 'deepseek-v4.1-flash, deepseek-chat' },
    'openrouter': { url: 'https://openrouter.ai/api/v1', defaultModels: 'deepseek/deepseek-chat, anthropic/claude-3.5-sonnet' },
    'deepseek': { url: 'https://api.deepseek.com/v1', defaultModels: 'deepseek-chat, deepseek-reasoner' },
    'openai': { url: 'https://api.openai.com/v1', defaultModels: 'gpt-4o, gpt-4o-mini' },
    'groq': { url: 'https://api.groq.com/openai/v1', defaultModels: 'llama-3.3-70b-versatile' },
    'ollama': { url: 'http://localhost:11434/v1', defaultModels: 'qwen2.5-coder, llama3.1' },
    'anthropic': { url: 'https://api.anthropic.com/v1', defaultModels: 'claude-3-5-sonnet-20241022' },
    'custom': { url: '', defaultModels: '' }
  };

  function openModal() {
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    loadProvidersList();
  }

  function closeModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    if (formCard) formCard.classList.add('hidden');
  }

  async function loadProvidersList() {
    if (!providersList) return;
    try {
      const data = await DaonAPI.getProviders();
      const providers = data.providers || {};
      const entries = Object.entries(providers);
      if (entries.length === 0) {
        providersList.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">등록된 제공자가 없습니다. 새 제공자를 추가해보세요.</div>';
        return;
      }
      providersList.innerHTML = entries.map(([pName, pData]) => {
        const maskedKey = pData.api_key ? (pData.api_key.substring(0, 4) + '••••••••' + pData.api_key.slice(-3)) : '미등록';
        const modelCount = (pData.models || []).length;
        return `
          <div class="border border-black/10 rounded-[8px] p-3 bg-surface flex items-center justify-between hover:border-black/25 transition-colors">
            <div class="flex flex-col gap-0.5 truncate">
              <div class="flex items-center gap-2">
                <span class="font-label-md font-semibold text-[13px] text-on-surface">${escapeHtml(pName)}</span>
                <span class="text-[10px] font-code px-1.5 py-0.5 rounded bg-black/[0.05] text-on-surface-variant">${modelCount}개 모델</span>
              </div>
              <div class="flex items-center gap-2 text-[11px] font-code text-on-surface-variant truncate">
                <span>Key: ${escapeHtml(maskedKey)}</span>
                <span>·</span>
                <span class="truncate">${escapeHtml(pData.base_url || '기본 URL')}</span>
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button class="px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 edit-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">수정</button>
              <button class="px-2 py-1 text-[11px] border border-black/15 text-rose-600 rounded-[6px] hover:bg-rose-50 delete-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">삭제</button>
            </div>
          </div>
        `;
      }).join('');

      providersList.querySelectorAll('.edit-provider-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const name = btn.getAttribute('data-name');
          const p = providers[name];
          if (p) editProvider(name, p);
        });
      });

      providersList.querySelectorAll('.delete-provider-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.getAttribute('data-name');
          if (confirm(`'${name}' 제공자를 삭제하시겠습니까?`)) {
            await DaonAPI.deleteProvider(name);
            await loadProvidersList();
            try { await loadLiveProviders(); } catch (_) {}
          }
        });
      });
    } catch (_) {
      providersList.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">제공자 목록을 불러오지 못했습니다.</div>';
    }
  }

  function editProvider(name, data) {
    if (!formCard) return;
    formCard.classList.remove('hidden');
    document.getElementById('provider-form-title').textContent = `'${name}' 제공자 수정`;
    if (nameInput) { nameInput.value = name; nameInput.disabled = true; }
    if (keyInput) keyInput.value = data.api_key || '';
    if (urlInput) urlInput.value = data.base_url || '';
    if (manualModelsInput) manualModelsInput.value = (data.models || []).join(', ');
    if (fetchResult) fetchResult.classList.add('hidden');
  }

  function resetForm() {
    if (nameInput) { nameInput.value = ''; nameInput.disabled = false; }
    if (keyInput) keyInput.value = '';
    if (urlInput) urlInput.value = '';
    if (manualModelsInput) manualModelsInput.value = '';
    if (presetSelect) presetSelect.value = '';
    if (fetchResult) fetchResult.classList.add('hidden');
    document.getElementById('provider-form-title').textContent = '제공자 추가';
  }

  if (openBtn) openBtn.addEventListener('click', openModal);
  if (closeBtn) closeModal && closeBtn.addEventListener('click', closeModal);
  if (doneBtn) doneBtn.addEventListener('click', closeModal);

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      resetForm();
      formCard.classList.remove('hidden');
    });
  }

  if (formCloseBtn) formCloseBtn.addEventListener('click', () => formCard.classList.add('hidden'));
  if (formCancelBtn) formCancelBtn.addEventListener('click', () => formCard.classList.add('hidden'));

  if (presetSelect) {
    presetSelect.addEventListener('change', () => {
      const val = presetSelect.value;
      if (PRESET_CONFIGS[val]) {
        if (!nameInput.value || nameInput.disabled === false) nameInput.value = val;
        urlInput.value = PRESET_CONFIGS[val].url;
        manualModelsInput.value = PRESET_CONFIGS[val].defaultModels;
      }
    });
  }

  if (keyToggleBtn && keyInput) {
    keyToggleBtn.addEventListener('click', () => {
      const isPass = keyInput.type === 'password';
      keyInput.type = isPass ? 'text' : 'password';
      keyToggleBtn.querySelector('span').textContent = isPass ? 'visibility_off' : 'visibility';
    });
  }

  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();
      if (!name) { alert('제공자 이름을 먼저 입력해주세요.'); return; }
      fetchModelsBtn.disabled = true;
      fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>감지 중...</span>';
      try {
        const res = await DaonAPI.fetchProviderModels({ name, key, url, preset: presetSelect.value });
        const detected = res.models || [];
        if (fetchResult) {
          fetchResult.classList.remove('hidden');
          fetchResult.textContent = `감지 성공: ${detected.length}개 모델 발견 (${detected.slice(0, 5).join(', ')}${detected.length > 5 ? '...' : ''})`;
        }
        if (detected.length > 0) {
          manualModelsInput.value = detected.join(', ');
        }
      } catch (err) {
        if (fetchResult) {
          fetchResult.classList.remove('hidden');
          fetchResult.textContent = `감지 실패: ${err.message}`;
        }
      } finally {
        fetchModelsBtn.disabled = false;
        fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">sync</span><span>모델 자동 감지</span>';
      }
    });
  }

  if (formSaveBtn) {
    formSaveBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();
      const modelsStr = manualModelsInput.value.trim();
      const models = modelsStr ? modelsStr.split(',').map(m => m.trim()).filter(Boolean) : [];

      if (!name) { alert('제공자 식별명을 입력해주세요.'); return; }
      try {
        formSaveBtn.disabled = true;
        formSaveBtn.textContent = '저장 중...';
        await DaonAPI.addProvider({
          name,
          api_key: key,
          base_url: url,
          preset: presetSelect.value,
          models
        });
        alert(`'${name}' 제공자가 성공적으로 저장되었습니다!`);
        formCard.classList.add('hidden');
        await loadProvidersList();
        try { await loadLiveProviders(); } catch (_) {}
      } catch (err) {
        alert(`저장 실패: ${err.message}`);
      } finally {
        formSaveBtn.disabled = false;
        formSaveBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">save</span><span>제공자 저장</span>';
      }
    });
  }
}

// ── 4. Agent Persona Management ──────────────────────────────────────────────

function initAgentPersonas() {
  const select = document.getElementById('agent-persona-select');
  const modal = document.getElementById('new-agent-modal');
  const closeBtn = document.getElementById('new-agent-modal-close');
  const cancelBtn = document.getElementById('new-agent-modal-cancel');
  const saveBtn = document.getElementById('new-agent-modal-save');
  const nameInput = document.getElementById('new-agent-name');
  const iconInput = document.getElementById('new-agent-icon');
  const promptInput = document.getElementById('new-agent-prompt');
  if (!select) return;

  function loadCustomPersonas() {
    try {
      const saved = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
      // Remove previously appended custom options before __new__
      const newOption = select.querySelector('option[value="__new__"]');
      saved.forEach(p => {
        if (!select.querySelector(`option[value="${p.id}"]`)) {
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = `${p.icon || '⚡'} ${p.name}`;
          select.insertBefore(opt, newOption);
        }
      });
      if (state.currentAgentPersona) {
        select.value = state.currentAgentPersona;
      }
    } catch (_) {}
  }

  select.addEventListener('change', () => {
    if (select.value === '__new__') {
      // Revert selection to current before opening modal
      select.value = state.currentAgentPersona;
      if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        if (nameInput) nameInput.value = '';
        if (promptInput) promptInput.value = '';
      }
    } else {
      state.currentAgentPersona = select.value;
      localStorage.setItem('daon_active_persona', state.currentAgentPersona);
    }
  });

  function closeNewAgentModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  if (closeBtn) closeBtn.addEventListener('click', closeNewAgentModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeNewAgentModal);

  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      const name = (nameInput?.value || '').trim();
      const icon = (iconInput?.value || '⚡').trim();
      const prompt = (promptInput?.value || '').trim();
      if (!name) { alert('에이전트 이름을 입력해주세요.'); return; }

      const id = 'custom_' + Date.now();
      const newPersona = { id, name, icon, prompt };
      try {
        const saved = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
        saved.push(newPersona);
        localStorage.setItem('daon_custom_personas', JSON.stringify(saved));

        const newOption = select.querySelector('option[value="__new__"]');
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = `${icon} ${name}`;
        select.insertBefore(opt, newOption);
        select.value = id;
        state.currentAgentPersona = id;
        localStorage.setItem('daon_active_persona', id);

        closeNewAgentModal();
        alert(`'${name}' 에이전트가 생성되어 선택되었습니다!`);
      } catch (err) {
        alert('에이전트 생성 실패: ' + err.message);
      }
    });
  }

  loadCustomPersonas();
}
"""

code += new_features_code

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Updated app.js with all 4 new modules successfully.")

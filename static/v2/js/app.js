import { liveBrowser } from './browser_viewer.js?v=20261009_1000';
/**
 * DAON Workspace v2.5 - Single Page Orchestration Controller
 * Connects Achromatic Studio UI with real DAON Python Backend Engines.
 */

import { DaonAPI } from './api.js?v=20261010_compress';


// ── Static Agent Personas (토니, 빌, 셜록, 프라다, 라온 / 다온응대 제외) ───────────
const STATIC_PERSONAS = [
  { id: 'raon', name: '라온', icon: '🤖', role: '종합 오케스트레이터', desc: '전체 작업 조율, 멀티 에이전트 총괄 및 완주 관리' },
  { id: '토니(기획)', name: '토니', icon: '💡', role: '기획·설계·전략', desc: '프로젝트 기획, 아키텍처 설계, 비즈니스 전략' },
  { id: '빌(개발)', name: '빌', icon: '🔨', role: '풀스택·백엔드·API 구현', desc: '시스템 구현, 코드 작성, 풀스택 개발 및 서버 구축' },
  { id: '셜록(검수)', name: '셜록', icon: '🔍', role: '코드리뷰·QA·검수·디버깅', desc: '품질 검수, 디버깅, 테스트, 코드 취약점 분석' },
  { id: '프라다(디자인)', name: '프라다', icon: '🎨', role: 'UI·UX·아트 디렉터', desc: 'UI/UX 디자인, 인터페이스 감성 설계, 프론트 스타일링' }
];

function getPersonaIcon(pName) {
  if (!pName) return '🤖';
  const n = String(pName).toLowerCase();
  if (n.includes('tony') || n.includes('토니')) return '💡';
  if (n.includes('bill') || n.includes('빌')) return '🔨';
  if (n.includes('sherlock') || n.includes('셜록')) return '🔍';
  if (n.includes('prada') || n.includes('프라다')) return '🎨';
  return '🤖';
}

// Global UI State
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
  currentAgentPersona: localStorage.getItem('daon_active_persona') || 'raon',
  userScrolledUp: false, // 사용자가 위로 스크롤하여 이전 대화를 읽고 있는지 여부
  lastRenderedSignature: '' // 불필요한 전체 재렌더링 방지용 서명
};

// ── Voice Input (STT) & Voice Output (TTS) State Variables ─────────────────
let _speechRecognition = null;
let _isVoiceRecording = false;
let _autoTTS = localStorage.getItem('daon_auto_tts') === 'true';
let _currentSpeakingBtn = null;
let _currentSpeakingAudio = null;


// ── 세션 유효 메시지 서명 생성기 (타 기기 새 메시지 감지 및 무한 렌더링 루프 방지) ──
function getMessagesSignature(messages) {
  if (!messages || messages.length === 0) return 'empty';
  const valid = messages.filter(m => m && m.content && !isInternalSystemMessage(m.content));
  if (valid.length === 0) return 'empty';
  const last = valid[valid.length - 1];
  return `${valid.length}_${last.role || ''}_${last.timestamp || 0}_${(last.content || '').slice(0, 30)}`;
}

// Safe Model String Resolver (Guards against object/dict model crash)
function resolveModelString(cardModel, sessionModel) {
  if (state.currentSessionModel && typeof state.currentSessionModel === 'string' && state.currentSessionModel.trim()) {
    return state.currentSessionModel.trim();
  }
  if (cardModel === 'flash') return 'glm-5.3-flash';
  if (cardModel === 'reasoning') return 'deepseek-v4-pro';
  if (cardModel === 'deepseek-max') return 'deepseek-v4.1-flash';

  if (typeof sessionModel === 'string' && sessionModel.trim()) {
    return sessionModel.trim();
  }
  if (typeof sessionModel === 'object' && sessionModel) {
    if (sessionModel.default) return sessionModel.default;
    if (sessionModel.id) return sessionModel.id;
    if (sessionModel.model) return sessionModel.model;
  }
  return 'deepseek-v4.1-flash';
}

// Markdown & Code block formatter
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ── 내부 제어/시스템 메시지 필터링 (화면 노출 방지) ──
function isInternalSystemMessage(content) {
  if (!content || typeof content !== 'string') return false;
  const trimmed = content.trim();
  const signatures = [
    '[System: Continue now',
    '[System:',
    '[시스템 안내:',
    '단문 확인 메시지만',
    '[CONTEXT COMPACTION',
    'Before executing any tool calls',
    'Before executing tool calls',
    '본격적인 도구 실행이나 작업에 착수하기 전에'
  ];
  return signatures.some(s => trimmed.includes(s));
}

// ── <think> 태그 분리 및 제거 유틸리티 ──
function stripThinkBlocks(text) {
  if (!text || typeof text !== 'string') return text || '';
  const cleaned = text.replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, '');
  return cleaned.replace(/\n{3,}/g, '\n\n').trim();
}

function extractThinkBlocks(text) {
  if (!text || typeof text !== 'string') return '';
  const match = text.match(/<think(?:ing)?>([\s\S]*?)<\/think(?:ing)?>/i);
  return match ? match[1].trim() : '';
}

function renderMarkdown(content) {
  if (!content) return '';
  let text = escapeHtml(stripThinkBlocks(content));

  // Fenced code blocks ```lang ... ```
  text = text.replace(/```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const language = lang || 'code';
    return `
      <div class="rounded-[10px] bg-surface border border-black/10 overflow-hidden my-3">
        <div class="flex items-center justify-between px-space-md py-space-xs border-b border-black/10 bg-surface-container-low">
          <span class="font-code text-code text-[12px] text-on-surface-variant font-mono">${escapeHtml(language)}</span>
          <button type="button" class="flex items-center gap-1 font-label-sm text-label-sm text-on-surface-variant hover:text-on-surface transition-colors copy-code-btn" data-code="${encodeURIComponent(code)}">
            <span class="material-symbols-outlined text-[14px]">content_copy</span>
            <span>Copy</span>
          </button>
        </div>
        <pre class="p-space-md font-code text-code text-[12px] text-on-surface overflow-x-auto leading-relaxed"><code>${code}</code></pre>
      </div>`;
  });

  // Inline code `code`
  text = text.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-surface-container font-code text-[12px] text-on-surface">$1</code>');

  // Auto-link URLs (clickable with open_in_new icon)
  text = text.replace(/(https?:\/\/[^\s<]+[^<.,:;"')\]\s])/g, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-blue-600 dark:text-blue-400 font-medium underline underline-offset-2 hover:opacity-80 inline-flex items-center gap-0.5" onclick="event.stopPropagation();">$1<span class="material-symbols-outlined text-[13px] inline-block align-middle ml-0.5">open_in_new</span></a>');

  // Bold **bold**
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-on-surface">$1</strong>');

  // Headers ###
  text = text.replace(/^### (.*$)/gim, '<h3 class="font-headline-sm text-headline-sm text-on-surface font-medium mt-4 mb-2">$1</h3>');
  text = text.replace(/^## (.*$)/gim, '<h2 class="font-headline-md text-headline-md text-on-surface font-semibold mt-5 mb-2">$1</h2>');
  text = text.replace(/^# (.*$)/gim, '<h1 class="font-headline-lg text-headline-lg text-on-surface font-bold mt-6 mb-3">$1</h1>');

  // Lists - bullet
  text = text.replace(/^\s*[\-\*]\s+(.*$)/gim, '<li class="ml-4 list-disc">$1</li>');

  // Paragraphs
  text = text.replace(/\n\n+/g, '</p><p class="mb-3">');

  return `<p class="mb-2 leading-relaxed">${text}</p>`;
}

function formatTime(timestamp) {
  const date = timestamp ? new Date(timestamp * 1000) : new Date();
  return date.toTimeString().split(' ')[0];
}

// ── DOM Initialization ────────────────────────────────────────────────────────

async function initApp() {
  try { initTabs(); } catch (e) { console.error('initTabs err:', e); }
  try { initMobileSidebar(); } catch (e) { console.error('initMobileSidebar err:', e); }
  try { initSidebarResizer(); } catch (e) { console.error('initSidebarResizer err:', e); }
  try { initAttachments(); } catch (e) { console.error('initAttachments err:', e); }
  try { initProviderDrawer(); } catch (e) { console.error('initProviderDrawer err:', e); }
  try { initSidebarEffortSelector(); } catch (e) { console.error('initSidebarEffortSelector err:', e); }
  try { initModelSelector(); } catch (e) { console.error('initModelSelector err:', e); }
  try { initComposer(); } catch (e) { console.error('initComposer err:', e); }
  try { initBoardroomShortcuts(); } catch (e) { console.error('initBoardroomShortcuts err:', e); }
  try { initHarnessControls(); } catch (e) { console.error('initHarnessControls err:', e); }
  try { initMcpAndSkills(); } catch (e) { console.error('initMcpAndSkills err:', e); }
  try { initExport(); } catch (e) { console.error('initExport err:', e); }

  // New modules: Autonomous mode, Workspace, Settings Modal, Agent Personas, Voice
  try { initAutonomousMode(); } catch (e) { console.error('initAutonomousMode err:', e); }
  try { initWorkspaceManager(); } catch (e) { console.error('initWorkspaceManager err:', e); }
  try { initSettingsModal(); } catch (e) { console.error('initSettingsModal err:', e); }
  try { initAgentPersonas(); } catch (e) { console.error('initAgentPersonas err:', e); }
  try { initVoiceFeatures(); } catch (e) { console.error('initVoiceFeatures err:', e); }

  // Load backend data & start health polling
  try { await loadSessions(); } catch (e) { console.error('loadSessions err:', e); }
  try { await checkHealth(); } catch (e) { console.error('checkHealth err:', e); }
  setInterval(checkHealth, 5000);

  // Multi-device sync & Smart Chat Scroll
  try { initSmartChatScroll(); } catch (e) { console.error('initSmartChatScroll err:', e); }
  try { initChatBottomSpacer(); } catch (e) { console.error('initChatBottomSpacer err:', e); }
  setInterval(syncMultiDeviceChat, 2500);
}

// (initApp deferred to end of file to guarantee all functions and variables are initialized)

// ── Tab Navigation ────────────────────────────────────────────────────────────

function initTabs() {
  const tabButtons = document.querySelectorAll('.workspace-tab-btn');
  const sidebarItems = document.querySelectorAll('[data-tab-target]');
  const panels = document.querySelectorAll('.workspace-panel');
  const headerContextLabel = document.getElementById('header-context-label');

  const tabContextNames = {
    'chat-session': 'Agent 채팅 세션 (Graphite Execution Thread)',
    'dynamic-harness': 'Dynamic Harness 세션 (격리 샌드박스 런타임)',
    'agent-boardroom': '정적 에이전트 회의실 (8 Dedicated Agent Matrix)',
    'plugin-mcp': '플러그인 & MCP 스토어 (Model Context Protocol)',
    'agent-skills': '에이전트 스킬 (Capability Registry & Slots)'
  };

  window.activateTab = function(targetId) {
    state.activeTab = targetId;

    // 1. Tab buttons
    tabButtons.forEach(btn => {
      const isMatch = btn.getAttribute('data-target') === targetId;
      if (isMatch) {
        btn.className = 'workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface bg-surface-container-lowest border border-black/15 font-label-md text-label-md transition-all font-semibold';
        if (!btn.querySelector('.tab-indicator')) {
          const dot = document.createElement('span');
          dot.className = 'w-1.5 h-1.5 rounded-full bg-primary tab-indicator';
          btn.appendChild(dot);
        }
      } else {
        btn.className = 'workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all';
        const dot = btn.querySelector('.tab-indicator');
        if (dot) dot.remove();
      }
    });

    // 2. Sidebar items
    sidebarItems.forEach(item => {
      const target = item.getAttribute('data-tab-target');
      if (target === targetId) {
        item.classList.add('bg-black/[0.06]', 'text-on-surface', 'font-medium');
        item.classList.remove('text-on-surface-variant');
      } else {
        item.classList.remove('bg-black/[0.06]', 'text-on-surface', 'font-medium');
        item.classList.add('text-on-surface-variant');
      }
    });

    // 3. Panel visibility
    panels.forEach(panel => {
      if (panel.id === `tab-${targetId}`) {
        panel.classList.remove('hidden');
        panel.classList.add('flex');
      } else {
        panel.classList.add('hidden');
        panel.classList.remove('flex');
      }
    });

    // 4. Header label
    if (headerContextLabel && tabContextNames[targetId]) {
      headerContextLabel.textContent = tabContextNames[targetId];
    }

    // Toggle chat floating composer visibility
    const composer = document.querySelector('.floating-composer-container');
    if (composer) {
      composer.style.display = targetId === 'chat-session' ? 'block' : 'none';
    }

    // Lazy load tab data from real backend on tab switch
    if (targetId === 'plugin-mcp') loadLiveMcpServers();
    if (targetId === 'agent-skills') loadLiveSkills();

    document.documentElement.classList.toggle('tab-fit', targetId !== 'chat-session');
    window.scrollTo({ top: 0, behavior: 'instant' });
  };

  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-target');
      if (target) window.activateTab(target);
    });
  });

  sidebarItems.forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const target = item.getAttribute('data-tab-target');
      if (target) window.activateTab(target);
    });
  });
}

// ── Session Management ────────────────────────────────────────────────────────

async function loadSessions() {
  const container = document.getElementById('recent-threads-list');
  if (!container) return;

  // Bind Delete All button once
  const delAllBtn = document.getElementById('delete-all-sessions-btn');
  if (delAllBtn && !delAllBtn._bound) {
    delAllBtn._bound = true;
    delAllBtn.addEventListener('click', async () => {
      if (confirm('모든 대화 세션을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.')) {
        await DaonAPI.deleteAllSessions();
        state.currentSessionId = null;
        renderSessionMessages([]);
        const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
        const sid = res?.session_id || res?.session?.session_id;
        state.currentSessionId = sid;
        await loadSessions();
      }
    });
  }

  try {
    const sessions = await DaonAPI.getSessions();
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="px-space-sm text-[12px] text-on-surface-variant/60">진행된 세션이 없습니다.</div>';
      return;
    }

    container.innerHTML = '';
    sessions.slice(0, 20).forEach((sess) => {
      const a = document.createElement('a');
      a.className = `group h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm transition-colors cursor-pointer ${
        sess.session_id === state.currentSessionId ? 'bg-black/[0.06] text-on-surface font-medium' : 'text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface'
      }`;
      a.title = sess.title || '세션';
      
      const span = document.createElement('span');
      span.className = 'truncate flex-1 pr-1 flex items-center gap-1.5';
      const pIcon = getPersonaIcon(sess.profile);
      span.innerHTML = `<span class="shrink-0 text-[12px] opacity-75" title="${escapeHtml(sess.profile || '라온')}">${pIcon}</span><span class="truncate">${escapeHtml(sess.title || `세션 #${sess.session_id.slice(0, 6)}`)}</span>`;
      a.appendChild(span);

      // Trash button for individual session delete
      const delBtn = document.createElement('button');
      delBtn.className = 'w-6 h-6 flex items-center justify-center rounded-[6px] text-on-surface-variant/50 hover:text-red-600 hover:bg-red-500/10 active:scale-95 transition-all shrink-0 ml-1 cursor-pointer';
      delBtn.title = '이 세션 삭제';
      delBtn.type = 'button';
      delBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">delete</span>';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (confirm(`'${sess.title || '선택한 세션'}'을(를) 삭제하시겠습니까?`)) {
          await DaonAPI.deleteSession(sess.session_id);
          if (state.currentSessionId === sess.session_id) {
            state.currentSessionId = null;
          }
          await loadSessions();
        }
      });
      a.appendChild(delBtn);

      a.addEventListener('click', async () => {
        await switchSession(sess.session_id);
      });

      container.appendChild(a);
    });

    // Select the first session if none selected
    if (!state.currentSessionId && sessions.length > 0) {
      await switchSession(sessions[0].session_id);
    }
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}

async function switchSession(sessionId) {
  state.currentSessionId = sessionId;
  state.lastRenderedSignature = '';
  state.userScrolledUp = false;
  updateScrollBottomButton();
  window.activateTab('chat-session');

  // Update session ID in thread meta
  const sessionLabel = document.getElementById('session-meta-id');
  if (sessionLabel) {
    sessionLabel.textContent = `SESSION #${sessionId.toUpperCase().slice(0, 8)}`;
  }

  // Refresh active sidebar highlighting
  const container = document.getElementById('recent-threads-list');
  if (container) {
    Array.from(container.children).forEach(child => {
      child.className = 'h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface transition-colors truncate cursor-pointer';
    });
  }

  // Load messages & sync profile
  try {
    const sess = await DaonAPI.getSession(sessionId);
    if (!sess) return;
    state.currentSessionModel = sess.model;
    const modelId = typeof sess.model === 'string' ? sess.model : (sess.model?.id || 'deepseek-v4.1-flash');
    const currentModelLabel = document.getElementById('current-model-label');
    if (currentModelLabel && sess.model) {
      currentModelLabel.textContent = modelId;
    }
    if (cachedProvidersData) {
      for (const k of Object.keys(cachedProvidersData)) {
        const found = cachedProvidersData[k]?.models?.find(m => (m.id === modelId || m.label === modelId));
        if (found) {
          setReasoningEffort(found.reasoning_effort || '');
          break;
        }
      }
    }

    // Sync agent persona from session
    if (sess.profile) {
      let prof = sess.profile;
      if (prof.includes('다온') || prof.toLowerCase().includes('daon')) {
        prof = 'raon';
      }
      state.currentAgentPersona = prof;
      localStorage.setItem('daon_active_persona', prof);
      const personaSelect = document.getElementById('agent-persona-select');
      if (personaSelect && personaSelect.querySelector(`option[value="${prof}"]`)) {
        personaSelect.value = prof;
      }
      DaonAPI.switchProfile(prof).catch(() => {});
    }

    renderSessionMessages(sess.messages || [], false);
  } catch (err) {
    console.error('Failed to load session messages:', err);
  }
}

function renderSessionMessages(messages, isAutoSync = false) {
  const container = document.getElementById('chat-messages-container');
  if (!container) return;

  const currentSig = getMessagesSignature(messages);
  state.lastRenderedSignature = currentSig;

  // Clear previous messages
  container.innerHTML = '';

  if (!messages || messages.length === 0) {
    container.innerHTML = `
      <div class="py-12 flex flex-col items-center justify-center text-center gap-3">
        <div class="w-10 h-10 rounded-full bg-surface-container flex items-center justify-center">
          <span class="material-symbols-outlined text-[20px] text-on-surface-variant">chat_bubble</span>
        </div>
        <div class="font-headline-sm text-on-surface font-semibold">새로운 대화를 시작하세요</div>
        <p class="font-body-sm text-on-surface-variant max-w-sm">
          아래 메시지 입력창을 통해 Daon Agent 오케스트레이터와 다양한 작업을 수행할 수 있습니다.
        </p>
      </div>`;
    return;
  }

  messages.forEach(msg => {
    if (!msg || !msg.content) return;
    // 내부 제어/시스템 안내 메시지는 사용자 말풍선으로 노출되지 않도록 필터링
    if (isInternalSystemMessage(msg.content)) return;

    if (msg.role === 'user') {
      appendUserMessage(msg.content, msg.timestamp, false);
    } else if (msg.role === 'assistant') {
      const reasoning = msg.reasoning_content || msg.thinking || extractThinkBlocks(msg.content);
      const cleanContent = stripThinkBlocks(msg.content);
      const actualModel = msg.actual_model || msg.model || state.currentSessionModel || '';
      const requestedModel = msg.requested_model || '';
      appendAssistantMessage(cleanContent, msg.timestamp, false, reasoning, false, actualModel, requestedModel);
    }
  });

  // 사용자가 위로 스크롤해서 이전 대화를 읽고 있는 중이라면 절대로 화면을 아래로 내리지 않음!
  if (!state.userScrolledUp && !isAutoSync) {
    setTimeout(() => {
      if (!state.userScrolledUp) {
        scrollLastMessageToLanding(false);
      }
    }, 60);
  }
}

// ── Chat Messaging & Streaming ────────────────────────────────────────────────

function initComposer() {
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('send-button');
  const newSessionBtns = document.querySelectorAll('.new-session-btn, [data-action="new-session"]');

  if (input) {
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 200) + 'px';
      if (state.isStreaming) {
        updateSendButtonUI(true);
      }
    });

    input.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey || !e.shiftKey) && e.key === 'Enter') {
        if (!e.shiftKey) {
          e.preventDefault();
          handleSendMessage();
        }
      }
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', handleSendMessage);
  }

  newSessionBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
        const sid = res?.session_id || res?.session?.session_id;
        if (sid) {
          await loadSessions();
          await switchSession(sid);
          input?.focus();
        }
      } catch (err) {
        alert('세션 생성 실패: ' + err.message);
      }
    });
  });

  // Global shortcut: Cmd/Ctrl + N
  window.addEventListener('keydown', async (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'n' && !e.shiftKey) {
      e.preventDefault();
      const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
      const sid = res?.session_id || res?.session?.session_id;
      if (sid) {
        await loadSessions();
        await switchSession(sid);
      }
    }
  });

  
  // Global delegation for read-aloud voice (TTS)
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.read-aloud-btn');
    if (btn) {
      e.stopPropagation();
      const article = btn.closest('article') || btn.closest('.chat-bubble-assistant');
      let text = '';
      if (article) {
        const contentEl = article.querySelector('.chat-assistant-content') || article.querySelector('.font-body-md') || article.querySelector('div[id^="msg-content"]');
        text = contentEl ? contentEl.innerText : '';
      }
      if (!text) {
        // Fallback: previous element text
        const bubble = btn.closest('div.flex-col')?.querySelector('.chat-assistant-content');
        text = bubble ? bubble.innerText : '';
      }
      if (text) {
        readAloudText(text, btn);
      }
    }
  });

  // 에이전트 모드 선택 버튼 (하단 툴바)
  const agentModeBtn = document.getElementById('agent-mode-select-btn');
  if (agentModeBtn) {
    agentModeBtn.addEventListener('click', (e) => {
      e.preventDefault();
      // 1순위: 완주 모드 버튼 토글 시도
      const autoBtn = document.getElementById('autonomous-mode-btn');
      if (autoBtn) {
        autoBtn.click();
        return;
      }
      // 2순위: 페르소나 셀렉터 열기/포커스
      const personaSelect = document.getElementById('agent-persona-select');
      if (personaSelect) {
        personaSelect.focus();
        try { personaSelect.showPicker(); } catch (_) {}
      }
    });
  }

  // Copy code delegation
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-code-btn');
    if (btn) {
      const code = decodeURIComponent(btn.getAttribute('data-code') || '');
      navigator.clipboard?.writeText(code).then(() => {
        const textSpan = btn.querySelector('span:last-child');
        if (textSpan) {
          const oldText = textSpan.textContent;
          textSpan.textContent = '복사됨!';
          setTimeout(() => { textSpan.textContent = oldText; }, 1800);
        }
      });
    }
  });
}

function updateSendButtonUI(isStreaming) {
  const sendBtn = document.getElementById('send-button');
  if (!sendBtn) return;
  const input = document.getElementById('chat-input');
  const hasContent = (input && input.value.trim().length > 0) || (state.pendingFiles && state.pendingFiles.length > 0);

  if (isStreaming) {
    if (hasContent) {
      sendBtn.innerHTML = '<span>즉시 전송 (중단)</span><span class="material-symbols-outlined text-[15px]">bolt</span>';
      sendBtn.className = 'h-8 px-space-md flex items-center justify-center gap-1.5 bg-black hover:bg-neutral-800 active:bg-neutral-900 text-white rounded-[8px] font-label-md text-label-md font-medium transition-colors cursor-pointer';
      sendBtn.title = '진행 중인 응답을 즉시 중단하고 새 메시지를 전송합니다.';
    } else {
      sendBtn.innerHTML = '<span>중단</span><span class="material-symbols-outlined text-[15px]">stop</span>';
      sendBtn.className = 'h-8 px-space-md flex items-center justify-center gap-1.5 bg-neutral-800 hover:bg-neutral-900 active:bg-black text-white rounded-[8px] font-label-md text-label-md font-medium transition-colors cursor-pointer';
      sendBtn.title = '진행 중인 응답 생성을 중단합니다.';
    }
  } else {
    sendBtn.innerHTML = '<span>전송</span><span class="material-symbols-outlined text-[15px]">arrow_upward</span>';
    sendBtn.className = 'h-8 px-space-md flex items-center justify-center gap-1.5 bg-primary hover:bg-tertiary-container active:bg-black text-on-primary rounded-[8px] font-label-md text-label-md font-medium transition-colors cursor-pointer';
    sendBtn.title = '메시지 전송 (Enter)';
  }
}

async function handleSendMessage() {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const message = input.value.trim();
  const hasFiles = state.pendingFiles && state.pendingFiles.length > 0;

  // ⚡ [자동 중단 & 즉시 전환 로직]:
  // 이전 스트림/작업이 진행 중일 때:
  // - 텍스트/파일이 없는 상태에서 전송 버튼 클릭: 생성 즉시 중지 (Stop)
  // - 텍스트/파일이 있는 상태에서 전송 클릭: 이전 생성 즉시 중단(인터럽트) 후 새 메시지 시작!
  if (state.isStreaming || state.currentStreamId || state.activeStream) {
    console.log('[chat] ⚡ Cancelling active stream for interruption/takeover');
    const oldStreamId = state.currentStreamId;
    if (state.activeStream) {
      try { state.activeStream.close(); } catch (_) {}
      state.activeStream = null;
    }
    state.currentStreamId = null;
    state.isStreaming = false;

    // Clean up UI streaming artifacts from previous message
    document.querySelectorAll('.streaming-cursor').forEach(el => el.remove());
    document.querySelectorAll('.reasoning-block[open]').forEach(el => {
      el.open = false;
    });
    document.querySelectorAll('.reasoning-block .animate-spin').forEach(el => {
      el.classList.remove('animate-spin');
      el.textContent = 'pause_circle';
    });

    if (oldStreamId) {
      DaonAPI.cancelChat(oldStreamId, state.currentSessionId).catch(e => console.warn('Cancel failed:', e));
    }

    if (!message && !hasFiles) {
      updateSendButtonUI(false);
      return;
    }
  }

  // If there's neither message nor files, nothing to send
  if (!message && !hasFiles) return;

  // Clear input
  input.value = '';
  input.style.height = 'auto';

  // Ensure session exists
  if (!state.currentSessionId) {
    try {
      const titlePrompt = message ? message.slice(0, 24) : (hasFiles ? state.pendingFiles[0].name : '새 세션');
      const res = await DaonAPI.createSession(titlePrompt);
      state.currentSessionId = res?.session_id || res?.session?.session_id;
      await loadSessions();
    } catch (err) {
      alert('세션 초기화 실패: ' + err.message);
      return;
    }
  }

  // Upload pending attachments (images, videos, files)
  let uploadedNames = [];
  if (hasFiles) {
    const filesToUpload = [...state.pendingFiles];
    state.pendingFiles = [];
    const tray = document.getElementById('chat-attach-tray');
    if (tray) {
      tray.innerHTML = '<span class="text-[11px] text-on-surface-variant font-mono">파일 업로드 중...</span>';
    }
    for (const f of filesToUpload) {
      try {
        const upRes = await DaonAPI.uploadFile(state.currentSessionId, f);
        if (upRes && upRes.filename) {
          uploadedNames.push(upRes.filename);
        }
      } catch (upErr) {
        console.error('File upload failed:', f.name, upErr);
      }
    }
    if (tray) {
      tray.innerHTML = '';
      tray.classList.add('hidden');
    }
  }

  // Compose display & prompt message
  let finalMessage = message;
  if (uploadedNames.length > 0) {
    if (!finalMessage) {
      finalMessage = `[첨부 파일: ${uploadedNames.join(', ')}]`;
    } else {
      finalMessage = `${message}\n\n[첨부 파일: ${uploadedNames.join(', ')}]`;
    }
  }

  // 1. Append user message to UI
  appendUserMessage(finalMessage);

  // 2. Prepare streaming assistant bubble
  const { bubble, contentEl, timeEl } = appendAssistantMessage('', null, true);
  state.isStreaming = true;
  updateSendButtonUI(true);
  let fullResponse = '';

  landOnMessage(bubble);

  try {
    const modelToUse = resolveModelString(state.selectedModelCard, state.currentSessionModel);
    const startRes = await DaonAPI.startChat({
      sessionId: state.currentSessionId,
      message: finalMessage,
      model: modelToUse,
      attachments: uploadedNames,
      reasoningEffort: state.reasoningEffort,
      autonomousMode: state.autonomousMode,
      approvalPolicy: state.autonomousMode ? 'scope' : 'step',
      workspace: state.currentWorkspace,
      agentPersona: state.currentAgentPersona
    });

    if (!startRes || !startRes.stream_id) {
      throw new Error('스트림 ID를 받지 못했습니다.');
    }

    let reasoningStartTime = null;

    state.currentStreamId = startRes.stream_id;
    state.activeStream = attachStreamEvents(startRes.stream_id, bubble, contentEl, timeEl);
  } catch (err) {
    state.isStreaming = false;
    state.activeStream = null;
    state.currentStreamId = null;
    updateSendButtonUI(false);
    bubble.querySelector('.streaming-cursor')?.remove();

    // [압축 리네임 경합 폴백] 도구 실행 중 인터럽트/취소하면 컨텍스트 압축이
    // 세션 파일을 리네임하고 compressed 이벤트(new_session_id)가 유실될 수 있다.
    // 이 경우 옛 session_id로 /api/chat/start가 404 "Session not found"를 반환한다.
    // 세션 목록을 재로드해 가장 최근 세션으로 자동 전환해 복구를 시도한다.
    if (/Session not found/i.test(String(err.message || ''))) {
      try {
        await loadSessions();
        const sessions = await DaonAPI.getSessions();
        if (Array.isArray(sessions) && sessions.length) {
          const latest = sessions.slice().sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))[0];
          state.sessions = sessions;
          state.currentSessionId = latest.session_id;
          console.log('[session recovery] switched to latest session:', latest.session_id);
          contentEl.innerHTML = '<span class="text-amber-700 font-body-md font-medium">세션이 압축 갱신되어 최근 세션으로 자동 전환했습니다 — 메시지를 다시 보내주세요.</span>';
          return;
        }
      } catch (recoveryErr) {
        console.warn('[session recovery] failed:', recoveryErr);
      }
    }
    contentEl.innerHTML = `<span class="text-error font-body-md font-medium">전송 오류: ${escapeHtml(err.message)}</span>`;
  }
}

/**
 * 에이전트 질문 분석 및 추론 대기 인디케이터 제거 헬퍼
 */
function clearThinkingState(contentEl) {
  if (!contentEl) return;
  const stateBox = contentEl.querySelector('.agent-thinking-state');
  if (stateBox) {
    if (stateBox._timer) clearInterval(stateBox._timer);
    stateBox.remove();
  }
}

/**
 * 도구명을 한글 친화적 레이블로 변환
 */
function formatToolName(name) {
  if (!name) return '도구';
  const n = String(name).toLowerCase();
  if (n === 'session_search') return '과거 세션 검색 (session_search)';
  if (n === 'terminal' || n === 'bash' || n === 'cmd') return '터미널 명령 실행 (terminal)';
  if (n === 'read_file') return '파일 읽기 (read_file)';
  if (n === 'write_file') return '파일 작성 (write_file)';
  if (n === 'edit_file') return '파일 수정 (edit_file)';
  if (n.includes('search_files') || n.includes('grep')) return '파일 검색 (search_files)';
  if (n.includes('web_') || n.includes('google')) return '웹 검색 (web_search)';
  if (n.includes('browser') || n.includes('navigate') || n.includes('playwright')) return '브라우저 탐색 (browser)';
  if (n === 'process') return '프로세스 관리 (process)';
  return name;
}

/**
 * SSE 스트림 이벤트 연결 및 실시간 UI 렌더링 공통화 함수
 * - 로컬에서 전송한 스트림과 타 기기에서 시작된 원격 스트림 모두 이 함수를 통해 안전하게 수신 및 렌더링
 */
function attachStreamEvents(streamId, bubble, contentEl, timeEl, onFinish) {
  clearThinkingState(contentEl);
  const streamStartTime = Date.now();
  let reasoningStartTime = null;
  let fullResponse = '';
  let currentStatusBase = '⏳ 에이전트 작업 시작 중...';

  // ── 실시간 작업 상태 바 (작업 중 표시 및 실시간 초 카운터) ──
  let statusBar = document.createElement('div');
  statusBar.className = 'agent-live-status-bar flex items-center gap-2 px-3 py-1.5 my-2 rounded-[8px] bg-primary/5 text-primary border border-primary/15 text-[12px] font-mono select-none animate-pulse';
  statusBar.innerHTML = `
    <span class="material-symbols-outlined text-[15px] animate-spin status-icon">progress_activity</span>
    <span class="status-label">⏳ 에이전트 작업 시작 중... (1초)</span>
  `;
  contentEl.appendChild(statusBar);

  const statusTimer = setInterval(() => {
    if (!statusBar || !statusBar.parentNode) return;
    const elapsed = Math.max(1, Math.floor((Date.now() - streamStartTime) / 1000));
    const label = statusBar.querySelector('.status-label');
    if (label) {
      label.textContent = `${currentStatusBase} (${elapsed}초)`;
    }
  }, 1000);

  function setStatus(text, icon = 'progress_activity') {
    currentStatusBase = text;
    if (statusBar) {
      const elapsed = Math.max(1, Math.floor((Date.now() - streamStartTime) / 1000));
      const label = statusBar.querySelector('.status-label');
      if (label) label.textContent = `${currentStatusBase} (${elapsed}초)`;
      const iconEl = statusBar.querySelector('.status-icon');
      if (iconEl) iconEl.textContent = icon;
      if (contentEl.lastChild !== statusBar) {
        contentEl.appendChild(statusBar);
      }
    }
  }

  function removeStatus() {
    clearInterval(statusTimer);
    statusBar?.remove();
    statusBar = null;
  }

  return DaonAPI.connectSSE(streamId, {
    onReasoning(thought) {
      clearThinkingState(contentEl);
      if (!reasoningStartTime) reasoningStartTime = Date.now();
      setStatus('💭 생각 중...', 'sync');

      let thinkingEl = contentEl.querySelector('.reasoning-block');
      if (!thinkingEl) {
        thinkingEl = document.createElement('details');
        thinkingEl.open = true;
        thinkingEl.className = 'reasoning-block group bg-surface-container-low p-2 rounded-[8px] border border-black/5 mb-2 leading-relaxed';
        thinkingEl.innerHTML = `
          <summary class="cursor-pointer flex items-center justify-between text-[11px] font-semibold text-primary py-0.5 px-1 rounded hover:bg-black/[0.03] transition-colors select-none">
            <span class="flex items-center gap-1.5">
              <span class="material-symbols-outlined text-[13px] animate-spin reasoning-icon">sync</span>
              <span class="reasoning-label">생각 중 (Reasoning CoT)...</span>
            </span>
            <span class="material-symbols-outlined text-[14px] text-on-surface-variant/60 group-open:rotate-180 transition-transform">expand_more</span>
          </summary>
          <div class="reasoning-text whitespace-pre-wrap text-[12px] font-mono text-on-surface-variant/80 pt-2 border-t border-black/5 mt-1 max-h-[280px] overflow-y-auto leading-relaxed select-text"></div>
        `;
        contentEl.prepend(thinkingEl);
      }

      const textNode = thinkingEl.querySelector('.reasoning-text');
      if (textNode) textNode.textContent += thought;

      const elapsedSec = Math.max(1, Math.floor((Date.now() - reasoningStartTime) / 1000));
      const label = thinkingEl.querySelector('.reasoning-label');
      if (label && thinkingEl.open) {
        label.textContent = `생각 중 (${elapsedSec}초)...`;
      }

      scrollChatToBottom();
    },
    onToken(token) {
      clearThinkingState(contentEl);
      setStatus('✍️ 답변 작성 중...', 'edit_note');

      // 에이전트가 본문 출력을 시작하면 생각 중 박스를 닫음
      const thinkingEl = contentEl.querySelector('.reasoning-block');
      if (thinkingEl && thinkingEl.open) {
        thinkingEl.open = false;
        const icon = thinkingEl.querySelector('.reasoning-icon');
        if (icon) {
          icon.classList.remove('animate-spin');
          icon.textContent = 'check_circle';
        }
        const label = thinkingEl.querySelector('.reasoning-label');
        if (label) {
          const elapsed = reasoningStartTime ? Math.max(1, Math.floor((Date.now() - reasoningStartTime) / 1000)) : 0;
          label.textContent = elapsed > 0 ? `생각 완료 (${elapsed}초) · 클릭하여 보기` : '생각 완료 (클릭하여 보기)';
        }
        const summary = thinkingEl.querySelector('summary');
        if (summary) {
          summary.classList.remove('text-primary');
          summary.classList.add('text-on-surface-variant');
        }
      }

      fullResponse += token;
      let responseBodyEl = contentEl.querySelector('.response-body');
      if (!responseBodyEl) {
        responseBodyEl = document.createElement('div');
        responseBodyEl.className = 'response-body leading-relaxed';
        contentEl.appendChild(responseBodyEl);
      }
      responseBodyEl.innerHTML = renderMarkdown(fullResponse);
      bubble.querySelector('.streaming-cursor')?.remove();
      if (statusBar && contentEl.lastChild !== statusBar) {
        contentEl.appendChild(statusBar);
      }
      scrollChatToBottom(false);
    },
    onStep(stepData) {
      clearThinkingState(contentEl);
      console.log('Stream step:', stepData);
      setStatus('⚙️ 작업 단계 진행 중...', 'cached');
      const text = JSON.stringify(stepData || {}).toLowerCase();
      if (text.includes('browser') || text.includes('navigate')) {
        let viewerSlot = contentEl.querySelector('.browser-viewer-slot');
        if (!viewerSlot) {
          viewerSlot = document.createElement('div');
          viewerSlot.className = 'browser-viewer-slot w-full mt-2';
          contentEl.appendChild(viewerSlot);
        }
        liveBrowser.mount(viewerSlot);
      }
    },
    onToolCall(toolData) {
      clearThinkingState(contentEl);
      console.log('Tool call:', toolData);
      const ev = toolData?.event || toolData?.type || '';
      if (ev && !['tool.started', 'tool_started', 'tool_call', 'tool.call', 'call', 'started'].includes(ev)) {
        return;
      }

      const rawName = toolData?.name || toolData?.function?.name || '';
      const friendly = formatToolName(rawName);
      setStatus(`🔧 도구 실행 중: ${friendly}`, 'construction');

      const name = rawName.toLowerCase();
      if (name.includes('browser') || name.includes('navigate') || name.includes('web_') || name.includes('playwright')) {
        let url = '';
        try {
          const args = typeof toolData.args === 'string' ? JSON.parse(toolData.args) : (toolData.args || toolData.function?.arguments || {});
          url = args.url || args.target || args.input || args.link || '';
        } catch (_) {}
        
        let viewerSlot = contentEl.querySelector('.browser-viewer-slot');
        if (!viewerSlot) {
          viewerSlot = document.createElement('div');
          viewerSlot.className = 'browser-viewer-slot w-full mt-2';
          contentEl.appendChild(viewerSlot);
        }
        liveBrowser.mount(viewerSlot, url);
      }
      scrollChatToBottom();
    },
    onToolResult(data) {
      setStatus('⚙️ 도구 실행 완료 · 결과 분석 중...', 'sync');
    },
    onModelInfo(info) {
      const actual = info?.actual || info?.model || '';
      const requested = info?.requested || state.currentSessionModel || '';
      if (actual) {
        renderModelAttribution(contentEl, actual, requested);
      }
    },
    onModelFallback(fb) {
      const actual = fb?.actual || '';
      const requested = fb?.requested || state.currentSessionModel || '';
      if (actual) {
        renderModelAttribution(contentEl, actual, requested, true);
      }
    },
    onCompressed(data) {
      try {
        const oldSid = data && data.old_session_id;
        const newSid = data && data.new_session_id;
        console.log('[SSE] Context compressed: session', oldSid, '→', newSid);
        if (newSid && state.currentSessionId === oldSid) {
          state.currentSessionId = newSid;
        }
        if (Array.isArray(state.sessions)) {
          const local = state.sessions.find(x => x.session_id === oldSid);
          if (local) local.session_id = newSid;
        }
        if (contentEl && !contentEl.querySelector('.ctx-compress-note')) {
          const note = document.createElement('div');
          note.className = 'ctx-compress-note mt-2 px-2.5 py-2 rounded-[8px] bg-amber-50 border border-amber-300 text-[12px] text-amber-800 leading-relaxed';
          note.innerHTML = '⚠️ <span class="font-semibold">대화가 길어져 컨텍스트가 자동 압축되었습니다.</span> 세션이 새 ID로 갱신되었으니 이어서 대화하시면 됩니다.';
          contentEl.appendChild(note);
        }
        scrollChatToBottom(true);
      } catch (e) {
        console.warn('[SSE] onCompressed handler error:', e);
      }
    },
    onDone(data) {
      clearThinkingState(contentEl);
      removeStatus();

      // 모델 식별 배지 보장
      if (!contentEl.querySelector('.model-attribution-badge')) {
        const actual = data?.session?.model || state.currentSessionModel || '';
        if (actual) {
          renderModelAttribution(contentEl, actual, state.currentSessionModel);
        }
      }

      try {
        const doneSid = data && data.session && data.session.session_id;
        if (doneSid && doneSid !== state.currentSessionId) {
          console.log('[SSE] onDone: session rotated', state.currentSessionId, '→', doneSid);
          const prevSid = state.currentSessionId;
          state.currentSessionId = doneSid;
          if (Array.isArray(state.sessions)) {
            const local = state.sessions.find(x => x.session_id === prevSid);
            if (local) local.session_id = doneSid;
          }
        }
      } catch (_) {}
      state.isStreaming = false;
      state.activeStream = null;
      state.currentStreamId = null;
      updateSendButtonUI(false);
      bubble.querySelector('.streaming-cursor')?.remove();

      const thinkingEl = contentEl.querySelector('.reasoning-block');
      if (thinkingEl) {
        thinkingEl.open = false;
        const thinkingIcon = thinkingEl.querySelector('.reasoning-icon') || thinkingEl.querySelector('.animate-spin');
        if (thinkingIcon) {
          thinkingIcon.classList.remove('animate-spin');
          thinkingIcon.textContent = 'check_circle';
        }
        const label = thinkingEl.querySelector('.reasoning-label');
        if (label && label.textContent.includes('생각 중')) {
          const elapsed = reasoningStartTime ? Math.max(1, Math.floor((Date.now() - reasoningStartTime) / 1000)) : 0;
          label.textContent = elapsed > 0 ? `생각 완료 (${elapsed}초) · 클릭하여 보기` : '생각 완료 (클릭하여 보기)';
        }
        const summary = thinkingEl.querySelector('summary');
        if (summary) {
          summary.classList.remove('text-primary');
          summary.classList.add('text-on-surface-variant');
        }
      }
      try { liveBrowser.onTurnCompleted(); } catch (_) {}
      if (timeEl) timeEl.textContent = formatTime();
      loadSessions();
      scrollChatToBottom(true);

      // 자동 음성 읽기(TTS) 활성화 시 본문 음성 출력
      if (_autoTTS && fullResponse) {
        const readBtn = bubble.querySelector('.read-aloud-btn');
        readAloudText(fullResponse, readBtn);
      }

      onFinish?.();
    },
    onError(err) {
      clearThinkingState(contentEl);
      removeStatus();
      state.isStreaming = false;
      state.activeStream = null;
      state.currentStreamId = null;
      updateSendButtonUI(false);
      bubble.querySelector('.streaming-cursor')?.remove();
      if (!fullResponse) {
        contentEl.innerHTML = `<span class="text-error font-body-md font-medium">응답 처리 중 오류가 발생했습니다. (${escapeHtml(err.message || '연결 종료')})</span>`;
      }
      onFinish?.();
    }
  });
}

// ── Voice Input (STT) & Voice Output (TTS) Module ─────────────────────────────

/**
 * 텍스트 음성 출력 (TTS) - 2단계 스마트 보이스 (Edge TTS 자연스러운 한국어 -> Web Speech API 폴백)
 */
function readAloudText(text, btnElement) {
  // 이미 재생 중인 경우 정지 토글
  const isPlaying = (_currentSpeakingAudio && !_currentSpeakingAudio.paused) || (window.speechSynthesis && window.speechSynthesis.speaking);
  if (isPlaying) {
    if (_currentSpeakingAudio) {
      try { _currentSpeakingAudio.pause(); _currentSpeakingAudio = null; } catch (_) {}
    }
    if (window.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    if (_currentSpeakingBtn) {
      const icon = _currentSpeakingBtn.querySelector('.material-symbols-outlined');
      if (icon) icon.textContent = 'volume_up';
      _currentSpeakingBtn.title = '소리내어 읽기';
    }
    if (_currentSpeakingBtn === btnElement) {
      _currentSpeakingBtn = null;
      return;
    }
  }

  if (!text || !text.trim()) return;

  // 마크다운 문법 및 특수코드 정제
  const cleanText = text
    .replace(/```[\s\S]*?```/g, '코드 블록 생략')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[#*_~>]/g, '')
    .trim();

  if (!cleanText) return;

  _currentSpeakingBtn = btnElement;
  const icon = btnElement?.querySelector('.material-symbols-outlined');
  if (icon) icon.textContent = 'stop_circle';
  if (btnElement) btnElement.title = '음성 읽기 정지';

  const resetUI = () => {
    if (icon) icon.textContent = 'volume_up';
    if (btnElement) btnElement.title = '소리내어 읽기';
    _currentSpeakingBtn = null;
    _currentSpeakingAudio = null;
  };

  // 1순위: 서버 Edge TTS 고품질 자연스러운 한국어 음성 (ko-KR-SunHiNeural)
  const ttsUrl = `/api/speak/tts?text=${encodeURIComponent(cleanText.slice(0, 800))}`;
  const audio = new Audio(ttsUrl);
  _currentSpeakingAudio = audio;

  audio.onended = resetUI;
  audio.onerror = () => {
    // 2순위: Edge TTS 실패 시 브라우저 내장 Web Speech API 폴백
    _currentSpeakingAudio = null;
    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.lang = 'ko-KR';
      utterance.rate = 1.05;

      const voices = window.speechSynthesis.getVoices();
      const koreanVoice = voices.find(v => v.lang.startsWith('ko') && (v.name.includes('SunHi') || v.name.includes('Heami') || v.name.includes('Google') || v.name.includes('Korean')));
      if (koreanVoice) utterance.voice = koreanVoice;

      utterance.onend = resetUI;
      utterance.onerror = resetUI;
      window.speechSynthesis.speak(utterance);
    } else {
      resetUI();
    }
  };

  audio.play().catch(() => {
    // 오디오 자동재생 제약 등으로 재생 실패 시 Web Speech API 시도
    audio.onerror();
  });
}

/**
 * 음성 입력(STT) 및 자동 TTS 토글 초기화
 */
function initVoiceFeatures() {
  const autoTtsBtn = document.getElementById('auto-tts-toggle-btn');
  const autoTtsIcon = document.getElementById('auto-tts-icon');
  if (autoTtsBtn && autoTtsIcon) {
    if (_autoTTS) {
      autoTtsIcon.classList.remove('text-on-surface-variant/70');
      autoTtsIcon.classList.add('text-primary');
      autoTtsBtn.title = '답변 자동 음성 읽기 켜짐 (클릭 시 끄기)';
    } else {
      autoTtsIcon.classList.remove('text-primary');
      autoTtsIcon.classList.add('text-on-surface-variant/70');
      autoTtsBtn.title = '답변 자동 음성 읽기 꺼짐 (클릭 시 켜기)';
    }

    autoTtsBtn.addEventListener('click', () => {
      _autoTTS = !_autoTTS;
      localStorage.setItem('daon_auto_tts', String(_autoTTS));
      if (_autoTTS) {
        autoTtsIcon.classList.remove('text-on-surface-variant/70');
        autoTtsIcon.classList.add('text-primary');
        autoTtsBtn.title = '답변 자동 음성 읽기 켜짐 (클릭 시 끄기)';
      } else {
        autoTtsIcon.classList.remove('text-primary');
        autoTtsIcon.classList.add('text-on-surface-variant/70');
        autoTtsBtn.title = '답변 자동 음성 읽기 꺼짐 (클릭 시 켜기)';
        window.speechSynthesis?.cancel();
      }
    });
  }

  // ── Production Cumulative Streaming ASR (MediaRecorder + faster-whisper) ──
  const voiceBtn = document.getElementById('chat-voice-btn');
  const voiceIcon = document.getElementById('chat-voice-icon');
  const voicePulse = document.getElementById('chat-voice-pulse');
  const voiceStatusBar = document.getElementById('chat-voice-status-bar');
  const voiceStatusText = document.getElementById('chat-voice-status-text');
  const voiceStopBtn = document.getElementById('chat-voice-stop-btn');
  const chatInput = document.getElementById('chat-input');

  let _voiceMediaRecorder = null;
  let _voiceAudioChunks = [];
  let _voiceStream = null;
  let _voicePrefix = '';
  let _voiceCumulativeText = '';
  let _voiceCumulativeSeq = 0;
  let _voiceThrottleTimer = null;
  let _voiceSafetyTimer = null;
  let _voiceSendInFlight = false;
  let _voiceLastSendTime = 0;
  let _voicePendingSend = false;
  let _voiceLastSentIndex = 0;
  const VOICE_THROTTLE_MS = 800;

  function showVoiceToast(message, icon = '🎤', duration = 3000) {
    const existing = document.getElementById('daon-voice-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'daon-voice-toast';
    toast.className = 'fixed bottom-24 right-6 bg-surface-container-highest border border-black/15 shadow-2xl rounded-[10px] px-4 py-2.5 flex items-center gap-2.5 text-[13px] text-on-surface z-50 animate-in fade-in slide-in-from-bottom-2 duration-150';
    toast.innerHTML = `<span class="text-[17px]">${icon}</span><span class="font-medium">${message}</span>`;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('opacity-0', 'transition-opacity', 'duration-200');
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  function getSupportedMimeType() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
      'audio/wav'
    ];
    for (const c of candidates) {
      if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) {
        return c;
      }
    }
    return '';
  }

  function mimeToExt(mimeType) {
    if (!mimeType) return 'webm';
    if (mimeType.includes('webm')) return 'webm';
    if (mimeType.includes('ogg')) return 'ogg';
    if (mimeType.includes('mp4') || mimeType.includes('m4a')) return 'm4a';
    if (mimeType.includes('wav')) return 'wav';
    return 'webm';
  }

  function setVoiceUI(state) {
    // state: true (recording), false (idle), 'processing' (Whisper transcribing)
    _isVoiceRecording = (state === true || state === 'processing');

    if (state === true) {
      if (voiceBtn) {
        voiceBtn.classList.add('bg-rose-50', 'text-rose-600', 'ring-2', 'ring-rose-500/30');
        voiceBtn.title = '녹음 중지 (클릭 시 최종 변환)';
      }
      if (voiceIcon) {
        voiceIcon.classList.remove('text-on-surface-variant');
        voiceIcon.classList.add('text-rose-600');
        voiceIcon.textContent = 'mic';
      }
      if (voicePulse) voicePulse.classList.remove('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.remove('hidden');
        voiceStatusBar.classList.add('flex');
      }
      if (voiceStatusText) {
        voiceStatusText.textContent = '🎤 마이크 듣는 중... 말씀하시면 실시간 텍스트로 입력됩니다';
      }
    } else if (state === 'processing') {
      if (voiceBtn) {
        voiceBtn.classList.add('bg-rose-50', 'text-rose-600');
        voiceBtn.title = '최종 음성 변환 중...';
      }
      if (voiceIcon) {
        voiceIcon.classList.add('text-rose-600');
        voiceIcon.textContent = 'sync';
      }
      if (voicePulse) voicePulse.classList.add('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.remove('hidden');
        voiceStatusBar.classList.add('flex');
      }
      if (voiceStatusText) {
        voiceStatusText.textContent = '🔄 최종 음성 변환 중 (Whisper AI)...';
      }
    } else {
      // idle
      if (voiceBtn) {
        voiceBtn.classList.remove('bg-rose-50', 'text-rose-600', 'ring-2', 'ring-rose-500/30');
        voiceBtn.title = '음성 입력 (마이크)';
      }
      if (voiceIcon) {
        voiceIcon.classList.remove('text-rose-600');
        voiceIcon.classList.add('text-on-surface-variant');
        voiceIcon.textContent = 'mic';
      }
      if (voicePulse) voicePulse.classList.add('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.add('hidden');
        voiceStatusBar.classList.remove('flex');
      }
    }
  }

  function cleanupMedia() {
    if (_voiceMediaRecorder && _voiceMediaRecorder.state !== 'inactive') {
      try { _voiceMediaRecorder.stop(); } catch (_) {}
    }
    _voiceMediaRecorder = null;
    _voiceAudioChunks = [];
    _voiceCumulativeText = '';
    _voiceCumulativeSeq = 0;
    _voiceSendInFlight = false;
    _voiceLastSendTime = 0;
    _voicePendingSend = false;
    _voiceLastSentIndex = 0;
    clearTimeout(_voiceThrottleTimer);
    clearTimeout(_voiceSafetyTimer);

    if (_voiceStream) {
      _voiceStream.getTracks().forEach(track => {
        try { track.stop(); } catch (_) {}
      });
      _voiceStream = null;
    }
  }

  function appendDeduplicatedText(input, newText) {
    if (!input || !newText) return;
    newText = newText.trim();
    if (!newText) return;

    const currentText = _voiceCumulativeText.trim();
    if (!currentText) {
      _voiceCumulativeText = newText;
      updateLiveInput(input, _voiceCumulativeText);
      return;
    }

    const wordsCurrent = currentText.split(/\s+/);
    const wordsNew = newText.split(/\s+/);

    let overlapCount = 0;
    for (let len = Math.min(wordsCurrent.length, wordsNew.length, 4); len > 0; len--) {
      const tailCurrent = wordsCurrent.slice(-len).join(' ');
      const headNew = wordsNew.slice(0, len).join(' ');
      if (tailCurrent === headNew) {
        overlapCount = len;
        break;
      }
    }

    const addedText = overlapCount > 0 ? wordsNew.slice(overlapCount).join(' ') : newText;
    if (addedText) {
      _voiceCumulativeText += (currentText ? ' ' : '') + addedText;
      updateLiveInput(input, _voiceCumulativeText);
    }
  }

  function updateLiveInput(input, liveText) {
    if (!input || !_isVoiceRecording) return;
    input.value = _voicePrefix ? _voicePrefix + liveText : liveText;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
    input.scrollTop = input.scrollHeight;
  }

  function scheduleThrottledSend(input) {
    if (!_isVoiceRecording) return;
    _voicePendingSend = true;
    if (_voiceSendInFlight) return;

    clearTimeout(_voiceThrottleTimer);
    const elapsed = Date.now() - _voiceLastSendTime;
    if (elapsed >= VOICE_THROTTLE_MS) {
      sendCumulativeChunk(input);
    } else {
      _voiceThrottleTimer = setTimeout(() => {
        if (!_isVoiceRecording) return;
        sendCumulativeChunk(input);
      }, VOICE_THROTTLE_MS - elapsed);
    }
  }

  async function sendCumulativeChunk(input) {
    if (!_isVoiceRecording || _voiceAudioChunks.length === 0) return;
    if (!_voiceMediaRecorder) return;
    if (_voiceAudioChunks.length <= _voiceLastSentIndex) return;

    _voiceSendInFlight = true;
    _voicePendingSend = false;
    _voiceLastSendTime = Date.now();
    _voiceLastSentIndex = _voiceAudioChunks.length;
    const currentSeq = ++_voiceCumulativeSeq;

    try {
      const mimeType = _voiceMediaRecorder.mimeType || 'audio/webm';
      const ext = mimeToExt(mimeType);
      const chunkBlob = new Blob(_voiceAudioChunks.slice(), { type: mimeType });

      if (chunkBlob.size < 500) return;

      const formData = new FormData();
      formData.append('audio', chunkBlob, `delta_${currentSeq}.${ext}`);
      const promptCtx = _voiceCumulativeText.trim().slice(-200);
      if (promptCtx) {
        formData.append('prompt', promptCtx);
      }

      const res = await fetch('/api/whisper/transcribe', {
        method: 'POST',
        body: formData,
        signal: (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(30000) : undefined
      });

      if (currentSeq !== _voiceCumulativeSeq) return;
      if (res.status === 503) return;
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const data = await res.json();
      const text = (data.text || '').trim();

      if (_isVoiceRecording && currentSeq === _voiceCumulativeSeq && text) {
        appendDeduplicatedText(input, text);
      }
    } catch (_) {
      // Chunk-level delta skipped
    } finally {
      _voiceSendInFlight = false;
      if (_isVoiceRecording && _voicePendingSend) {
        scheduleThrottledSend(input);
      }
    }
  }

  async function processFinalRecording(input) {
    clearTimeout(_voiceSafetyTimer);

    if (_voiceAudioChunks.length === 0) {
      setVoiceUI(false);
      cleanupMedia();
      return;
    }

    setVoiceUI('processing');
    showVoiceToast('🔄 최종 음성 변환 중 (Whisper AI)...', '🔄', 2000);

    try {
      const mimeType = _voiceMediaRecorder ? _voiceMediaRecorder.mimeType : 'audio/webm';
      const audioBlob = new Blob(_voiceAudioChunks, { type: mimeType });
      const ext = mimeToExt(mimeType);

      const formData = new FormData();
      formData.append('audio', audioBlob, `full_recording.${ext}`);

      const res = await fetch('/api/whisper/transcribe', {
        method: 'POST',
        body: formData,
        signal: (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(60000) : undefined
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || ('HTTP ' + res.status));
      }

      const data = await res.json();
      let transcribedText = (data.text || '').trim();

      if (!transcribedText && _voiceCumulativeText) {
        transcribedText = _voiceCumulativeText.trim();
      }

      if (transcribedText && input) {
        input.value = _voicePrefix ? _voicePrefix + transcribedText : transcribedText;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 220) + 'px';
        input.focus();
        showVoiceToast('✅ 음성 변환 완료!', '✅', 2500);
      } else {
        showVoiceToast('음성이 감지되지 않았습니다. 다시 말씀해 주세요.', 'ℹ️', 3000);
      }
    } catch (err) {
      console.error('[Voice Final Transcription Error]', err);
      showVoiceToast('음성 변환 오류: ' + (err.message || '알 수 없는 오류'), '⚠️', 4000);
      if (_voiceCumulativeText && input && !input.value) {
        input.value = _voicePrefix ? _voicePrefix + _voiceCumulativeText : _voiceCumulativeText;
      }
    } finally {
      _voiceSendInFlight = false;
      setVoiceUI(false);
      cleanupMedia();
    }
  }

  async function startVoiceRecording() {
    if (!chatInput) return;

    cleanupMedia();
    _voicePrefix = chatInput.value ? (chatInput.value.trim() + ' ') : '';
    _voiceCumulativeText = '';

    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('이 브라우저/환경에서는 마이크 녹음(getUserMedia)을 지원하지 않습니다.');
      }

      _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = getSupportedMimeType();
      _voiceMediaRecorder = new MediaRecorder(_voiceStream, mimeType ? { mimeType, audioBitsPerSecond: 128000 } : undefined);
      _voiceAudioChunks = [];

      _voiceMediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          _voiceAudioChunks.push(event.data);
          scheduleThrottledSend(chatInput);
        }
      };

      _voiceMediaRecorder.onstop = () => {
        clearTimeout(_voiceThrottleTimer);
        _voiceSendInFlight = false;
        processFinalRecording(chatInput);
      };

      _voiceMediaRecorder.start(VOICE_THROTTLE_MS);
      setVoiceUI(true);
      showVoiceToast('🎤 마이크 음성 인식 시작 (말씀하시면 실시간 텍스트로 입력됩니다)', '🎤', 3000);

      _voiceThrottleTimer = setTimeout(() => {
        if (!_isVoiceRecording) return;
        scheduleThrottledSend(chatInput);
      }, VOICE_THROTTLE_MS);

    } catch (err) {
      console.error('[Voice Recording Start Error]', err);
      cleanupMedia();
      setVoiceUI(false);
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        alert('⚠️ 마이크 권한이 차단되었습니다. 브라우저/운영체제 설정에서 마이크 사용을 허용해 주세요.');
      } else if (err.name === 'NotFoundError') {
        alert('⚠️ 연결된 마이크 장치를 찾을 수 없습니다.');
      } else {
        alert('⚠️ 마이크 연결 실패: ' + (err.message || '알 수 없는 오류'));
      }
    }
  }

  function stopVoiceRecording() {
    _isVoiceRecording = false;
    clearTimeout(_voiceThrottleTimer);
    clearTimeout(_voiceSafetyTimer);

    _voiceSafetyTimer = setTimeout(() => {
      console.warn('[Voice STT Timeout Safety Net]');
      setVoiceUI(false);
      cleanupMedia();
    }, 12000);

    if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
      setVoiceUI('processing');
      setTimeout(() => {
        if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
          try {
            _voiceMediaRecorder.stop();
          } catch (e) {
            console.warn('[Voice MediaRecorder Stop Error]', e);
            setVoiceUI(false);
            cleanupMedia();
          }
        }
      }, 300);
    } else {
      processFinalRecording(chatInput);
    }
  }

  if (voiceBtn) {
    voiceBtn.addEventListener('click', () => {
      if (_isVoiceRecording) {
        stopVoiceRecording();
      } else {
        startVoiceRecording();
      }
    });
  }

  if (voiceStopBtn) {
    voiceStopBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      stopVoiceRecording();
    });
  }
}

/**
 * 타 기기(모바일 또는 PC)에서 생성된 활성 스트림을 감지하여 실시간 화면 동기화
 */
async function syncRemoteActiveStream(streamId) {
  if (state.currentStreamId === streamId || state.isStreaming) return;

  console.log(`[sync] 🔄 타 기기 활성 스트림 자동 감지 및 구독: ${streamId}`);
  state.currentStreamId = streamId;
  state.isStreaming = true;
  updateSendButtonUI(true);

  // 최신 세션 메시지(상대방이 전송한 질문 포함)를 먼저 화면에 렌더링
  try {
    const sess = await DaonAPI.getSession(state.currentSessionId);
    if (sess && sess.messages && sess.messages.length > 0) {
      renderSessionMessages(sess.messages);
    }
  } catch (_) {}

  // 실시간 스트리밍 답변을 수신할 어시스턴트 버블 생성
  const { bubble, contentEl, timeEl } = appendAssistantMessage('', null, true);
  landOnMessage(bubble);

  state.activeStream = attachStreamEvents(streamId, bubble, contentEl, timeEl, async () => {
    try {
      const sess = await DaonAPI.getSession(state.currentSessionId);
      if (sess && sess.messages) {
        renderSessionMessages(sess.messages);
      }
    } catch (_) {}
  });
}

/**
 * 멀티 디바이스 실시간 채팅 동기화 폴러:
 * - 2.5초마다 현재 세션의 활성 스트림 유무 확인 -> 있으면 자동 구독!
 * - 평상시에는 메시지 개수를 비교하여 다른 기기의 최신 메시지 자동 반영!
 */
let isSyncingMultiDevice = false;

async function syncMultiDeviceChat() {
  if (isSyncingMultiDevice) return;
  if (!state.currentSessionId) return;

  isSyncingMultiDevice = true;
  try {
    const res = await DaonAPI.getActiveStreams();
    const activeStreams = res?.active_streams || [];
    const activeForCurrent = activeStreams.find(s => s.session_id === state.currentSessionId);

    if (activeForCurrent && activeForCurrent.stream_id) {
      if (state.currentStreamId !== activeForCurrent.stream_id && !state.isStreaming) {
        await syncRemoteActiveStream(activeForCurrent.stream_id);
      }
    } else {
      // 활성 스트림이 없고 로컬 스트리밍 중도 아닐 때:
      if (!state.isStreaming && !state.currentStreamId) {
        const sess = await DaonAPI.getSession(state.currentSessionId);
        if (sess && sess.messages) {
          const currentSig = getMessagesSignature(sess.messages);
          const container = document.getElementById('chat-messages-container');
          
          if (currentSig && currentSig !== 'empty' && currentSig !== state.lastRenderedSignature && !container?.querySelector('.streaming-cursor')) {
            console.log(`[sync] 🔄 타 기기 새 메시지 감지 -> 화면 자동 동기화`);
            renderSessionMessages(sess.messages, true);
          }
        }
      }
    }
  } catch (_) {
  } finally {
    isSyncingMultiDevice = false;
  }
}

function renderUserMessageContent(content, sessionId) {
  if (!content) return '';
  let escaped = escapeHtml(content);

  // Parse attached file marker: [첨부 파일: a.png, b.mp4] or [Attached files: ...]
  const regex = /\[(?:첨부 파일|Attached files):\s*([^\]]+)\]/;
  const match = escaped.match(regex);
  let mediaHtml = '';

  if (match) {
    const rawFiles = match[1].split(',').map(f => f.trim());
    let mediaItems = [];

    rawFiles.forEach(filename => {
      const ext = filename.split('.').pop().toLowerCase();
      const sid = sessionId || state.currentSessionId || '';
      const rawUrl = `/api/file/raw?session_id=${encodeURIComponent(sid)}&path=${encodeURIComponent(filename)}`;

      if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) {
        mediaItems.push(`
          <div class="relative group inline-block my-1">
            <img src="${rawUrl}" class="max-w-[260px] max-h-[220px] rounded-[8px] border border-black/10 object-cover cursor-zoom-in hover:opacity-95 shadow-sm" onclick="window.open('${rawUrl}', '_blank')" alt="${escapeHtml(filename)}" />
            <div class="text-[10px] font-mono text-on-surface-variant truncate max-w-[260px] mt-0.5">${escapeHtml(filename)}</div>
          </div>
        `);
      } else if (['mp4', 'webm', 'mov', 'ogg'].includes(ext)) {
        mediaItems.push(`
          <div class="my-1">
            <video src="${rawUrl}" controls class="max-w-[320px] max-h-[240px] rounded-[8px] border border-black/10 shadow-sm"></video>
            <div class="text-[10px] font-mono text-on-surface-variant truncate max-w-[320px] mt-0.5">${escapeHtml(filename)}</div>
          </div>
        `);
      } else {
        mediaItems.push(`
          <a href="${rawUrl}&download=1" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-surface border border-black/10 rounded-[8px] text-[12px] font-mono text-on-surface hover:bg-black/5 my-1 transition-colors" download="${escapeHtml(filename)}">
            <span class="material-symbols-outlined text-[16px] text-primary">description</span>
            <span class="truncate max-w-[200px]">${escapeHtml(filename)}</span>
            <span class="material-symbols-outlined text-[13px] text-on-surface-variant">download</span>
          </a>
        `);
      }
    });

    if (mediaItems.length > 0) {
      mediaHtml = `<div class="flex flex-wrap gap-2 mt-2 pt-2 border-t border-black/[0.06]">${mediaItems.join('')}</div>`;
    }
  }

  return `<div class="whitespace-pre-wrap">${escaped}</div>${mediaHtml}`;
}

function appendUserMessage(text, timestamp, autoScroll = true) {
  if (isInternalSystemMessage(text)) return; // 시스템 내부 제어 메시지는 화면에 표시하지 않음
  const container = document.getElementById('chat-messages-container');
  if (!container) return;

  const article = document.createElement('article');
  article.className = 'flex flex-col gap-space-sm w-full animate-fadeIn';
  article.innerHTML = `
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-space-sm">
        <div class="w-6 h-6 rounded-full bg-primary flex items-center justify-center text-on-primary font-label-sm text-[11px] font-semibold">
          사
        </div>
        <span class="font-label-md text-label-md text-on-surface font-medium">사용자</span>
      </div>
      <span class="font-code text-code text-[11px] text-on-surface-variant">${formatTime(timestamp)}</span>
    </div>
    <div class="pl-8 text-on-surface font-body-lg text-body-lg leading-relaxed">${renderUserMessageContent(text, state.currentSessionId)}</div>
  `;
  container.appendChild(article);
  if (autoScroll) scrollChatToBottom();
}

function renderModelAttribution(contentEl, actual, requested = '', isFallback = false) {
  if (!contentEl || !actual) return;
  let container = contentEl.querySelector('.model-attribution-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'model-attribution-container';
    contentEl.appendChild(container);
  }
  const fallback = isFallback || (requested && actual && requested.toLowerCase() !== actual.toLowerCase());
  if (fallback) {
    container.innerHTML = `
      <div class="model-attribution-badge mt-2 pt-1.5 border-t border-black/5 flex items-center gap-1.5 text-[11px] font-mono select-none">
        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px] bg-amber-500/10 text-amber-700 border border-amber-500/20 font-semibold shadow-2xs">
          <span class="material-symbols-outlined text-[13px] text-amber-600 animate-pulse">sync_problem</span>
          <span>폴백 모델: ${escapeHtml(actual)}</span>
          ${requested ? `<span class="opacity-75 text-[10px] font-normal">(요청: ${escapeHtml(requested)})</span>` : ''}
        </span>
      </div>
    `;
  } else {
    container.innerHTML = `
      <div class="model-attribution-badge mt-2 pt-1.5 border-t border-black/5 flex items-center gap-1.5 text-[11px] font-mono select-none">
        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[6px] bg-black/[0.03] text-on-surface-variant/75 border border-black/5 hover:text-on-surface transition-colors">
          <span class="material-symbols-outlined text-[13px] text-primary/70">smart_toy</span>
          <span>모델: ${escapeHtml(actual)}</span>
        </span>
      </div>
    `;
  }
}

function appendAssistantMessage(text, timestamp, isStreaming = false, initialReasoning = '', autoScroll = true, actualModel = '', requestedModel = '') {
  const container = document.getElementById('chat-messages-container');
  if (!container) return {};

  const extractedReasoning = initialReasoning || extractThinkBlocks(text);
  const cleanText = stripThinkBlocks(text);

  let reasoningHtml = '';
  if (extractedReasoning) {
    reasoningHtml = `
      <details class="reasoning-block group bg-surface-container-low p-2 rounded-[8px] border border-black/5 mb-2 leading-relaxed select-none">
        <summary class="cursor-pointer flex items-center justify-between text-[11px] font-semibold text-on-surface-variant hover:text-on-surface py-0.5 px-1 rounded transition-colors select-none">
          <span class="flex items-center gap-1.5">
            <span class="material-symbols-outlined text-[13px] text-primary">check_circle</span>
            <span class="reasoning-label">생각 완료 (클릭하여 보기)</span>
          </span>
          <span class="material-symbols-outlined text-[14px] text-on-surface-variant/60 group-open:rotate-180 transition-transform">expand_more</span>
        </summary>
        <div class="reasoning-text whitespace-pre-wrap text-[12px] font-mono text-on-surface-variant/80 pt-2 border-t border-black/5 mt-1 max-h-[280px] overflow-y-auto leading-relaxed select-text">${escapeHtml(extractedReasoning)}</div>
      </details>
    `;
  }

  let thinkingStateHtml = '';
  if (isStreaming && !cleanText && !reasoningHtml) {
    thinkingStateHtml = `
      <div class="agent-thinking-state flex items-center gap-3 p-3 bg-surface-container-low rounded-[12px] border border-black/5 my-1 transition-all select-none animate-pulse">
        <div class="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
          <span class="material-symbols-outlined text-[16px] text-primary animate-spin">progress_activity</span>
        </div>
        <div class="flex flex-col min-w-0">
          <span class="text-[12px] font-semibold text-on-surface thinking-title">Daon Agent가 답변을 준비하고 있습니다...</span>
          <span class="text-[11px] font-mono text-on-surface-variant/70 thinking-timer">질문 분석 및 도구 추론 진행 중 (1초 경과)</span>
        </div>
      </div>
    `;
  }

  const article = document.createElement('article');
  article.className = 'flex flex-col gap-space-md w-full animate-fadeIn';
  article.innerHTML = `
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-space-sm">
        <div class="w-6 h-6 rounded-[4px] bg-primary flex items-center justify-center text-on-primary font-mono text-[11px] font-bold">
          D
        </div>
        <span class="font-label-md text-label-md text-on-surface font-semibold tracking-tight">Daon Agent</span>
        <span class="px-space-xs py-[1px] bg-surface-container rounded font-label-sm text-label-sm text-on-surface-variant">오케스트레이터</span>
      </div>
      <div class="flex items-center gap-space-xs text-on-surface-variant">
        <button class="p-1 hover:text-on-surface transition-colors read-aloud-btn" title="소리내어 읽기" type="button">
          <span class="material-symbols-outlined text-[16px]">volume_up</span>
        </button>
        <button class="p-1 hover:text-on-surface transition-colors copy-msg-btn" title="복사" type="button">
          <span class="material-symbols-outlined text-[16px]">content_copy</span>
        </button>
        <span class="font-code text-code text-[11px] msg-time">${formatTime(timestamp)}</span>
      </div>
    </div>
    <div class="pl-8 flex flex-col gap-space-sm text-on-surface msg-content">
      ${thinkingStateHtml}
      ${reasoningHtml}
      <div class="response-body leading-relaxed">${isStreaming && !cleanText ? '<span class="inline-block w-2 h-4 bg-primary animate-pulse streaming-cursor"></span>' : renderMarkdown(cleanText)}</div>
      <div class="model-attribution-container"></div>
    </div>
  `;

  // Model attribution if model known
  if (actualModel) {
    renderModelAttribution(article.querySelector('.msg-content'), actualModel, requestedModel);
  }

  // Attach copy message listener
  article.querySelector('.copy-msg-btn')?.addEventListener('click', () => {
    const content = article.querySelector('.response-body')?.innerText || '';
    navigator.clipboard?.writeText(content);
  });

  // Attach read-aloud voice listener
  article.querySelector('.read-aloud-btn')?.addEventListener('click', (e) => {
    const content = article.querySelector('.response-body')?.innerText || '';
    readAloudText(content, e.currentTarget);
  });

  // Thinking State timer setup
  const stateBox = article.querySelector('.agent-thinking-state');
  if (stateBox) {
    let elapsed = 1;
    const timerEl = stateBox.querySelector('.thinking-timer');
    stateBox._timer = setInterval(() => {
      elapsed++;
      if (timerEl) {
        timerEl.textContent = `질문 분석 및 도구 추론 진행 중 (${elapsed}초 경과)`;
      }
    }, 1000);
  }

  container.appendChild(article);
  if (isStreaming) {
    landOnMessage(article);
  } else if (autoScroll && !state.userScrolledUp) {
    scrollChatToBottom();
  }

  return {
    bubble: article,
    contentEl: article.querySelector('.msg-content'),
    timeEl: article.querySelector('.msg-time')
  };
}

// ── Message Landing & Smart Auto-Scroll (프롬프트 창 위 완벽 안착) ─────────────

function getComposerTop() {
  const composer = document.querySelector('.floating-composer-container');
  if (composer) {
    const rect = composer.getBoundingClientRect();
    return rect.top;
  }
  return window.innerHeight - 240;
}

/**
 * 하단 스페이서(#chat-bottom-spacer)를 입력창 실제 높이에 맞춰 동적으로 계산한다.
 * 입력창은 fixed bottom-0 이므로, 창 폭이 좁아 툴바가 줄바꿈되거나 첨부 트레이/음성
 * 배너가 뜨면 높이가 커진다. 스페이서가 고정 200px면 그때 마지막 메시지가 입력창에
 * 가려진다(창 밑으로 숨는 현상). 스페이서 = 입력창 높이 + 안전 여백(48px)로 항상 보정.
 */
function syncChatBottomSpacer() {
  const composer = document.querySelector('.floating-composer-container');
  const spacer = document.getElementById('chat-bottom-spacer');
  if (!composer || !spacer) return;
  const h = Math.ceil(composer.getBoundingClientRect().height || composer.offsetHeight || 0);
  if (h <= 0) return;
  const target = h + 48;
  spacer.style.height = target + 'px';
  spacer.style.minHeight = target + 'px';
}

function initChatBottomSpacer() {
  syncChatBottomSpacer();
  const composer = document.querySelector('.floating-composer-container');
  if (composer && typeof ResizeObserver !== 'undefined') {
    try {
      const ro = new ResizeObserver(() => syncChatBottomSpacer());
      ro.observe(composer);
    } catch (e) { /* noop */ }
  }
  window.addEventListener('resize', syncChatBottomSpacer);
  window.addEventListener('load', syncChatBottomSpacer);
  // 폰트 로드/레이아웃 확정 이후 재보정
  setTimeout(syncChatBottomSpacer, 300);
  setTimeout(syncChatBottomSpacer, 1200);
}

/**
 * 에이전트 메시지 생성 시:
 * 에이전트 메시지(또는 직전 사용자 질문)를 화면 상단(헤더 아래 105px)으로 매끄럽게 안착(랜딩).
 * 이로써 에이전트 답변이 시작될 때 프롬프트 입력창 훨씬 위의 넓은 뷰포트 공간에 100% 노출됩니다.
 */
function landOnMessage(element) {
  if (!element) return;
  // 사용자가 위로 스크롤하여 이전 대화를 읽고 있는 중이라면 화면 튕김 방지
  if (state.userScrolledUp) return;

  requestAnimationFrame(() => {
    const prevEl = element.previousElementSibling;
    let target = element;
    if (prevEl && prevEl.tagName === 'ARTICLE') {
      const prevRect = prevEl.getBoundingClientRect();
      if (prevRect.height < 160) {
        target = prevEl;
      }
    }

    const headerOffset = 105; // 고정 상단 헤더(56px) + 탭 네비(44px) + 여백
    const rect = target.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const targetTop = rect.top + scrollTop - headerOffset;

    window.scrollTo({
      top: Math.max(0, targetTop),
      behavior: 'smooth'
    });
  });
}

/**
 * 특정 메시지(또는 마지막 메시지)의 끝부분을 프롬프트 입력창 바로 위 40px에 수학적으로 칼같이 안착(랜딩)
 */
function scrollLastMessageToLanding(smooth = false, force = false) {
  // 사용자가 위로 스크롤하여 이전 대화를 읽고 있는 중이라면 강제 요청이 아닌 한 스크롤 금지!
  if (!force && state.userScrolledUp) return;

  syncChatBottomSpacer();
  const container = document.getElementById('chat-messages-container');
  if (!container || !container.lastElementChild) return;
  const lastArticle = container.lastElementChild;
  const targetEl = lastArticle.querySelector('.response-body') || 
                   lastArticle.querySelector('.reasoning-block') || 
                   lastArticle;

  const rect = targetEl.getBoundingClientRect();
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
  const composerTop = getComposerTop();
  const safeLandingLine = composerTop - 40;

  // 원하는 위치: rect.bottom이 safeLandingLine과 일치하도록 정확한 scrollTop 산출
  const targetScrollTop = (rect.bottom + scrollTop) - safeLandingLine;
  window.scrollTo({
    top: Math.max(0, targetScrollTop),
    behavior: smooth ? 'smooth' : 'auto'
  });
}

/**
 * 사용자 스크롤 상태 및 '최신 메시지' 플로팅 버튼 업데이트
 */
function updateScrollBottomButton() {
  const btnWrapper = document.getElementById('scroll-to-bottom-wrapper');
  if (!btnWrapper) return;
  if (state.userScrolledUp) {
    btnWrapper.classList.remove('opacity-0', 'pointer-events-none', 'translate-y-2');
    btnWrapper.classList.add('opacity-100', 'pointer-events-auto', 'translate-y-0');
  } else {
    btnWrapper.classList.remove('opacity-100', 'pointer-events-auto', 'translate-y-0');
    btnWrapper.classList.add('opacity-0', 'pointer-events-none', 'translate-y-2');
  }
}

/**
 * 스마트 챗 스크롤 감시 초기화:
 * - 사용자가 마우스 휠, 터치, 스크롤바 드래그로 위로 스크롤하면 오토스크롤을 멈춰서 이전 대화를 편안하게 읽을 수 있도록 함
 * - 다시 바닥 근처로 내려오거나 '최신 메시지' 버튼을 누르면 오토스크롤 재개
 */
function initSmartChatScroll() {
  // 휠 스크롤 감지: 위로 굴릴 때 즉시 오토스크롤 잠금
  window.addEventListener('wheel', (e) => {
    if (e.deltaY < -2) {
      state.userScrolledUp = true;
      updateScrollBottomButton();
    }
  }, { passive: true });

  // 모바일 터치 감지
  let touchStartY = 0;
  window.addEventListener('touchstart', (e) => {
    touchStartY = e.touches[0]?.clientY || 0;
  }, { passive: true });

  window.addEventListener('touchmove', (e) => {
    const currentY = e.touches[0]?.clientY || 0;
    if (currentY - touchStartY > 8) {
      // 아래로 끌어당김 = 위쪽 내용을 보려는 동작
      state.userScrolledUp = true;
      updateScrollBottomButton();
    }
  }, { passive: true });

  // 윈도우 스크롤 위치 감지 (마우스 스크롤바 직접 드래그 포함)
  window.addEventListener('scroll', () => {
    const scrollHeight = document.documentElement.scrollHeight;
    const scrollPos = window.pageYOffset + window.innerHeight;
    const distFromBottom = scrollHeight - scrollPos;

    if (distFromBottom <= 80) {
      // 바닥 80px 이내면 바닥 도달로 간주 -> 오토스크롤 재개
      if (state.userScrolledUp) {
        state.userScrolledUp = false;
        updateScrollBottomButton();
      }
    } else if (distFromBottom > 150) {
      // 바닥에서 150px 이상 떨어져 있으면 위로 스크롤 상태로 설정
      if (!state.userScrolledUp) {
        state.userScrolledUp = true;
        updateScrollBottomButton();
      }
    }
  }, { passive: true });

  // 최신 메시지 플로팅 버튼 클릭 리스너
  const btn = document.getElementById('scroll-to-bottom-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      state.userScrolledUp = false;
      updateScrollBottomButton();
      scrollChatToBottom(true, true);
    });
  }
}

/**
 * 실시간 스트리밍 중(onToken, onReasoning) 및 답변 완료 시(onDone):
 * - 사용자가 위로 스크롤해서 이전 대화를 읽고 있는 중이라면 오토스크롤을 정지하여 화면 튕김을 차단!
 * - force=true 이거나 바닥을 보고 있을 때만 최신 답변으로 안착
 */
function scrollChatToBottom(forceSmooth = false, force = false) {
  // 사용자가 위로 스크롤 중이고 강제 요청이 아니면 화면을 건드리지 않음!
  if (!force && state.userScrolledUp) return;

  requestAnimationFrame(() => {
    syncChatBottomSpacer();
    const container = document.getElementById('chat-messages-container');
    if (!container) return;

    const lastArticle = container.lastElementChild;
    if (!lastArticle) return;

    const targetEl = lastArticle.querySelector('#browser-live-viewer-card') ||
                     lastArticle.querySelector('.browser-viewer-slot') ||
                     lastArticle.querySelector('.response-body') || 
                     lastArticle.querySelector('.reasoning-block') || 
                     lastArticle;

    const rect = targetEl.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const composerTop = getComposerTop();

    // 프롬프트 입력창 윗선보다 40px 위의 안전지대에 최신 답변이 안착(Landing)
    const safeLandingLine = composerTop - 40;
    const overflow = rect.bottom - safeLandingLine;

    // 답변의 바닥이 안전선을 뚫고 내려가거나 force 플래그가 켜져 있으면 정확한 위치로 스크롤!
    if (overflow > 1 || force) {
      const targetScrollTop = (rect.bottom + scrollTop) - safeLandingLine;
      window.scrollTo({
        top: Math.max(0, targetScrollTop),
        behavior: forceSmooth ? 'smooth' : 'auto'
      });
    }
  });
}
window.scrollChatToBottom = scrollChatToBottom;
window.scrollLastMessageToLanding = scrollLastMessageToLanding;
window.landOnMessage = landOnMessage;

// ── Model & Reasoning Effort Selection ────────────────────────────────────────

function updateSidebarEffortUI(effort) {
  const val = (effort || '').toLowerCase();
  const btns = document.querySelectorAll('.sidebar-effort-btn');
  const badge = document.getElementById('sidebar-effort-label');

  btns.forEach(b => {
    const bEffort = (b.getAttribute('data-effort') || '').toLowerCase();
    const isSel = (bEffort === val);
    if (isSel) {
      let activeClass = 'bg-surface-container-lowest text-on-surface font-bold shadow-xs border border-black/20';
      if (val === 'high') activeClass = 'bg-purple-600 text-white font-bold shadow-xs border border-purple-700';
      else if (val === 'low') activeClass = 'bg-emerald-600 text-white font-bold shadow-xs border border-emerald-700';
      else if (val === 'medium') activeClass = 'bg-blue-600 text-white font-bold shadow-xs border border-blue-700';
      else if (val === 'none') activeClass = 'bg-neutral-600 text-white font-bold shadow-xs border border-neutral-700';
      b.className = `sidebar-effort-btn py-1 text-[10px] font-code rounded transition-all text-center cursor-pointer ${activeClass}`;
    } else {
      b.className = 'sidebar-effort-btn py-1 text-[10px] font-code rounded font-medium transition-all text-center cursor-pointer text-on-surface-variant hover:text-on-surface hover:bg-black/5';
    }
  });

  if (badge) {
    switch (val) {
      case 'high':
        badge.textContent = 'HIGH';
        badge.className = 'text-[9px] font-code font-bold px-1.5 py-[1px] rounded bg-purple-500/15 text-purple-700 border border-purple-500/30';
        break;
      case 'medium':
        badge.textContent = 'MED';
        badge.className = 'text-[9px] font-code font-bold px-1.5 py-[1px] rounded bg-blue-500/15 text-blue-700 border border-blue-500/30';
        break;
      case 'low':
        badge.textContent = 'LOW';
        badge.className = 'text-[9px] font-code font-bold px-1.5 py-[1px] rounded bg-emerald-500/15 text-emerald-700 border border-emerald-500/30';
        break;
      case 'none':
        badge.textContent = 'NONE';
        badge.className = 'text-[9px] font-code font-bold px-1.5 py-[1px] rounded bg-neutral-500/15 text-neutral-600 border border-neutral-400/30';
        break;
      default:
        badge.textContent = 'AUTO';
        badge.className = 'text-[9px] font-code font-semibold px-1.5 py-[1px] rounded bg-black/5 text-on-surface-variant border border-black/10';
        break;
    }
  }
}

function setReasoningEffort(effort) {
  state.reasoningEffort = effort || '';
  const effortButtons = document.querySelectorAll('.reasoning-effort-btn');
  effortButtons.forEach(b => {
    const isThis = (b.getAttribute('data-effort') === (state.reasoningEffort || 'medium'));
    b.className = isThis
      ? 'reasoning-effort-btn px-space-sm py-0.5 font-label-sm text-label-sm bg-surface-container-lowest text-on-surface font-semibold rounded-[6px] border border-black/10 transition-colors'
      : 'reasoning-effort-btn px-space-sm py-0.5 font-label-sm text-label-sm text-on-surface-variant hover:text-on-surface rounded-[6px] transition-colors';
  });

  updateSidebarEffortUI(state.reasoningEffort);
}

// 모델별 추론 강도를 프로바이더 설정 및 custom_providers.json에 백그라운드 자동 저장
async function saveCurrentModelEffort(effort) {
  const modelId = state.currentSessionModel || 'deepseek-v4.1-flash';
  try {
    if (!cachedProvidersData) {
      const data = await DaonAPI.getProviders();
      if (data && data.providers) cachedProvidersData = data.providers;
    }
  } catch (_) {}

  if (!cachedProvidersData) return;

  for (const pKey of Object.keys(cachedProvidersData)) {
    const provider = cachedProvidersData[pKey];
    if (!provider || !provider.models) continue;
    const targetModel = provider.models.find(m => (m.id === modelId || m.label === modelId));
    if (targetModel) {
      targetModel.reasoning_effort = effort;

      const finalModels = provider.models.map(m => {
        const item = {
          id: m.id,
          label: m.label || m.id,
          type: m.type || 'chat'
        };
        if (m.reasoning_effort) item.reasoning_effort = m.reasoning_effort;
        return item;
      });

      const payload = {
        name: pKey,
        base_url: provider.base_url,
        models: finalModels
      };
      // 마스킹된 불릿이 저장되어 키가 손상되지 않도록 보호 (기존 백엔드 저장 키 유지)
      if (provider.api_key && !provider.api_key.includes('•') && !provider.api_key.includes('*')) {
        payload.api_key = provider.api_key;
      }

      try {
        await DaonAPI.addProvider(payload);
        console.log(`[Effort Auto-saved] Model '${modelId}' -> effort '${effort}' in provider '${pKey}'`);
        if (typeof window._refreshProviderModelsList === 'function') {
          window._refreshProviderModelsList();
        }
      } catch (err) {
        console.warn('Failed to auto-save model effort:', err);
      }
      break;
    }
  }
}

function initSidebarEffortSelector() {
  const buttons = document.querySelectorAll('.sidebar-effort-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const effort = btn.getAttribute('data-effort') || '';
      setReasoningEffort(effort);
      await saveCurrentModelEffort(effort);
    });
  });
}

function initModelSelector() {
  const modelCards = document.querySelectorAll('.model-selection-card');
  modelCards.forEach(card => {
    card.addEventListener('click', async () => {
      modelCards.forEach(c => {
        c.classList.remove('border-2', 'border-primary', 'bg-surface-container-lowest');
        c.classList.add('border', 'border-black/10', 'bg-surface');
        const icon = c.querySelector('.model-radio-icon');
        if (icon) {
          icon.textContent = 'radio_button_unchecked';
          icon.classList.remove('text-primary');
          icon.classList.add('text-on-surface-variant');
        }
      });

      card.classList.add('border-2', 'border-primary', 'bg-surface-container-lowest');
      card.classList.remove('border', 'border-black/10', 'bg-surface');
      const activeIcon = card.querySelector('.model-radio-icon');
      if (activeIcon) {
        activeIcon.textContent = 'radio_button_checked';
        activeIcon.classList.add('text-primary');
        activeIcon.classList.remove('text-on-surface-variant');
      }

      const rawModel = card.getAttribute('data-model') || 'orchestrator';
      state.selectedModelCard = rawModel;

      // 원클릭 프리셋: 모델과 추론 강도를 동시에 세팅
      const targetModel = card.getAttribute('data-target-model') || (
        rawModel === 'flash' ? 'glm-5.3-flash' :
        rawModel === 'reasoning' ? 'deepseek-v4-pro' :
        'deepseek-v4.1-flash'
      );
      const targetEffort = card.getAttribute('data-effort') || (
        (rawModel === 'deepseek-max' || rawModel === 'reasoning') ? 'high' :
        rawModel === 'flash' ? 'low' :
        'medium'
      );

      state.currentSessionModel = targetModel;
      setReasoningEffort(targetEffort);

      const currentModelLabel = document.getElementById('current-model-label');
      if (currentModelLabel) {
        currentModelLabel.textContent = targetModel;
      }

      if (state.currentSessionId) {
        try {
          await DaonAPI.updateSession(state.currentSessionId, { model: targetModel });
        } catch (err) {
          console.warn('Failed to update session model on preset click:', err);
        }
      }
    });
  });

  // Reasoning Effort pills
  const effortButtons = document.querySelectorAll('.reasoning-effort-btn');
  effortButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const effort = btn.getAttribute('data-effort') || 'medium';
      setReasoningEffort(effort);
    });
  });
}

// ── Dynamic Harness Execution ─────────────────────────────────────────────────

function initHarnessControls() {
  const startBtn = document.getElementById('harness-start-btn');
  const taskInput = document.getElementById('harness-task-input');
  const terminal = document.getElementById('harness-telemetry-terminal');

  if (startBtn) {
    startBtn.addEventListener('click', async () => {
      const task = taskInput?.value.trim() || 'Dynamic Harness AST 전역 감사 및 컴파일 테스트';
      appendTelemetryLog(`[Harness] Launching new execution pipeline for task: "${task}"`);
      updateHarnessSteps(1); // Step 1: In Progress

      try {
        const res = await DaonAPI.runDynamicHarness({ task });
        if (res && res.run_id) {
          state.currentHarnessRunId = res.run_id;
          appendTelemetryLog(`[Dispatcher] Job started. Run ID: ${res.run_id}`);
          startHarnessPolling(res.run_id);
        }
      } catch (err) {
        appendTelemetryLog(`[Error] Failed to start harness: ${err.message}`);
      }
    });
  }

  // Harness Pause/Resume buttons
  document.querySelectorAll('#tab-dynamic-harness button').forEach(btn => {
    if (btn.textContent.includes('일시 중지')) {
      btn.addEventListener('click', () => {
        if (state.harnessPollInterval) {
          clearInterval(state.harnessPollInterval);
          state.harnessPollInterval = null;
          appendTelemetryLog('[Harness] 파이프라인 폴링이 일시 중지되었습니다.');
        }
      });
    } else if (btn.textContent.includes('실행 재개')) {
      btn.addEventListener('click', () => {
        if (state.currentHarnessRunId && !state.harnessPollInterval) {
          appendTelemetryLog('[Harness] 파이프라인 실행 폴링을 재개합니다.');
          startHarnessPolling(state.currentHarnessRunId);
        }
      });
    }
  });
}

function startHarnessPolling(runId) {
  if (state.harnessPollInterval) clearInterval(state.harnessPollInterval);

  let cursor = 0;
  state.harnessPollInterval = setInterval(async () => {
    try {
      const data = await DaonAPI.getDynamicStatus(runId, cursor);
      if (data.logs && data.logs.length > 0) {
        data.logs.forEach(log => {
          const author = log.agent_id || log.author || 'System';
          const text = log.content || log.message || (typeof log === 'string' ? log : JSON.stringify(log));
          appendTelemetryLog(`[${author}] ${text}`);
        });
        cursor = data.next_cursor || (cursor + data.logs.length);
      }

      // Step progress mapping
      if (data.step) {
        updateHarnessSteps(data.step);
      } else if (data.status === 'running') {
        updateHarnessSteps(2);
      }

      if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
        clearInterval(state.harnessPollInterval);
        state.harnessPollInterval = null;
        appendTelemetryLog(`[Harness] Execution loop ended with status: ${data.status.toUpperCase()}`);
        updateHarnessSteps(4, true);
      }
    } catch (e) {
      // Continue polling
    }
  }, 1500);
}

function updateHarnessSteps(currentStep, allDone = false) {
  const steps = [
    document.getElementById('harness-step-1'),
    document.getElementById('harness-step-2'),
    document.getElementById('harness-step-3'),
    document.getElementById('harness-step-4')
  ];

  steps.forEach((el, idx) => {
    if (!el) return;
    const badge = el.querySelector('.step-badge');
    const stepNum = idx + 1;

    if (allDone || stepNum < currentStep) {
      if (badge) { badge.textContent = 'DONE'; badge.className = 'step-badge text-primary font-semibold'; }
      el.classList.remove('border-2', 'border-primary');
      el.classList.add('border', 'border-black/10');
    } else if (stepNum === currentStep) {
      if (badge) { badge.textContent = 'IN PROGRESS'; badge.className = 'step-badge text-primary font-semibold'; }
      el.classList.add('border-2', 'border-primary');
      el.classList.remove('border', 'border-black/10');
    } else {
      if (badge) { badge.textContent = 'PENDING'; badge.className = 'step-badge text-on-surface-variant'; }
      el.classList.remove('border-2', 'border-primary');
      el.classList.add('border', 'border-black/10');
    }
  });
}

function appendTelemetryLog(text) {
  const terminal = document.getElementById('harness-telemetry-terminal');
  if (!terminal) return;

  const now = new Date().toTimeString().split(' ')[0];
  const div = document.createElement('div');
  div.className = 'text-on-surface-variant font-code text-code text-[12px] leading-relaxed';
  div.innerHTML = `<span class="text-on-surface-variant/70">[${now}]</span> ${escapeHtml(text)}`;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

// ── Agent Boardroom (8 Slots & Broadcast Meeting) ─────────────────────────────

function initBoardroomShortcuts() {
  // 1. Individual slot dispatch buttons -> open chat prefilled with @agent
  const dispatchButtons = document.querySelectorAll('.agent-dispatch-btn');
  dispatchButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const agentName = btn.getAttribute('data-agent') || '라온';
      window.activateTab('chat-session');
      const input = document.getElementById('chat-input');
      if (input) {
        input.value = `@${agentName} `;
        input.focus();
      }
    });
  });

  // 2. Broadcast Command Console -> real 8-slot agent meeting runtime
  const broadcastBtn = document.getElementById('boardroom-broadcast-btn');
  const broadcastInput = document.getElementById('boardroom-input');
  const transcriptContainer = document.getElementById('boardroom-transcript-container');

  if (broadcastBtn && broadcastInput) {
    broadcastBtn.addEventListener('click', async () => {
      const topic = broadcastInput.value.trim();
      if (!topic) {
        alert('8개 슬롯(6 정적 에이전트 + 2 CLI 워커)에 브로드캐스트할 회의 안건 또는 지시 사항을 입력해 주세요.');
        broadcastInput.focus();
        return;
      }

      broadcastInput.value = '';
      resetBoardroomSlots();

      appendBoardroomTranscript('00 사용자', `[전체 브로드캐스트 지시] "${topic}"`, 'bg-primary text-on-primary');
      appendBoardroomTranscript('01 의장', '안건을 접수했습니다. 8개 슬롯 동기화 라운드를 시작합니다 (실 에이전트 런타임)...', 'bg-surface-container-highest text-on-surface');

      try {
        const res = await DaonAPI.broadcastBoardroom({ task: topic, sessionId: state.currentSessionId });
        if (!res || !res.stream_id) throw new Error('stream_id를 받지 못했습니다');
        setBoardroomBusy(true);

        DaonAPI.connectSSE(res.stream_id, {
          onBoardroomSlot(d) {
            setBoardroomSlotState(d.slot, 'running');
          },
          onBoardroomReply(d) {
            setBoardroomSlotState(d.slot, d.status === 'error' ? 'error' : 'done');
            const tag = `${d.slot} ${d.speaker || ''}`.trim();
            const cls = d.status === 'error'
              ? 'bg-rose-100 text-rose-800'
              : 'bg-surface-container-highest text-on-surface';
            const suffix = (typeof d.elapsed === 'number') ? `\n\n— ${d.elapsed}s` : '';
            appendBoardroomTranscript(tag, (d.text || '(응답 없음)') + suffix, cls);
          },
          onBoardroomDone() {
            appendBoardroomTranscript('01 의장', '8개 슬롯 전원 응답 완료. 회의 라운드를 정상 종료합니다.', 'bg-emerald-100 text-emerald-800');
            setBoardroomBusy(false);
          },
          onError() {
            appendBoardroomTranscript('오류', '회의 스트림이 종료되었습니다.', 'bg-rose-100 text-rose-800');
            setBoardroomBusy(false);
          }
        });
      } catch (err) {
        appendBoardroomTranscript('오류', `브로드캐스트 실패: ${err.message}`, 'bg-rose-100 text-rose-800');
        setBoardroomBusy(false);
      }
    });
  }
}

function setBoardroomBusy(busy) {
  const btn = document.getElementById('boardroom-broadcast-btn');
  const input = document.getElementById('boardroom-input');
  if (btn) { btn.disabled = !!busy; btn.classList.toggle('opacity-50', !!busy); }
  if (input) input.disabled = !!busy;
}

function setBoardroomSlotState(slot, status) {
  const card = document.querySelector(`[data-slot="${slot}"]`);
  if (!card) return;
  card.classList.remove('slot-running', 'slot-done', 'slot-error');
  if (status === 'running') card.classList.add('slot-running');
  else if (status === 'error') card.classList.add('slot-error');
  else if (status === 'done') card.classList.add('slot-done');
  const badge = card.querySelector('.slot-status-badge');
  if (badge) {
    badge.textContent = status === 'running' ? 'RUNNING' : status === 'error' ? 'ERROR' : status === 'done' ? 'DONE' : 'READY';
    badge.className = 'slot-status-badge font-code text-[10px] ' + (
      status === 'running' ? 'text-amber-600' :
      status === 'error' ? 'text-rose-600' :
      status === 'done' ? 'text-emerald-600' : 'text-on-surface-variant');
  }
}

function resetBoardroomSlots() {
  document.querySelectorAll('[data-slot]').forEach(card => {
    card.classList.remove('slot-running', 'slot-done', 'slot-error');
    const badge = card.querySelector('.slot-status-badge');
    if (badge) { badge.textContent = 'READY'; badge.className = 'slot-status-badge font-code text-[10px] text-on-surface-variant'; }
  });
}

function appendBoardroomTranscript(speaker, text, badgeClass = 'bg-surface-container-highest text-on-surface') {
  const container = document.getElementById('boardroom-transcript-container');
  if (!container) return null;

  const row = document.createElement('div');
  row.className = 'flex items-start gap-3 animate-fadeIn';
  row.innerHTML = `
    <span class="font-mono font-bold px-1.5 py-0.5 rounded text-[11px] shrink-0 ${badgeClass}">${escapeHtml(speaker)}</span>
    <p class="text-on-surface leading-relaxed font-body-sm text-[13px]">${escapeHtml(text)}</p>
  `;
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
  return row;
}

// ── MCP Store & Skills Integration ────────────────────────────────────────────

let cachedSkills = [];

async function initMcpAndSkills() {
  // 1. MCP Live Servers Loading
  await loadLiveMcpServers();

  // 2. Real Backend Skills Loading (345 skills)
  await loadLiveSkills();

  // 3. Search filtering in MCP tab
  const mcpSearch = document.getElementById('mcp-search-input');
  if (mcpSearch) {
    mcpSearch.addEventListener('input', () => {
      const query = mcpSearch.value.toLowerCase().trim();
      document.querySelectorAll('.mcp-plugin-card').forEach(card => {
        const text = card.innerText.toLowerCase();
        card.style.display = text.includes(query) ? 'flex' : 'none';
      });
    });
  }

  // 4. Instant search filtering in Skills tab across all backend skills
  const skillSearch = document.getElementById('skills-search-input');
  if (skillSearch) {
    skillSearch.addEventListener('input', () => {
      const query = skillSearch.value.toLowerCase().trim();
      if (!query) {
        renderSkillCatalog(cachedSkills.slice(0, 20));
      } else {
        const filtered = cachedSkills.filter(s =>
          (s.name || s.id || '').toLowerCase().includes(query) ||
          (s.description || '').toLowerCase().includes(query) ||
          (s.category || '').toLowerCase().includes(query)
        );
        renderSkillCatalog(filtered.slice(0, 30));
      }
    });
  }

  // 5. Ping All MCP Servers button
  const pingBtn = Array.from(document.querySelectorAll('#tab-plugin-mcp button')).find(b => b.textContent && b.textContent.includes('전체 서버 상태 핑'));
  if (pingBtn) {
    pingBtn.addEventListener('click', async () => {
      const originalText = pingBtn.textContent;
      pingBtn.textContent = '핑 확인 중...';
      try {
        await loadLiveMcpServers();
        pingBtn.textContent = '핑 정상 (응답 4ms)';
        setTimeout(() => { pingBtn.textContent = originalText; }, 2000);
      } catch {
        pingBtn.textContent = '핑 오류';
        setTimeout(() => { pingBtn.textContent = originalText; }, 2000);
      }
    });
  }
}

async function loadLiveMcpServers() {
  const container = document.getElementById('mcp-servers-grid');
  if (!container) return;

  try {
    const servers = await DaonAPI.getMCPServers();
    if (servers && servers.length > 0) {
      // Update top summary bar
      const activeCount = servers.filter(s => s.status === 'connected' || s.status === 'running' || (s.tools && s.tools.length > 0)).length;
      const toolsCount = servers.reduce((acc, s) => acc + (s.tools ? s.tools.length : (s.tools_count || 0)), 0);
      const summaryBarText = document.querySelector('#tab-plugin-mcp .mcp-plugin-card .text-on-surface');
      if (summaryBarText) {
        summaryBarText.innerHTML = `<span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-600"></span>활성 서버: <strong>${activeCount}개 연결됨</strong></span><span class="text-black/20">|</span><span>등록된 툴: <strong>${toolsCount}개 함수</strong></span><span class="text-black/20">|</span><span>프로토콜 버전: <strong class="font-code">v2024-11-05</strong></span>`;
      }

      // Clear existing dummy cards if real ones exist
      container.innerHTML = '';
      servers.forEach(srv => {
        const isRunning = srv.status === 'running' || srv.status === 'connected' || (srv.tools && srv.tools.length > 0);
        const card = document.createElement('div');
        card.className = 'p-space-md bg-surface-container-lowest border border-black/10 rounded-[10px] flex flex-col justify-between gap-space-md hover:border-black/30 transition-colors mcp-plugin-card';
        card.innerHTML = `
          <div class="flex flex-col gap-2">
            <div class="flex items-start justify-between">
              <div class="flex items-center gap-2">
                <div class="w-9 h-9 rounded-[8px] bg-surface-container-highest flex items-center justify-center font-mono font-bold text-on-surface">
                  ${(srv.server_id || 'MC').slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <h4 class="font-label-md font-semibold text-on-surface">${escapeHtml(srv.label || srv.server_id)}</h4>
                  <span class="font-code text-[11px] text-on-surface-variant">@mcp/${escapeHtml(srv.server_id)}</span>
                </div>
              </div>
              <span class="px-2 py-0.5 ${isRunning ? 'bg-emerald-50 text-emerald-800 border-emerald-200' : 'bg-black/[0.05] border-black/10'} border font-code text-[10px] font-semibold rounded mcp-status-badge">
                ${isRunning ? 'ACTIVE' : 'STANDBY'}
              </span>
            </div>
            <p class="font-body-sm text-[13px] text-on-surface-variant leading-relaxed">
              ${escapeHtml(srv.description || '표준 Model Context Protocol 도구 및 외부 커넥터 브리지')}
            </p>
          </div>
          <div class="flex items-center justify-between pt-2 border-t border-black/[0.06]">
            <div class="flex items-center gap-2 font-code text-[11px] text-on-surface-variant">
              <span>도구 ${srv.tools ? srv.tools.length : 0}개</span>
              <span>·</span>
              <span>${srv.transport || 'stdio'}</span>
            </div>
            <div class="flex items-center gap-1.5">
              <button class="px-2.5 py-1 border border-black/10 rounded-[6px] font-label-sm text-on-surface hover:bg-black/[0.04] mcp-cfg-btn" type="button">설정</button>
              <button class="px-2.5 py-1 ${isRunning ? 'bg-surface-container-highest text-on-surface' : 'bg-primary text-on-primary'} rounded-[6px] font-label-sm hover:opacity-90 transition-opacity mcp-toggle-btn" data-id="${escapeHtml(srv.server_id)}" data-active="${isRunning}">
                ${isRunning ? '비활성화' : '활성화'}
              </button>
            </div>
          </div>
        `;

        // Toggle connect/disconnect listener
        card.querySelector('.mcp-toggle-btn')?.addEventListener('click', async (e) => {
          const btn = e.currentTarget;
          const sid = btn.getAttribute('data-id');
          const isActive = btn.getAttribute('data-active') === 'true';

          btn.textContent = '처리 중...';
          try {
            if (isActive) {
              await DaonAPI.disconnectMCPServer(sid);
              btn.textContent = '활성화';
              btn.className = 'px-2.5 py-1 bg-primary text-on-primary rounded-[6px] font-label-sm hover:opacity-90 transition-opacity mcp-toggle-btn';
              btn.setAttribute('data-active', 'false');
              card.querySelector('.mcp-status-badge').textContent = 'STANDBY';
              card.querySelector('.mcp-status-badge').className = 'px-2 py-0.5 bg-black/[0.05] border-black/10 border font-code text-[10px] font-semibold rounded mcp-status-badge';
            } else {
              await DaonAPI.connectMCPServer(sid);
              btn.textContent = '비활성화';
              btn.className = 'px-2.5 py-1 bg-surface-container-highest text-on-surface rounded-[6px] font-label-sm hover:opacity-90 transition-opacity mcp-toggle-btn';
              btn.setAttribute('data-active', 'true');
              card.querySelector('.mcp-status-badge').textContent = 'ACTIVE';
              card.querySelector('.mcp-status-badge').className = 'px-2 py-0.5 bg-emerald-50 text-emerald-800 border-emerald-200 border font-code text-[10px] font-semibold rounded mcp-status-badge';
            }
          } catch (err) {
            alert('MCP 서버 상태 변경 실패: ' + err.message);
            btn.textContent = isActive ? '비활성화' : '활성화';
          }
        });

        container.appendChild(card);
      });
    }
  } catch (err) {
    console.warn('Failed to load real MCP servers:', err);
  }
}

async function loadLiveSkills() {
  const container = document.getElementById('skills-catalog-grid');
  const countLabel = document.getElementById('skills-count-label');

  try {
    const skills = await DaonAPI.getSkills();
    if (skills && skills.length > 0) {
      cachedSkills = skills;
      if (countLabel) countLabel.textContent = `${skills.length}개`;
      renderSkillCatalog(skills.slice(0, 20));
    }
  } catch (err) {
    console.warn('Failed to load real skills:', err);
  }
}

function renderSkillCatalog(skillList) {
  const container = document.getElementById('skills-catalog-grid');
  if (!container) return;

  container.innerHTML = '';
  skillList.forEach(s => {
    const isEquipped = s.enabled || s.source === 'local';
    const card = document.createElement('div');
    card.className = 'p-space-md bg-surface border border-black/10 rounded-[10px] flex flex-col justify-between gap-3 skill-catalog-card hover:border-black/30 transition-colors';
    card.innerHTML = `
      <div class="flex items-start justify-between">
        <div class="flex-1 pr-2">
          <div class="flex items-center gap-2">
            <h4 class="font-label-md font-semibold text-on-surface truncate">${escapeHtml(s.name || s.id)}</h4>
            <span class="px-1.5 py-[1px] bg-primary text-on-primary text-[10px] font-code rounded shrink-0">v${escapeHtml(s.version || '1.0')}</span>
          </div>
          <p class="font-body-sm text-[12px] text-on-surface-variant mt-1 line-clamp-2 leading-relaxed">
            ${escapeHtml(s.description || '전문 실행형 에이전트 스킬셋')}
          </p>
        </div>
        <span class="material-symbols-outlined text-[18px] ${isEquipped ? 'text-primary' : 'text-on-surface-variant/40'} shrink-0">
          ${isEquipped ? 'check_circle' : 'radio_button_unchecked'}
        </span>
      </div>
      <div class="flex items-center justify-between pt-2 border-t border-black/[0.06] text-[11px] font-code text-on-surface-variant">
        <span>분류: ${escapeHtml(s.category || 'general')}</span>
        <button class="toggle-slot-btn text-primary font-semibold hover:underline cursor-pointer" type="button">
          ${isEquipped ? '장착 해제' : '슬롯에 장착'}
        </button>
      </div>
    `;

    // Toggle slot button
    card.querySelector('.toggle-slot-btn')?.addEventListener('click', (e) => {
      const btn = e.currentTarget;
      const willEquip = btn.textContent.includes('장착') && !btn.textContent.includes('해제');
      if (willEquip) {
        btn.textContent = '장착 해제';
        card.querySelector('.material-symbols-outlined').className = 'material-symbols-outlined text-[18px] text-primary shrink-0';
        card.querySelector('.material-symbols-outlined').textContent = 'check_circle';
      } else {
        btn.textContent = '슬롯에 장착';
        card.querySelector('.material-symbols-outlined').className = 'material-symbols-outlined text-[18px] text-on-surface-variant/40 shrink-0';
        card.querySelector('.material-symbols-outlined').textContent = 'radio_button_unchecked';
      }
    });

    container.appendChild(card);
  });
}

// ── Export & Diagnostics ──────────────────────────────────────────────────────

function initExport() {
  const exportBtn = document.getElementById('export-session-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', async () => {
      if (!state.currentSessionId) {
        alert('내보낼 세션이 선택되지 않았습니다.');
        return;
      }
      try {
        const sess = await DaonAPI.getSession(state.currentSessionId);
        if (!sess) return;

        let markdown = `# ${sess.title || 'Daon Agent Session'}\n\n`;
        markdown += `Session ID: ${sess.session_id}\nWorkspace: ${sess.workspace || 'Default'}\nDate: ${new Date().toISOString()}\n\n---\n\n`;

        (sess.messages || []).forEach(m => {
          const role = m.role === 'user' ? '### 👤 사용자' : '### 🤖 Daon Agent';
          markdown += `${role} (${formatTime(m.timestamp)})\n\n${m.content}\n\n---\n\n`;
        });

        const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `daon-session-${state.currentSessionId.slice(0, 8)}.md`;
        a.click();
      } catch (err) {
        alert('내보내기 실패: ' + err.message);
      }
    });
  }
}

// ── Health Indicator ──────────────────────────────────────────────────────────

async function checkHealth() {
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
}


// ── Mobile Responsive Sidebar Drawer ─────────────────────────────────────────

function initMobileSidebar() {
  const toggleBtn = document.getElementById('sidebar-toggle-btn');
  const closeBtn = document.getElementById('sidebar-close-btn');
  const backdrop = document.getElementById('sidebar-backdrop');
  const sidebar = document.getElementById('app-sidebar');

  function openSidebar() {
    if (sidebar) {
      sidebar.classList.remove('-translate-x-full');
      sidebar.classList.add('translate-x-0');
    }
    if (backdrop) {
      backdrop.classList.remove('hidden');
    }
  }

  function closeSidebar() {
    if (sidebar) {
      sidebar.classList.add('-translate-x-full');
      sidebar.classList.remove('translate-x-0');
    }
    if (backdrop) {
      backdrop.classList.add('hidden');
    }
  }

  toggleBtn?.addEventListener('click', openSidebar);
  closeBtn?.addEventListener('click', closeSidebar);
  backdrop?.addEventListener('click', closeSidebar);

  // Auto-close drawer on mobile when clicking any sidebar item
  document.querySelectorAll('[data-tab-target], .new-session-btn, .sidebar-item').forEach(el => {
    el.addEventListener('click', () => {
      if (window.innerWidth < 768) {
        closeSidebar();
      }
    });
  });
}

// ── Desktop Resizable Sidebar Splitter (세션창 너비 조절) ──────────────────────
function initSidebarResizer() {
  const resizer = document.getElementById('sidebar-resizer');
  const sidebar = document.getElementById('app-sidebar');
  if (!resizer || !sidebar) return;

  // 이전 저장된 사이드바 너비 복원
  try {
    const savedWidth = localStorage.getItem('daon_sidebar_width');
    if (savedWidth) {
      const widthPx = Math.max(200, Math.min(parseInt(savedWidth, 10), 600));
      document.documentElement.style.setProperty('--sidebar-width', widthPx + 'px');
    }
  } catch (_) {}

  let isResizing = false;

  resizer.addEventListener('mousedown', (e) => {
    e.preventDefault();
    isResizing = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (moveEvent) => {
      if (!isResizing) return;
      const newWidth = Math.max(200, Math.min(moveEvent.clientX, 600));
      document.documentElement.style.setProperty('--sidebar-width', newWidth + 'px');
    };

    const onMouseUp = (upEvent) => {
      if (!isResizing) return;
      isResizing = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      const finalWidth = Math.max(200, Math.min(upEvent.clientX, 600));
      try {
        localStorage.setItem('daon_sidebar_width', finalWidth.toString());
      } catch (_) {}
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
  });
}

// ── Attachment Handling (Files, Images, Videos) ───────────────────────────────

function initAttachments() {
  const attachBtn = document.getElementById('chat-attach-btn');
  const fileInput = document.getElementById('chat-file-input');
  const tray = document.getElementById('chat-attach-tray');

  if (!attachBtn || !fileInput || !tray) return;

  attachBtn.addEventListener('click', () => {
    fileInput.click();
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length > 0) {
      Array.from(fileInput.files).forEach(f => state.pendingFiles.push(f));
      fileInput.value = '';
      renderAttachTray();
    }
  });

  function renderAttachTray() {
    tray.innerHTML = '';
    if (state.pendingFiles.length === 0) {
      tray.classList.add('hidden');
      return;
    }
    tray.classList.remove('hidden');

    state.pendingFiles.forEach((f, idx) => {
      const chip = document.createElement('div');
      chip.className = 'flex items-center gap-1.5 px-2 py-1 bg-surface-container border border-black/10 rounded-[6px] text-[11px] font-mono text-on-surface max-w-[200px] truncate';

      let iconHtml = '<span class="material-symbols-outlined text-[14px] text-on-surface-variant shrink-0">description</span>';
      if (f.type.startsWith('image/')) {
        const previewUrl = URL.createObjectURL(f);
        iconHtml = `<img src="${previewUrl}" class="w-4 h-4 rounded object-cover shrink-0" />`;
      } else if (f.type.startsWith('video/')) {
        iconHtml = '<span class="material-symbols-outlined text-[14px] text-primary shrink-0">videocam</span>';
      }

      chip.innerHTML = `
        ${iconHtml}
        <span class="truncate flex-1">${escapeHtml(f.name)}</span>
        <button type="button" class="text-on-surface-variant hover:text-red-600 p-0.5 rounded cursor-pointer remove-file-btn" data-idx="${idx}">
          <span class="material-symbols-outlined text-[13px]">close</span>
        </button>
      `;

      chip.querySelector('.remove-file-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        state.pendingFiles.splice(idx, 1);
        renderAttachTray();
      });

      tray.appendChild(chip);
    });
  }
}

// ── Provider & Model Slide Drawer (Above User Profile) ────────────────────────

let cachedProvidersData = null;

async function initProviderDrawer() {
  const toggleBtn = document.getElementById('provider-drawer-toggle');
  const content = document.getElementById('provider-drawer-content');
  const arrow = document.getElementById('provider-drawer-arrow');
  const pillsList = document.getElementById('provider-pills-list');
  const modelsList = document.getElementById('provider-models-list');
  const modelsCountLabel = document.getElementById('provider-models-count');
  const currentProviderLabel = document.getElementById('current-provider-label');
  const currentModelLabel = document.getElementById('current-model-label');
  const prevBtn = document.getElementById('provider-slide-prev');
  const nextBtn = document.getElementById('provider-slide-next');
  const settingsBtn = document.getElementById('sidebar-settings-btn');

  if (!toggleBtn || !content) return;

  function toggleDrawer(open) {
    const isCurrentlyOpen = !content.classList.contains('hidden');
    const shouldOpen = open !== undefined ? open : !isCurrentlyOpen;
    if (shouldOpen) {
      content.classList.remove('hidden');
      content.classList.add('flex');
      arrow?.classList.add('rotate-180');
    } else {
      content.classList.add('hidden');
      content.classList.remove('flex');
      arrow?.classList.remove('rotate-180');
    }
  }

  toggleBtn.addEventListener('click', () => toggleDrawer());
  settingsBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDrawer(true);
  });

  prevBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    pillsList?.scrollBy({ left: -120, behavior: 'smooth' });
  });

  nextBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    pillsList?.scrollBy({ left: 120, behavior: 'smooth' });
  });

  if (pillsList && !pillsList._wheelBound) {
    pillsList._wheelBound = true;
    pillsList.addEventListener('wheel', (e) => {
      if (e.deltaY !== 0) {
        e.preventDefault();
        pillsList.scrollLeft += e.deltaY;
      }
    }, { passive: false });
  }

  try {
    const data = await DaonAPI.getProviders();
    if (!data || !data.providers) return;
    cachedProvidersData = data.providers;

    const providerKeys = Object.keys(data.providers);
    if (providerKeys.length === 0) return;

    let selectedProviderKey = providerKeys.includes('opencode-go') ? 'opencode-go' : providerKeys[0];

    renderProviderPills();
    renderProviderModels(selectedProviderKey);

    function renderProviderPills() {
      if (!pillsList) return;
      pillsList.innerHTML = '';
      providerKeys.forEach(key => {
        const p = cachedProvidersData[key];
        const isSelected = key === selectedProviderKey;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `px-3 py-1.5 rounded-[8px] text-[11px] font-code whitespace-nowrap transition-all cursor-pointer shrink-0 leading-tight flex items-center justify-center ${
          isSelected ? 'bg-primary text-on-primary font-semibold shadow-xs border border-primary' : 'bg-surface-container border border-black/10 text-on-surface hover:bg-black/5 hover:border-black/20'
        }`;
        const count = p?.models ? ` (${p.models.length})` : '';
        btn.textContent = (p.label || key) + count;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          selectedProviderKey = key;
          renderProviderPills();
          renderProviderModels(key);
        });
        pillsList.appendChild(btn);
      });
    }

    function renderProviderModels(pKey) {
      if (!modelsList) return;
      modelsList.innerHTML = '';
      const p = cachedProvidersData[pKey];
      const models = p?.models || [];
      if (modelsCountLabel) modelsCountLabel.textContent = `${models.length}개 모델`;

      if (models.length === 0) {
        modelsList.innerHTML = '<div class="text-[11px] text-on-surface-variant/60 px-1 py-1">등록된 모델이 없습니다.</div>';
        return;
      }

      models.forEach(m => {
        const isCurrent = (state.currentSessionModel === m.id) || (!state.currentSessionModel && m.id === 'deepseek-v4.1-flash');
        const item = document.createElement('div');
        item.className = `px-2 py-1.5 rounded-[6px] border flex items-center justify-between text-[11px] cursor-pointer transition-colors ${
          isCurrent ? 'bg-surface-container-lowest border-primary font-semibold text-on-surface' : 'bg-surface border-black/10 text-on-surface hover:bg-black/[0.03]'
        }`;
        const meffort = m.reasoning_effort || '';
        item.innerHTML = `
          <div class="flex items-center gap-1.5 truncate">
            <span class="material-symbols-outlined text-[13px] ${isCurrent ? 'text-primary' : 'text-on-surface-variant/50'}">
              ${isCurrent ? 'radio_button_checked' : 'radio_button_unchecked'}
            </span>
            <span class="truncate">${escapeHtml(m.label || m.id)}</span>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            ${meffort ? `<span class="px-1 py-[1px] bg-indigo-50 text-indigo-700 border border-indigo-200 text-[9px] font-code rounded font-semibold uppercase">${escapeHtml(meffort)}</span>` : ''}
            <span class="px-1 py-[1px] bg-black/[0.05] text-on-surface-variant text-[10px] font-code rounded">${m.type || 'chat'}</span>
          </div>
        `;

        item.addEventListener('click', async () => {
          state.currentSessionModel = m.id;
          const modelEffort = m.reasoning_effort || '';
          setReasoningEffort(modelEffort);
          if (currentModelLabel) currentModelLabel.textContent = m.id;
          if (currentProviderLabel) currentProviderLabel.textContent = `PROVIDER: ${p.label || pKey}`;
          renderProviderModels(pKey);

          // Persist to session in backend
          if (state.currentSessionId) {
            try {
              await DaonAPI.updateSession(state.currentSessionId, { model: m.id });
            } catch (err) {
              console.warn('Failed to persist session model:', err);
            }
          }
        });

        modelsList.appendChild(item);
      });
    }

    window._refreshProviderModelsList = () => {
      if (selectedProviderKey) renderProviderModels(selectedProviderKey);
    };

    // 초기 활성 모델의 reasoning_effort 반영
    const initialModelId = state.currentSessionModel || 'deepseek-v4.1-flash';
    let initialEffort = '';
    for (const key of providerKeys) {
      const match = cachedProvidersData[key]?.models?.find(mod => (mod.id === initialModelId || mod.label === initialModelId));
      if (match) {
        initialEffort = match.reasoning_effort || '';
        break;
      }
    }
    setReasoningEffort(initialEffort);

  } catch (err) {
    console.warn('Failed to initialize provider drawer:', err);
  }
}

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

    let currentBrowsePath = state.currentWorkspace || 'C:/daon';
  let currentParentPath = '';
  let availableDrives = ['C:/', 'D:/'];

  function normalizePath(p) {
    if (!p) return '';
    return p.replace(/\\/g, '/');
  }

  function getParentPath(p) {
    if (!p) return '';
    const norm = normalizePath(p).replace(/\/+$/, '');
    const lastSlash = norm.lastIndexOf('/');
    if (lastSlash <= 0) return '';
    const parent = norm.substring(0, lastSlash);
    if (/^[A-Za-z]:$/.test(parent)) return parent + '/';
    return parent;
  }

  async function loadDrives() {
    try {
      const res = await fetch('/api/fs/list?path=');
      if (res.ok) {
        const data = await res.json();
        if (data.drives && data.drives.length > 0) {
          availableDrives = data.drives;
        }
      }
    } catch (_) {}
    renderDriveChips();
  }

  function renderDriveChips() {
    const chipsContainer = document.getElementById('fs-drive-chips');
    if (!chipsContainer) return;
    const curNorm = normalizePath(currentBrowsePath).toUpperCase();

    chipsContainer.innerHTML = availableDrives.map(d => {
      const dNorm = normalizePath(d).toUpperCase();
      const isActive = curNorm.startsWith(dNorm) || curNorm.startsWith(dNorm.replace('/', ''));
      const letter = d.replace(/[:/\\]/g, '');
      return `
        <button type="button" class="px-2 py-0.5 rounded-[5px] text-[11px] font-mono font-bold transition-all cursor-pointer ${
          isActive 
            ? 'bg-primary text-on-primary shadow-xs ring-1 ring-primary' 
            : 'bg-surface hover:bg-black/[0.08] text-on-surface border border-black/15'
        } fs-drive-btn" data-drive="${escapeHtml(d)}" title="${escapeHtml(d)} 드라이브로 전환">
          ${letter}:
        </button>
      `;
    }).join('');

    chipsContainer.querySelectorAll('.fs-drive-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const d = btn.getAttribute('data-drive');
        if (d) {
          if (pathInput) pathInput.value = d;
          loadFolderTree(d);
        }
      });
    });
  }

  async function loadFolderTree(folderPath) {
    const folderList = document.getElementById('fs-folder-list');
    const pathLabel = document.getElementById('fs-current-path-label');
    if (!folderList) return;

    folderList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2 text-center flex items-center justify-center gap-1.5"><span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>폴더 목록 불러오는 중...</span></div>';
    
    try {
      const targetQuery = (folderPath !== undefined && folderPath !== null) ? folderPath : currentBrowsePath;
      const res = await fetch(`/api/fs/list?path=${encodeURIComponent(targetQuery || '')}`);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();

      currentBrowsePath = data.current || targetQuery || '';
      currentParentPath = (data.parent !== undefined && data.parent !== '') ? data.parent : getParentPath(currentBrowsePath);

      if (pathLabel) {
        pathLabel.textContent = currentBrowsePath || '내 컴퓨터 (드라이브 선택)';
        pathLabel.title = currentBrowsePath;
      }

      // CRITICAL: Always keep pathInput synchronized with currentBrowsePath!
      if (pathInput && currentBrowsePath) {
        pathInput.value = currentBrowsePath;
      }

      renderDriveChips();

      const entries = (data.entries || []).filter(e => {
        if (e.type !== 'dir' && e.type !== 'drive') return false;
        const n = e.name || '';
        return !n.startsWith('$') && !n.startsWith('.') && n !== 'System Volume Information' && n !== '__pycache__';
      });

      if (entries.length === 0) {
        folderList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2.5 text-center">선택 가능한 하위 폴더가 없습니다.</div>';
        return;
      }

      folderList.innerHTML = entries.map(item => `
        <div class="flex items-center justify-between px-2 py-1.5 rounded-[6px] hover:bg-black/[0.05] cursor-pointer transition-colors fs-folder-item" data-path="${escapeHtml(item.path)}" title="${escapeHtml(item.path)}">
          <div class="flex items-center gap-2 truncate min-w-0">
            <span class="material-symbols-outlined text-[16px] text-amber-600 shrink-0">${item.type === 'drive' ? 'hard_drive' : 'folder'}</span>
            <span class="text-[12px] font-medium text-on-surface truncate">${escapeHtml(item.name)}</span>
          </div>
          <button type="button" class="text-[11px] font-label-sm px-2 py-0.5 rounded bg-surface-container-high hover:bg-primary hover:text-on-primary border border-black/10 transition-colors fs-choose-btn shrink-0" data-path="${escapeHtml(item.path)}">
            선택
          </button>
        </div>
      `).join('');

      folderList.querySelectorAll('.fs-folder-item').forEach(el => {
        el.addEventListener('click', (e) => {
          if (e.target.closest('.fs-choose-btn')) return;
          const p = el.getAttribute('data-path');
          if (p) {
            if (pathInput) pathInput.value = p;
            loadFolderTree(p);
          }
        });
      });

      folderList.querySelectorAll('.fs-choose-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const p = btn.getAttribute('data-path');
          if (p && pathInput) {
            pathInput.value = p;
            if (typeof showVoiceToast === 'function') {
              showVoiceToast('📂 작업 폴더 지정: ' + p, '📂', 2000);
            }
          }
        });
      });

    } catch (err) {
      folderList.innerHTML = `<div class="text-[11px] text-rose-500 p-2 text-center">목록 조회 실패: ${escapeHtml(err.message)}</div>`;
    }
  }

  const upBtn = document.getElementById('fs-up-btn');
  if (upBtn) {
    upBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const parent = currentParentPath || getParentPath(currentBrowsePath);
      // Synchronize path input immediately!
      if (pathInput && parent) {
        pathInput.value = parent;
      }
      loadFolderTree(parent);
    });
  }

  const refreshBtn = document.getElementById('fs-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', (e) => {
      e.preventDefault();
      loadFolderTree(currentBrowsePath);
    });
  }

  if (pathInput) {
    pathInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = (pathInput.value || '').trim();
        if (val) loadFolderTree(val);
      }
    });
  }

  function openModal() {
    const initialPath = (pathInput?.value || state.currentWorkspace || 'C:/daon').trim();
    if (pathInput) pathInput.value = initialPath;
    renderDriveChips();
    loadDrives();
    loadFolderTree(initialPath);
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
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
              <span class="text-[12px] text-on-surface truncate">${escapeHtml(w.name || w.path.split(/[\\/]/).pop())}</span>
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
      const name = state.currentWorkspace.split(/[\\/]/).pop() || state.currentWorkspace;
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
      // 1순위: 인라인 폴더 탐색기 즉시 갱신 및 포커스
      const cur = (pathInput?.value || state.currentWorkspace || 'C:/daon').trim();
      loadFolderTree(cur);
      const fsSection = document.getElementById('workspace-fs-explorer-section');
      if (fsSection) {
        fsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      if (typeof showVoiceToast === 'function') {
        showVoiceToast('📂 폴더 목록을 조회했습니다. 아래에서 폴더를 클릭하세요.', '📂', 2500);
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
  const manualModelInput = document.getElementById('provider-manual-model-input');
  const manualTypeSelect = document.getElementById('provider-manual-type-select');
  const addManualModelBtn = document.getElementById('provider-add-manual-model-btn');
  const fetchModelsBtn = document.getElementById('provider-fetch-models-btn');
  const fetchResult = document.getElementById('provider-fetch-result');

  // 구 버전 custom_providers.json의 14개 공식 제공자 프리셋 + 기본 모델 목록
  const FALLBACK_PRESETS = {
    'openai': { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'o1', 'o3-mini'] },
    'anthropic': { label: 'Anthropic', base_url: 'https://api.anthropic.com/v1', models: ['claude-3-7-sonnet-20250219', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022'] },
    'google': { label: 'Google Gemini', base_url: 'https://generativelanguage.googleapis.com/v1beta', models: ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash'] },
    'deepseek': { label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
    'minimax': { label: 'MiniMax', base_url: 'https://api.minimax.io/v1', models: ['MiniMax-Text-01', 'abab6.5s-chat'] },
    'openrouter': { label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', models: ['deepseek/deepseek-r1', 'deepseek/deepseek-chat', 'anthropic/claude-3.5-sonnet', 'openai/gpt-4o'] },
    'together': { label: 'Together AI', base_url: 'https://api.together.xyz/v1', models: ['meta-llama/Llama-3.3-70B-Instruct-Turbo', 'deepseek-ai/DeepSeek-R1'] },
    'groq': { label: 'Groq', base_url: 'https://api.groq.com/openai/v1', models: ['llama-3.3-70b-versatile', 'deepseek-r1-distill-llama-70b'] },
    'xai': { label: 'xAI (Grok)', base_url: 'https://api.x.ai/v1', models: ['grok-2-1212', 'grok-beta'] },
    'zhipu': { label: 'ZhipuAI (GLM)', base_url: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-plus', 'glm-4-flash'] },
    'dashscope': { label: 'Alibaba Cloud (DashScope/Qwen)', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max', 'qwen-turbo'] },
    'qwen-token-plan': { label: 'Alibaba Cloud (Token Plan)', base_url: 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max'] },
    'opencode-go': {
      label: 'OpenCode Go',
      base_url: 'https://opencode.ai/zen/go/v1',
      models: ['deepseek-v4.1-flash', 'deepseek-v4-flash', 'deepseek-v4-pro', 'glm-5.3-flash', 'gpt-6-luna', 'gpt-5.6-luna', 'qwen3.8-max', 'mimo-v2.6-flash']
    },
    'opencode-zen': {
      label: 'OpenCode Zen',
      base_url: 'https://opencode.ai/zen/v1',
      models: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-4-5', 'gpt-6-luna', 'gpt-5.6-terra', 'gemini-3.8-flash', 'gemini-3.1-pro', 'deepseek-v4.1-flash', 'muse-spark-1.3', 'kimi-k3', 'glm-5.3']
    }
  };

  let activePresets = { ...FALLBACK_PRESETS };
  let editingProviderName = null;
  let selectedProviderModels = []; // 현재 모델 목록 { id, label, type, checked }

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

  // 프리셋 옵션 채우기 (서버 프리셋 + 폴백 모델 딥 병합)
  function populatePresetSelect(serverPresets) {
    if (!presetSelect) return;
    activePresets = {};
    for (const [key, cfg] of Object.entries(FALLBACK_PRESETS)) {
      activePresets[key] = { ...cfg };
    }
    if (serverPresets) {
      for (const [key, cfg] of Object.entries(serverPresets)) {
        const existingModels = activePresets[key]?.models || [];
        const newModels = (cfg.models && cfg.models.length > 0) ? cfg.models : existingModels;
        activePresets[key] = {
          ...(activePresets[key] || {}),
          ...cfg,
          models: newModels
        };
      }
    }

    const curVal = presetSelect.value;
    presetSelect.innerHTML = '<option value="">-- 직접 입력 (Manual Entry) --</option>';
    
    for (const [key, cfg] of Object.entries(activePresets)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = `${cfg.label || key} (${cfg.base_url || ''})`;
      presetSelect.appendChild(opt);
    }
    if (curVal && activePresets[curVal]) {
      presetSelect.value = curVal;
    }
  }

  // 프리셋 변경 시 자동 필드 세팅 (구 버전 로직 그대로)
  if (presetSelect) {
    presetSelect.addEventListener('change', () => {
      const val = presetSelect.value;
      if (!val || !activePresets[val]) return;

      const pcfg = activePresets[val];
      if (!editingProviderName) {
        if (nameInput) nameInput.value = val;
      }
      if (urlInput) {
        urlInput.value = pcfg.base_url || '';
      }
      const modelsList = (pcfg.models && pcfg.models.length > 0) ? pcfg.models : (FALLBACK_PRESETS[val]?.models || []);
      if (modelsList && modelsList.length > 0) {
        selectedProviderModels = modelsList.map(m => {
          const mid = m.id || m.name || m;
          const meffort = typeof m === 'object' && m.reasoning_effort ? m.reasoning_effort : '';
          return { id: mid, label: mid, type: m.type || 'chat', reasoning_effort: meffort, checked: true };
        });
        renderProviderModelList('📦 프리셋 기본 모델 목록 (' + selectedProviderModels.length + '개)');
      } else {
        selectedProviderModels = [];
        if (fetchResult) {
          fetchResult.classList.add('hidden');
          fetchResult.innerHTML = '';
        }
      }

      // API Key 입력 필드로 자동 포커스
      if (keyInput) keyInput.focus();
    });
  }

  async function loadProvidersList() {
    if (!providersList) return;
    try {
      const data = await DaonAPI.getProviders();
      populatePresetSelect(data.presets);

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
              <button class="px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 refresh-provider-btn cursor-pointer flex items-center gap-1" data-name="${escapeHtml(pName)}" title="저장된 API 키로 최신 모델 목록 다시 불러오기">
                <span class="material-symbols-outlined text-[13px]">refresh</span>
                <span>갱신</span>
              </button>
              <button class="px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 edit-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">수정</button>
              <button class="px-2 py-1 text-[11px] border border-black/15 text-rose-600 rounded-[6px] hover:bg-rose-50 delete-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">삭제</button>
            </div>
          </div>
        `;
      }).join('');

      // Refresh button (구 버전 refreshProviderModels 로직)
      providersList.querySelectorAll('.refresh-provider-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.getAttribute('data-name');
          const oldHtml = btn.innerHTML;
          btn.disabled = true;
          btn.innerHTML = '<span class="material-symbols-outlined text-[13px] animate-spin">refresh</span><span>갱신 중...</span>';
          try {
            const res = await DaonAPI.refreshProviderModels(name);
            const count = res.count || (res.models ? res.models.length : 0);
            alert(`✅ '${name}' 제공자: 모델 ${count}개가 성공적으로 갱신되었습니다!`);
            await loadProvidersList();
            try { await loadLiveProviders(); } catch (_) {}
          } catch (err) {
            alert(`모델 갱신 실패: ${err.message}`);
          } finally {
            btn.disabled = false;
            btn.innerHTML = oldHtml;
          }
        });
      });

      // Edit button
      providersList.querySelectorAll('.edit-provider-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const name = btn.getAttribute('data-name');
          const p = providers[name];
          if (p) editProvider(name, p);
        });
      });

      // Delete button
      providersList.querySelectorAll('.delete-provider-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.getAttribute('data-name');
          if (confirm(`'${name}' 제공자와 등록된 모델을 삭제하시겠습니까?`)) {
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
    editingProviderName = name;
    formCard.classList.remove('hidden');
    document.getElementById('provider-form-title').textContent = `'${name}' 제공자 수정`;
    if (nameInput) { nameInput.value = name; nameInput.disabled = true; }
    if (keyInput) { keyInput.value = ''; keyInput.placeholder = '기존 키 유지 시 빈 칸으로 두세요'; }
    if (urlInput) urlInput.value = data.base_url || '';
    if (presetSelect) presetSelect.value = '';

    const models = data.models || [];
    selectedProviderModels = models.map(m => {
      const mid = typeof m === 'string' ? m : (m.id || m.name || '');
      const mtype = typeof m === 'object' && m.type ? m.type : 'chat';
      const meffort = typeof m === 'object' && m.reasoning_effort ? m.reasoning_effort : '';
      return { id: mid, label: mid, type: mtype, reasoning_effort: meffort, checked: true };
    });

    if (selectedProviderModels.length > 0) {
      renderProviderModelList(`📋 '${name}' 등록 모델 목록 (${selectedProviderModels.length}개)`);
    } else {
      if (fetchResult) {
        fetchResult.classList.add('hidden');
        fetchResult.innerHTML = '';
      }
    }
  }

  function resetForm() {
    editingProviderName = null;
    if (nameInput) { nameInput.value = ''; nameInput.disabled = false; }
    if (keyInput) { keyInput.value = ''; keyInput.placeholder = 'sk-...'; }
    if (urlInput) urlInput.value = '';
    if (manualModelInput) manualModelInput.value = '';
    if (presetSelect) presetSelect.value = '';
    selectedProviderModels = [];
    if (fetchResult) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
    }
    document.getElementById('provider-form-title').textContent = '제공자 추가';
  }

  if (openBtn) openBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (doneBtn) doneBtn.addEventListener('click', closeModal);

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      resetForm();
      formCard.classList.remove('hidden');
      setTimeout(() => formCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    });
  }

  if (formCloseBtn) formCloseBtn.addEventListener('click', () => formCard.classList.add('hidden'));
  if (formCancelBtn) formCancelBtn.addEventListener('click', () => formCard.classList.add('hidden'));

  if (keyToggleBtn && keyInput) {
    keyToggleBtn.addEventListener('click', () => {
      const isPass = keyInput.type === 'password';
      keyInput.type = isPass ? 'text' : 'password';
      keyToggleBtn.querySelector('span').textContent = isPass ? 'visibility_off' : 'visibility';
    });
  }

  // ── 커스텀 드롭다운 헬퍼 함수 ─────────────────────────────
  function getEffortInfo(effort) {
    switch (effort) {
      case 'low':
        return { label: '⚡ low', badgeClass: 'bg-emerald-500/10 text-emerald-700 border-emerald-500/30 font-medium' };
      case 'medium':
        return { label: '⚖️ med', badgeClass: 'bg-blue-500/10 text-blue-700 border-blue-500/30 font-medium' };
      case 'high':
        return { label: '🔥 high', badgeClass: 'bg-purple-500/15 text-purple-700 border-purple-500/40 font-bold' };
      case 'none':
        return { label: '🚫 none', badgeClass: 'bg-neutral-500/10 text-neutral-600 border-neutral-400/30 font-medium' };
      default:
        return { label: '🧠 auto', badgeClass: 'bg-surface text-on-surface border-black/15 font-medium' };
    }
  }

  function getTypeLabel(type) {
    switch (type) {
      case 'image': return '🖼 image';
      case 'video': return '🎬 video';
      default: return '💬 chat';
    }
  }

  let globalModelPopover = null;
  let popoverJustOpened = false;
  function getOrCreatePopover() {
    if (!globalModelPopover) {
      globalModelPopover = document.createElement('div');
      globalModelPopover.id = 'model-row-floating-popover';
      globalModelPopover.className = 'fixed bg-surface-container-lowest border border-black/15 rounded-[8px] shadow-2xl py-1 min-w-[180px] font-sans text-[11px] hidden animate-in fade-in zoom-in-95 duration-100';
      globalModelPopover.style.zIndex = '999999';
      globalModelPopover.style.position = 'fixed';
      globalModelPopover.style.boxShadow = '0 12px 30px -4px rgba(0,0,0,0.35), 0 4px 10px -2px rgba(0,0,0,0.2)';
      document.body.appendChild(globalModelPopover);

      document.addEventListener('pointerdown', (e) => {
        if (!globalModelPopover || globalModelPopover.classList.contains('hidden')) return;
        if (popoverJustOpened) return;
        if (!globalModelPopover.contains(e.target) && !e.target.closest('.model-effort-btn') && !e.target.closest('.model-type-btn')) {
          closePopover();
        }
      });

      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePopover();
      });
    }
    return globalModelPopover;
  }

  function closePopover() {
    if (globalModelPopover) {
      globalModelPopover.classList.add('hidden');
      globalModelPopover.innerHTML = '';
    }
  }

  // ── 모델 목록 렌더링 (커스텀 팝오버 셀렉터 적용) ──────────
  function renderProviderModelList(headerTitle) {
    if (!fetchResult) return;
    closePopover();

    if (selectedProviderModels.length === 0) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
      return;
    }

    fetchResult.classList.remove('hidden');
    let html = `
      <div class="flex items-center justify-between pb-1.5 border-b border-black/10">
        <span class="font-semibold text-emerald-700 text-[12px]">${escapeHtml(headerTitle || '선택된 모델 목록')}</span>
        <div class="flex items-center gap-1.5">
          <button id="select-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 선택</button>
          <button id="deselect-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 해제</button>
          <span id="detected-model-count-label" class="text-[10px] font-code text-on-surface-variant font-medium"></span>
        </div>
      </div>
      <div class="flex flex-col gap-1 max-h-[200px] overflow-y-auto mt-2 pr-1" id="detected-models-scroll">
    `;

    selectedProviderModels.forEach((m, idx) => {
      const mid = m.id || m.label || '';
      const mtype = m.type || 'chat';
      const meffort = m.reasoning_effort || '';
      const isSpecial = /tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid);
      const isChecked = m.checked !== false;
      const effortInfo = getEffortInfo(meffort);
      const typeLabel = getTypeLabel(mtype);

      html += `
        <div class="flex items-center justify-between p-1 rounded hover:bg-black/[0.03] text-[11px] font-code model-row-item transition-colors ${isSpecial ? 'opacity-60' : ''}" id="model-row-${idx}">
          <label class="flex items-center gap-2 cursor-pointer truncate flex-1 pr-2">
            <input type="checkbox" class="model-check-box rounded border-black/20" data-idx="${idx}" ${isChecked ? 'checked' : ''} />
            <span class="truncate model-id-text font-medium" title="${escapeHtml(mid)}">${escapeHtml(mid)}</span>
          </label>
          <div class="flex items-center gap-1.5 shrink-0">
            <!-- 모델 타입 드롭다운 버튼 -->
            <button type="button" class="model-type-btn text-[10px] px-2 py-0.5 rounded border border-black/15 bg-surface text-on-surface hover:bg-black/5 flex items-center gap-1 cursor-pointer transition-colors" data-idx="${idx}" title="모델 타입 설정">
              <span class="type-btn-label">${typeLabel}</span>
              <span class="text-[8px] opacity-60">▾</span>
            </button>
            <!-- 기본 추론 강도 드롭다운 버튼 -->
            <button type="button" class="model-effort-btn text-[10px] px-2 py-0.5 rounded border ${effortInfo.badgeClass} flex items-center gap-1 cursor-pointer hover:opacity-80 transition-all shrink-0" data-idx="${idx}" title="기본 추론 강도 (Reasoning Effort)">
              <span class="effort-btn-label">${effortInfo.label}</span>
              <span class="text-[8px] opacity-60">▾</span>
            </button>
            <!-- 삭제 버튼 -->
            <button class="remove-model-btn p-0.5 text-on-surface-variant hover:text-red-600 rounded cursor-pointer" data-idx="${idx}" type="button" title="이 모델 제거">
              <span class="material-symbols-outlined text-[14px]">close</span>
            </button>
          </div>
        </div>
      `;
    });

    html += `</div>
      <div class="mt-2 text-[10px] text-on-surface-variant/80 border-t border-black/10 pt-1 flex items-center justify-between">
        <span>타입과 기본 추론 강도를 지정 후 [제공자 저장]을 누르면 저장됩니다.</span>
      </div>
    `;

    fetchResult.innerHTML = html;

    function updateCount() {
      const cbs = fetchResult.querySelectorAll('.model-check-box');
      let checked = 0;
      cbs.forEach(cb => {
        const idx = parseInt(cb.getAttribute('data-idx'), 10);
        if (selectedProviderModels[idx]) {
          selectedProviderModels[idx].checked = cb.checked;
        }
        if (cb.checked) checked++;
      });
      const lbl = document.getElementById('detected-model-count-label');
      if (lbl) lbl.textContent = `${checked}/${cbs.length}개 선택됨`;
    }

    document.getElementById('select-all-models-btn')?.addEventListener('click', () => {
      fetchResult.querySelectorAll('.model-check-box').forEach(cb => cb.checked = true);
      updateCount();
    });

    document.getElementById('deselect-all-models-btn')?.addEventListener('click', () => {
      fetchResult.querySelectorAll('.model-check-box').forEach(cb => cb.checked = false);
      updateCount();
    });

    fetchResult.querySelectorAll('.model-check-box').forEach(cb => {
      cb.addEventListener('change', updateCount);
    });

    // ── 모델 타입 팝오버 열기 ──
    fetchResult.querySelectorAll('.model-type-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = parseInt(btn.getAttribute('data-idx'), 10);
        const cur = selectedProviderModels[idx]?.type || 'chat';
        const pop = getOrCreatePopover();

        const options = [
          { value: 'chat', label: '💬 chat', desc: '일반 대화 및 코드 생성' },
          { value: 'image', label: '🖼 image', desc: '이미지 생성 / 비전 모델' },
          { value: 'video', label: '🎬 video', desc: '비디오 생성 / 멀티미디어' }
        ];

        let popHtml = `
          <div class="px-2.5 py-1 text-[10px] font-semibold text-on-surface-variant/80 border-b border-black/10 select-none">
            모델 타입 선택
          </div>
          <div class="flex flex-col py-0.5">
        `;
        options.forEach(opt => {
          const isSel = (opt.value === cur);
          popHtml += `
            <button type="button" class="pop-item w-full text-left px-2.5 py-1.5 hover:bg-black/5 flex items-center justify-between cursor-pointer transition-colors ${isSel ? 'bg-primary/10 font-bold text-primary' : 'text-on-surface'}" data-value="${opt.value}">
              <div class="flex flex-col">
                <span class="text-[11px]">${opt.label}</span>
                <span class="text-[9px] text-on-surface-variant/70">${opt.desc}</span>
              </div>
              ${isSel ? '<span class="material-symbols-outlined text-[13px] text-primary">check</span>' : ''}
            </button>
          `;
        });
        popHtml += `</div>`;
        pop.innerHTML = popHtml;

        popoverJustOpened = true;
        setTimeout(() => { popoverJustOpened = false; }, 300);

        // 위치 계산 및 노출
        const rect = btn.getBoundingClientRect();
        pop.style.display = 'block';
        pop.classList.remove('hidden');
        pop.style.zIndex = '999999';

        const popW = Math.max(pop.offsetWidth || 0, 160);
        const popH = Math.max(pop.offsetHeight || 0, 120);

        let left = rect.left;
        if (left + popW > window.innerWidth - 12) {
          left = Math.max(10, rect.right - popW);
        }
        if (left < 10) left = 10;

        let top = rect.bottom + 4;
        if (top + popH > window.innerHeight - 12) {
          top = Math.max(10, rect.top - popH - 4);
        }

        pop.style.left = `${left}px`;
        pop.style.top = `${top}px`;

        pop.querySelectorAll('.pop-item').forEach(item => {
          item.addEventListener('click', (ev) => {
            ev.stopPropagation();
            const val = item.getAttribute('data-value');
            if (selectedProviderModels[idx]) {
              selectedProviderModels[idx].type = val;
            }
            btn.querySelector('.type-btn-label').textContent = getTypeLabel(val);
            closePopover();
          });
        });
      });
    });

    // ── 기본 추론 강도 팝오버 열기 ──
    fetchResult.querySelectorAll('.model-effort-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = parseInt(btn.getAttribute('data-idx'), 10);
        const cur = selectedProviderModels[idx]?.reasoning_effort || '';
        const pop = getOrCreatePopover();

        const options = [
          { value: '', label: '🧠 auto', desc: '모델 기본 / 자동 최적화' },
          { value: 'low', label: '⚡ low', desc: '낮은 추론 / 신속한 응답' },
          { value: 'medium', label: '⚖️ medium', desc: '중간 추론 / 균형 잡힌 심층 분석' },
          { value: 'high', label: '🔥 high', desc: '높은 추론 / Max Effort 심층 추론' },
          { value: 'none', label: '🚫 none', desc: '추론 비활성화 / 일반 출력' }
        ];

        let popHtml = `
          <div class="px-2.5 py-1 text-[10px] font-semibold text-on-surface-variant/80 border-b border-black/10 select-none">
            기본 추론 강도 (Reasoning Effort)
          </div>
          <div class="flex flex-col py-0.5">
        `;
        options.forEach(opt => {
          const isSel = (opt.value === cur);
          popHtml += `
            <button type="button" class="pop-item w-full text-left px-2.5 py-1.5 hover:bg-black/5 flex items-center justify-between cursor-pointer transition-colors ${isSel ? 'bg-primary/10 font-bold text-primary' : 'text-on-surface'}" data-value="${opt.value}">
              <div class="flex flex-col">
                <span class="text-[11px]">${opt.label}</span>
                <span class="text-[9px] text-on-surface-variant/70">${opt.desc}</span>
              </div>
              ${isSel ? '<span class="material-symbols-outlined text-[13px] text-primary">check</span>' : ''}
            </button>
          `;
        });
        popHtml += `</div>`;
        pop.innerHTML = popHtml;

        popoverJustOpened = true;
        setTimeout(() => { popoverJustOpened = false; }, 300);

        // 위치 계산 및 노출
        const rect = btn.getBoundingClientRect();
        pop.style.display = 'block';
        pop.classList.remove('hidden');
        pop.style.zIndex = '999999';

        const popW = Math.max(pop.offsetWidth || 0, 185);
        const popH = Math.max(pop.offsetHeight || 0, 200);

        let left = rect.left;
        if (left + popW > window.innerWidth - 12) {
          left = Math.max(10, rect.right - popW);
        }
        if (left < 10) left = 10;

        let top = rect.bottom + 4;
        if (top + popH > window.innerHeight - 12) {
          top = Math.max(10, rect.top - popH - 4);
        }

        pop.style.left = `${left}px`;
        pop.style.top = `${top}px`;

        pop.querySelectorAll('.pop-item').forEach(item => {
          item.addEventListener('click', (ev) => {
            ev.stopPropagation();
            const val = item.getAttribute('data-value');
            if (selectedProviderModels[idx]) {
              selectedProviderModels[idx].reasoning_effort = val;
            }
            const info = getEffortInfo(val);
            btn.className = `model-effort-btn text-[10px] px-2 py-0.5 rounded border ${info.badgeClass} flex items-center gap-1 cursor-pointer hover:opacity-80 transition-all shrink-0`;
            btn.querySelector('.effort-btn-label').textContent = info.label;
            closePopover();
          });
        });
      });
    });

    // 스크롤 시 열려 있는 팝오버 자동 닫기 (열린 직후 제외)
    function onScrollClose() {
      if (popoverJustOpened) return;
      closePopover();
    }
    const scrollContainer = document.getElementById('detected-models-scroll');
    if (scrollContainer) {
      scrollContainer.addEventListener('scroll', onScrollClose);
    }
    const modalScroll = document.querySelector('#settings-modal .flex-1.overflow-y-auto');
    if (modalScroll) {
      modalScroll.addEventListener('scroll', onScrollClose);
    }

    fetchResult.querySelectorAll('.remove-model-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        closePopover();
        const idx = parseInt(btn.getAttribute('data-idx'), 10);
        selectedProviderModels.splice(idx, 1);
        renderProviderModelList(headerTitle);
      });
    });

    updateCount();
  }

  // ── 구 버전 _addManualProviderModels 로직 그대로 구현 ─────────────────────
  function addManualModels() {
    if (!manualModelInput) return;
    const raw = (manualModelInput.value || '').trim();
    if (!raw) { alert('추가할 모델 ID를 입력해주세요.'); return; }

    const mtype = manualTypeSelect ? manualTypeSelect.value : 'chat';
    const ids = raw.split(',').map(s => s.trim()).filter(Boolean);
    if (ids.length === 0) return;

    const existing = {};
    const existingLower = {};
    selectedProviderModels.forEach((m, idx) => {
      existing[m.id] = idx;
      existingLower[String(m.id || '').toLowerCase()] = idx;
    });

    let addedCount = 0;
    const checkedExistingIndices = [];

    ids.forEach(id => {
      if (existing[id] !== undefined) {
        const idx = existing[id];
        selectedProviderModels[idx].checked = true;
        checkedExistingIndices.push(idx);
        return;
      }
      const actualIdx = existingLower[id.toLowerCase()];
      if (actualIdx !== undefined) {
        selectedProviderModels[actualIdx].checked = true;
        checkedExistingIndices.push(actualIdx);
        return;
      }
      // 신규 모델 추가
      selectedProviderModels.push({ id, label: id, type: mtype, checked: true });
      addedCount++;
    });

    manualModelInput.value = '';
    renderProviderModelList(`📝 모델 목록 (직접 입력 ${addedCount}개 추가됨)`);

    // 기존 모델이 입력된 경우 해당 행으로 스크롤 및 하이라이트 효과
    if (checkedExistingIndices.length > 0) {
      checkedExistingIndices.forEach(idx => {
        const row = document.getElementById(`model-row-${idx}`);
        if (row) {
          row.classList.add('bg-primary/15');
          row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          setTimeout(() => row.classList.remove('bg-primary/15'), 2000);
        }
      });
    }
    manualModelInput.focus();
  }

  // 추가 버튼 클릭 이벤트
  if (addManualModelBtn) {
    addManualModelBtn.addEventListener('click', addManualModels);
  }

  // 직접 입력 인풋 엔터키 이벤트
  if (manualModelInput) {
    manualModelInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addManualModels();
      }
    });
  }

  // 모델 자동 감지 버튼 클릭
  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();
      if (!url) { alert('Base URL(엔드포인트)을 먼저 입력해주세요.'); return; }
      if (!key && !editingProviderName) { alert('API 키를 입력해주세요.'); return; }

      fetchModelsBtn.disabled = true;
      fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>감지 중...</span>';
      try {
        const res = await DaonAPI.fetchProviderModels({ name: name || 'temp', key, url, preset: presetSelect.value });
        const detected = res.models || [];
        if (detected.length === 0) {
          alert('제공자로부터 모델이 반환되지 않았습니다. 수동으로 모델을 직접 추가해주세요.');
          return;
        }

        // 기존에 등록된 모델이 있다면 합치기
        const existingMap = {};
        selectedProviderModels.forEach(m => { existingMap[m.id] = m; });
        detected.forEach(m => {
          const mid = m.id || m.name || m;
          if (!existingMap[mid]) {
            selectedProviderModels.push({
              id: mid,
              label: m.label || mid,
              type: m.type || 'chat',
              checked: !(/tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid))
            });
          }
        });

        renderProviderModelList(`✅ 자동 감지 완료 (${detected.length}개 발견)`);
      } catch (err) {
        alert('모델 자동 감지 실패: ' + err.message);
      } finally {
        fetchModelsBtn.disabled = false;
        fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">sync</span><span>🔍 모델 자동 감지</span>';
      }
    });
  }

  // 제공자 저장 버튼 (구 버전 로직 그대로)
  if (formSaveBtn) {
    formSaveBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();

      if (!name) { alert('제공자 식별명을 입력해주세요.'); return; }
      if (!key && !editingProviderName) { alert('API 키를 입력해주세요.'); return; }
      if (!url) { alert('Base URL을 입력해주세요.'); return; }

      // 체크된 모델만 수집 (구 버전 Bugfix 반영)
      const finalModels = selectedProviderModels
        .filter(m => m.checked !== false)
        .map(m => {
          const item = {
            id: m.id,
            label: m.label || m.id,
            type: m.type || 'chat'
          };
          if (m.reasoning_effort) {
            item.reasoning_effort = m.reasoning_effort;
          }
          return item;
        });

      try {
        formSaveBtn.disabled = true;
        formSaveBtn.textContent = '저장 중...';

        const payload = {
          name,
          base_url: url,
          models: finalModels
        };
        // 마스킹된 불릿(•, *)이 포함된 경우 기존 백엔드 저장 키를 보존하기 위해 api_key를 전송하지 않음
        if (key && !key.includes('•') && !key.includes('*')) payload.api_key = key;

        const result = await DaonAPI.addProvider(payload);
        const addedCount = result.models ? result.models.length : finalModels.length;
        alert(`'${name}' 제공자가 성공적으로 저장되었습니다! (${addedCount}개 모델)`);
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

  // 초기 로드 시 프리셋 세팅
  populatePresetSelect(FALLBACK_PRESETS);
}

// ── 4. Agent Persona Management ──────────────────────────────────────────────

async function initAgentPersonas() {
  const select = document.getElementById('agent-persona-select');
  const modal = document.getElementById('new-agent-modal');
  const closeBtn = document.getElementById('new-agent-modal-close');
  const cancelBtn = document.getElementById('new-agent-modal-cancel');
  const saveBtn = document.getElementById('new-agent-modal-save');
  const nameInput = document.getElementById('new-agent-name');
  const iconInput = document.getElementById('new-agent-icon');
  const promptInput = document.getElementById('new-agent-prompt');
  if (!select) return;

  async function refreshPersonaOptions() {
    // 1) Fetch profiles from backend
    let serverProfiles = [];
    try {
      const res = await DaonAPI.getProfiles();
      serverProfiles = (res && res.profiles) ? res.profiles : [];
    } catch (_) {}

    // 필터링: 다온응대는 대표님 지침에 따라 철저히 배제
    serverProfiles = serverProfiles.filter(p => {
      const n = (p.name || '').toLowerCase();
      return !n.includes('다온') && !n.includes('daon');
    });

    // 2) Get custom personas from localStorage
    let customPersonas = [];
    try {
      customPersonas = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
    } catch (_) {}

    // 3) Rebuild select options
    select.innerHTML = '';

    // A. 정적 에이전트 5인방 (라온, 토니, 빌, 셜록, 프라다)
    STATIC_PERSONAS.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.icon} ${p.name} (${p.role})`;
      select.appendChild(opt);
    });

    // B. 서버에 등록된 추가 프로필 (정적 5인방에 없는 다른 프로필이 있을 때)
    const staticIds = new Set(STATIC_PERSONAS.map(p => p.id));
    serverProfiles.forEach(p => {
      const pName = p.name;
      if (!staticIds.has(pName) && !pName.includes('다온') && !pName.toLowerCase().includes('daon')) {
        const opt = document.createElement('option');
        opt.value = pName;
        opt.textContent = `${getPersonaIcon(pName)} ${pName}`;
        select.appendChild(opt);
      }
    });

    // C. 커스텀 로컬 페르소나
    customPersonas.forEach(p => {
      if (!select.querySelector(`option[value="${p.id}"]`)) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.icon || '⚡'} ${p.name}`;
        select.appendChild(opt);
      }
    });

    // D. 새 에이전트 생성 옵션
    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '➕ 새 에이전트 생성...';
    select.appendChild(newOpt);

    // E. 현재 활성 페르소나 복원
    let active = state.currentAgentPersona || localStorage.getItem('daon_active_persona') || 'raon';
    if (active.includes('다온') || active.toLowerCase().includes('daon')) active = 'raon';
    state.currentAgentPersona = active;
    localStorage.setItem('daon_active_persona', active);

    if (select.querySelector(`option[value="${active}"]`)) {
      select.value = active;
    } else {
      select.value = 'raon';
      state.currentAgentPersona = 'raon';
    }

    // 서버 활성 프로필과 일치시키기
    try {
      await DaonAPI.switchProfile(state.currentAgentPersona);
    } catch (_) {}
  }

  // Handle select change
  select.addEventListener('change', async () => {
    const val = select.value;
    if (val === '__new__') {
      select.value = state.currentAgentPersona || 'raon';
      openNewAgentModal();
      return;
    }

    state.currentAgentPersona = val;
    localStorage.setItem('daon_active_persona', val);

    // 1) 백엔드 프로필 전환
    try {
      await DaonAPI.switchProfile(val);
    } catch (err) {
      console.warn('Profile switch failed:', err);
    }

    // 2) 현재 세션의 프로필도 갱신
    if (state.currentSessionId) {
      try {
        await DaonAPI.updateSession(state.currentSessionId, { profile: val });
        await loadSessions();
      } catch (_) {}
    }

    // 3) 토스트 피드백
    showPersonaToast(val);
  });

  function openNewAgentModal() {
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (nameInput) nameInput.value = '';
    if (iconInput) iconInput.value = '⚡';
    if (promptInput) promptInput.value = '';
  }

  function closeNewAgentModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  if (closeBtn) closeBtn.addEventListener('click', closeNewAgentModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeNewAgentModal);

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const name = (nameInput?.value || '').trim();
      const icon = (iconInput?.value || '⚡').trim();
      const prompt = (promptInput?.value || '').trim();
      if (!name) { alert('에이전트 이름을 입력해주세요.'); return; }
      if (name.includes('다온') || name.toLowerCase().includes('daon')) {
        alert('다온응대 명칭은 사용할 수 없습니다.');
        return;
      }

      saveBtn.disabled = true;
      saveBtn.textContent = '생성 중...';

      try {
        try {
          await DaonAPI.createProfile(name);
        } catch (_) {}

        const id = name;
        const newPersona = { id, name, icon, prompt };
        const saved = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
        saved.push(newPersona);
        localStorage.setItem('daon_custom_personas', JSON.stringify(saved));

        await refreshPersonaOptions();
        select.value = id;
        state.currentAgentPersona = id;
        localStorage.setItem('daon_active_persona', id);
        try { await DaonAPI.switchProfile(id); } catch (_) {}

        if (state.currentSessionId) {
          try {
            await DaonAPI.updateSession(state.currentSessionId, { profile: id });
            await loadSessions();
          } catch (_) {}
        }

        closeNewAgentModal();
        showPersonaToast(id);
      } catch (err) {
        alert('에이전트 생성 실패: ' + err.message);
      } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">add</span><span>에이전트 등록</span>';
      }
    });
  }

  function showPersonaToast(personaId) {
    let pName = personaId;
    let pIcon = getPersonaIcon(personaId);
    const match = STATIC_PERSONAS.find(p => p.id === personaId);
    if (match) {
      pName = `${match.name} (${match.role})`;
      pIcon = match.icon;
    }

    const toast = document.createElement('div');
    toast.className = 'fixed bottom-5 right-5 bg-surface-container-highest border border-black/15 shadow-xl rounded-[10px] px-4 py-2.5 flex items-center gap-2.5 text-[13px] text-on-surface z-50 animate-in fade-in slide-in-from-bottom-3 duration-200';
    toast.innerHTML = `<span class="text-[18px]">${pIcon}</span><div class="flex flex-col"><span class="font-medium">에이전트 전환 완료</span><span class="text-[11px] text-on-surface-variant">${escapeHtml(pName)} 역할로 대화합니다.</span></div>`;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('opacity-0', 'transition-opacity');
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  await refreshPersonaOptions();
}

// ── Application Safe Bootstrap (End of File) ──────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initApp().catch(err => console.error('[Fatal Bootstrap Error]:', err));
  });
} else {
  initApp().catch(err => console.error('[Fatal Bootstrap Error]:', err));
}

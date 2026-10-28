import { liveBrowser } from './browser_viewer.js?v=20261022_viewerclose';
/**
 * DAON Workspace v2.5 - Single Page Orchestration Controller
 * Connects Achromatic Studio UI with real DAON Python Backend Engines.
 */

import { DaonAPI } from './api.js?v=20261018_boardroom';
import { initSecretsUI } from './secrets.js';
import { bgmPlayer } from './music_player.js?v=20261012_bgm1';


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
  lastRenderedSignature: '', // 불필요한 전체 재렌더링 방지용 서명
  runningSessionIds: new Set() // 백그라운드 포함 현재 작업/추론 중인 세션 ID 집합
};

// ── Voice Input (STT) & Voice Output (TTS) State Variables ─────────────────
let _speechRecognition = null;
let _isVoiceRecording = false;
let _autoTTS = localStorage.getItem('daon_auto_tts') === 'true';
let _currentSpeakingBtn = null;
let _currentSpeakingAudio = null;
let _lastReadAloudAt = 0;
let _lastReadAloudText = '';
let _speakSessionSeq = 0;
let _ttsActive = false; // 문장 사이 합성 대기 포함, 전체 읽기 세션 재생 중 여부
const TTS_UI_VERSION = 'v17'; // 스피커 버튼 툴팁에 표시 — 구버전 실행 여부 판별용
const _ttsAudios = new Set(); // 생성된 모든 Audio 추적 — 정지 시 전부 중단 (겹친 세션 잔류음 방지)
function _ttsStopAll() {
  _speakSessionSeq++;
  _ttsActive = false;
  _lastReadAloudAt = 0;
  try {
    _ttsAudios.forEach(a => { try { a.pause(); } catch (_) {} });
    _ttsAudios.clear();
  } catch (_) {}
  _currentSpeakingAudio = null;
  try { window.speechSynthesis?.cancel(); } catch (_) {}
}


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

// ── In-App Confirm Dialog (Electron & Web safe, non-blocking) ────────────────
// 네이티브 confirm()은 Electron WebContentsView 환경 및 외부 SDK(Reticle 등)의
// 모킹 간섭으로 인해 취소(false/undefined)가 강제 반환되거나 포커스 드롭이 발생함.
// 인앱 모달로 대체하여 100% 안정적으로 동작하게 함.
export function daonConfirm(message, {
  title = '확인',
  confirmText = '확인',
  cancelText = '취소',
  danger = false
} = {}) {
  return new Promise((resolve) => {
    let modal = document.getElementById('daon-confirm-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'daon-confirm-modal';
      modal.className = 'fixed inset-0 z-[100000] flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs transition-opacity animate-in fade-in duration-150';
      document.body.appendChild(modal);
    }
    modal.innerHTML = `
      <div class="bg-surface-container-lowest border border-black/15 dark:border-white/15 rounded-[14px] w-full max-w-[420px] shadow-2xl flex flex-col overflow-hidden animate-in zoom-in-95 duration-150 select-none">
        <div class="px-5 py-3.5 border-b border-black/10 dark:border-white/10 flex items-center justify-between bg-surface-container-low">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-[20px] ${danger ? 'text-red-500' : 'text-primary'}">${danger ? 'warning' : 'help_outline'}</span>
            <h3 class="font-label-md font-semibold text-on-surface text-[14px]">${escapeHtml(title)}</h3>
          </div>
          <button type="button" class="_cfm-close p-1 rounded hover:bg-black/5 dark:hover:bg-white/10 text-on-surface-variant flex items-center justify-center cursor-pointer">
            <span class="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
        <div class="p-5 text-[13px] leading-relaxed text-on-surface font-body-sm whitespace-pre-wrap">${escapeHtml(message)}</div>
        <div class="px-5 py-3 border-t border-black/10 dark:border-white/10 bg-surface-container-low flex items-center justify-end gap-2">
          <button type="button" class="_cfm-cancel px-3.5 py-1.5 border border-black/15 dark:border-white/15 rounded-[8px] text-[12px] font-label-md text-on-surface hover:bg-black/5 dark:hover:bg-white/10 transition-colors cursor-pointer">${escapeHtml(cancelText)}</button>
          <button type="button" class="_cfm-ok px-4 py-1.5 rounded-[8px] text-[12px] font-label-md font-medium text-white ${danger ? 'bg-red-600 hover:bg-red-700' : 'bg-primary text-on-primary hover:bg-black/80'} shadow-sm transition-all cursor-pointer">${escapeHtml(confirmText)}</button>
        </div>
      </div>
    `;
    modal.classList.remove('hidden');
    modal.style.display = 'flex';

    function cleanup(result) {
      document.removeEventListener('keydown', keyHandler, true);
      modal.classList.add('hidden');
      modal.style.display = 'none';
      modal.innerHTML = '';
      resolve(result);
    }

    function keyHandler(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        cleanup(false);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        cleanup(true);
      }
    }

    modal.querySelector('._cfm-close')?.addEventListener('click', () => cleanup(false));
    modal.querySelector('._cfm-cancel')?.addEventListener('click', () => cleanup(false));
    modal.querySelector('._cfm-ok')?.addEventListener('click', () => cleanup(true));
    modal.addEventListener('click', (e) => {
      if (e.target === modal) cleanup(false);
    });
    document.addEventListener('keydown', keyHandler, true);
    setTimeout(() => {
      const okBtn = modal.querySelector('._cfm-ok');
      if (okBtn) okBtn.focus();
    }, 50);
  });
}
window.daonConfirm = daonConfirm;

// 외부 스크립트에 의해 window.confirm이 변조/침묵된 경우 네이티브 confirm 복구
try {
  if (typeof window.confirm !== 'function' || window.confirm.toString().includes('DIALOG_OPENED')) {
    const _ifr = document.createElement('iframe');
    _ifr.style.display = 'none';
    document.body.appendChild(_ifr);
    if (_ifr.contentWindow?.confirm) {
      window.confirm = _ifr.contentWindow.confirm.bind(window);
    }
    _ifr.remove();
  }
} catch (_) {}

// ── DOM Initialization ────────────────────────────────────────────────────────

async function initApp() {
  try { ensureWorkshopShell(); } catch (e) { console.error('ensureWorkshopShell err:', e); }
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
  try { bgmPlayer.initUI(); } catch (e) { console.error('bgmPlayer err:', e); }
  try { initTheme(); } catch (e) { console.error('initTheme err:', e); }

  // New modules: Autonomous mode, Workspace, Settings Modal, Agent Personas, Voice
  try { initAutonomousMode(); } catch (e) { console.error('initAutonomousMode err:', e); }
  try { initWorkspaceManager(); } catch (e) { console.error('initWorkspaceManager err:', e); }
  try { initSettingsModal(); } catch (e) { console.error('initSettingsModal err:', e); }
  try { initAgentPersonas(); } catch (e) { console.error('initAgentPersonas err:', e); }
  try { initVoiceFeatures(); } catch (e) { console.error('initVoiceFeatures err:', e); }
  try { initSecretsUI(DaonAPI, { getSessionId: () => state.currentSessionId }); } catch (e) { console.error('initSecretsUI err:', e); }

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
    'agent-skills': '에이전트 스킬 (Capability Registry & Slots)',
    'agent-workshop': '작업장 — 멀티뷰 작업장 (Multi-View Workshop)'
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
    if (targetId === 'agent-boardroom') refreshBoardroomWorkerModelStates();
    if (targetId === 'plugin-mcp') loadLiveMcpServers();
    if (targetId === 'agent-skills') loadLiveSkills();
    if (targetId === 'agent-workshop') ensureWorkshopFrame();

    // [mobile-fixed-chat] 모든 탭을 뷰포트 고정 레이아웃으로 통일.
    // 채팅만 예외였을 때는 문서 전체 스크롤이 발생해 모바일에서 화면이 흔들렸다.
    document.documentElement.classList.add('tab-fit');
    resetScrollForTab(targetId);
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

  // 선택 상태(세션 ID Set) — 재렌더에도 유지
  if (!state.selectedSessions) state.selectedSessions = new Set();

  // "선택 삭제" 버튼 바인딩(1회) — 체크된 세션만 한 번에 삭제
  const delSelBtn = document.getElementById('delete-selected-sessions-btn');
  if (delSelBtn && !delSelBtn._bound) {
    delSelBtn._bound = true;
    delSelBtn.addEventListener('click', async () => {
      const ids = [...state.selectedSessions];
      if (ids.length === 0) return;
      const ok = await daonConfirm(`선택한 ${ids.length}개 세션을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`, {
        title: '선택 세션 일괄 삭제',
        confirmText: '삭제',
        danger: true
      });
      if (!ok) return;
      for (const id of ids) {
        try { await DaonAPI.deleteSession(id); } catch (e) { console.warn('session delete failed:', id, e); }
        state.selectedSessions.delete(id);
        if (state.currentSessionId === id) state.currentSessionId = null;
      }
      await loadSessions();
      if (!state.currentSessionId) {
        const rest = await DaonAPI.getSessions().catch(() => []);
        if (rest && rest.length) await switchSession(rest[0].session_id);
        else renderSessionMessages([]);
      }
    });
  }

  // "전체 선택/해제" 체크박스 바인딩(1회)
  const selAll = document.getElementById('sessions-select-all');
  if (selAll && !selAll._bound) {
    selAll._bound = true;
    selAll.addEventListener('change', () => {
      const checks = [...container.querySelectorAll('.session-check')];
      checks.forEach(c => {
        c.checked = selAll.checked;
        const id = c.getAttribute('data-session-id');
        if (!id) return;
        if (selAll.checked) state.selectedSessions.add(id);
        else state.selectedSessions.delete(id);
      });
      updateSelectedSessionsUI();
    });
  }

  try {
    const [sessions, activeRes] = await Promise.all([
      DaonAPI.getSessions().catch(() => []),
      DaonAPI.getActiveStreams().catch(() => ({}))
    ]);
    // 👑 대표님 지침: 메인 대화창(RECENT THREADS)에는 오직 대표님과 라온의 1:1 대화 세션만 표시
    // 회의실(boardroom), 작업장(workshop/multi), 동료 에이전트 작업 세션은 완전 격리
    const raonSessions = (sessions || []).filter(s => {
      if (s.source === 'boardroom' || s.source === 'workshop' || s.source === 'delegate' || s.source === 'subagent' || s.surface === 'multi' || s.surface === 'boardroom') return false;
      const prof = (s.profile || '').toLowerCase();
      if (prof && prof !== 'raon' && prof !== '라온' && prof !== 'default') return false;
      const tit = String(s.title || '');
      if (tit.startsWith('[라온 총괄기획자의 작업 지시]') || tit.startsWith('[회의실 브리핑]')) return false;
      return true;
    });

    if (!raonSessions || raonSessions.length === 0) {
      container.innerHTML = '<div class="px-space-sm text-[12px] text-on-surface-variant/60">진행된 세션이 없습니다.</div>';
      state.selectedSessions.clear();
      updateSelectedSessionsUI();
      return;
    }

    const runningSessionIds = new Set((activeRes?.active_streams || []).map(s => s.session_id));
    if (state.isStreaming && state.currentSessionId) {
      runningSessionIds.add(state.currentSessionId);
    }
    state.runningSessionIds = runningSessionIds;

    // 1. 현재 작업 중인 세션을 목록 맨 위(최상단)로 이동
    let sortedSessions = [...raonSessions];
    if (state.currentSessionId) {
      const activeIdx = sortedSessions.findIndex(s => s.session_id === state.currentSessionId);
      if (activeIdx > 0) {
        const [activeSess] = sortedSessions.splice(activeIdx, 1);
        sortedSessions.unshift(activeSess);
      }
    }

    // 2. 백그라운드 작업 중인 세션들을 상위권(현재 세션 바로 다음)으로 배치하여 한눈에 확인 가능하도록 정렬
    if (runningSessionIds && runningSessionIds.size > 0) {
      const backgroundRunning = [];
      sortedSessions = sortedSessions.filter((s, idx) => {
        if (idx === 0 && s.session_id === state.currentSessionId) return true;
        if (runningSessionIds.has(s.session_id)) {
          backgroundRunning.push(s);
          return false;
        }
        return true;
      });
      if (state.currentSessionId && sortedSessions.length > 0 && sortedSessions[0].session_id === state.currentSessionId) {
        sortedSessions.splice(1, 0, ...backgroundRunning);
      } else {
        sortedSessions.unshift(...backgroundRunning);
      }
    }

    // 화면에 표시되지 않는(25개 초과) 세션의 잔여 선택 정리
    const visibleIds = new Set(sortedSessions.slice(0, 25).map(s => s.session_id));
    for (const id of [...state.selectedSessions]) {
      if (!visibleIds.has(id)) state.selectedSessions.delete(id);
    }

    container.innerHTML = '';
    sortedSessions.slice(0, 25).forEach((sess) => {
      const isCurrent = sess.session_id === state.currentSessionId;
      const isRunning = runningSessionIds.has(sess.session_id);
      const a = document.createElement('a');
      a.setAttribute('data-session-id', sess.session_id);

      // 눈에 확 띄는 색상 & 디자인 적용 (현재 열린 세션 vs 백그라운드 작업 중 세션 vs 비활성 세션)
      if (isCurrent) {
        a.className = 'session-item group min-h-[36px] py-1 flex items-center px-space-sm rounded-[9px] font-body-sm text-body-sm transition-all cursor-pointer relative bg-purple-500/15 dark:bg-purple-900/30 border-l-[3.5px] border-l-purple-600 dark:border-l-purple-400 border border-purple-500/35 text-purple-950 dark:text-purple-100 font-semibold shadow-xs';
      } else if (isRunning) {
        a.className = 'session-item group min-h-[34px] py-1 flex items-center px-space-sm rounded-[8px] font-body-sm text-body-sm transition-all cursor-pointer relative bg-amber-500/10 dark:bg-amber-400/10 border-l-[3.5px] border-l-amber-500 border border-amber-500/30 text-amber-950 dark:text-amber-100 font-medium shadow-2xs hover:bg-amber-500/15';
      } else {
        a.className = 'session-item group min-h-[32px] py-1 flex items-center px-space-sm rounded-[8px] font-body-sm text-body-sm transition-all cursor-pointer text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface border border-transparent';
      }
      a.title = sess.title || '세션';

      // 선택 체크박스 (여러 세션 일괄 삭제용)
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.className = 'session-check w-3.5 h-3.5 mr-1.5 shrink-0 cursor-pointer accent-purple-600';
      chk.setAttribute('data-session-id', sess.session_id);
      chk.checked = state.selectedSessions.has(sess.session_id);
      chk.title = '이 세션 선택';
      chk.addEventListener('click', (e) => e.stopPropagation());
      chk.addEventListener('change', (e) => {
        if (e.target.checked) state.selectedSessions.add(sess.session_id);
        else state.selectedSessions.delete(sess.session_id);
        updateSelectedSessionsUI();
      });
      a.appendChild(chk);

      const span = document.createElement('span');
      span.className = 'session-title-wrap truncate flex-1 pr-1 flex items-center gap-1.5';
      const pIcon = getPersonaIcon(sess.profile);

      // 열지 않은 세션도 백그라운드 작업 중이면 확연한 "작업중" 배지 표시
      let badgeHtml = '';
      if (isRunning) {
        if (isCurrent) {
          badgeHtml = '<span class="session-running-badge shrink-0 px-1.5 py-[1px] text-[9px] font-mono font-bold rounded-full bg-purple-600 text-white flex items-center gap-1 shadow-xs"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>작업중</span>';
        } else {
          badgeHtml = '<span class="session-running-badge shrink-0 px-1.5 py-[1px] text-[9px] font-mono font-bold rounded-full bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/40 flex items-center gap-1 shadow-xs animate-pulse"><span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping"></span>작업중</span>';
        }
      }

      span.innerHTML = `<span class="shrink-0 text-[12px] opacity-80" title="${escapeHtml(sess.profile || '라온')}">${pIcon}</span><span class="truncate flex-1">${escapeHtml(sess.title || `세션 #${sess.session_id.slice(0, 6)}`)}</span>${badgeHtml}`;
      a.appendChild(span);

      // 개별 세션 삭제 버튼
      const delBtn = document.createElement('button');
      delBtn.className = `w-5 h-5 flex items-center justify-center rounded text-on-surface-variant/40 hover:text-red-600 hover:bg-red-500/10 active:scale-95 transition-all shrink-0 ml-1 cursor-pointer ${
        isCurrent ? 'opacity-70 hover:opacity-100' : 'opacity-0 group-hover:opacity-100'
      }`;
      delBtn.title = '이 세션 삭제';
      delBtn.type = 'button';
      delBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">delete</span>';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const title = sess.title || '선택한 세션';
        const ok = await daonConfirm(`'${title}'을(를) 삭제하시겠습니까?`, {
          title: '세션 삭제',
          confirmText: '삭제',
          danger: true
        });
        if (ok) {
          await DaonAPI.deleteSession(sess.session_id);
          state.selectedSessions.delete(sess.session_id);
          if (state.runningSessionIds) state.runningSessionIds.delete(sess.session_id);
          if (state.currentSessionId === sess.session_id) {
            state.currentSessionId = null;
          }
          await loadSessions();
        }
      });
      a.appendChild(delBtn);

      a.addEventListener('click', async () => {
        if (state.currentSessionId !== sess.session_id) {
          await switchSession(sess.session_id);
        }
      });

      container.appendChild(a);
    });

    updateSelectedSessionsUI();

    // Select the first session if none selected
    if (!state.currentSessionId && sortedSessions.length > 0) {
      await switchSession(sortedSessions[0].session_id);
    }
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}

// ── 사이드바 세션들의 실시간 "작업중" 배지 & 테두리 실시간 동기화 함수 ────────
function updateRunningSessionBadges(activeStreams = []) {
  const runningSet = new Set((activeStreams || []).map(s => s.session_id));
  if (state.isStreaming && state.currentSessionId) {
    runningSet.add(state.currentSessionId);
  }
  state.runningSessionIds = runningSet;

  const container = document.getElementById('recent-threads-list');
  if (!container) return;

  const items = container.querySelectorAll('a[data-session-id]');
  items.forEach(a => {
    const sid = a.getAttribute('data-session-id');
    const isCurrent = sid === state.currentSessionId;
    const isRunning = runningSet.has(sid);

    // 1. 세션 테두리 및 배경 디자인 실시간 업데이트
    if (isCurrent) {
      a.className = 'session-item group min-h-[36px] py-1 flex items-center px-space-sm rounded-[9px] font-body-sm text-body-sm transition-all cursor-pointer relative bg-purple-500/15 dark:bg-purple-900/30 border-l-[3.5px] border-l-purple-600 dark:border-l-purple-400 border border-purple-500/35 text-purple-950 dark:text-purple-100 font-semibold shadow-xs';
    } else if (isRunning) {
      a.className = 'session-item group min-h-[34px] py-1 flex items-center px-space-sm rounded-[8px] font-body-sm text-body-sm transition-all cursor-pointer relative bg-amber-500/10 dark:bg-amber-400/10 border-l-[3.5px] border-l-amber-500 border border-amber-500/30 text-amber-950 dark:text-amber-100 font-medium shadow-2xs hover:bg-amber-500/15';
    } else {
      a.className = 'session-item group min-h-[32px] py-1 flex items-center px-space-sm rounded-[8px] font-body-sm text-body-sm transition-all cursor-pointer text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface border border-transparent';
    }

    // 2. "작업중" 배지 동적 업데이트
    const wrap = a.querySelector('.session-title-wrap');
    let badge = a.querySelector('.session-running-badge');

    if (isRunning) {
      const badgeHtml = isCurrent
        ? '<span class="session-running-badge shrink-0 px-1.5 py-[1px] text-[9px] font-mono font-bold rounded-full bg-purple-600 text-white flex items-center gap-1 shadow-xs"><span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>작업중</span>'
        : '<span class="session-running-badge shrink-0 px-1.5 py-[1px] text-[9px] font-mono font-bold rounded-full bg-amber-500/20 text-amber-700 dark:text-amber-300 border border-amber-500/40 flex items-center gap-1 shadow-xs animate-pulse"><span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping"></span>작업중</span>';

      if (badge) {
        const temp = document.createElement('div');
        temp.innerHTML = badgeHtml;
        badge.replaceWith(temp.firstElementChild);
      } else if (wrap) {
        const temp = document.createElement('div');
        temp.innerHTML = badgeHtml;
        wrap.appendChild(temp.firstElementChild);
      }
    } else {
      if (badge) badge.remove();
    }
  });
}

// 선택 삭제 UI 갱신 — 개수 라벨 / 버튼 활성 / 전체선택 체크박스 상태
function updateSelectedSessionsUI() {
  const container = document.getElementById('recent-threads-list');
  const n = (state.selectedSessions && state.selectedSessions.size) || 0;
  const btn = document.getElementById('delete-selected-sessions-btn');
  const lbl = document.getElementById('delete-selected-label');
  if (lbl) lbl.textContent = n > 0 ? `선택 삭제 (${n})` : '선택 삭제';
  if (btn) {
    btn.disabled = n === 0;
    btn.classList.toggle('opacity-40', n === 0);
    btn.classList.toggle('pointer-events-none', n === 0);
  }
  const selAll = document.getElementById('sessions-select-all');
  if (selAll && container) {
    const checks = [...container.querySelectorAll('.session-check')];
    const on = checks.filter(c => c.checked).length;
    selAll.checked = checks.length > 0 && on === checks.length;
    selAll.indeterminate = on > 0 && on < checks.length;
  }
}

async function switchSession(sessionId) {
  // 열려있던 세션의 UI SSE 리스너가 살아있다면 안전하게 연결 해제 (백엔드 세션 작업은 지속됨)
  if (state.activeStream) {
    try { state.activeStream.close(); } catch (_) {}
    state.activeStream = null;
  }
  state.currentStreamId = null;
  state.isStreaming = false;
  updateSendButtonUI(false);

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

  // 사이드바 목록 즉각 갱신: 현재 세션이 맨 위로 올라오고 활성 스타일로 강조됨
  await loadSessions();

  // Load messages & sync profile
  try {
    const sess = await DaonAPI.getSession(sessionId);
    if (!sess) return;
    state.currentSessionModel = sess.model;
    const modelId = typeof sess.model === 'string' ? sess.model : (sess.model?.id || 'deepseek-v4.1-flash');
    syncModelAndProviderDisplay(modelId);

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
    } else {
      // 프로필 미지정 세션은 항상 기본 오케스트레이터 'raon'으로 안전 복원 (프라다/타 에이전트 페르소나 잔류 방지)
      state.currentAgentPersona = 'raon';
      localStorage.setItem('daon_active_persona', 'raon');
      const personaSelect = document.getElementById('agent-persona-select');
      if (personaSelect && personaSelect.querySelector('option[value="raon"]')) {
        personaSelect.value = 'raon';
      }
      DaonAPI.switchProfile('raon').catch(() => {});
    }

    renderSessionMessages(sess.messages || [], false);
    await syncMultiDeviceChat();
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

  // ── 메시지 턴 그룹화: 단일 턴 내 다중 어시스턴트 메시지(도구 호출 전 중간 멘트 등) 병합 ──
  // 도구 실행 루프 동안 출력된 중간 멘트들은 thought/reasoning 과정으로 모으고,
  // 최종 답변만 메인 말풍선으로 렌더링하여 작업 완료 후 말풍선이 여러 개로 쪼개지는 파편화 방지
  let pendingAssistants = [];

  const flushAssistants = () => {
    if (pendingAssistants.length === 0) return;
    if (pendingAssistants.length === 1) {
      const msg = pendingAssistants[0];
      const reasoning = msg.reasoning_content || msg.thinking || extractThinkBlocks(msg.content);
      const cleanContent = stripThinkBlocks(msg.content);
      const actualModel = msg.actual_model || msg.model || state.currentSessionModel || '';
      const requestedModel = msg.requested_model || '';
      appendAssistantMessage(cleanContent, msg.timestamp, false, reasoning, false, actualModel, requestedModel);
    } else {
      let targetIdx = pendingAssistants.length - 1;
      while (targetIdx > 0 && !stripThinkBlocks(pendingAssistants[targetIdx].content || '').trim()) {
        targetIdx--;
      }
      const finalMsg = pendingAssistants[targetIdx];
      const earlierMsgs = pendingAssistants.filter((_, idx) => idx !== targetIdx);

      const reasoningParts = [];
      earlierMsgs.forEach(m => {
        const r = m.reasoning_content || m.thinking || extractThinkBlocks(m.content);
        const c = stripThinkBlocks(m.content || '').trim();
        if (r) reasoningParts.push(r);
        if (c) reasoningParts.push(`■ 진행 과정: ${c}`);
      });
      const finalR = finalMsg.reasoning_content || finalMsg.thinking || extractThinkBlocks(finalMsg.content);
      if (finalR) reasoningParts.push(finalR);

      const combinedReasoning = reasoningParts.join('\n\n');
      const cleanContent = stripThinkBlocks(finalMsg.content || '');
      const actualModel = finalMsg.actual_model || finalMsg.model || state.currentSessionModel || '';
      const requestedModel = finalMsg.requested_model || '';
      appendAssistantMessage(cleanContent, finalMsg.timestamp, false, combinedReasoning, false, actualModel, requestedModel);
    }
    pendingAssistants = [];
  };

  messages.forEach(msg => {
    if (!msg || !msg.content) return;
    // 내부 제어/시스템 안내 메시지는 사용자 말풍선으로 노출되지 않도록 필터링
    if (isInternalSystemMessage(msg.content)) return;

    if (msg.role === 'user') {
      flushAssistants();
      appendUserMessage(msg.content, msg.timestamp, false);
    } else if (msg.role === 'assistant') {
      pendingAssistants.push(msg);
    }
  });
  flushAssistants();

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
      // [핵심 1] 재생 중이면 텍스트 검사와 무관하게 즉시 전부 정지!
      const isPlaying = _ttsActive || _ttsAudios.size > 0 || (_currentSpeakingAudio && !_currentSpeakingAudio.paused) || (window.speechSynthesis && window.speechSynthesis.speaking);
      if (isPlaying) {
        _ttsStopAll();
        try {
          document.querySelectorAll('.read-aloud-btn').forEach(b => {
            const ic = b.querySelector('.material-symbols-outlined');
            if (ic) ic.textContent = 'volume_up';
            b.title = '소리내어 읽기 ' + TTS_UI_VERSION;
          });
        } catch (_) {}
        _currentSpeakingBtn = null;
        return;
      }
      // [핵심 2] 실제 v2 DOM 클래스(.response-body 또는 .msg-content)로 텍스트 추출
      const article = btn.closest('article') || btn.closest('.chat-bubble-assistant');
      let text = '';
      if (article) {
        const contentEl = article.querySelector('.response-body') || article.querySelector('.msg-content');
        text = contentEl ? contentEl.innerText : '';
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
      const targetProfile = state.currentAgentPersona || 'raon';
      const res = await DaonAPI.createSession(titlePrompt, targetProfile);
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
    if (!state.runningSessionIds) state.runningSessionIds = new Set();
    state.runningSessionIds.add(state.currentSessionId);
    updateRunningSessionBadges(Array.from(state.runningSessionIds).map(id => ({ session_id: id })));

    state.activeStream = attachStreamEvents(startRes.stream_id, bubble, contentEl, timeEl);
  } catch (err) {
    state.isStreaming = false;
    state.activeStream = null;
    state.currentStreamId = null;
    if (state.runningSessionIds && state.currentSessionId) {
      state.runningSessionIds.delete(state.currentSessionId);
      updateRunningSessionBadges(Array.from(state.runningSessionIds).map(id => ({ session_id: id })));
    }
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
  let lastToolMark = 0; // 마지막 도구 활동 시점의 fullResponse 길이 — 자동TTS는 이 뒤(최종 보고)만 읽음
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
      lastToolMark = fullResponse.length;
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
      lastToolMark = fullResponse.length;
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
      // 채팅 → 다이나믹 하네스: 백그라운드 실행 안내 + 하네스 탭 자동 전환 (구 UI 패턴 이식)
      // 하네스는 이제 백그라운드로 돌므로 챗 턴은 즉시 끝난다. 진행/결과는
      // 하네스 탭에서 폴링으로 확인하고, 사용자는 언제든 챗으로 돌아와 계속 대화할 수 있다.
      if (name === 'execute_dynamic_harness') {
        setStatus('🎯 다이나믹 하네스 백그라운드 실행 중...', 'sync');
        try {
          if (typeof window.activateTab === 'function') window.activateTab('dynamic-harness');
        } catch (_) {}
        return;
      }
      // 채팅 → 회의실: 라온이 회의를 소집하면 회의실 탭으로 넘어가 토론을 중계한다.
      // (라온은 발언자가 아니라 사회자 — 진행/결론은 회의실 탭에서 지켜본다.)
      if (name === 'convene_boardroom') {
        setStatus('🏛️ 회의실 소집 중... (에이전트 토론 → 라온 취합 → 재질의)', 'groups');
        try { startBoardroomLiveWatch(); } catch (_) {}
        try {
          if (typeof window.activateTab === 'function') window.activateTab('agent-boardroom');
        } catch (_) {}
        return;
      }
      if (name === 'open_workshop') {
        setStatus('🏗️ 작업장으로 승계 중...', 'construction');
        return;
      }
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
      lastToolMark = fullResponse.length;
      setStatus('⚙️ 도구 실행 완료 · 결과 분석 중...', 'sync');
      try { handleBoardroomToolResult(data); } catch (e) { console.warn('boardroom tool result err:', e); }
    },
    onHarnessStarted(data) {
      lastToolMark = fullResponse.length;
      // 챗에서 백그라운드 하네스가 시작되면 run_id가 push된다.
      // 이 run_id로 하네스 탭 폴링을 시작해, 챗에서 비동기로 돌린 작업의
      // 진행상황을 'Dynamic Harness 세션' 탭에서 실시간으로 볼 수 있게 한다.
      try {
        const runId = data?.run_id || '';
        if (!runId) return;
        state.currentHarnessRunId = runId;
        setStatus('🎯 다이나믹 하네스 실행 중...', 'sync');
        // 하네스 탭으로 전환 (사용자가 다른 탭에 있으면 보여준다)
        try {
          if (typeof window.activateTab === 'function') window.activateTab('dynamic-harness');
        } catch (_) {}
        // 폴링 시작 — 탭에서 직접 시작한 것과 동일 경로
        if (typeof startHarnessPolling === 'function') startHarnessPolling(runId);
        if (typeof appendTelemetryLog === 'function') {
          appendTelemetryLog(`[Dispatcher] 챗에서 하네스 잡 시작됨. Run ID: ${runId}`);
        }
      } catch (e) {
        console.warn('[SSE] onHarnessStarted handler error:', e);
      }
    },
    onHarnessDone(data) {
      // 백그라운드 하네스 잡 완료 알림 (턴이 살아있을 때만 도착)
      // 하네스 완료 이후 텍스트 = 최종 보고 → 자동TTS 표시를 여기로 갱신
      lastToolMark = fullResponse.length;
      try {
        const ok = data?.ok !== false;
        const res = String(data?.result || '').trim();
        const runId = data?.run_id || '';
        setStatus(ok ? '✅ 다이나믹 하네스 완료' : '⚠️ 다이나믹 하네스 오류', ok ? 'check_circle' : 'error');
        const note = document.createElement('div');
        note.className = 'harness-done-note mt-2 px-2.5 py-2 rounded-[8px] text-[12px] leading-relaxed ' +
          (ok ? 'bg-emerald-50 border border-emerald-300 text-emerald-800'
              : 'bg-rose-50 border border-rose-300 text-rose-800');
        const head = ok ? '🎯 <b>다이나믹 하네스 완료</b>' : '⚠️ <b>다이나믹 하네스 오류</b>';
        const tail = res ? escapeHtml(res).slice(0, 400) : "결과는 'Dynamic Harness 세션' 탭에서 확인하세요.";
        note.innerHTML = head + (runId ? ` <span class="opacity-70">(run_id: ${escapeHtml(runId)})</span>` : '') + '<br>' + tail;
        contentEl.appendChild(note);
        if (typeof scrollChatToBottom === 'function') scrollChatToBottom();
      } catch (e) {
        console.warn('[SSE] onHarnessDone handler error:', e);
      }
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
        const doneProfile = String(data?.session?.profile || '').toLowerCase();
        const currentProfile = String(state.currentAgentPersona || 'raon').toLowerCase();
        // 🚨 세션 하이재킹 차단: 프로필이 불일치하는 외부/서브 에이전트 세션의 doneSid 로 메인 세션을 덮어쓰지 않는다
        const isProfileCompatible = !doneProfile || doneProfile === currentProfile || (currentProfile.includes('raon') && doneProfile.includes('raon'));
        if (doneSid && doneSid !== state.currentSessionId && isProfileCompatible) {
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
      if (state.runningSessionIds) {
        if (data?.session?.session_id) state.runningSessionIds.delete(data.session.session_id);
        if (state.currentSessionId) state.runningSessionIds.delete(state.currentSessionId);
      }
      updateRunningSessionBadges(Array.from(state.runningSessionIds || []).map(id => ({ session_id: id })));
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

      // 자동 음성 읽기(TTS): 마지막 도구 사용 이후의 최종 보고만 읽기 (중간 작업보고 제외)
      if (_autoTTS && fullResponse) {
        // lastToolMark 뒤가 비어있어도 fullResponse 전체를 넘기지 않고, pickAutoTTSText가 지능적으로 결론부만 추출하도록 처리
        const rawFinal = (lastToolMark > 0 && lastToolMark < fullResponse.length)
          ? fullResponse.slice(lastToolMark)
          : fullResponse;
        const speakText = pickAutoTTSText(rawFinal);
        if (speakText) {
          // bubble이 동기화로 교체됐을 수 있으니 못 찾으면 화면의 마지막 읽기 버튼으로 폴백
          let readBtn = null;
          try {
            readBtn = bubble.querySelector('.read-aloud-btn')
              || [...document.querySelectorAll('.read-aloud-btn')].pop() || null;
          } catch (_) { readBtn = null; }
          try {
            window._ttsDiag = `v=${TTS_UI_VERSION} mark=${lastToolMark}/${fullResponse.length} speak=${speakText.length}`;
            console.log('[TTS-auto]', window._ttsDiag);
            if (readBtn) readBtn.title = `자동읽기 ${window._ttsDiag} (클릭 시 정지)`;
          } catch (_) {}
          readAloudText(speakText, readBtn, { mode: 'replace' });
        }
      }

      onFinish?.();
    },
    onError(err) {
      clearThinkingState(contentEl);
      removeStatus();
      state.isStreaming = false;
      state.activeStream = null;
      state.currentStreamId = null;
      if (state.runningSessionIds && state.currentSessionId) {
        state.runningSessionIds.delete(state.currentSessionId);
      }
      updateRunningSessionBadges(Array.from(state.runningSessionIds || []).map(id => ({ session_id: id })));
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
 * 텍스트 음성 출력 (TTS) - 3단계 스마트 보이스
 * (Supertonic 로컬 고품질 한국어 -> Edge TTS -> Web Speech API 폴백)
 * 목소리: Supertonic 10종 (F1~F5 여성 / M1~M5 남성), localStorage 'daon_tts_voice' 저장
 */
const TTS_VOICES = ['F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5'];
function getTTSVoice() {
  try {
    const v = (localStorage.getItem('daon_tts_voice') || 'F1').toUpperCase();
    return TTS_VOICES.includes(v) ? v : 'F1';
  } catch (_) { return 'F1'; }
}
/**
 * 자동 TTS용 정제:
 * 1. '최종 결과', '결과 보고', '요약', '결론' 등의 섹션이 있으면 해당 섹션만 추출
 * 2. 없으면 마지막 5문단만 읽는다 (핵심 결론 위주)
 */
function pickAutoTTSText(full) {
  if (!full) return '';
  let text = String(full)
    .replace(/```[\s\S]*?```/g, ' ')      // 코드 블록 제거
    .replace(/https?:\/\/\S+/g, ' ')      // URL 제거
    .replace(/^>.*$/gm, ' ')              // 인용구 제거
    .trim();
  // 1순위: '최종 결과', '결과 보고', '결론', '요약' 헤더 뒤의 내용만 추출
  // (이모지·번호 장식이 앞에 붙어도 인식: "## ✅ 최종 결과", "**요약**" 등)
  const summaryMatch = text.match(/(?:^|\n)\s*(?:#{1,4}|\*\*)?\s*[^\n]{0,20}?(?:최종\s*결과|결과\s*보고|요약|결론|완료\s*내역)[^\n]*\n([\s\S]+)/i);
  if (summaryMatch && summaryMatch[1].trim()) {
    text = summaryMatch[1].trim();
  } else {
    // 2순위: 헤더가 없으면 마지막 5문단만 읽기 (핵심 결론 위주)
    const paragraphs = text.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
    if (paragraphs.length > 5) {
      text = paragraphs.slice(-5).join('\n\n');
    }
  }
  // 특수문자 마크다운 기호 제거 + 공백 정리
  text = text.replace(/[#*_~`]/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  // 음성으로 읽기에 너무 길면 최대 800자(약 7~8문장) 이내로 간결화 (한글 기준 여유분)
  if (text.length > 800) {
    const sentences = text.split(/(?<=[.!?])\s+/);
    let shortText = '';
    for (const s of sentences) {
      if ((shortText + s).length > 800) break;
      shortText += (shortText ? ' ' : '') + s;
    }
    text = shortText || text.slice(0, 800) + '...';
  }
  return text;
}
/**
 * TTS 문장 분할: 마침표·물음표·느낌표·마침표(。)·줄바꿈 기준.
 * 너무 짧은 조각은 합성 오버헤드 방지로 병합, 200자 초과 조각은 강제 절단.
 */
function splitForTTS(t) {
  const parts = String(t).split(/(?<=[.!?。])\s+|\n+/).map(s => s.trim()).filter(Boolean);
  const merged = [];
  for (const p of parts) {
    const last = merged[merged.length - 1];
    if (last && last.length < 30 && (last.length + p.length + 1) < 200) {
      merged[merged.length - 1] = last + ' ' + p;
    } else merged.push(p);
  }
  const out = [];
  for (const m of merged) {
    if (m.length <= 200) out.push(m);
    else { for (let i = 0; i < m.length; i += 200) out.push(m.slice(i, i + 200)); }
  }
  return out.filter(Boolean);
}
/**
 * 문장 단위 순차 재생: 첫 문장 합성 즉시 재생 + 재생 중 다음 문장 프리페치.
 * 중단(새 읽기/정지 버튼)이면 speakSession 불일치로 루프 탈출.
 */
function playTTSChunked(chunks, voice, speakSession, resetUI, fullFallbackText) {
  const urls = new Array(chunks.length).fill(null);
  const pending = new Map();
  const fetchChunkUrl = async (chunkText) => {
    const url = `/api/speak/tts?text=${encodeURIComponent(chunkText.slice(0, 500))}&voice=${voice}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('TTS HTTP ' + resp.status);
    const buf = await resp.blob();
    if (!buf || buf.size < 100) throw new Error('Empty audio');
    return URL.createObjectURL(buf);
  };
  const ensureChunk = (i) => {
    if (urls[i]) return Promise.resolve(urls[i]);
    if (!pending.has(i)) {
      pending.set(i, fetchChunkUrl(chunks[i]).then(u => { urls[i] = u; return u; }));
    }
    return pending.get(i);
  };
  const playOne = (url) => new Promise((resolve, reject) => {
    if (speakSession !== _speakSessionSeq) return reject(new Error('stopped'));
    const a = new Audio(url);
    _ttsAudios.add(a);
    try { if (_currentSpeakingAudio && _currentSpeakingAudio !== a) _currentSpeakingAudio.pause(); } catch (_) {}
    _currentSpeakingAudio = a;
    a.onended = () => { try { _ttsAudios.delete(a); } catch (_) {} resolve(); };
    a.onerror = () => { try { _ttsAudios.delete(a); } catch (_) {} reject(new Error('audio error')); };
    a.play().catch(reject);
  });
  const cleanup = () => urls.forEach(u => { try { if (u) URL.revokeObjectURL(u); } catch (_) {} });
  const fallbackSpeech = () => {
    if (speakSession !== _speakSessionSeq) return;
    _currentSpeakingAudio = null;
    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(fullFallbackText);
      utterance.lang = 'ko-KR';
      utterance.rate = 1.05;
      try {
        const voices = window.speechSynthesis.getVoices();
        const koreanVoice = voices.find(v => v.lang.startsWith('ko') && (v.name.includes('SunHi') || v.name.includes('Heami') || v.name.includes('Google') || v.name.includes('Korean')));
        if (koreanVoice) utterance.voice = koreanVoice;
      } catch (_) {}
      utterance.onend = resetUI;
      utterance.onerror = resetUI;
      window.speechSynthesis.speak(utterance);
    } else resetUI();
  };
  (async () => {
    try {
      const firstP = ensureChunk(0);
      if (chunks.length > 1) ensureChunk(1);
      const firstUrl = await firstP;
      if (speakSession !== _speakSessionSeq) { cleanup(); return; }
      await playOne(firstUrl);
      for (let idx = 1; idx < chunks.length; idx++) {
        if (speakSession !== _speakSessionSeq) break;
        if (idx + 1 < chunks.length) ensureChunk(idx + 1);
        const u = await ensureChunk(idx);
        if (speakSession !== _speakSessionSeq) break;
        await playOne(u);
      }
      cleanup();
      if (speakSession === _speakSessionSeq) resetUI();
    } catch (e) {
      cleanup();
      fallbackSpeech();
    }
  })();
}
function readAloudText(text, btnElement, opts) {
  // opts.mode: 'toggle' (수동 버튼: 재생 중 클릭 → 정지) / 'replace' (자동: 이전 중단 후 새 재생)
  const mode = (opts && opts.mode) || 'toggle';
  // 재생 중 읽기 버튼 클릭 → 전부 정지 후 종료 (같은/다른 버튼 구분 안 함:
  // 채팅 동기화가 말풍선 DOM을 갈아엎으면 버튼 객체가 바뀌어 '다른 버튼'으로 오판됨).
  // 수동 토글: 멈춘 상태에서 한 번 더 누르면 다시 재생. 자동 replace: 정지 후 새 재생 계속.
  const isPlaying = _ttsActive || _ttsAudios.size > 0 || (_currentSpeakingAudio && !_currentSpeakingAudio.paused) || (window.speechSynthesis && window.speechSynthesis.speaking);
  if (isPlaying) {
    _ttsStopAll();
    // 구 DOM 참조가 끊겼을 수 있으니 화면의 모든 읽기 버튼 아이콘을 되돌림
    try {
      document.querySelectorAll('.read-aloud-btn').forEach(b => {
        const ic = b.querySelector('.material-symbols-outlined');
        if (ic) ic.textContent = 'volume_up';
        b.title = '소리내어 읽기 ' + TTS_UI_VERSION;
      });
    } catch (_) {}
    _currentSpeakingBtn = null;
    if (mode === 'toggle') return;
  }

  if (!text || !text.trim()) return;

  // 중복 호출 차단: 같은 텍스트가 600ms 안에 또 들어오면 무시 (이중 바인딩 안전망)
  const _now = Date.now();
  const _key = text.slice(0, 120);
  if (_key === _lastReadAloudText && (_now - _lastReadAloudAt) < 600) return;
  _lastReadAloudAt = _now;
  _lastReadAloudText = _key;

  // 이전 재생이 남아 있으면 전부 정지 (추적 중인 모든 오디오 + 브라우저 음성)
  _ttsStopAll();

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
    _ttsActive = false;
    if (icon) icon.textContent = 'volume_up';
    if (btnElement) btnElement.title = '소리내어 읽기 ' + TTS_UI_VERSION;
    _currentSpeakingBtn = null;
    _currentSpeakingAudio = null;
  };

  // 1순위: 서버 Supertonic 로컬 TTS (선택 목소리, 고품질 한국어)
  // 긴 텍스트는 문장 단위로 쪼개 첫 문장부터 순차 재생 (체감 딜레이 7초→1초대)
  const voice = getTTSVoice();
  const speakSession = _speakSessionSeq;
  const chunks = splitForTTS(cleanText);
  const useChunked = chunks.length > 1 && cleanText.length > 80;
  if (useChunked) {
    _ttsActive = true;
    playTTSChunked(chunks, voice, speakSession, resetUI, cleanText);
    return;
  }
  const ttsUrl = `/api/speak/tts?text=${encodeURIComponent(cleanText.slice(0, 2000))}&voice=${voice}`;
  const audio = new Audio(ttsUrl);
  _ttsAudios.add(audio);
  _currentSpeakingAudio = audio;
  _ttsActive = true;

  audio.onended = () => { try { _ttsAudios.delete(audio); } catch (_) {} resetUI(); };
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
  // TTS 목소리 선택 셀렉트 (Supertonic 10종, localStorage 저장)
  const ttsVoiceSel = document.getElementById('tts-voice-select');
  if (ttsVoiceSel) {
    ttsVoiceSel.value = getTTSVoice();
    ttsVoiceSel.addEventListener('change', () => {
      try { localStorage.setItem('daon_tts_voice', ttsVoiceSel.value); } catch (_) {}
    });
  }
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
        _ttsStopAll(); // window.speechSynthesis?.cancel() 대신 모든 TTS 오디오 강제 중단
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

  isSyncingMultiDevice = true;
  try {
    const res = await DaonAPI.getActiveStreams().catch(() => ({}));
    const activeStreams = res?.active_streams || [];

    // 1. 열지 않은 세션을 포함하여 모든 세션의 실시간 '작업중' 배지 및 테두리 동기화
    updateRunningSessionBadges(activeStreams);

    if (!state.currentSessionId) return;

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
  let cleanContent = String(content);

  // ★ 실시간 브라우저 환경 컨텍스트가 포함된 경우 순수 사용자 요청만 추출하여 깔끔하게 노출
  if (cleanContent.includes('[실시간 브라우저 환경 컨텍스트') || cleanContent.includes('[★ 현재 사용자가 직접 보고 있는 활성 브라우저 화면]')) {
    const userReqMatch = cleanContent.match(/\[사용자 요청\][\s\S]*?(?:"""|```)?\s*([\s\S]+?)\s*(?:"""|```)?$/i);
    if (userReqMatch && userReqMatch[1] && userReqMatch[1].trim()) {
      cleanContent = userReqMatch[1].trim();
    } else {
      cleanContent = cleanContent.replace(/\[(?:실시간 브라우저 환경 컨텍스트|★ 현재 사용자가 직접 보고 있는 활성 브라우저 화면)[\s\S]*?\[사용자 요청\]\s*/i, '').trim();
    }
  }

  let escaped = escapeHtml(cleanContent);

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
        <button class="p-1 hover:text-on-surface transition-colors read-aloud-btn" title="소리내어 읽기 ${TTS_UI_VERSION}" type="button">
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

  // 읽기 버튼은 전역 위임 핸들러(문서 클릭, '.read-aloud-btn')에서만 처리
  // (여기에 개별 바인딩을 추가하면 전역 위임과 중복 호출되어 음성이 두 번 나옴)

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

/**
 * [mobile-fixed-chat] 채팅 스크롤 주인 = 채팅 패널 내부 메시지 영역.
 * 기존에는 window(문서) 스크롤이었으나, 모바일 주소창 접힘/펼침에 따라
 * 화면이 커졌다 작아졌다 하고 fixed 컴포저가 흔들려서 내부 스크롤로 전환함.
 * CSS: #tab-chat-session > div:first-child { overflow-y:auto }
 */
function getChatScroller() {
  const panel = document.getElementById('tab-chat-session');
  if (panel && panel.firstElementChild) return panel.firstElementChild;
  return document.scrollingElement || document.documentElement;
}

/** 탭 전환 시 각 스크롤 영역만 안전하게 리셋 (window 스크롤 없음) */
function resetScrollForTab(targetId) {
  try {
    if (targetId === 'chat-session') {
      const sc = getChatScroller();
      if (sc) sc.scrollTop = 0;
    } else {
      const panel = document.getElementById('tab-' + targetId);
      if (panel) panel.scrollTop = 0;
    }
    if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
  } catch (e) { /* noop */ }
}

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

    const headerOffset = 12; // 내부 스크롤이므로 헤더 오프셋은 작은 여백만
    const scroller = getChatScroller();
    const sRect = scroller.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    const targetTop = scroller.scrollTop + (rect.top - sRect.top) - headerOffset;

    scroller.scrollTo({
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

  const scroller = getChatScroller();
  const sRect = scroller.getBoundingClientRect();
  const rect = targetEl.getBoundingClientRect();
  const composerTop = getComposerTop();
  // 스크롤 영역 내부 좌표계로 변환 (뷰포트 좌표 → 영역 기준)
  const safeLandingLine = (composerTop - sRect.top) - 40;

  // 원하는 위치: rect.bottom(영역 기준)이 safeLandingLine과 일치하도록 scrollTop 산출
  const relBottom = rect.bottom - sRect.top;
  const targetScrollTop = scroller.scrollTop + relBottom - safeLandingLine;
  scroller.scrollTo({
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

  // 스크롤 위치 감지 (내부 스크롤 컨테이너 기준 — window는 더 이상 스크롤되지 않음)
  const scroller = getChatScroller();
  if (scroller) {
    scroller.addEventListener('scroll', () => {
      const distFromBottom = scroller.scrollHeight - (scroller.scrollTop + scroller.clientHeight);

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
  }

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

    const scroller = getChatScroller();
    const sRect = scroller.getBoundingClientRect();
    const rect = targetEl.getBoundingClientRect();
    const composerTop = getComposerTop();

    // 프롬프트 입력창 윗선보다 40px 위의 안전지대 (스크롤 영역 내부 좌표계)
    const safeLandingLine = (composerTop - sRect.top) - 40;
    const relBottom = rect.bottom - sRect.top;
    const overflow = relBottom - safeLandingLine;

    // 답변의 바닥이 안전선을 뚫고 내려가거나 force 플래그가 켜져 있으면 정확한 위치로 스크롤!
    if (overflow > 1 || force) {
      const targetScrollTop = scroller.scrollTop + relBottom - safeLandingLine;
      scroller.scrollTo({
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
  // Reasoning Effort pills (하단 추론 강도 버튼 - 좌측 사이드바 및 백엔드 custom_providers.json 영구 연동)
  const effortButtons = document.querySelectorAll('.reasoning-effort-btn');
  effortButtons.forEach(btn => {
    btn.addEventListener('click', async () => {
      const effort = btn.getAttribute('data-effort') || 'medium';
      setReasoningEffort(effort);
      await saveCurrentModelEffort(effort);
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
      // 목업 텔레메트리/스텝 초기화 + 이전 폴링 중단
      if (state.harnessPollInterval) { clearInterval(state.harnessPollInterval); state.harnessPollInterval = null; }
      if (terminal) terminal.innerHTML = '';
      hideHarnessInteractionPanel();
      appendTelemetryLog(`[Harness] 새 파이프라인 시작 — 작업: "${task}"`);
      updateHarnessSteps(1); // Step 1: 요구사항 파싱/의도 확인
      setHarnessLoopStatus('시작 중', 'amber');

      try {
        const res = await DaonAPI.runDynamicHarness({ task });
        if (res && res.run_id) {
          state.currentHarnessRunId = res.run_id;
          appendTelemetryLog(`[Dispatcher] Job started. Run ID: ${res.run_id}`);
          startHarnessPolling(res.run_id);
        }
      } catch (err) {
        appendTelemetryLog(`[Error] Failed to start harness: ${err.message}`);
        setHarnessLoopStatus('시작 실패', 'rose');
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

  // ── 목업 초기화: 실데이터만 표시 (하드코딩 로그·스텝 제거) ──
  if (terminal && !state.currentHarnessRunId) {
    terminal.innerHTML = '<div class="text-on-surface-variant">[Harness] 대기 중 — 작업을 입력하고 "새 하네스 작업 시작"을 누르세요.</div>';
    updateHarnessSteps(0);
    setHarnessLoopStatus('대기 중', 'gray');
  }
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

      // ── 상태별 UI 처리 (실연동) ──
      if (data.status === 'clarifying') {
        updateHarnessSteps(1);
        setHarnessLoopStatus('의도 확인 대기', 'amber');
        if (Array.isArray(data.questions) && data.questions.length) {
          renderHarnessClarification(runId, data.questions, data.turn || 1);
        }
      } else if (data.status === 'awaiting_approval') {
        updateHarnessSteps(3);
        setHarnessLoopStatus('승인 대기', 'amber');
        renderHarnessApproval(runId, data.approval_message || '작업 승인이 필요합니다.',
                              data.available_actions || ['approve', 'reject']);
      } else if (data.status === 'recovering') {
        updateHarnessSteps(4);
        setHarnessLoopStatus('자가치유 복구 중', 'amber');
      } else if (data.status === 'running') {
        hideHarnessInteractionPanel();
        updateHarnessSteps(data.phase || 2);
        setHarnessLoopStatus(`실행 중 · Phase ${data.phase || 2}`, 'emerald');
      }

      if (data.status === 'done' || data.status === 'completed' || data.status === 'error'
          || data.status === 'failed' || data.status === 'cancelled') {
        clearInterval(state.harnessPollInterval);
        state.harnessPollInterval = null;
        hideHarnessInteractionPanel();
        const ok = (data.status === 'done' || data.status === 'completed');
        appendTelemetryLog(`[Harness] 실행 종료 — 상태: ${String(data.status).toUpperCase()}`
                           + (data.error ? ` / ${data.error}` : ''));
        if (data.result) appendTelemetryLog(`[Harness] 결과: ${String(data.result).slice(0, 500)}`);
        updateHarnessSteps(4, true);
        setHarnessLoopStatus(ok ? '완료' : `종료(${data.status})`, ok ? 'emerald' : 'rose');
      }
    } catch (e) {
      // Continue polling
    }
  }, 1500);
}

// ── 하네스 상호작용 패널 (의도 확인 질문 / 승인) ───────────────────────────────
function ensureHarnessInteractionPanel() {
  let panel = document.getElementById('harness-interaction-panel');
  if (panel) return panel;
  const host = document.querySelector('#tab-dynamic-harness .max-w-\\[1440px\\]')
            || document.querySelector('#tab-dynamic-harness > div');
  if (!host) return null;
  panel = document.createElement('div');
  panel.id = 'harness-interaction-panel';
  panel.className = 'hidden bg-surface border border-black/20 rounded-[12px] p-space-md flex-col gap-3';
  host.insertBefore(panel, host.firstChild);
  return panel;
}

function hideHarnessInteractionPanel() {
  const panel = document.getElementById('harness-interaction-panel');
  if (panel) { panel.classList.add('hidden'); panel.classList.remove('flex'); }
}

function renderHarnessClarification(runId, questions, turn) {
  const panel = ensureHarnessInteractionPanel();
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.classList.add('flex');
  panel.innerHTML = `
    <div class="flex items-center gap-2 text-on-surface font-label-md font-semibold">
      <span class="material-symbols-outlined text-[18px] text-primary">help</span>
      <span>CEO 의도 확인 (턴 ${turn}) · ${questions.length}개 질문</span>
    </div>
    <div class="flex flex-col gap-2" id="harness-q-list"></div>
    <div class="flex items-center justify-end gap-2">
      <button id="harness-skip-btn" type="button" class="px-3 py-1.5 border border-black/10 rounded-[8px] font-label-md text-on-surface hover:bg-black/[0.04] transition-colors">건너뛰고 진행</button>
      <button id="harness-answer-btn" type="button" class="px-4 py-1.5 bg-primary text-on-primary rounded-[8px] font-label-md font-medium hover:bg-black/80 transition-colors">답변 제출</button>
    </div>`;
  const qList = panel.querySelector('#harness-q-list');
  questions.forEach((q, i) => {
    const wrap = document.createElement('div');
    wrap.className = 'flex flex-col gap-1';
    const lab = document.createElement('label');
    lab.className = 'font-body-sm text-[13px] text-on-surface';
    lab.textContent = `Q${i + 1}. ${q}`;
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'harness-answer-input bg-surface-container-lowest border border-black/10 rounded-[8px] px-3 py-1.5 font-body-sm text-on-surface outline-none focus:border-black transition-colors';
    inp.placeholder = '답변을 입력하세요...';
    wrap.appendChild(lab);
    wrap.appendChild(inp);
    qList.appendChild(wrap);
  });
  const answerBtn = panel.querySelector('#harness-answer-btn');
  if (answerBtn) answerBtn.onclick = async () => {
    const answers = [...panel.querySelectorAll('.harness-answer-input')].map(i => i.value.trim() || '(무응답)');
    try {
      await DaonAPI.submitDynamicAnswer(runId, answers);
      appendTelemetryLog(`[Harness] 답변 제출 완료 (${answers.length}개)`);
      hideHarnessInteractionPanel();
    } catch (e) { appendTelemetryLog(`[Error] 답변 제출 실패: ${e.message}`); }
  };
  const skipBtn = panel.querySelector('#harness-skip-btn');
  if (skipBtn) skipBtn.onclick = async () => {
    try {
      await DaonAPI.submitDynamicAnswer(runId, questions.map(() => ''));
      appendTelemetryLog('[Harness] 의도 확인을 건너뛰고 진행합니다');
      hideHarnessInteractionPanel();
    } catch (e) { appendTelemetryLog(`[Error] ${e.message}`); }
  };
  const first = panel.querySelector('.harness-answer-input');
  if (first) first.focus();
}

function renderHarnessApproval(runId, message, actions) {
  const panel = ensureHarnessInteractionPanel();
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.classList.add('flex');
  panel.innerHTML = `
    <div class="flex items-center gap-2 text-on-surface font-label-md font-semibold">
      <span class="material-symbols-outlined text-[18px] text-primary">verified_user</span>
      <span>작업 승인 필요</span>
    </div>
    <p class="font-body-sm text-[13px] text-on-surface-variant leading-relaxed">${escapeHtml(message)}</p>
    <div class="flex items-center justify-end gap-2">
      <button id="harness-reject-btn" type="button" class="px-3 py-1.5 border border-black/10 rounded-[8px] font-label-md text-on-surface hover:bg-black/[0.04] transition-colors">거부</button>
      <button id="harness-approve-btn" type="button" class="px-4 py-1.5 bg-primary text-on-primary rounded-[8px] font-label-md font-medium hover:bg-black/80 transition-colors">승인</button>
    </div>`;
  const approveBtn = panel.querySelector('#harness-approve-btn');
  if (approveBtn) approveBtn.onclick = async () => {
    try {
      await DaonAPI.approveDynamicRun(runId, 'approve');
      appendTelemetryLog('[Harness] 작업을 승인했습니다');
      hideHarnessInteractionPanel();
    } catch (e) { appendTelemetryLog(`[Error] 승인 실패: ${e.message}`); }
  };
  const rejectBtn = panel.querySelector('#harness-reject-btn');
  if (rejectBtn) rejectBtn.onclick = async () => {
    try {
      await DaonAPI.approveDynamicRun(runId, 'reject');
      appendTelemetryLog('[Harness] 작업을 거부했습니다');
      hideHarnessInteractionPanel();
    } catch (e) { appendTelemetryLog(`[Error] 거부 실패: ${e.message}`); }
  };
}

function setHarnessLoopStatus(text, tone) {
  const el = document.getElementById('harness-loop-status');
  if (!el) return;
  el.textContent = text;
  const dot = el.parentElement ? el.parentElement.querySelector('span.rounded-full') : null;
  const map = {
    emerald: 'bg-emerald-600', amber: 'bg-amber-500', rose: 'bg-rose-500', gray: 'bg-on-surface-variant'
  };
  if (dot) dot.className = `w-2 h-2 rounded-full ${map[tone] || 'bg-emerald-600'} ${tone === 'emerald' ? 'animate-pulse' : ''}`;
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

      // 참여 슬롯(체크박스) 수집 — 선택된 에이전트/워커만 이번 회의에 참여
      const selectedSlots = [...document.querySelectorAll('.boardroom-slot-check')]
        .filter(c => c.checked)
        .map(c => c.getAttribute('data-slot'))
        .filter(Boolean);
      if (selectedSlots.length === 0) {
        alert('회의에 참여할 에이전트/워커를 최소 1명 선택하세요. (슬롯 카드의 체크박스)');
        return;
      }

      broadcastInput.value = '';
      resetBoardroomSlots();

      appendBoardroomTranscript('00 사용자', `[브로드캐스트 지시] "${topic}"`, 'bg-primary text-on-primary');
      appendBoardroomTranscript('01 사회자', `안건을 접수했습니다. 라온은 사회자로만 참여하고, 선택된 ${selectedSlots.length}개 에이전트가 토론합니다 (라운드 종료 시 라온이 취합→재질의).`, 'bg-surface-container-highest text-on-surface');

      // 슬롯별 선택 모델 수집 (기본 모델이면 생략 → 프로필 기본값 사용)
      // 저장값과 동일한 소스(드롭다운)에서 읽어 localStorage/서버 저장값과 어긋나지 않게 한다.
      const slotModels = readBoardroomModelSelects();

      try {
        const res = await DaonAPI.broadcastBoardroom({ task: topic, sessionId: state.currentSessionId, models: slotModels, slots: selectedSlots });
        if (!res || !res.stream_id) throw new Error('stream_id를 받지 못했습니다');
        setBoardroomBusy(true);
        refreshBoardroomWorkerModelStates();

        DaonAPI.connectSSE(res.stream_id, {
          onBoardroomSlot(d) {
            setBoardroomSlotState(d.slot, 'running');
          },
          onBoardroomReply(d) {
            setBoardroomSlotState(d.slot, d.status === 'error' ? 'error' : 'done');
            const modelTag = d.model ? ` · ${d.model}` : '';
            const tag = `${d.slot} ${d.speaker || ''}${modelTag}`.trim();
            const cls = d.status === 'error'
              ? 'bg-rose-100 text-rose-800'
              : 'bg-surface-container-highest text-on-surface';
            const suffix = (typeof d.elapsed === 'number') ? `\n\n— ${d.elapsed}s` : '';
            appendBoardroomTranscript(tag, (d.text || '(응답 없음)') + suffix, cls);
          },
          onBoardroomRoundStart(d) {
            if (d && d.skipped) return;
            appendBoardroomTranscript('01 사회자',
              `── ${d.round}라운드 (${d.mode || ''}) ──`, 'bg-primary/10 text-on-surface');
          },
          onBoardroomModeration(d) {
            const v = (d && d.verdict) || {};
            const verdictTag = v.consensus
              ? '합의 도달'
              : `이견 ${((v.disagreements || []).length)}건 → 재질의`;
            appendBoardroomTranscript('01 사회자 (취합)',
              `${d.round}라운드 취합: ${verdictTag}\n${v.summary || ''}`, 'bg-sky-100 text-sky-900');
          },
          onBoardroomDone(d) {
            refreshBoardroomWorkerModelStates();
            const n = (d && Array.isArray(d.slots)) ? d.slots.length : selectedSlots.length;
            appendBoardroomTranscript('01 사회자', `${n}개 에이전트 발언 완료. 회의 라운드를 종료합니다.`, 'bg-emerald-100 text-emerald-800');
            setBoardroomBusy(false);
            // 버튼 없이 결론 카드만 남긴다 — 승인은 채팅에서, 승계는 자동(open_workshop).
            try {
              if (d && d.conclusion) showBoardroomConclusion(d.conclusion);
            } catch (e) { console.error('boardroom conclusion render err:', e); }
          },
          onError() {
            refreshBoardroomWorkerModelStates();
            appendBoardroomTranscript('오류', '회의 스트림이 종료되었습니다.', 'bg-rose-100 text-rose-800');
            setBoardroomBusy(false);
          }
        });
      } catch (err) {
        refreshBoardroomWorkerModelStates();
        appendBoardroomTranscript('오류', `브로드캐스트 실패: ${err.message}`, 'bg-rose-100 text-rose-800');
        setBoardroomBusy(false);
      }
    });
  }

  // 슬롯별 모델 선택 드롭다운을 실 모델 목록(/api/models)으로 채운다
  initBoardroomSlotSelector();
  loadBoardroomModels();
}

// ── 회의실 참여 슬롯 선택기 (체크박스) ──────────────────────────────────────────
// 각 슬롯 카드의 체크박스로 '이번 회의에 참여할 에이전트/워커'를 고른다.
// (2명이든 3명이든, 에이전트만/워커만/전체 자유 선택). 선택은 localStorage에
// 저장되어 새로고침 후에도 유지된다. 브로드캐스트 시 선택 슬롯만 실제로 실행된다.
// ⚠️ 라온(01)은 사회자 — 참석자가 아니므로 선택 목록에서 제외한다(2026-10-15 확정).
const BOARDROOM_ALL_SLOTS = ['02', '03', '04', '05', '06', '07', '08'];
const BOARDROOM_AGENT_SLOTS = ['02', '03', '04', '05', '06']; // 5 정적 에이전트 (라온 제외)
const BOARDROOM_WORKER_SLOTS = ['07', '08'];                  // 2 CLI 워커

// 슬롯별 모델 선택 저장 키. 서버(/api/boardroom/selection)가 진실의 원천이고
// localStorage는 서버 조회 실패 시의 폴백이다.
const BOARDROOM_MODELS_KEY = 'daon_boardroom_models';
let _boardroomServerModels = null; // 서버 저장 모델(있으면 localStorage보다 우선)

function initBoardroomSlotSelector() {
  const checks = [...document.querySelectorAll('.boardroom-slot-check')];
  if (checks.length === 0) return;

  // 저장된 선택 복원 (localStorage — 즉시 반영. 서버 저장값이 있으면 곧 덮어쓴다)
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('daon_boardroom_slots') || 'null'); } catch (e) { saved = null; }
  const want = Array.isArray(saved) ? saved : BOARDROOM_ALL_SLOTS;
  checks.forEach(c => { c.checked = want.includes(c.getAttribute('data-slot')); });

  checks.forEach(c => {
    if (c.dataset.bound) return;
    c.dataset.bound = '1';
    c.addEventListener('change', () => {
      updateBoardroomSlotCard(c);
      updateBoardroomSelectionCount();
      persistBoardroomSlots();
    });
    updateBoardroomSlotCard(c);
  });

  // 빠른 선택 버튼: 전체 / 해제 / 에이전트만 / 워커만
  const bind = (id, slots) => {
    const b = document.getElementById(id);
    if (b && !b.dataset.bound) {
      b.dataset.bound = '1';
      b.addEventListener('click', () => setBoardroomSlots(checks, slots));
    }
  };
  bind('boardroom-sel-all', BOARDROOM_ALL_SLOTS);
  bind('boardroom-sel-none', []);
  bind('boardroom-sel-agents', BOARDROOM_AGENT_SLOTS);
  bind('boardroom-sel-workers', BOARDROOM_WORKER_SLOTS);

  updateBoardroomSelectionCount();
  // 서버에 저장된 선택이 있으면 그것을 우선 적용한다(채팅 소집 경로와 같은 기준).
  restoreBoardroomSelectionFromServer(checks);
}

// 서버 저장값 우선 복원. 저장값이 없으면 위에서 적용한 localStorage 값이 그대로 남는다.
async function restoreBoardroomSelectionFromServer(checks) {
  let sel = null;
  try { sel = await DaonAPI.getBoardroomSelection(); } catch (e) { sel = null; }
  if (!sel || !sel.ok || !sel.saved) return;

  const slots = Array.isArray(sel.slots) ? sel.slots : null;
  if (slots && slots.length) {
    checks.forEach(c => {
      c.checked = slots.includes(c.getAttribute('data-slot'));
      updateBoardroomSlotCard(c);
    });
    updateBoardroomSelectionCount();
    try { localStorage.setItem('daon_boardroom_slots', JSON.stringify(slots)); } catch (e) { /* 무시 */ }
  }
  _boardroomServerModels = (sel.models && typeof sel.models === 'object') ? sel.models : null;
  applyBoardroomModels(_boardroomServerModels);
}

function setBoardroomSlots(checks, slots) {
  checks.forEach(c => {
    c.checked = slots.includes(c.getAttribute('data-slot'));
    updateBoardroomSlotCard(c);
  });
  updateBoardroomSelectionCount();
  persistBoardroomSlots();
}

function updateBoardroomSlotCard(check) {
  const slot = check.getAttribute('data-slot');
  const card = document.querySelector(`div[data-slot="${slot}"]`);
  if (card) card.classList.toggle('slot-excluded', !check.checked);
}

function updateBoardroomSelectionCount() {
  const all = [...document.querySelectorAll('.boardroom-slot-check')];
  const on = all.filter(c => c.checked).length;
  const el = document.getElementById('boardroom-selected-count');
  if (el) el.textContent = `${on}/${all.length} Agents Selected`;
}

function persistBoardroomSlots() {
  try {
    const on = [...document.querySelectorAll('.boardroom-slot-check')].filter(c => c.checked).map(c => c.getAttribute('data-slot'));
    localStorage.setItem('daon_boardroom_slots', JSON.stringify(on));
  } catch (e) { /* 저장 실패는 무시 */ }
  syncBoardroomSelectionToServer();
}

// ── 슬롯별 모델 선택 저장/복원 ────────────────────────────────────────────────
// localStorage['daon_boardroom_models'] = {"02": "glm-5.3-flash", ...}
// 서버(/api/boardroom/selection)에도 같이 올려서, 채팅에서 라온이 회의를 소집할 때
// (convene_boardroom 도구) 같은 참여자·같은 모델이 쓰이게 한다.

function readBoardroomModels() {
  try {
    const raw = JSON.parse(localStorage.getItem(BOARDROOM_MODELS_KEY) || 'null');
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw;
  } catch (e) { /* 손상된 값은 무시 */ }
  return {};
}

// 현재 드롭다운에 선택된 {슬롯: 모델} — 브로드캐스트 시 수집하는 값과 동일한 소스.
function readBoardroomModelSelects() {
  const out = {};
  document.querySelectorAll('.boardroom-model-select').forEach(sel => {
    const slot = sel.getAttribute('data-slot');
    if (slot && sel.value) out[slot] = sel.value;
  });
  return out;
}

// 저장값(localStorage ∪ 서버, 서버 우선)을 각 드롭다운에 적용한다.
// /api/models 목록에 없는 모델이면 <option>을 직접 추가해 선택을 보존한다
// (기본 모델로 조용히 되돌리지 않는다).
function applyBoardroomModels(serverModels) {
  const want = Object.assign({}, readBoardroomModels(), serverModels || {});
  document.querySelectorAll('.boardroom-model-select').forEach(sel => {
    const slot = sel.getAttribute('data-slot');
    const model = slot ? want[slot] : '';
    if (!model) return;
    const known = [...sel.options].some(o => o.value === model);
    if (!known) {
      const opt = document.createElement('option');
      opt.value = model;
      opt.textContent = `${model} (저장됨)`;
      sel.appendChild(opt);
    }
    sel.value = model;
  });
  try { localStorage.setItem(BOARDROOM_MODELS_KEY, JSON.stringify(want)); } catch (e) { /* 무시 */ }
  return want;
}

function persistBoardroomModels() {
  try {
    localStorage.setItem(BOARDROOM_MODELS_KEY, JSON.stringify(readBoardroomModelSelects()));
  } catch (e) { /* 저장 실패는 무시 */ }
  syncBoardroomSelectionToServer();
}

// 회의실 탭의 현재 선택(참여 슬롯 + 슬롯별 모델)을 서버에 저장한다.
// 실패해도 UI는 계속 동작해야 하므로 조용히 무시한다(서버 쪽은 저장값 없음 → 전체 참여 폴백).
function syncBoardroomSelectionToServer() {
  if (typeof DaonAPI.saveBoardroomSelection !== 'function') return;
  const slots = [...document.querySelectorAll('.boardroom-slot-check')]
    .filter(c => c.checked)
    .map(c => c.getAttribute('data-slot'))
    .filter(Boolean);
  try {
    DaonAPI.saveBoardroomSelection({ slots, models: readBoardroomModelSelects() });
  } catch (e) { /* 무시 */ }
}

// 회의실 8슬롯의 모델 선택 <select>를 /api/models 실데이터로 채운다.
async function loadBoardroomModels() {
  let optsHtml = '';
  try {
    const res = await fetch(`${DaonAPI.baseUrl}/api/models`);
    if (res.ok) {
      const data = await res.json();
      const groups = data.groups || [];
      optsHtml = groups.map(g => {
        const glabel = escapeHtml(String(g.provider || g.provider_key || ''));
        const inner = (g.models || []).map(m => {
          const id = (typeof m === 'string') ? m : (m && m.id ? m.id : '');
          const lbl = (typeof m === 'object' && m && m.label) ? m.label : id;
          if (!id) return '';
          return `<option value="${escapeHtml(String(id))}">${escapeHtml(String(lbl))}</option>`;
        }).join('');
        return glabel ? `<optgroup label="${glabel}">${inner}</optgroup>` : inner;
      }).join('');
    }
  } catch (e) {
    /* 모델 목록 조회 실패는 조용히 무시 — 기본 모델로 정상 동작 */
  }

  document.querySelectorAll('.boardroom-model-select').forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = '<option value="">기본 모델</option>' + optsHtml;
    if (cur) sel.value = cur;
    // 선택 즉시 저장 — 페이지를 다시 열어도 슬롯별 모델이 유지된다.
    if (!sel.dataset.modelBound) {
      sel.dataset.modelBound = '1';
      sel.addEventListener('change', () => persistBoardroomModels());
    }
  });

  // 옵션을 채운 '직후' 저장값을 적용해 선택을 복원한다.
  applyBoardroomModels(_boardroomServerModels);
}

const BOARDROOM_WORKER_MODEL_STATES = {
  applied:  { label: '적용됨', icon: 'task_alt', cls: 'slot-model-applied' },
  restored: { label: '재시작 후 복원됨', icon: 'history', cls: 'slot-model-restored' },
  default:  { label: '기본값', icon: 'settings_backup_restore', cls: 'slot-model-default' },
  failed:   { label: '실패', icon: 'error', cls: 'slot-model-failed' },
  checking: { label: '상태 확인 중', icon: 'sync', cls: 'slot-model-default' }
};
const BOARDROOM_WORKER_KEYS = { '07': 'worker-codex', '08': 'worker-claude' };
const BOARDROOM_DEFAULT_MODELS = {
  '07': 'muse-spark-1.3-contributor (OpenCode Go)',
  '08': 'deepseek-v4.1-flash (OpenCode Go)'
};

function setBoardroomWorkerModelState(slot, payload = {}) {
  const slotId = String(slot || '').padStart(2, '0');
  const row = document.getElementById(`slot-model-state-${slotId}`);
  if (!row) return;
  const badge = document.getElementById(`slot-model-badge-${slotId}`);
  const label = row.querySelector('.slot-model-state-label');
  const icon = badge && badge.querySelector('.material-symbols-outlined');
  const modelNode = document.getElementById(`slot-model-name-${slotId}`);
  const isolation = document.getElementById(`slot-model-isolation-${slotId}`);
  const errorNode = document.getElementById(`slot-model-error-${slotId}`);
  const select = document.querySelector(`.boardroom-model-select[data-slot="${slotId}"]`);
  const selectedModel = String(payload.requested_model || (select && select.value) || '').trim();
  const runningRaw = String(payload.model_running || payload.resolved_model || '').trim();
  const parsed = runningRaw.match(/^(.*?)\s*\(([^()]*)\)\s*$/);
  const runningModel = (parsed ? parsed[1] : runningRaw).trim();
  const providerRaw = String((parsed && parsed[2]) || payload.provider || '').trim();
  const providerLabel = ({ 'opencode-go': 'OpenCode Go', 'openrouter': 'OpenRouter', 'minimax': 'MiniMax' })[providerRaw.toLowerCase()] || providerRaw;
  const hasError = !!String(payload.model_error || payload.error || '').trim();
  let stateKey = 'checking';
  let errorText = String(payload.model_error || payload.error || '').trim();

  if (hasError) {
    stateKey = 'failed';
  } else if (selectedModel && runningModel && payload.model_saved === true && runningModel !== selectedModel) {
    stateKey = 'failed';
    errorText = `요청 모델(${selectedModel})과 실행 모델(${runningModel})이 다릅니다.`;
  } else if (payload.model_restored === true || payload.restored === true || payload.model_source === 'restored' || payload.source === 'selection_restore') {
    stateKey = 'restored';
  } else if (!selectedModel && runningModel) {
    stateKey = 'default';
  } else if (runningModel && payload.model_saved === true && (!selectedModel || runningModel === selectedModel)) {
    stateKey = 'applied';
  }

  const config = BOARDROOM_WORKER_MODEL_STATES[stateKey];
  if (row.dataset.modelState !== stateKey) {
    row.dataset.modelState = stateKey;
    badge.classList.remove('slot-model-applied', 'slot-model-restored', 'slot-model-default', 'slot-model-failed');
    badge.classList.add(config.cls);
  }
  if (label) label.textContent = config.label;
  if (icon) icon.textContent = config.icon;

  const displayModel = runningModel || selectedModel || (stateKey === 'default' ? BOARDROOM_DEFAULT_MODELS[slotId] : '');
  const shortModel = displayModel.replace(/-contributor(?=\s|$)/, '');
  const fullModel = [displayModel, providerLabel && !displayModel.includes(`(${providerLabel})`) ? `(${providerLabel})` : '']
    .filter(Boolean).join(' ');
  if (modelNode) {
    const visibleParts = [shortModel, providerLabel].filter(Boolean);
    modelNode.textContent = visibleParts.length
      ? visibleParts.join(' · ')
      : (stateKey === 'checking' ? '워커 모델 상태 확인 전' : '모델 정보 없음');
    modelNode.title = fullModel || '실행 모델 정보가 아직 제공되지 않았습니다.';
    modelNode.setAttribute('aria-label', fullModel || '실행 모델 정보가 아직 제공되지 않았습니다.');
  }

  const explicitlyIsolated = payload.isolated === true || payload.profile_isolated === true || payload.codex_home_isolated === true;
  // 슬롯 07은 모델 실행 확인 시 백엔드의 전용 CODEX_HOME 계약을 적용한 것으로 표시한다.
  const isolated = explicitlyIsolated || (slotId === '07' && !!runningModel && payload.model_saved === true && stateKey !== 'failed');
  if (isolation) {
    isolation.hidden = !isolated;
    if (isolated && !isolation.textContent.trim()) {
      isolation.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">shield_lock</span><span>격리됨</span>';
    }
    isolation.title = slotId === '07'
      ? '개인 ~/.codex 설정과 분리된 슬롯 전용 CODEX_HOME 사용 확인'
      : '별도 워커 프로필 사용 확인';
  }

  if (errorNode) {
    const friendly = errorText
      .replace(/^no_api_key:/, 'API 키 없음: ')
      .replace(/^claude_requires_anthropic_native:/, 'Anthropic 네이티브 미지원: ')
      .replace(/^unsupported_provider:/, '지원하지 않는 공급자: ')
      .replace(/^set_worker_model failed:\s*/i, '모델 설정 실패: ');
    errorNode.textContent = stateKey === 'failed'
      ? (friendly || '요청한 모델을 워커에서 확인하지 못했습니다.')
      : '';
    errorNode.title = errorText || '';
    errorNode.hidden = stateKey !== 'failed';
    if (stateKey === 'failed') row.setAttribute('aria-describedby', errorNode.id);
    else row.removeAttribute('aria-describedby');
  }
}

let _boardroomWorkerModelRefresh = null;
let _boardroomWorkerModelRefreshAgain = false;
async function refreshBoardroomWorkerModelStates() {
  if (_boardroomWorkerModelRefresh) {
    _boardroomWorkerModelRefreshAgain = true;
    return _boardroomWorkerModelRefresh;
  }
  _boardroomWorkerModelRefresh = (async () => {
    do {
      _boardroomWorkerModelRefreshAgain = false;
      try {
        const base = (typeof DaonAPI !== 'undefined' && DaonAPI.baseUrl) ? DaonAPI.baseUrl : '';
        const response = await fetch(`${base}/api/workers`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`워커 상태 조회 HTTP ${response.status}`);
        const data = await response.json();
        const workers = Array.isArray(data.workers) ? data.workers : [];
        const byName = new Map(workers.map(worker => [String(worker.name || ''), worker]));
        for (const [slot, workerKey] of Object.entries(BOARDROOM_WORKER_KEYS)) {
          const worker = byName.get(workerKey);
          if (!worker) {
            setBoardroomWorkerModelState(slot, { model_error: `${workerKey} 상태를 /api/workers 응답에서 찾지 못했습니다.` });
            continue;
          }
          setBoardroomWorkerModelState(slot, worker);
        }
      } catch (error) {
        const message = error && error.message ? error.message : String(error || '알 수 없는 오류');
        for (const slot of Object.keys(BOARDROOM_WORKER_KEYS)) {
          setBoardroomWorkerModelState(slot, { model_error: `워커 모델 상태 조회 실패: ${message}` });
        }
        console.warn('[boardroom] worker model status refresh failed:', error);
      }
    } while (_boardroomWorkerModelRefreshAgain);
  })();
  try {
    await _boardroomWorkerModelRefresh;
  } finally {
    _boardroomWorkerModelRefresh = null;
  }
}

function setBoardroomBusy(busy) {
  const btn = document.getElementById('boardroom-broadcast-btn');
  const input = document.getElementById('boardroom-input');

  if (btn) { btn.disabled = !!busy; btn.classList.toggle('opacity-50', !!busy); }
  if (input) input.disabled = !!busy;
  document.querySelectorAll('.boardroom-slot-check, .boardroom-sel-btn').forEach(el => { el.disabled = !!busy; });
}

function setBoardroomSlotState(slot, status) {
  const card = document.querySelector(`div[data-slot="${slot}"]`);
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
  document.querySelectorAll('div[data-slot]').forEach(card => {
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

// ── 회의실 → 작업장 승계 (토론 세션을 그대로 멀티뷰 작업장으로) ──────────────
// 회의실에서 각 슬롯이 실제로 돈 세션(session_id)을 모아, 예전 멀티뷰(/multi
// = 라온 지휘형 오케스트레이터)의 pane 복원용 localStorage에 심고 새 탭으로 연다.
// 이러면 각 에이전트가 '아까 회의에서 한 발언' 맥락을 그대로 안고 작업을 이어간다.
// 상세 = 스킬 daon-boardroom-vs-multi
const BOARDROOM_PROFILE_MAP = {
  raon: 'raon',
  bill: '빌(개발)',
  sherlock: '셜록(검수)',
  tony: '토니(기획)',
  prada: '프라다(디자인)',
  daon: '다온(응대)',
  'worker-codex': '코덱스 (Codex)',
  'worker-claude': '클로드 (Claude)',
};
const BOARDROOM_MAX_PANES = 6; // 멀티뷰 MAX_PANES와 동일

// ── Workshop (in-app Multi-View iframe) ───────────────────────────────────────
// 회의실 승계/탭 진입 시 /multi?embed=1 을 iframe 에 lazy-load 하고 재진입 시 리로드한다.
// /multi 는 동일 origin 이므로 iframe 이 부모 localStorage(daon_multi_panes)를 그대로 읽는다.
const WORKSHOP_IFRAME_SRC = '/multi?embed=1';
// ⚠️ 서버가 동적 라우트(/multi)에 X-Frame-Options: DENY 를 보내면 브라우저가 임베드를 차단한다.
//    /static/ 정적 경로는 '같은 파일'을 서빙하면서 XFO 헤더가 없어 임베드 가능 → 자동 폴백.
const WORKSHOP_IFRAME_FALLBACK_SRC = '/static/multi/index.html?embed=1';
const WORKSHOP_FALLBACK_DELAY_MS = 4000;

// 현재 유효한 워크숍 URL(폴백 사용 여부 반영)
function workshopActiveSrc(frame) {
  return frame.dataset.workshopFallbackUsed === '1'
    ? WORKSHOP_IFRAME_FALLBACK_SRC
    : (frame.getAttribute('data-src') || WORKSHOP_IFRAME_SRC);
}

// XFO 차단 감지 → 정적 경로로 1회 폴백(차단되면 contentDocument 가 null 로 남는다)
function armWorkshopFallbackWatch(frame) {
  if (frame.dataset.workshopFallbackArmed === '1') return;
  frame.dataset.workshopFallbackArmed = '1';
  setTimeout(() => {
    if (frame.dataset.workshopFallbackUsed === '1') return;
    if (frame.contentDocument) return;   // 정상 로드됨
    console.warn('[workshop] /multi 임베드 차단(X-Frame-Options: DENY) → 정적 경로로 폴백:',
      WORKSHOP_IFRAME_FALLBACK_SRC);
    frame.dataset.workshopFallbackUsed = '1';
    frame.setAttribute('src', WORKSHOP_IFRAME_FALLBACK_SRC);
  }, WORKSHOP_FALLBACK_DELAY_MS);
}

// ⚠️ 서빙 셸 이원화 대응: `/` 는 static/v2/index.html 이 아니라 루트 index.html 을 서빙한다.
//    (정적 마크업에 작업장 탭이 없는 셸에서도 동일 기능이 뜨도록 런타임 보강. 전부 멱등)
const WORKSHOP_TAB_IDLE_CLASS = 'workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] '
  + 'text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all';
const WORKSHOP_TAB_INNER_HTML = '<span class="material-symbols-outlined text-[16px]">construction</span>'
  + '<span>작업장 (Multi-View)</span>';
const WORKSHOP_PANEL_CLASS = 'workspace-panel hidden flex-col items-stretch w-full flex-1';
const WORKSHOP_IFRAME_CLASS = 'w-full h-full block border-0 m-0 bg-surface-container-lowest';

// initTabs() 가 NodeList 를 캡처하기 전에 호출되어야 한다(활성 스타일/패널 토글이 붙도록).
function ensureWorkshopShell() {
  const tabs = document.getElementById('workspace-tabs');
  if (tabs && !tabs.querySelector('[data-target="agent-workshop"]')) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = WORKSHOP_TAB_IDLE_CLASS;
    btn.setAttribute('data-target', 'agent-workshop');
    btn.innerHTML = WORKSHOP_TAB_INNER_HTML;
    tabs.appendChild(btn);
  }
  if (!document.getElementById('tab-agent-workshop')) {
    const main = document.querySelector('main');
    if (main) {
      const sec = document.createElement('section');
      sec.id = 'tab-agent-workshop';
      sec.className = WORKSHOP_PANEL_CLASS;
      sec.setAttribute('aria-label', '작업장 (Multi-View)');
      const fr = document.createElement('iframe');
      fr.id = 'workshop-frame';
      fr.setAttribute('data-src', WORKSHOP_IFRAME_SRC);
      fr.setAttribute('loading', 'lazy');
      fr.className = WORKSHOP_IFRAME_CLASS;
      fr.title = '작업장 (Multi-View)';
      sec.appendChild(fr);
      main.appendChild(sec);
    }
  }
  const anchorItem = document.querySelector('.sidebar-item[data-tab-target="agent-skills"]');
  if (anchorItem && !document.querySelector('.sidebar-item[data-tab-target="agent-workshop"]')) {
    const item = anchorItem.cloneNode(true);
    item.setAttribute('data-tab-target', 'agent-workshop');
    item.className = 'sidebar-item h-[34px] flex items-center gap-2 px-space-sm rounded-[10px] font-body-sm '
      + 'text-body-sm text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface transition-colors truncate cursor-pointer';
    const icon = item.querySelector('.material-symbols-outlined');
    if (icon) icon.textContent = 'construction';
    const label = item.querySelector('.truncate');
    if (label) label.textContent = '작업장 (Multi-View)';
    anchorItem.parentNode.appendChild(item);
  }
}

// 최초 진입: data-src 를 src 로 승격(이때만 실제 네트워크 로드 발생 → 부팅 시 6-pane 미기동)
function ensureWorkshopFrame() {
  const frame = document.getElementById('workshop-frame');
  if (!frame) return null;
  if (!frame.getAttribute('src')) {
    frame.addEventListener('load', () => { try { frame.dataset.workshopLoaded = '1'; } catch (_) {} }, { once: true });
    frame.setAttribute('src', frame.getAttribute('data-src') || WORKSHOP_IFRAME_SRC);
    armWorkshopFallbackWatch(frame);
  }
  return frame;
}

// 재진입/승계: 이미 붙은 프레임을 리로드해 갱신된 localStorage 승계를 반영
function reloadWorkshopFrame() {
  const frame = document.getElementById('workshop-frame');
  if (!frame) return null;
  if (!frame.getAttribute('src')) return ensureWorkshopFrame();   // 아직 미로드 → 로드만
  // 정상 문서가 붙어 있으면 그대로 리로드, 차단/미로드면 유효 src 로 재세팅
  if (frame.contentDocument) {
    try { frame.contentWindow.location.reload(); return frame; } catch (_) {}
  }
  frame.setAttribute('src', workshopActiveSrc(frame));
  armWorkshopFallbackWatch(frame);
  return frame;
}

// ⚠️ 대표님 확정(2026-10-15): "작업장 가기" 버튼을 사용자에게 누르게 하지 않는다.
//    승인은 채팅에서 라온이 받고, 라온이 open_workshop 도구를 호출하면
//    프론트가 감지해 자동으로 작업장 탭을 연다(openBoardroomWorkshop).
function openBoardroomWorkshop(panes) {
  const raw = Array.isArray(panes) ? panes : [];
  const mapped = raw
    .filter(p => p && p.profile)
    .map(p => ({
      profile: BOARDROOM_PROFILE_MAP[p.profile] || p.profile,
      sessionId: p.sessionId || null,
      model: p.model || null,
    }));
  if (mapped.length === 0) return false;

  // 라온(지휘자)을 맨 앞에 — 작업장이 라온 지휘로 시작한다.
  const raonSession = (typeof state !== 'undefined' && state.currentSessionId) ? state.currentSessionId : null;
  const list = [{ profile: 'raon', sessionId: raonSession, model: null }]
    .concat(mapped)
    .filter((p, i, arr) => arr.findIndex(q => q.profile === p.profile && q.sessionId === p.sessionId) === i)
    .slice(0, BOARDROOM_MAX_PANES);

  try {
    localStorage.setItem('daon_multi_panes', JSON.stringify(
      list.map(p => ({ profile: p.profile, sessionId: p.sessionId, model: p.model }))
    ));
    // 🚨 세션 엉킴 원천 차단: 클래식 활성 세션(daon_active_session_id)을 타 에이전트 세션으로 덮어쓰지 않는다.
  } catch (e) {
    console.warn('boardroom→workshop localStorage write failed:', e);
    return false;
  }
  if (document.getElementById('tab-agent-workshop')) {
    window.activateTab('agent-workshop');
    reloadWorkshopFrame();          // iframe 리로드 → daon_multi_panes 승계 반영
    return true;
  }
  console.warn('workshop panel missing — ensureWorkshopShell() 선행 실패');
  return false;
}

// 도구 결과 페이로드를 dict로 꺼낸다(문자열/이중 인코딩 방어).
function parseToolPayload(data) {
  let v = data && (data.result ?? data.output ?? data.content ?? data.result_json);
  if (typeof v === 'string') { try { v = JSON.parse(v); } catch (_) { return null; } }
  if (typeof v === 'string') { try { v = JSON.parse(v); } catch (_) { return null; } }
  return (v && typeof v === 'object') ? v : null;
}

// 라온의 open_workshop 도구 결과를 감지해 자동으로 작업장을 연다(버튼 없음).
function handleBoardroomToolResult(data) {
  const rawName = (data && (data.name || data.tool_name || (data.function && data.function.name))) || '';
  if (String(rawName).toLowerCase() !== 'open_workshop') return;
  const payload = parseToolPayload(data);
  if (!payload || payload.ok === false) {
    console.warn('open_workshop 실패:', payload && payload.error);
    return;
  }
  if (openBoardroomWorkshop(payload.panes || [])) {
    setStatus('🏗️ 작업장 승계 완료 — 라온 지휘로 작업 시작', 'construction');
  }
}

// ── 회의실 라이브 중계 (도구 경로) ────────────────────────────────────────────
// 라온이 채팅에서 convene_boardroom을 호출하면 SSE stream_id가 없다.
// 서버가 전역 링버퍼(/api/boardroom/live)에 쌓은 이벤트를 폴링해 회의실 탭에 그린다.
let _brLiveTimer = null;
let _brLiveSince = 0;
const BR_LIVE_INTERVAL_MS = 1500;

function startBoardroomLiveWatch() {
  stopBoardroomLiveWatch();
  _brLiveSince = 0;
  const container = document.getElementById('boardroom-transcript-container');
  if (container) container.innerHTML = '';
  appendBoardroomTranscript('01 사회자',
    '회의실을 소집했습니다. 참석 에이전트만 발언하고, 라온은 사회자로 취합·재질의합니다.',
    'bg-surface-container-highest text-on-surface');
  _brLiveTimer = setInterval(pollBoardroomLive, BR_LIVE_INTERVAL_MS);
  pollBoardroomLive();
}

function stopBoardroomLiveWatch() {
  if (_brLiveTimer) { clearInterval(_brLiveTimer); _brLiveTimer = null; }
}

async function pollBoardroomLive() {
  try {
    const base = (typeof DaonAPI !== 'undefined' && DaonAPI.baseUrl) ? DaonAPI.baseUrl : '';
    const r = await fetch(`${base}/api/boardroom/live?since=${_brLiveSince}`);
    const d = await r.json();
    if (!d || !d.ok) return;
    for (const e of (d.events || [])) {
      _brLiveSince = Math.max(_brLiveSince, e.seq || 0);
      renderBoardroomLiveEvent(e);
    }
    if (!d.active) stopBoardroomLiveWatch();
  } catch (_) { /* 폴링 실패는 조용히 무시(서버 재기동 등) */ }
}

function renderBoardroomLiveEvent(e) {
  const ev = e.event;
  const d = e.data || {};
  if (ev === 'boardroom_round_start') {
    if (d.skipped) return;
    appendBoardroomTranscript('01 사회자',
      `── ${d.round}라운드 (${d.mode || ''}) ──`, 'bg-primary/10 text-on-surface');
    return;
  }
  if (ev === 'boardroom_slot') {
    try { setBoardroomSlotState(d.slot, 'running'); } catch (_) {}
    return;
  }
  if (ev === 'boardroom_reply') {
    try { setBoardroomSlotState(d.slot, d.status === 'error' ? 'error' : 'done'); } catch (_) {}
    const modelTag = d.model ? ` · ${d.model}` : '';
    const tag = `${d.slot} ${d.speaker || ''}${modelTag}`.trim();
    const cls = d.status === 'error'
      ? 'bg-rose-100 text-rose-800'
      : 'bg-surface-container-highest text-on-surface';
    const suffix = (typeof d.elapsed === 'number') ? `\n\n— ${d.elapsed}s` : '';
    appendBoardroomTranscript(tag, (d.text || '(응답 없음)') + suffix, cls);
    return;
  }
  if (ev === 'boardroom_moderation') {
    const v = d.verdict || {};
    const verdictTag = v.consensus
      ? '합의 도달'
      : `이견 ${((v.disagreements || []).length)}건 → 재질의`;
    appendBoardroomTranscript('01 사회자 (취합)',
      `${d.round}라운드 취합: ${verdictTag}\n${v.summary || ''}`, 'bg-sky-100 text-sky-900');
    return;
  }
  if (ev === 'boardroom_conclusion') {
    showBoardroomConclusion(d);
    return;
  }
  if (ev === 'boardroom_done') {
    refreshBoardroomWorkerModelStates();
    stopBoardroomLiveWatch();
    return;
  }
}

// 결론 카드 — 버튼 없이 결론만 보여준다(승인은 채팅에서, 승계는 자동).
function showBoardroomConclusion(c) {
  const container = document.getElementById('boardroom-transcript-container');
  if (!container || !c) return;
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const list = (arr, title) => (Array.isArray(arr) && arr.length)
    ? `<div class="mt-2"><div class="font-label-md font-semibold text-on-surface">${title}</div>`
      + arr.map(x => `<div class="text-[13px] text-on-surface leading-relaxed">• ${esc(x)}</div>`).join('')
      + '</div>'
    : '';
  const row = document.createElement('div');
  row.className = 'mt-3 p-3 rounded-[10px] bg-surface-container-highest border border-black/10 animate-fadeIn';
  row.innerHTML = `
    <div class="flex items-center gap-2 mb-1">
      <span class="material-symbols-outlined text-[18px] text-on-surface">gavel</span>
      <span class="font-label-md font-semibold text-on-surface">회의 결론 (${esc(c.rounds || 1)}라운드${c.consensus ? ' · 합의' : ' · 이견 잔존'})</span>
    </div>
    <div class="text-[13px] text-on-surface leading-relaxed whitespace-pre-wrap">${esc(c.summary || '')}</div>
    ${list(c.points, '합의 사항')}
    ${list(c.disagreements, '남은 이견')}
    ${list(c.next_steps, '다음 단계(작업)')}
    <div class="mt-3 pt-2.5 border-t border-black/10 flex flex-wrap items-center justify-between gap-2">
      <div class="text-[12px] text-on-surface-variant">채팅창에서 '승인'을 말하거나, 아래 버튼을 눌러 바로 작업장을 여세요:</div>
      <button type="button" id="btn-boardroom-approve-workshop" class="px-3 py-1.5 rounded-[8px] bg-purple-600 hover:bg-purple-700 text-white font-medium text-[13px] flex items-center gap-1.5 shadow-sm transition-all cursor-pointer">
        <span class="material-symbols-outlined text-[16px]">construction</span>
        <span>작업 승인 및 작업장 열기</span>
      </button>
    </div>
  `;
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;

  const approveBtn = row.querySelector('#btn-boardroom-approve-workshop');
  if (approveBtn) {
    approveBtn.addEventListener('click', async () => {
      approveBtn.disabled = true;
      approveBtn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">sync</span><span>작업장 여는 중...</span>';
      try {
        let panesToOpen = Array.isArray(c.panes) && c.panes.length > 0 ? c.panes : [];
        if (panesToOpen.length === 0) {
          const lastRes = await fetch('/api/boardroom/last').then(r => r.json()).catch(() => null);
          if (lastRes && Array.isArray(lastRes.panes) && lastRes.panes.length > 0) {
            panesToOpen = lastRes.panes;
          }
        }
        if (openBoardroomWorkshop(panesToOpen)) {
          setStatus('🏗️ 회의 결론 승인 완료 — 작업장에서 작업을 시작합니다.', 'construction');
        } else {
          // 폴백: 기본 작업장 열기
          if (typeof window.activateTab === 'function') {
            window.activateTab('agent-workshop');
            reloadWorkshopFrame();
          }
        }
      } catch (err) {
        console.warn('작업장 열기 실패:', err);
      } finally {
        approveBtn.disabled = false;
        approveBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">done</span><span>작업장 열림</span>';
      }
    });
  }
}

// ── MCP Store & Skills Integration ────────────────────────────────────────────

let cachedSkills = [];
let equippedSkillIds = new Set(['daon-self-knowledge', 'daon-dynamic-harness-protocol', 'laya-daemon-single-instance-lock', 'daon-external-skill-patching']);

async function initMcpAndSkills() {
  // 1. MCP Live Servers Loading
  await loadLiveMcpServers();

  // 2. Real Backend Skills Loading
  await loadLiveSkills();

  // 3. Search filtering in MCP tab
  const mcpSearch = document.getElementById('mcp-search-input');
  if (mcpSearch && !mcpSearch._bound) {
    mcpSearch._bound = true;
    mcpSearch.addEventListener('input', () => {
      const query = mcpSearch.value.toLowerCase().trim();
      document.querySelectorAll('#mcp-servers-grid .mcp-plugin-card').forEach(card => {
        const text = card.innerText.toLowerCase();
        card.style.display = text.includes(query) ? 'flex' : 'none';
      });
    });
  }

  // 4. Instant search filtering in Skills tab across all backend skills
  const skillSearch = document.getElementById('skills-search-input');
  if (skillSearch && !skillSearch._bound) {
    skillSearch._bound = true;
    skillSearch.addEventListener('input', () => {
      const query = skillSearch.value.toLowerCase().trim();
      if (!query) {
        renderSkillCatalog(cachedSkills.slice(0, 30));
      } else {
        const filtered = cachedSkills.filter(s =>
          (s.name || s.id || '').toLowerCase().includes(query) ||
          (s.description || '').toLowerCase().includes(query) ||
          (s.category || '').toLowerCase().includes(query)
        );
        renderSkillCatalog(filtered.slice(0, 50));
      }
    });
  }

  // 5. Ping All MCP Servers button
  const pingBtn = document.getElementById('mcp-ping-all-btn') || Array.from(document.querySelectorAll('#tab-plugin-mcp button')).find(b => b.textContent && b.textContent.includes('전체 서버 상태 핑'));
  if (pingBtn && !pingBtn._bound) {
    pingBtn._bound = true;
    pingBtn.addEventListener('click', async () => {
      const originalText = pingBtn.textContent;
      pingBtn.textContent = '핑 확인 중...';
      try {
        await loadLiveMcpServers();
        pingBtn.textContent = '핑 정상 (응답 완료)';
        setTimeout(() => { pingBtn.textContent = originalText; }, 2000);
      } catch {
        pingBtn.textContent = '핑 오류';
        setTimeout(() => { pingBtn.textContent = originalText; }, 2000);
      }
    });
  }

  // 6. Custom MCP Register Modal
  const addMcpBtn = document.getElementById('add-custom-mcp-btn');
  const mcpAddModal = document.getElementById('mcp-add-modal');
  const mcpAddClose = document.getElementById('mcp-add-modal-close');
  const mcpAddCancel = document.getElementById('mcp-add-modal-cancel');
  const mcpAddSubmit = document.getElementById('mcp-add-modal-submit');
  const mcpAddTransport = document.getElementById('mcp-add-transport');
  const mcpAddStdioGroup = document.getElementById('mcp-add-stdio-group');
  const mcpAddHttpGroup = document.getElementById('mcp-add-http-group');

  if (addMcpBtn && mcpAddModal && !addMcpBtn._bound) {
    addMcpBtn._bound = true;
    addMcpBtn.addEventListener('click', () => {
      mcpAddModal.classList.remove('hidden');
      mcpAddModal.classList.add('flex');
    });

    const closeMcpAddModal = () => {
      mcpAddModal.classList.add('hidden');
      mcpAddModal.classList.remove('flex');
    };

    mcpAddClose?.addEventListener('click', closeMcpAddModal);
    mcpAddCancel?.addEventListener('click', closeMcpAddModal);

    mcpAddTransport?.addEventListener('change', () => {
      if (mcpAddTransport.value === 'http') {
        mcpAddStdioGroup?.classList.add('hidden');
        mcpAddHttpGroup?.classList.remove('hidden');
      } else {
        mcpAddStdioGroup?.classList.remove('hidden');
        mcpAddHttpGroup?.classList.add('hidden');
      }
    });

    mcpAddSubmit?.addEventListener('click', async () => {
      const serverId = document.getElementById('mcp-add-id')?.value.trim();
      const label = document.getElementById('mcp-add-label')?.value.trim() || serverId;
      const transport = mcpAddTransport?.value || 'stdio';

      if (!serverId) {
        alert('서버 ID를 입력해주세요.');
        return;
      }

      const payload = { server_id: serverId, label, transport };
      if (transport === 'http') {
        const url = document.getElementById('mcp-add-url')?.value.trim();
        const authToken = document.getElementById('mcp-add-auth')?.value.trim();
        if (!url) {
          alert('HTTP 엔드포인트 URL을 입력해주세요.');
          return;
        }
        payload.url = url;
        payload.auth_token = authToken;
      } else {
        const command = document.getElementById('mcp-add-command')?.value.trim() || 'npx';
        const rawArgs = document.getElementById('mcp-add-args')?.value.trim() || '';
        payload.command = command;
        payload.args = rawArgs ? rawArgs.split(/\s+/) : [];
      }

      mcpAddSubmit.disabled = true;
      mcpAddSubmit.textContent = '등록 중...';

      try {
        const res = await fetch(`${DaonAPI.baseUrl}/api/mcp/servers/add`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
          closeMcpAddModal();
          await loadLiveMcpServers();
        } else {
          alert('등록 실패: ' + (data.error || '알 수 없는 오류'));
        }
      } catch (err) {
        alert('등록 요청 실패: ' + err.message);
      } finally {
        mcpAddSubmit.disabled = false;
        mcpAddSubmit.textContent = '등록 및 연결';
      }
    });
  }

  // 7. MCP Server Detail Inspector Modal
  const mcpDetailModal = document.getElementById('mcp-detail-modal');
  const mcpDetailClose = document.getElementById('mcp-detail-modal-close');
  const mcpDetailOk = document.getElementById('mcp-detail-modal-ok');
  if (mcpDetailModal && !mcpDetailModal._bound) {
    mcpDetailModal._bound = true;
    const closeDetail = () => {
      mcpDetailModal.classList.add('hidden');
      mcpDetailModal.classList.remove('flex');
    };
    mcpDetailClose?.addEventListener('click', closeDetail);
    mcpDetailOk?.addEventListener('click', closeDetail);
  }

  // 8. Add Custom Skill button
  const addSkillBtn = document.getElementById('add-custom-skill-btn');
  if (addSkillBtn && !addSkillBtn._bound) {
    addSkillBtn._bound = true;
    addSkillBtn.addEventListener('click', () => {
      alert('스킬 생성 가이드: \n\n새 에이전트 스킬은 `skills/<카테고리>/<스킬이름>/SKILL.md` 경로에 마크다운 및 실행 스크립트로 정의됩니다.\n저장 시 Laya 및 e5 랭커가 실시간 감지하여 자동 인덱싱합니다.');
    });
  }
}

function showMcpServerDetails(srv) {
  const modal = document.getElementById('mcp-detail-modal');
  const title = document.getElementById('mcp-detail-title');
  const body = document.getElementById('mcp-detail-body');
  if (!modal || !body) return;

  const sid = srv.server_id || 'unknown';
  if (title) title.textContent = `${srv.label || sid} (@mcp/${sid})`;

  const tools = srv.tools || [];
  const toolsHtml = tools.length > 0
    ? tools.map(t => `
        <div class="p-2 bg-surface border border-black/10 rounded-[6px] flex flex-col gap-1">
          <div class="flex items-center justify-between">
            <span class="font-code font-bold text-on-surface text-[12px]">${escapeHtml(t.name || '')}</span>
            <span class="px-1.5 py-0.5 bg-primary/10 text-primary font-code text-[10px] rounded">함수 도구</span>
          </div>
          <p class="text-[11px] text-on-surface-variant leading-relaxed">${escapeHtml(t.description || '도구 설명 없음')}</p>
        </div>
      `).join('')
    : '<div class="text-on-surface-variant/60 py-3 text-center">연결된 도구가 없거나 서버 대기 중입니다.</div>';

  body.innerHTML = `
    <div class="grid grid-cols-2 gap-2 p-3 bg-surface border border-black/10 rounded-[8px] font-code text-[11px]">
      <div><span class="text-on-surface-variant">서버 ID:</span> <strong class="text-on-surface">${escapeHtml(sid)}</strong></div>
      <div><span class="text-on-surface-variant">연결 방식:</span> <strong class="text-on-surface">${srv.transport || 'stdio'}</strong></div>
      <div><span class="text-on-surface-variant">활성화 상태:</span> <strong class="${srv.enabled ? 'text-emerald-700' : 'text-on-surface-variant'}">${srv.enabled ? '활성화 (ON)' : '비활성화 (OFF)'}</strong></div>
      <div><span class="text-on-surface-variant">실제 통신:</span> <strong class="${srv.connected ? 'text-emerald-700' : 'text-rose-700'}">${srv.connected ? '연결됨 (Connected)' : '대기/중단'}</strong></div>
      ${srv.command ? `<div class="col-span-2 truncate"><span class="text-on-surface-variant">명령어:</span> <span class="text-on-surface">${escapeHtml(srv.command)} ${(srv.args || []).join(' ')}</span></div>` : ''}
      ${srv.url ? `<div class="col-span-2 truncate"><span class="text-on-surface-variant">URL:</span> <span class="text-on-surface">${escapeHtml(srv.url)}</span></div>` : ''}
      ${srv.error ? `<div class="col-span-2 text-rose-700"><span class="font-bold">오류 내용:</span> ${escapeHtml(srv.error)}</div>` : ''}
    </div>
    <div class="flex flex-col gap-2 mt-1">
      <div class="flex items-center justify-between">
        <h4 class="font-label-md font-semibold text-on-surface">제공 도구 목록 (총 ${tools.length}개)</h4>
        <span class="font-code text-[11px] text-on-surface-variant">MCP Schema Tools</span>
      </div>
      <div class="flex flex-col gap-1.5 max-h-[260px] overflow-y-auto pr-1">
        ${toolsHtml}
      </div>
    </div>
  `;

  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

async function loadLiveMcpServers() {
  const container = document.getElementById('mcp-servers-grid');
  if (!container) return;

  try {
    const servers = await DaonAPI.getMCPServers();
    if (servers && servers.length > 0) {
      // 1. 활성 카운트 및 도구 계산
      // enabled: 사용자가 토글 스위치로 켜둔 서버
      // connected: 실제로 백엔드 프로세스/HTTP가 정상 연결된 서버
      const enabledServers = servers.filter(s => s.enabled === true);
      const connectedServers = servers.filter(s => s.connected === true);

      const activeCount = enabledServers.length;
      const connectedCount = connectedServers.length;
      const toolsCount = servers.reduce((acc, s) => {
        return acc + (s.connected && s.tools ? s.tools.length : (s.tools_count || 0));
      }, 0);

      // 상단 요약 바 갱신
      const activeCountLabel = document.getElementById('mcp-active-count-label');
      const toolsCountLabel = document.getElementById('mcp-tools-count-label');
      const totalCountLabel = document.getElementById('mcp-total-count-label');
      const activeIndicator = document.getElementById('mcp-active-indicator');
      const summaryBarText = document.querySelector('#tab-plugin-mcp .mcp-plugin-card .text-on-surface');

      if (activeCountLabel) {
        activeCountLabel.innerHTML = `${connectedCount}개 실행 중 <span class="text-xs font-normal text-on-surface-variant">(${activeCount}개 활성화)</span>`;
      }
      if (toolsCountLabel) toolsCountLabel.textContent = `${toolsCount}개 함수`;
      if (totalCountLabel) totalCountLabel.textContent = `${servers.length}개 등록`;
      if (activeIndicator) {
        activeIndicator.className = connectedCount > 0 ? 'w-2 h-2 rounded-full bg-emerald-600' : 'w-2 h-2 rounded-full bg-amber-500';
      }
      if (summaryBarText && !activeCountLabel) {
        summaryBarText.innerHTML = `<span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full ${connectedCount > 0 ? 'bg-emerald-600' : 'bg-amber-500'}"></span>활성 서버: <strong>${connectedCount}개 실행 중 (${activeCount}개 활성화)</strong></span><span class="text-black/20">|</span><span>등록된 툴: <strong>${toolsCount}개 함수</strong></span><span class="text-black/20">|</span><span>전체: <strong>${servers.length}개</strong></span><span class="text-black/20">|</span><span>프로토콜: <strong class="font-code">v2024-11-05</strong></span>`;
      }

      // 2. 서버 카드 렌더링
      container.innerHTML = '';
      servers.forEach(srv => {
        const isEnabled = srv.enabled === true;
        const isConnected = srv.connected === true;
        const sid = srv.server_id || '';
        const label = srv.label || sid;
        const toolsList = srv.tools || [];
        const srvToolsCount = toolsList.length || srv.tools_count || 0;
        const isExpired = srv.expired === true;
        const errorMsg = srv.error || '';

        // 상태 뱃지
        let badgeText = 'STANDBY';
        let badgeClass = 'bg-black/[0.05] text-on-surface-variant border-black/10';
        if (isConnected) {
          badgeText = 'ACTIVE';
          badgeClass = 'bg-emerald-50 text-emerald-800 border-emerald-300';
        } else if (isExpired) {
          badgeText = 'EXPIRED';
          badgeClass = 'bg-amber-50 text-amber-800 border-amber-300';
        } else if (errorMsg) {
          badgeText = 'ERROR';
          badgeClass = 'bg-rose-50 text-rose-800 border-rose-300';
        } else if (isEnabled) {
          badgeText = 'CONNECTING';
          badgeClass = 'bg-blue-50 text-blue-800 border-blue-300';
        }

        const card = document.createElement('div');
        card.className = 'p-space-md bg-surface-container-lowest border border-black/10 rounded-[10px] flex flex-col justify-between gap-space-md hover:border-black/30 transition-colors mcp-plugin-card';
        card.setAttribute('data-server-id', sid);

        card.innerHTML = `
          <div class="flex flex-col gap-2">
            <div class="flex items-start justify-between">
              <div class="flex items-center gap-2.5">
                <div class="w-9 h-9 rounded-[8px] bg-surface-container-highest flex items-center justify-center font-mono font-bold text-on-surface text-sm shrink-0">
                  ${sid.slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <h4 class="font-label-md font-semibold text-on-surface flex items-center gap-1.5">
                    ${escapeHtml(label)}
                  </h4>
                  <span class="font-code text-[11px] text-on-surface-variant">@mcp/${escapeHtml(sid)}</span>
                </div>
              </div>
              <span class="px-2 py-0.5 border font-code text-[10px] font-semibold rounded shrink-0 ${badgeClass} mcp-status-badge">
                ${badgeText}
              </span>
            </div>
            <p class="font-body-sm text-[12px] text-on-surface-variant leading-relaxed">
              ${escapeHtml(srv.description || (sid === 'daon-design' ? 'Daon UI/UX 디자인 에이전트 전용 디자인 토큰, 믹싱, 트렌드 도구' : sid === 'windows-computer-use' ? 'Windows 화면 캡처, 마우스/키보드 자동 제어 및 OS 자동화 도구' : sid === 'rea' ? 'Ghidra 기반 역공학, 바이너리 분석 및 소프트웨어 리버스 엔지니어링 브리지' : sid === 'reticle' ? 'AI 코드 검증, 정적 분석 및 보안 가드레일 레이어' : sid === 'filesystem' ? '프로젝트 디렉토리 안전 파일 읽기/쓰기 샌드박스' : sid === 'playmcp-gateway' ? '카카오톡, 네이버 검색 등 23개 국내 서비스 게이트웨이' : '표준 Model Context Protocol 도구 및 외부 커넥터'))}
            </p>
            ${errorMsg ? `<div class="px-2 py-1 bg-rose-50 border border-rose-200 rounded text-[11px] text-rose-700 font-mono truncate" title="${escapeHtml(errorMsg)}">오류: ${escapeHtml(errorMsg)}</div>` : ''}
          </div>
          <div class="flex items-center justify-between pt-2 border-t border-black/[0.06]">
            <div class="flex items-center gap-2 font-code text-[11px] text-on-surface-variant">
              <span>도구 <strong class="text-on-surface">${srvToolsCount}개</strong></span>
              <span>·</span>
              <span>${srv.transport || 'stdio'}</span>
            </div>
            <div class="flex items-center gap-1.5">
              <button class="px-2.5 py-1 border border-black/10 rounded-[6px] font-label-sm text-on-surface hover:bg-black/[0.04] mcp-cfg-btn cursor-pointer" type="button" data-id="${escapeHtml(sid)}">
                설정
              </button>
              <button class="px-2.5 py-1 ${isEnabled ? 'bg-surface-container-highest text-on-surface border border-black/10 hover:bg-black/[0.05]' : 'bg-primary text-on-primary hover:bg-black/80'} rounded-[6px] font-label-sm transition-all mcp-toggle-btn cursor-pointer" type="button" data-id="${escapeHtml(sid)}" data-enabled="${isEnabled}">
                ${isEnabled ? '비활성화' : '활성화'}
              </button>
            </div>
          </div>
        `;

        // 토글 버튼 리스너
        card.querySelector('.mcp-toggle-btn')?.addEventListener('click', async (e) => {
          const btn = e.currentTarget;
          const targetSid = btn.getAttribute('data-id');
          const currentEnabled = btn.getAttribute('data-enabled') === 'true';

          btn.disabled = true;
          btn.textContent = '처리 중...';

          try {
            if (currentEnabled) {
              await DaonAPI.disconnectMCPServer(targetSid);
            } else {
              await DaonAPI.connectMCPServer(targetSid);
            }
            // 즉시 백엔드 상태를 재조회하여 전체 UI(상단 요약 바 + 카드 전체)를 완벽히 갱신
            await loadLiveMcpServers();
          } catch (err) {
            alert('MCP 서버 상태 변경 실패: ' + err.message);
            btn.disabled = false;
            btn.textContent = currentEnabled ? '비활성화' : '활성화';
          }
        });

        // 설정 버튼 리스너
        card.querySelector('.mcp-cfg-btn')?.addEventListener('click', () => {
          showMcpServerDetails(srv);
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
      renderActiveSkillSlots();
      renderSkillCatalog(skills.slice(0, 30));
    }
  } catch (err) {
    console.warn('Failed to load real skills:', err);
  }
}

function renderActiveSkillSlots() {
  const grid = document.getElementById('skills-active-slots-grid');
  const countLabel = document.getElementById('skills-active-count-label');
  if (!grid) return;

  const equipped = cachedSkills.filter(s => equippedSkillIds.has(s.id || s.name));
  const displayList = equipped.length > 0 ? equipped.slice(0, 6) : cachedSkills.slice(0, 4);

  if (countLabel) countLabel.textContent = `${displayList.length} / 6`;

  grid.innerHTML = '';
  displayList.forEach((s, idx) => {
    const slotDiv = document.createElement('div');
    slotDiv.className = 'p-2.5 bg-surface-container-lowest border border-black/15 rounded-[8px] flex flex-col gap-1';
    slotDiv.innerHTML = `
      <span class="font-code text-[10px] text-on-surface-variant">SLOT 0${idx + 1}</span>
      <span class="font-label-md font-semibold text-on-surface truncate" title="${escapeHtml(s.name || s.id)}">${escapeHtml(s.name || s.id)}</span>
      <span class="font-body-sm text-[11px] text-on-surface-variant truncate">${escapeHtml(s.category || '코어 실행 스킬')}</span>
    `;
    grid.appendChild(slotDiv);
  });
}

function renderSkillCatalog(skillList) {
  const container = document.getElementById('skills-catalog-grid');
  if (!container) return;

  container.innerHTML = '';
  skillList.forEach(s => {
    const sid = s.id || s.name;
    const isEquipped = equippedSkillIds.has(sid) || s.enabled || s.source === 'local';
    const card = document.createElement('div');
    card.className = 'p-space-md bg-surface border border-black/10 rounded-[10px] flex flex-col justify-between gap-3 skill-catalog-card hover:border-black/30 transition-colors';
    card.innerHTML = `
      <div class="flex items-start justify-between">
        <div class="flex-1 pr-2">
          <div class="flex items-center gap-2">
            <h4 class="font-label-md font-semibold text-on-surface truncate">${escapeHtml(s.name || sid)}</h4>
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
        <button class="toggle-slot-btn ${isEquipped ? 'text-on-surface-variant' : 'text-primary'} font-semibold hover:underline cursor-pointer" type="button" data-id="${escapeHtml(sid)}">
          ${isEquipped ? '장착 해제' : '슬롯에 장착'}
        </button>
      </div>
    `;

    // Toggle slot button
    card.querySelector('.toggle-slot-btn')?.addEventListener('click', (e) => {
      const btn = e.currentTarget;
      const targetId = btn.getAttribute('data-id');
      if (equippedSkillIds.has(targetId)) {
        equippedSkillIds.delete(targetId);
        btn.textContent = '슬롯에 장착';
        btn.className = 'toggle-slot-btn text-primary font-semibold hover:underline cursor-pointer';
        card.querySelector('.material-symbols-outlined').className = 'material-symbols-outlined text-[18px] text-on-surface-variant/40 shrink-0';
        card.querySelector('.material-symbols-outlined').textContent = 'radio_button_unchecked';
      } else {
        if (equippedSkillIds.size >= 6) {
          alert('슬롯은 최대 6개까지 장착할 수 있습니다.');
          return;
        }
        equippedSkillIds.add(targetId);
        btn.textContent = '장착 해제';
        btn.className = 'toggle-slot-btn text-on-surface-variant font-semibold hover:underline cursor-pointer';
        card.querySelector('.material-symbols-outlined').className = 'material-symbols-outlined text-[18px] text-primary shrink-0';
        card.querySelector('.material-symbols-outlined').textContent = 'check_circle';
      }
      renderActiveSkillSlots();
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

// ── Theme Switcher (Dark / Light Mode) ────────────────────────────────────────

function initTheme() {
  const toggleBtn = document.getElementById('theme-toggle-btn');
  if (!toggleBtn) return;

  function updateThemeUI(isDark) {
    if (isDark) {
      document.documentElement.classList.add('dark');
      toggleBtn.title = '라이트 모드로 전환 (현재: 다크 모드)';
    } else {
      document.documentElement.classList.remove('dark');
      toggleBtn.title = '다크 모드로 전환 (현재: 라이트 모드)';
    }
  }

  // 초기 상태 반영
  const currentIsDark = document.documentElement.classList.contains('dark');
  updateThemeUI(currentIsDark);

  toggleBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('daon_theme', isDark ? 'dark' : 'light');
    updateThemeUI(isDark);
    if (typeof showToast === 'function') {
      showToast(isDark ? '🌙 다크 모드로 전환되었습니다.' : '☀️ 라이트 모드로 전환되었습니다.');
    }
  });
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
  const chatInput = document.getElementById('chat-input');
  const inputContainer = chatInput ? chatInput.closest('.bg-surface-container-lowest') : null;

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

  // 🖼️ 클립보드 이미지 복사 붙여넣기 (Ctrl+V) 핸들러
  function handleClipboardImagePaste(e) {
    try {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items || items.length === 0) return;

      const imgFiles = [];
      for (const item of items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f && f.type && f.type.startsWith('image/')) {
            const ext = f.type.split('/')[1]?.replace('+xml', '') || 'png';
            const now = new Date();
            const timeStr = `${now.getHours()}${now.getMinutes()}${now.getSeconds()}`;
            const cleanName = (f.name && f.name !== 'image.png') ? f.name : `screenshot_${timeStr}.${ext}`;
            const namedFile = new File([f], cleanName, { type: f.type });
            imgFiles.push(namedFile);
          }
        }
      }

      if (imgFiles.length > 0) {
        e.preventDefault(); // 텍스트 영역에 깨진 바이너리 텍스트 입력 방지
        imgFiles.forEach(f => state.pendingFiles.push(f));
        renderAttachTray();
        console.log(`[Paste] ${imgFiles.length} image(s) attached.`);
        if (typeof showToast === 'function') {
          showToast(`🖼️ 이미지 ${imgFiles.length}장이 첨부되었습니다.`);
        }
      }
    } catch (err) {
      console.warn('[Paste] Image paste failed:', err);
    }
  }

  // 1) 채팅 입력창에서 Ctrl+V
  if (chatInput) {
    chatInput.addEventListener('paste', handleClipboardImagePaste);
  }

  // 2) 채팅창 영역 전체에서도 이미지 Ctrl+V 지원 (입력창 외부에 포커스 있을 때)
  document.addEventListener('paste', (e) => {
    if (e.target === chatInput) return; // 이미 위에서 처리
    // 다른 입력 폼(설정 모달 등)에서는 무시
    if (e.target && (e.target.tagName === 'INPUT' || (e.target.tagName === 'TEXTAREA' && e.target !== chatInput))) return;
    handleClipboardImagePaste(e);
  });

  // 📦 채팅 입력창에 파일/이미지 드래그 & 드롭 지원
  if (inputContainer) {
    ['dragenter', 'dragover'].forEach(eventName => {
      inputContainer.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        inputContainer.classList.add('border-primary', 'bg-primary/[0.02]');
      }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
      inputContainer.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        inputContainer.classList.remove('border-primary', 'bg-primary/[0.02]');
      }, false);
    });

    inputContainer.addEventListener('drop', (e) => {
      const dt = e.dataTransfer;
      const files = dt && dt.files;
      if (files && files.length > 0) {
        Array.from(files).forEach(f => state.pendingFiles.push(f));
        renderAttachTray();
        if (typeof showToast === 'function') {
          showToast(`📎 파일 ${files.length}개가 첨부되었습니다.`);
        }
      }
    }, false);
  }

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

function syncModelAndProviderDisplay(modelId) {
  if (!modelId) return;
  const currentModelLabel = document.getElementById('current-model-label');
  const currentProviderLabel = document.getElementById('current-provider-label');
  if (currentModelLabel) currentModelLabel.textContent = modelId;

  if (cachedProvidersData) {
    const target = String(modelId).toLowerCase();
    for (const [pKey, p] of Object.entries(cachedProvidersData)) {
      const match = (p?.models || []).find(m => {
        const mid = (typeof m === 'string' ? m : (m?.id || m?.label || '')).toLowerCase();
        return mid === target;
      });
      if (match) {
        if (currentProviderLabel) {
          currentProviderLabel.textContent = `PROVIDER: ${p.label || pKey}`;
        }
        const meffort = (typeof match === 'object' && match.reasoning_effort) || '';
        if (typeof setReasoningEffort === 'function') setReasoningEffort(meffort);
        return;
      }
    }
  }
}

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

    // 초기 활성 모델 및 프로바이더 라벨 정확히 반영
    const initialModelId = state.currentSessionModel || 'deepseek-v4.1-flash';
    syncModelAndProviderDisplay(initialModelId);

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
          const ok = await daonConfirm(`'${name}' 제공자와 등록된 모델을 삭제하시겠습니까?`, {
            title: '제공자 삭제',
            confirmText: '삭제',
            danger: true
          });
          if (ok) {
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
      window.addEventListener('resize', closePopover);
    }
    return globalModelPopover;
  }

  function closePopover() {
    if (globalModelPopover) {
      globalModelPopover.classList.add('hidden');
      globalModelPopover.style.display = 'none';
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

// ── [백그라운드 기상 폴러 2026-10-17] ──────────────────────────────
// 서버에 등록된 watch(워커/터미널/하네스 백그라운드 작업) 중 완료된 것이
// 있으면, 8초 간격 폴링으로 감지해 chat-input에 기상 메시지를 넣고
// handleSendMessage()로 턴을 자동 오픈한다 = 서버가 라온을 깨운다.
// 규칙: 스트리밍 중·사용자 입력 중·화면 숨김 상태에서는 절대 끼어들지 않고
// 다음 폴링으로 연기한다. ack는 전송 직전에만 해서 중복 기상을 막는다.
let __wakeTimer = null;
async function wakePollOnce() {
  try {
    if (document.visibilityState !== 'visible') return;
    if (!state.currentSessionId) return;
    if (state.isStreaming || state.currentStreamId || state.activeStream) return;
    const input = document.getElementById('chat-input');
    if (!input) return;
    if (input.value.trim()) return; // 사용자가 타이핑 중이면 건드리지 않음
    const r = await DaonAPI.wakePoll(state.currentSessionId);
    const done = (r && r.completions) || [];
    if (!done.length) return;
    await DaonAPI.wakeAck(done.map(c => c.id));
    const lines = done.map(c => {
      const mark = c.stale ? '⚠️ 기록유실' : (c.ok ? '✅ 완료' : '❌ 실패');
      const need = c.needs_input ? '\n  ‼️ 라온의 입력이 필요해서 멈춰 있음 — 최우선 처리!' : '';
      const summ = (c.summary || '(결과 없음)').slice(0, 600);
      const nxt = c.next ? `\n  이어할 일: ${c.next}` : '';
      return `• ${mark} [${c.label}] (${c.kind}/${c.ref})\n  결과: ${summ}${nxt}${need}`;
    });
    input.value = `🔔 [자동 기상 — 백그라운드 작업 완료 ${done.length}건]\n${lines.join('\n')}\n위 완료를 대표님께 먼저 보고하고, '이어할 일'이 있으면 끊김 없이 계속 진행해.`;
    input.style.height = 'auto';
    handleSendMessage();
  } catch (e) {
    console.warn('[wake] poll failed:', e);
  }
}
function startWakePoller() {
  if (__wakeTimer) return;
  __wakeTimer = setInterval(wakePollOnce, 8000);
}
startWakePoller();

// ── Global App State ──
const State = {
  sessions: [],
  activeSessionId: null,
  activeWorkspacePath: '',
  models: [],
  activeModelId: '',
  settings: {},

  // Profiles state
  profiles: [],
  activeProfileName: 'default',

  // Monaco editor instance
  editor: null,
  // Tabs tracker: [{ path, name, mode, model, content, dirty }]
  openTabs: [],
  activeTabIndex: -1,

  // File tree cached expanded folders
  expandedDirs: new Set(),

  // Chat stream tracker
  currentEventSource: null,
  currentStreamId: null,

  // Harness state
  harnessPollInterval: null,
  harnessRunId: null,
  harnessLogCursor: 0,
  harnessAgentCards: {},

  // File upload queue
  pendingFiles: [],

  // Panel Visibility States
  leftPanelVisible: localStorage.getItem('daon_left_panel_visible') !== 'false',
  rightPanelVisible: localStorage.getItem('daon_right_panel_visible') !== 'false',
  explorerVisible: localStorage.getItem('daon_explorer_visible') !== 'false'
};

// ── Element Bindings ──
const $ = (id) => document.getElementById(id);

// Simple MD Parser
function renderMd(text) {
  if (!text) return '';

  // ── Phase 0: extract code blocks & inline code BEFORE image/link parsing ──
  var placeholders = [];
  var phIndex = 0;

  // Code blocks: ```...``` → placeholder (prevent markdown inside code from being parsed)
  text = text.replace(/```([\s\S]*?)```/g, function (match, code) {
    var ph = '\x00MDCOD' + (phIndex++) + '\x00';
    placeholders.push({ ph: ph, html: '<pre class="md-code"><code>' + _mdEscapeContent(code) + '</code></pre>' });
    return ph;
  });

  // Inline code: `...` → placeholder
  text = text.replace(/`([^`]+)`/g, function (match, code) {
    var ph = '\x00MDINC' + (phIndex++) + '\x00';
    placeholders.push({ ph: ph, html: '<code class="md-inline">' + _mdEscapeContent(code) + '</code>' });
    return ph;
  });

  // ── Phase 1: extract images & links ──
  // Images: ![alt](url) — only matches outside code placeholders
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (match, alt, url) {
    var ph = '\x00MDIMG' + (phIndex++) + '\x00';
    var safeUrl = _mdEscapeContent(url);
    var safeAlt = _mdEscapeContent(alt);
    placeholders.push({ ph: ph, html: '<img src="' + safeUrl + '" alt="' + safeAlt + '" class="md-image" loading="lazy" onerror="this.style.display=\'none\'">' });
    return ph;
  });

  // Videos: bare URLs ending in video extensions → inline <video> player
  text = text.replace(/(https?:\/\/[^\s<>"']+?\.(?:mp4|webm|mov|ogg)(?:\?[^\s<>"']*)?)/gi, function (match, url) {
    var ph = '\x00MDVID' + (phIndex++) + '\x00';
    var safeUrl = _mdEscapeContent(url);
    placeholders.push({ ph: ph, html: '<video controls preload="metadata" style="max-width:100%;border-radius:8px;margin:6px 0;" src="' + safeUrl + '"></video>' });
    return ph;
  });

  // Markdown video links: [text](url.mp4) → inline <video> player
  text = text.replace(/\[([^\]]*)\]\(([^)\s]+?\.(?:mp4|webm|mov|ogg)(?:\?[^)\s]*)?)\)/gi, function (match, txt, url) {
    var ph = '\x00MDVID' + (phIndex++) + '\x00';
    var safeUrl = _mdEscapeContent(url);
    placeholders.push({ ph: ph, html: '<video controls preload="metadata" style="max-width:100%;border-radius:8px;margin:6px 0;" src="' + safeUrl + '"></video>' });
    return ph;
  });

  // Links: [text](url)
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, txt, url) {
    var ph = '\x00MDLNK' + (phIndex++) + '\x00';
    var safeUrl = _mdEscapeContent(url);
    placeholders.push({ ph: ph, html: '<a href="' + safeUrl + '" target="_blank" rel="noopener" class="md-link">' + _mdEscapeContent(txt) + '</a>' });
    return ph;
  });

  // ── Phase 2: HTML escape remaining text ──
  var html = text
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">");

  // Headings
  html = html.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.*?)$/gm, '<h1>$1</h1>');
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Lists
  html = html.replace(/^\- (.*?)$/gm, '<li>$1</li>');
  // Paragraphs (split by double newlines)
  html = html.split('\n\n').map(function (p) {
    if (p.trim().startsWith('<h') || p.trim().startsWith('<pre') || p.trim().startsWith('<li>')) return p;
    return '<p>' + p.replace(/\n/g, '<br>') + '</p>';
  }).join('');

  // ── Phase 3: restore all placeholders ──
  placeholders.forEach(function (entry) {
    html = html.replace(entry.ph, entry.html);
  });

  return html;
}

function _mdEscapeContent(str) {
  var A = String.fromCharCode(38);
  return str.replace(/&/g, A + 'amp;')
    .replace(/</g, A + 'lt;')
    .replace(/>/g, A + 'gt;')
    .replace(/"/g, A + 'quot;')
    .replace(/'/g, A + '#039;');
}

// ── API Helpers ──
async function api(url, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  // If body is already a string (caller pre-serialized), don't double-stringify
  const body = opts.body
    ? (typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body))
    : undefined;

  // AbortController timeout — prevent fetch from hanging indefinitely
  const timeout = opts.timeout || 15000; // 15s default
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const res = await fetch(url, {
      method: opts.method || 'GET',
      headers,
      body: body,
      cache: 'no-store',
      signal: controller.signal
    });
    clearTimeout(timer);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP error ${res.status}`);
    }
    return res.json();
  } catch (e) {
    clearTimeout(timer);
    if (e.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeout}ms: ${url}`);
    }
    throw e;
  }
}
function showToast(msg, ms = 2800) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._toastTimeout);
  el._toastTimeout = setTimeout(() => {
    el.classList.remove('show');
  }, ms);
}
function esc(str) {
  if (!str) return '';
  return str.toString()
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/"/g, '"')
    .replace(/'/g, '&#039;');
}

var _currentPanel = 'chat';
var _skillsData = null;
var _cronSkillsCache = null;
var _cronSelectedSkills = [];
var _memoryData = null;
var _workspaceList = [];
var _profilesCache = null;
var _editingSkillName = null;
const S = {
  // ── Internal state (mirrors the real S from ui.js) ──
  _messages: [],
  _toolCalls: [],
  _entries: [],
  _currentDir: '.',
  _pendingFiles: [],
  _lastUsage: null,

  get session() {
    return State.sessions.find(x => x.session_id === State.activeSessionId) || null;
  },
  set session(val) {
    if (val && val.session_id) {
      const idx = State.sessions.findIndex(x => x.session_id === val.session_id);
      if (idx >= 0) State.sessions[idx] = val;
      else State.sessions.unshift(val);
      if (val.messages) this._messages = val.messages;
      if (val.tool_calls) this._toolCalls = val.tool_calls;
    }
  },

  get messages() { return this._messages; },
  set messages(val) { this._messages = val || []; },

  get toolCalls() { return this._toolCalls; },
  set toolCalls(val) { this._toolCalls = val || []; },

  get entries() { return this._entries; },
  set entries(val) { this._entries = val || []; },

  get currentDir() { return this._currentDir; },
  set currentDir(val) { this._currentDir = val || '.'; },

  get pendingFiles() { return this._pendingFiles; },
  set pendingFiles(val) { this._pendingFiles = val || []; },

  get busy() { return State.currentStreamId !== null; },

  get activeStreamId() { return State.currentStreamId || null; },
  set activeStreamId(val) { State.currentStreamId = val; },

  get activeProfile() { return State.activeProfileName; },
  set activeProfile(val) { State.activeProfileName = val; },

  get lastUsage() { return this._lastUsage; },
  set lastUsage(val) { this._lastUsage = val; },
};
// ── Init & Setup ──
// 전역 테마 변경 함수 정의
window.changeTheme = function (themeName) {
  document.documentElement.setAttribute('data-theme', themeName);
  localStorage.setItem('daon_theme', themeName);

  // 모나코 에디터 테마 변경
  if (window.monaco && State.editor) {
    const monacoTheme = themeName === 'light' ? 'vs' : 'vs-dark';
    monaco.editor.setTheme(monacoTheme);
  }
};

window.addEventListener('DOMContentLoaded', async () => {
  // 각 초기화 단계를 개별 try-catch로 감싸 하나가 실패해도 나머지가 실행되도록 방어
  try {
    const savedTheme = localStorage.getItem('daon_theme') || 'midnight';
    const themeSelect = $('themeSelect');
    if (themeSelect) {
      themeSelect.value = savedTheme;
    }
  } catch (e) { console.error('[daon-init] theme init failed:', e); }

  try { initCliConsole(); } catch (e) { console.error('[daon-init] initCliConsole failed:', e); }
  try { initMonaco(); } catch (e) { console.error('[daon-init] initMonaco failed:', e); }
  try { initResizers(); } catch (e) { console.error('[daon-init] initResizers failed:', e); }
  try { initWebExplorerEvents(); } catch (e) { console.error('[daon-init] initWebExplorerEvents failed:', e); }

  try {
    await loadInitialData();
  } catch (e) {
    console.error('[daon-init] loadInitialData failed:', e);
    // loadInitialData 실패 시에도 기본 UI는 동작하게 setupEventListeners 호출
  }

  try { setupEventListeners(); } catch (e) { console.error('[daon-init] setupEventListeners failed:', e); }
  try { initHarnessManual(); } catch (e) { console.error('[daon-init] initHarnessManual failed:', e); }
  try { initHarnessSkillPicker(); } catch (e) { console.error('[daon-init] initHarnessSkillPicker failed:', e); }
  try { initSpeak(); } catch (e) { console.error('[daon-init] initSpeak failed:', e); }
});

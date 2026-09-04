// ═══════════════════════════════════════════════════════════════
// browser_ai.js — DAON IDE Browser View + BrowserAI Skill Recommendation
// ═══════════════════════════════════════════════════════════════

// ── State ──
var _browserCurrentUrl = '';
var _browserViewVisible = false;
var _browserHistory = [];       // {url, title} stack
var _browserHistoryIdx = -1;    // current position in stack
var _browserMode = 'grid';      // 'grid' (mini view overview) | 'focus' (full browser control)
var _gridPollTimer = null;

// ── Domain → Skill Mapping (Phase 2) ──
var DOMAIN_SKILL_MAP = {
  'github.com': [
    { name: 'github-pr-workflow', icon: '🔀', desc: 'PR 생성 및 리뷰 워크플로우' },
    { name: 'github-code-review', icon: '🔍', desc: '코드 리뷰 및 피드백' },
    { name: 'github-issues', icon: '📋', desc: 'Issue 분석 및 작성' },
  ],
  'notion.so': [
    { name: 'research', icon: '🔬', desc: '리서치 및 정보 수집' },
    { name: 'writing', icon: '✍️', desc: '글쓰기 및 문서 작성' },
    { name: 'summarizer', icon: '📝', desc: '페이지 요약' },
  ],
  'youtube.com': [
    { name: 'youtube-production', icon: '🎬', desc: '영상 기획 및 제작 가이드' },
    { name: 'summarizer', icon: '📝', desc: '영상 내용 요약' },
  ],
  'figma.com': [
    { name: 'css-designer', icon: '🎨', desc: 'CSS 디자인 변환' },
    { name: 'html-generator', icon: '🏗️', desc: 'HTML 코드 생성' },
  ],
  'stackoverflow.com': [
    { name: 'debugger', icon: '🐛', desc: '코드 디버깅 지원' },
    { name: 'html-generator', icon: '🏗️', desc: '솔루션 코드 생성' },
  ],
  'localhost': [
    { name: 'debugger', icon: '🐛', desc: '로컬 앱 디버깅' },
    { name: 'sherlock-qa', icon: '🔎', desc: 'QA 및 품질 점검' },
    { name: 'dashboard', icon: '📊', desc: '대시보드 분석' },
  ],
  'docs.google.com': [
    { name: 'research', icon: '🔬', desc: '리서치 보조' },
    { name: 'writing', icon: '✍️', desc: '문서 작성 도우미' },
  ],
  'vercel.app': [
    { name: 'landing-page', icon: '🚀', desc: '랜딩페이지 분석/생성' },
    { name: 'css-designer', icon: '🎨', desc: 'CSS 디자인 분석' },
  ],
  'codepen.io': [
    { name: 'html-generator', icon: '🏗️', desc: 'HTML 코드 생성' },
    { name: 'css-designer', icon: '🎨', desc: 'CSS 스타일링' },
    { name: 'gsap-animator', icon: '✨', desc: 'GSAP 애니메이션' },
  ],
};

// ── Domain → Suggested Actions Mapping ──
var DOMAIN_ACTIONS_MAP = {
  'github.com': [
    { icon: '🔀', label: 'PR 만들기', prompt: '이 저장소에서 PR을 만들어줘' },
    { icon: '📋', label: 'Issue 작성', prompt: '이 저장소에 Issue를 작성해줘' },
    { icon: '📖', label: 'README 요약', prompt: '이 저장소의 README를 요약해줘' },
    { icon: '🔍', label: '코드 리뷰', prompt: '이 PR의 코드를 리뷰해줘' },
  ],
  'youtube.com': [
    { icon: '📝', label: '영상 요약', prompt: '이 유튜브 영상을 요약해줘' },
    { icon: '📋', label: '스크립트 추출', prompt: '이 영상의 스크립트를 추출해줘' },
    { icon: '🎬', label: '쇼츠 기획', prompt: '이 영상을 쇼츠로 기획해줘' },
  ],
  'notion.so': [
    { icon: '📝', label: '페이지 요약', prompt: '이 Notion 페이지를 요약해줘' },
    { icon: '✍️', label: '내용 보강', prompt: '이 Notion 문서의 내용을 보강해줘' },
  ],
  'figma.com': [
    { icon: '🏗️', label: 'HTML 변환', prompt: '이 Figma 디자인을 HTML로 변환해줘' },
    { icon: '🎨', label: 'CSS 추출', prompt: '이 디자인의 CSS를 추출해줘' },
  ],
};

// ═══════════════════════════════════════════
// Browser View Toggle (canvas 영역)
// ═══════════════════════════════════════════
function toggleBrowserView() {
  _browserViewVisible = !_browserViewVisible;
  var browserWrap = document.getElementById('browserViewWrap');
  var monacoContainer = document.getElementById('monacoContainer');
  var imgPreview = document.getElementById('imgPreviewContainer');
  var mdPreview = document.getElementById('mdPreviewContainer');
  var htmlPreview = document.getElementById('htmlPreviewContainer');
  var welcomeCanvas = document.getElementById('welcomeCanvas');
  var harnessOverlay = document.getElementById('harnessManualOverlay');
  var toggleBtn = document.getElementById('toggleBrowserBtn');

  if (_browserViewVisible) {
    // Hide other canvas content
    if (monacoContainer) monacoContainer.style.display = 'none';
    if (imgPreview) imgPreview.style.display = 'none';
    if (mdPreview) mdPreview.style.display = 'none';
    if (htmlPreview) htmlPreview.style.display = 'none';
    if (welcomeCanvas) welcomeCanvas.style.display = 'none';
    if (harnessOverlay) harnessOverlay.style.display = 'none';
    // Show browser view
    if (browserWrap) browserWrap.style.display = 'flex';
    if (toggleBtn) toggleBtn.classList.add('active');

    // Switch to active browser mode (grid or focus)
    setBrowserMode(_browserMode || 'grid');

    // Show default BrowserAI recommendations
    if (typeof onBrowserUrlChange === 'function') {
      onBrowserUrlChange(_browserCurrentUrl || '');
    }
  } else {
    // Hide browser view
    if (browserWrap) browserWrap.style.display = 'none';
    if (toggleBtn) toggleBtn.classList.remove('active');
    if (_gridPollTimer) {
      clearInterval(_gridPollTimer);
      _gridPollTimer = null;
    }
    // Restore monaco (default editor view)
    if (monacoContainer) monacoContainer.style.display = 'flex';
    if (htmlPreview) htmlPreview.style.display = '';
    if (imgPreview) imgPreview.style.display = '';
    if (mdPreview) mdPreview.style.display = '';
    if (welcomeCanvas) {
      var activeFile = document.getElementById('activeFilePath');
      welcomeCanvas.style.display = (activeFile && activeFile.textContent !== '파일을 탐색기에서 선택하세요') ? 'none' : 'flex';
    }
    // Hide Electron browser overlay
    if (window.electronAPI) {
      window.electronAPI.setVisibility(false);
    }
  }
}

// ═══════════════════════════════════════════
// Browser Navigation Controls
// ═══════════════════════════════════════════
async function browserGoToAddress() {
  var input = document.getElementById('browserCanvasUrlInput') || document.getElementById('browserUrlInput');
  var url = (input ? (input.value || '').trim() : '');
  console.log('[BrowserAI] browserGoToAddress() 호출됨, 입력값:', url);
  if (!url) return;

  // Auto-add protocol
  if (!/^https?:\/\//i.test(url)) {
    url = 'https://' + url;
    input.value = url;
  }
  console.log('[BrowserAI] 최종 URL:', url, '| electronAPI 존재:', !!window.electronAPI);

  _browserCurrentUrl = url;

  // Push to history stack (skip duplicates at the top)
  if (_browserHistoryIdx < 0 || _browserHistory[_browserHistoryIdx].url !== url) {
    // Truncate forward history if navigating from middle of stack
    if (_browserHistoryIdx < _browserHistory.length - 1) {
      _browserHistory = _browserHistory.slice(0, _browserHistoryIdx + 1);
    }
    _browserHistory.push({ url: url, title: url });
    _browserHistoryIdx = _browserHistory.length - 1;
  }

  var browserWrap = document.getElementById('browserViewWrap');
  var frame = document.getElementById('browserFrame');
  var placeholder = document.getElementById('browserPlaceholder');
  var errorDiv = document.getElementById('browserFrameError');
  if (errorDiv) errorDiv.style.display = 'none';

  // Show loading state
  if (placeholder) {
    placeholder.style.display = 'flex';
    placeholder.innerHTML = '<div class="browser-placeholder-icon">⏳</div>' +
      '<div class="browser-placeholder-text">로딩 중... ' + _escBai(url) + '</div>';
  }

  // Ensure browser view wrap is visible before navigating
  if (browserWrap) {
    var wrapDisplay = browserWrap.style.display;
    console.log('[BrowserAI] browserViewWrap display:', wrapDisplay);
    if (wrapDisplay === 'none' || !wrapDisplay) {
      // Auto-show browser view if it was hidden
      browserWrap.style.display = 'flex';
      var monacoContainer = document.getElementById('monacoContainer');
      var welcomeCanvas = document.getElementById('welcomeCanvas');
      if (monacoContainer) monacoContainer.style.display = 'none';
      if (welcomeCanvas) welcomeCanvas.style.display = 'none';
      _browserViewVisible = true;
      var toggleBtn = document.getElementById('toggleBrowserBtn');
      if (toggleBtn) toggleBtn.classList.add('active');
    }
  }

  if (window.electronAPI) {
    console.log('[BrowserAI] Electron 모드: IPC navigate 호출');
    if (frame) frame.style.display = 'none';
    if (placeholder) placeholder.style.display = 'none';

    // Ensure we have a valid active tab ID
    if (!_activeTabId || !_browserTabs.some(function (t) { return t.id === _activeTabId; })) {
      var newTabId = 'tab' + Date.now();
      window.electronAPI.newTab(newTabId, url);
      _activeTabId = newTabId;
    } else {
      window.electronAPI.navigate(_activeTabId, url);
    }

    // Switch to focus mode to display the website in center
    setBrowserMode('focus', _activeTabId);

    // Sync URL to backend only — AI shares the same CDP-connected WebContentsView page.
    // Playwright connects via CDP and sees the same page the user sees.
    fetch('/api/browser/sync_url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (r) { return r.json(); }).then(function (data) {
      console.log('[BrowserAI] 백엔드 URL 동기화 응답:', data);
      if (data.ok) {
        _browserCurrentUrl = data.url || url;
        onBrowserUrlChange(_browserCurrentUrl);
      }
    }).catch(function (e) {
      console.error('[BrowserAI] URL 동기화 실패:', e);
      // Still update local state even if sync fails
      _browserCurrentUrl = url;
      onBrowserUrlChange(_browserCurrentUrl);
    });
  } else {
    console.log('[BrowserAI] 비-Electron 모드: iframe proxy 사용');
    // Non-Electron mode: route through server-side proxy to bypass X-Frame-Options
    var _iframeLoadTimeout = null;
    if (frame) {
      frame.style.display = '';
      frame.removeAttribute('sandbox');
      frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation');
      frame.onerror = function (err) {
        console.error('[BrowserAI] iframe 로드 오류:', err);
        if (_iframeLoadTimeout) { clearTimeout(_iframeLoadTimeout); _iframeLoadTimeout = null; }
        if (errorDiv) errorDiv.style.display = 'flex';
        if (placeholder) placeholder.style.display = 'none';
      };
      frame.onload = function () {
        console.log('[BrowserAI] iframe 로드 완료:', url);
        if (_iframeLoadTimeout) { clearTimeout(_iframeLoadTimeout); _iframeLoadTimeout = null; }
        if (placeholder) placeholder.style.display = 'none';
        if (errorDiv) errorDiv.style.display = 'none';
      };
      frame.src = _proxyUrl(url);

      // Timeout: if iframe doesn't load in 10 seconds, show error
      _iframeLoadTimeout = setTimeout(function () {
        if (placeholder && placeholder.style.display !== 'none') {
          placeholder.innerHTML = '<div class="browser-placeholder-icon">⚠️</div>' +
            '<div class="browser-placeholder-text">페이지 로드 시간 초과. 사이트가 iframe 표시를 차단했을 수 있습니다.</div>';
        }
      }, 10000);
    }

    // Non-Electron: just sync URL to backend (don't launch Playwright browser)
    // Playwright will connect on-demand when AI needs it (snapshot, click, etc.)
    try {
      console.log('[BrowserAI] 백엔드 URL 동기화 (sync_url)...');
      const response = await fetch('/api/browser/sync_url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      });
      const data = await response.json();
      console.log('[BrowserAI] 백엔드 URL 동기화 응답:', data);
      _browserCurrentUrl = data.url || url;
      onBrowserUrlChange(_browserCurrentUrl);
    } catch (e) {
      console.error('[BrowserAI] 백엔드 URL 동기화 실패:', e);
      _browserCurrentUrl = url;
      onBrowserUrlChange(_browserCurrentUrl);
    }
  }
}

// ── Proxy helper: bypass X-Frame-Options in non-Electron (dev) mode ──
function _proxyUrl(url) {
  // Electron mode: WebContentsView handles it natively (no iframe restriction)
  if (window.electronAPI) return url;
  // Dev mode: route through server-side proxy that strips X-Frame-Options / CSP
  return '/api/browser/proxy?url=' + encodeURIComponent(url);
}

function _navigateIframeTo(url) {
  var frame = document.getElementById('browserFrame');
  var placeholder = document.getElementById('browserPlaceholder');
  var errorDiv = document.getElementById('browserFrameError');
  if (errorDiv) errorDiv.style.display = 'none';
  if (placeholder) {
    placeholder.style.display = 'flex';
    placeholder.innerHTML = '<div class="browser-placeholder-icon">⏳</div>' +
      '<div class="browser-placeholder-text">로딩 중... ' + _escBai(url) + '</div>';
  }
  if (frame) {
    frame.style.display = '';
    frame.src = _proxyUrl(url);
    frame.onload = function () {
      if (placeholder) placeholder.style.display = 'none';
    };
    frame.onerror = function () {
      if (errorDiv) errorDiv.style.display = 'flex';
      if (placeholder) placeholder.style.display = 'none';
    };
  }
  _browserCurrentUrl = url;
  // Sync address bar
  var input = document.getElementById('browserCanvasUrlInput');
  if (input && input.value !== url) input.value = url;
}

function browserGoBack() {
  if (window.electronAPI) {
    window.electronAPI.goBack(_activeTabId);
  } else if (_browserHistoryIdx > 0) {
    _browserHistoryIdx--;
    var entry = _browserHistory[_browserHistoryIdx];
    _navigateIframeTo(entry.url);
  }
}

// Aliases matching HTML onclick handlers
function browserBack() { browserGoBack(); }
function browserForward() { browserGoForward(); }
function browserReload() { browserRefresh(); }

function browserGoForward() {
  if (window.electronAPI) {
    window.electronAPI.goForward(_activeTabId);
  } else if (_browserHistoryIdx < _browserHistory.length - 1) {
    _browserHistoryIdx++;
    var entry = _browserHistory[_browserHistoryIdx];
    _navigateIframeTo(entry.url);
  }
}

function browserRefresh() {
  if (window.electronAPI) {
    window.electronAPI.reload(_activeTabId);
  } else {
    var frame = document.getElementById('browserFrame');
    if (frame && _browserCurrentUrl) {
      // Force reload by resetting src through proxy
      frame.src = '';
      setTimeout(function () { frame.src = _proxyUrl(_browserCurrentUrl); }, 50);
    } else if (_browserCurrentUrl) {
      _navigateIframeTo(_browserCurrentUrl);
    }
  }
}

/**
 * Close the current page — clear the browser view back to placeholder.
 * (Electron: navigate to about:blank, iframe: clear src)
 */
function browserClosePage() {
  if (window.electronAPI) {
    window.electronAPI.navigate(_activeTabId, 'about:blank');
    // CRITICAL: Hide the Electron WebContentsView overlay to prevent white screen
    window.electronAPI.setVisibility(false);
  }
  // Clear iframe
  var frame = document.getElementById('browserFrame');
  var placeholder = document.getElementById('browserPlaceholder');
  var errorDiv = document.getElementById('browserFrameError');
  if (errorDiv) errorDiv.style.display = 'none';
  if (frame) {
    frame.style.display = 'none';
    frame.src = '';
  }
  if (placeholder) {
    placeholder.style.display = 'flex';
    placeholder.innerHTML = '<div class="browser-placeholder-icon">🌐</div>' +
      '<div class="browser-placeholder-text">주소를 입력하고 Enter 를 누르면 브라우저가 열립니다</div>';
  }
  // Reset state
  _browserCurrentUrl = '';
  _browserHistory = [];
  _browserHistoryIdx = -1;
  // Clear address bar
  var input = document.getElementById('browserCanvasUrlInput') || document.getElementById('browserUrlInput');
  if (input) input.value = '';

  // Sync backend
  fetch('/api/browser/sync_url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: 'about:blank' })
  }).catch(function () { /* ignore */ });
  // Refresh BrowserAI recommendations
  if (typeof onBrowserUrlChange === 'function') {
    onBrowserUrlChange('');
  }
}

// ═══════════════════════════════════════════
// BrowserAI: URL Change → Skill Recommendation
// ═══════════════════════════════════════════
function onBrowserUrlChange(url) {
  try {
    var parsed = new URL(url);
    var hostname = parsed.hostname;
    var displayUrl = hostname + parsed.pathname;

    // Update context display
    var statusEl = document.getElementById('browseraiContext');
    if (statusEl) {
      statusEl.innerHTML = '<div class="browserai-domain" style="color:var(--text); font-weight:500;">🔗 ' + _escBai(hostname) + ' 감지됨</div>' +
        '<div class="browserai-page-title" style="font-size:11px; margin-top:2px; color:var(--muted);">' + _escBai(displayUrl) + '</div>';
    }

    // Find matching skills
    var skills = findMatchingSkills(hostname);
    renderBrowserAISkills(skills);

    // Find matching actions
    var actions = findMatchingActions(hostname);
    renderBrowserAIActions(actions);

  } catch (e) {
    // Invalid URL — show default recommendations
    var statusEl = document.getElementById('browseraiContext');
    if (statusEl && url) {
      statusEl.innerHTML = '<div class="browserai-domain" style="color:var(--text); font-weight:500;">🌐 URL 로드 중...</div>';
    }
    if (!url) {
      // Show default welcome state in sidebar
      var skills = findMatchingSkills(null);
      renderBrowserAISkills(skills);
      var actions = findMatchingActions(null);
      renderBrowserAIActions(actions);
    }
  }
}

function findMatchingSkills(hostname) {
  if (!hostname) {
    return [
      { name: 'summarizer', icon: '📝', desc: '이 페이지를 요약합니다' },
      { name: 'research', icon: '🔬', desc: '페이지 내용을 분석합니다' },
    ];
  }

  // Exact match first
  if (DOMAIN_SKILL_MAP[hostname]) {
    return DOMAIN_SKILL_MAP[hostname];
  }

  // Partial match
  for (var domain in DOMAIN_SKILL_MAP) {
    if (hostname.indexOf(domain) !== -1 || domain.indexOf(hostname) !== -1) {
      return DOMAIN_SKILL_MAP[domain];
    }
  }

  // Check for localhost
  if (hostname === '127.0.0.1' || hostname.startsWith('localhost')) {
    return DOMAIN_SKILL_MAP['localhost'] || [];
  }

  // Default: general skills
  return [
    { name: 'summarizer', icon: '📝', desc: '이 페이지를 요약합니다' },
    { name: 'research', icon: '🔬', desc: '페이지 내용을 분석합니다' },
  ];
}

function findMatchingActions(hostname) {
  if (!hostname) {
    return [
      { icon: '📝', label: '페이지 요약', prompt: '이 페이지를 요약해줘: ' + (_browserCurrentUrl || '') },
      { icon: '🔍', label: '페이지 분석', prompt: '이 페이지를 분석해줘: ' + (_browserCurrentUrl || '') },
      { icon: '📸', label: '스크린샷 + 리뷰', prompt: '이 페이지의 디자인을 리뷰해줘: ' + (_browserCurrentUrl || '') },
    ];
  }

  for (var domain in DOMAIN_ACTIONS_MAP) {
    if (hostname.indexOf(domain) !== -1 || domain.indexOf(hostname) !== -1) {
      return DOMAIN_ACTIONS_MAP[domain];
    }
  }

  // Default actions
  return [
    { icon: '📝', label: '페이지 요약', prompt: '이 페이지를 요약해줘: ' + (_browserCurrentUrl || '') },
    { icon: '🔍', label: '페이지 분석', prompt: '이 페이지를 분석해줘: ' + (_browserCurrentUrl || '') },
    { icon: '📸', label: '스크린샷 + 리뷰', prompt: '이 페이지의 디자인을 리뷰해줘: ' + (_browserCurrentUrl || '') },
  ];
}

// ═══════════════════════════════════════════
// BrowserAI: Render Skills & Actions
// ═══════════════════════════════════════════
function renderBrowserAISkills(skills) {
  var container = document.getElementById('browseraiSkills');
  var title = document.getElementById('browseraiSkillsSection');
  if (!container) return;

  if (title) title.style.display = (skills && skills.length) ? 'block' : 'none';

  if (!skills || !skills.length) {
    container.innerHTML = '<div class="browserai-empty"><div class="bai-empty-icon">🌐</div><div class="bai-empty-text">이 사이트에 매칭되는 스킬이 없습니다</div></div>';
    return;
  }

  var html = '';
  skills.forEach(function (s) {
    html += '<div class="browserai-skill-card" onclick="executeBrowserAISkill(\'' + _escBai(s.name) + '\')">'
      + '<div class="bsk-icon">' + s.icon + '</div>'
      + '<div class="bsk-info">'
      + '<div class="bsk-name">' + _escBai(s.name) + '</div>'
      + '<div class="bsk-desc">' + _escBai(s.desc) + '</div>'
      + '</div>'
      + '<button class="bsk-run" onclick="event.stopPropagation();executeBrowserAISkill(\'' + _escBai(s.name) + '\')">실행</button>'
      + '</div>';
  });
  container.innerHTML = html;
}

function renderBrowserAIActions(actions) {
  var container = document.getElementById('browseraiActions');
  var title = document.getElementById('browseraiActionsSection');
  if (!container) return;

  if (title) title.style.display = (actions && actions.length) ? 'block' : 'none';

  if (!actions || !actions.length) {
    container.innerHTML = '<div style="font-size:11px;color:var(--muted)">추천 행동이 없습니다</div>';
    return;
  }

  var html = '';
  actions.forEach(function (a) {
    html += '<button class="browserai-action-btn" onclick="executeBrowserAIAction(\'' + _escBai(a.prompt) + '\')">'
      + '<span class="bact-icon">' + a.icon + '</span>'
      + _escBai(a.label)
      + '</button>';
  });
  container.innerHTML = html;
}

// ═══════════════════════════════════════════
// BrowserAI: Execute Skill / Action
// ═══════════════════════════════════════════
function executeBrowserAISkill(skillName) {
  // DAON IDE: 채팅 프롬프트 입력창(#promptInput)에 텍스트 삽입
  var promptInput = document.getElementById('promptInput');
  if (promptInput) {
    var prompt = '[System: 당신은 브라우저 제어 도구(Playwright, Puppeteer 등)를 사용하여 아래 URL에 직접 접속하고 화면(DOM)을 확인 및 클릭할 수 있습니다. 반드시 도구를 사용해 페이지를 불러온 후 답변하세요.]\n\n'
      + 'URL: ' + _browserCurrentUrl + '\n'
      + '요청: ' + skillName + ' 스킬을 사용해서 이 페이지를 분석해줘.';
    promptInput.value = prompt;
    promptInput.dispatchEvent(new Event('input'));
    promptInput.focus();
  }
}

function executeBrowserAIAction(promptText) {
  // DAON IDE: 채팅 프롬프트 입력창(#promptInput)에 텍스트 삽입
  var promptInput = document.getElementById('promptInput');
  if (promptInput) {
    var fullPrompt = '[System: 당신은 브라우저 제어 도구(Playwright, Puppeteer 등)를 사용하여 아래 URL에 직접 접속하고 화면(DOM)을 확인 및 클릭할 수 있습니다. 반드시 도구를 사용해 페이지를 불러온 후 답변하세요.]\n\n'
      + 'URL: ' + _browserCurrentUrl + '\n'
      + '요청: ' + promptText;
    promptInput.value = fullPrompt;
    promptInput.dispatchEvent(new Event('input'));
    promptInput.focus();
  }
}

// ── Helpers ──
function _escBai(str) {
  if (!str) return '';
  return str.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"');
}

// ═══════════════════════════════════════════
// Initialization
// ═══════════════════════════════════════════
(function _initBrowserAI() {
  // 이미 DOM이 준비되었는지 확인 (동기 스크립트이므로 DOMContentLoaded는 이미 발생함)
  function _setup() {
    // 캔버스 내 브라우저 주소창 (기본)
    var addressBar = document.getElementById('browserCanvasUrlInput');
    if (addressBar) {
      addressBar.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          browserGoToAddress();
        }
      });
    }
    // 사이드 패널 브라우저 주소창 (fallback)
    var panelBar = document.getElementById('browserUrlInput');
    if (panelBar && panelBar !== addressBar) {
      panelBar.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          browserNavigate();  // 사이드 패널 브라우저는 별도 함수 사용
        }
      });
    }

    // Toggle browser view button
    var toggleBtn = document.getElementById('toggleBrowserBtn');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', toggleBrowserView);
    }

    // Show default BrowserAI recommendations on init
    setTimeout(function () {
      if (typeof onBrowserUrlChange === 'function') {
        onBrowserUrlChange('');
      }
    }, 100);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setup);
  } else {
    // DOM already ready — run immediately
    _setup();
  }
})();

// ═══════════════════════════════════════════
// Electron IPC Bounds Sync
// ═══════════════════════════════════════════
// ═══════════════════════════════════════════
// Electron IPC Bounds Sync
// ═══════════════════════════════════════════
function syncElectronBrowserBounds() {
  if (!window.electronAPI) {
    return;
  }
  // Only show Electron WebContentsView when browser view is visible AND in focus mode
  if (!_browserViewVisible || _browserMode !== 'focus') {
    window.electronAPI.setVisibility(false);
    return;
  }

  var container = document.getElementById('browserFrameWrap');
  if (container && container.offsetParent !== null) {
    var rect = container.getBoundingClientRect();
    window.electronAPI.setBounds({
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    });
    window.electronAPI.setVisibility(true);
  } else {
    window.electronAPI.setVisibility(false);
  }
}

window.addEventListener('resize', syncElectronBrowserBounds);
// Also sync periodically for dynamic layout changes
setInterval(syncElectronBrowserBounds, 500);

// ═══════════════════════════════════════════
// Auto-open browser view when AI triggers navigate
// ═══════════════════════════════════════════
(function _autoOpenBrowserPoll() {
  if (!window.electronAPI) return; // Electron-only feature

  var _lastPending = '';
  var _lastPendingTs = 0;
  var _lastAgentUrl = '';
  setInterval(function () {
    fetch('/api/browser/status')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var agentUrl = data.url || '';
        if (agentUrl && agentUrl !== _lastAgentUrl) {
          var hadPrev = !!_lastAgentUrl;
          _lastAgentUrl = agentUrl;
          if (hadPrev) {
            for (var ti = 0; ti < _browserTabs.length; ti++) {
              if (_browserTabs[ti].url === agentUrl && !_browserTabs[ti].active) {
                console.log('[BrowserAI] Agent switched tab →', _browserTabs[ti].id, agentUrl);
                browserSwitchTab(_browserTabs[ti].id);
                break;
              }
            }
          }
        }

        var pending = data.pending_url || '';
        if (pending) {
          if (pending !== _lastPending) {
            _lastPending = pending;
            _lastPendingTs = Date.now();
            console.log('[BrowserAI] AI requested navigate to:', pending, '- auto-opening browser view');
            if (!_browserViewVisible) {
              toggleBrowserView();
            }
            // If in grid mode, switch to focus mode to see the agent's work
            setBrowserMode('focus');
            var input = document.getElementById('browserCanvasUrlInput') || document.getElementById('browserUrlInput');
            if (input) input.value = pending;
            browserGoToAddress();
          } else if (!_browserViewVisible) {
            console.log('[BrowserAI] Same pending URL, browser view hidden — restoring view');
            toggleBrowserView();
          }
        } else {
          _lastPending = '';
          _lastPendingTs = 0;
        }
      })
      .catch(function () { /* ignore poll errors */ });
  }, 3000);
})();

// ═══════════════════════════════════════════
// Browser Mini View Grid & Focus Mode Controller
// ═══════════════════════════════════════════

function setBrowserMode(mode, targetTabId) {
  _browserMode = mode || 'grid';
  var gridContainer = document.getElementById('browserGridContainer');
  var focusContainer = document.getElementById('browserFocusContainer');
  var btnGrid = document.getElementById('btnBrowserGridMode');
  var btnFocus = document.getElementById('btnBrowserFocusMode');

  if (btnGrid) btnGrid.classList.toggle('active', _browserMode === 'grid');
  if (btnFocus) btnFocus.classList.toggle('active', _browserMode === 'focus');

  if (_browserMode === 'grid') {
    if (focusContainer) focusContainer.style.display = 'none';
    if (gridContainer) gridContainer.style.display = 'flex';
    // Hide Electron WebContentsView overlay so HTML cards are clickable & visible
    if (window.electronAPI) {
      window.electronAPI.setVisibility(false);
    }
    fetchBrowserGrid();
    if (!_gridPollTimer) {
      _gridPollTimer = setInterval(fetchBrowserGrid, 2500);
    }
  } else {
    // Focus Mode (Full Browser in center)
    if (_gridPollTimer) {
      clearInterval(_gridPollTimer);
      _gridPollTimer = null;
    }
    if (gridContainer) gridContainer.style.display = 'none';
    if (focusContainer) focusContainer.style.display = 'flex';
    if (targetTabId) {
      browserSwitchTab(targetTabId);
    }
    // Let DOM layout update, then sync bounds & attach Electron WebContentsView
    setTimeout(function() {
      syncElectronBrowserBounds();
    }, 100);
  }
}

async function fetchBrowserGrid() {
  var cardsContainer = document.getElementById('browserGridCards');
  if (!cardsContainer) return;

  try {
    var res = await fetch('/api/browser/grid');
    var data = await res.json();
    var tabs = (data && data.tabs) || [];

    // Fallback: If backend CDP returns empty list but Electron has open tabs, render _browserTabs
    if (tabs.length === 0 && _browserTabs && _browserTabs.length > 0) {
      tabs = _browserTabs.map(function(t, idx) {
        return {
          id: t.id,
          index: idx,
          url: t.url || 'about:blank',
          title: t.title || t.url || ('브라우저 ' + (idx + 1)),
          active: !!t.active,
          session_id: ''
        };
      });
    }

    if (tabs.length === 0) {
      cardsContainer.innerHTML =
        '<div class="browser-grid-empty">' +
          '<div class="bge-icon">🌐</div>' +
          '<div class="bge-title">현재 열려있는 에이전트 브라우저가 없습니다</div>' +
          '<div class="bge-sub">새 에이전트 브라우저를 열거나, 채팅창에서 에이전트에게 작업을 지시하세요.</div>' +
          '<button class="bgh-btn bgh-btn-primary" onclick="browserNewTab()" style="margin-top:12px;">＋ 새 브라우저 열기</button>' +
        '</div>';
      return;
    }

    var html = '';
    tabs.forEach(function(tab, idx) {
      var tabId = tab.id || ('tab' + (idx + 1));
      var title = tab.title || tab.url || ('브라우저 ' + (idx + 1));
      var url = tab.url || 'about:blank';
      var sessionLabel = tab.session_id ? ('Agent: ' + tab.session_id.substring(0, 8)) : ('Session ' + (idx + 1));
      var isBlank = (!tab.url || tab.url === 'about:blank');
      var thumbImg = tab.thumbnail ?
        ('<img class="bmc-thumb-img" src="' + tab.thumbnail + '" alt="preview" />') :
        ('<div class="bmc-thumb-blank"><span>' + (isBlank ? '📄 빈 페이지' : '⏳ 미리보기 준비 중...') + '</span></div>');
      var activeClass = tab.active ? ' is-active' : '';

      html +=
        '<div class="browser-mini-card' + activeClass + '" onclick="setBrowserMode(\'focus\', \'' + _escTab(tabId) + '\')">' +
          '<div class="bmc-header">' +
            '<span class="bmc-session-badge">' + _escTab(sessionLabel) + '</span>' +
            '<span class="bmc-status-dot" title="' + (tab.active ? '현재 활성' : '백그라운드 실행 중') + '"></span>' +
            '<button class="bmc-close-btn" onclick="event.stopPropagation();browserCloseTab(\'' + _escTab(tabId) + '\');setTimeout(fetchBrowserGrid, 350);" title="탭 닫기">✕</button>' +
          '</div>' +
          '<div class="bmc-thumb-wrap">' +
            thumbImg +
            '<div class="bmc-hover-overlay">' +
              '<span class="bmc-overlay-text">🔍 클릭하여 전체 화면 제어</span>' +
            '</div>' +
          '</div>' +
          '<div class="bmc-footer">' +
            '<div class="bmc-title" title="' + _escTab(title) + '">' + _escTab(title) + '</div>' +
            '<div class="bmc-url" title="' + _escTab(url) + '">' + _escTab(url) + '</div>' +
          '</div>' +
        '</div>';
    });

    cardsContainer.innerHTML = html;
  } catch (e) {
    console.debug('[BrowserAI] fetchBrowserGrid error:', e);
  }
}

// ═══════════════════════════════════════════
// Tab management (2026-08-27)
// Electron TabManager와 동기화되는 다중 탭 UI.
// _activeTabId: 현재 활성 탭
// ═══════════════════════════════════════════
var _browserTabs = [];
var _activeTabId = 'tab1';

function _escTab(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '\x26amp;')
    .replace(/</g, '\x26lt;')
    .replace(/>/g, '\x26gt;')
    .replace(/"/g, '\x26quot;')
    .replace(/'/g, '\x26#39;');
}

function browserNewTab(initialUrl) {
  if (!window.electronAPI) return;
  var id = 'tab' + Date.now();
  var url = initialUrl || 'about:blank';
  window.electronAPI.newTab(id, url);
  _activeTabId = id;

  if (_browserMode === 'grid') {
    window.electronAPI.setVisibility(false);
    setTimeout(fetchBrowserGrid, 300);
  } else {
    setBrowserMode('focus', id);
  }
}

function browserSwitchTab(id) {
  if (!window.electronAPI || !id) return;
  _activeTabId = id;
  window.electronAPI.switchTab(id);
  // Also notify backend to focus this tab
  fetch('/api/browser/focus', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tab_id: id })
  }).catch(function() { /* ignore */ });

  // Update URL input if url is known
  for (var i = 0; i < _browserTabs.length; i++) {
    if (_browserTabs[i].id === id && _browserTabs[i].url) {
      _browserCurrentUrl = _browserTabs[i].url;
      var input = document.getElementById('browserCanvasUrlInput');
      if (input) input.value = _browserCurrentUrl;
      break;
    }
  }

  if (_browserMode === 'focus') {
    syncElectronBrowserBounds();
  } else {
    window.electronAPI.setVisibility(false);
  }
}

function browserCloseTab(id) {
  if (!window.electronAPI || !id) return;
  window.electronAPI.closeTab(id);
  fetch('/api/browser/close_tab', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tab_id: id })
  }).catch(function() { /* ignore */ });

  _browserTabs = _browserTabs.filter(function (t) { return t.id !== id; });
  if (_activeTabId === id) {
    _activeTabId = _browserTabs.length > 0 ? _browserTabs[0].id : null;
  }

  if (_browserMode === 'grid') {
    window.electronAPI.setVisibility(false);
    setTimeout(fetchBrowserGrid, 300);
  } else {
    if (_activeTabId) {
      browserSwitchTab(_activeTabId);
    } else {
      setBrowserMode('grid');
    }
  }
}

function renderBrowserTabs() {
  var wrap = document.getElementById('browserTabs');
  if (!wrap) return;
  var html = '';
  for (var i = 0; i < _browserTabs.length; i++) {
    var t = _browserTabs[i];
    html += '<div class="browser-tab' + (t.active ? ' active' : '') + '"'
      + ' onclick="browserSwitchTab(\'' + _escTab(t.id) + '\')"'
      + ' title="' + _escTab(t.title || t.id) + '">'
      + '<span class="browser-tab-title">' + _escTab(t.title || t.id) + '</span>'
      + '<span class="browser-tab-close" onclick="event.stopPropagation();browserCloseTab(\'' + _escTab(t.id) + '\')">×</span>'
      + '</div>';
  }
  wrap.innerHTML = html;
}

// Electron → 탭 목록 수신 (생성/전환/닫기/제목 변경 시 브로드캐스트)
if (window.electronAPI && window.electronAPI.onTabsUpdated) {
  window.electronAPI.onTabsUpdated(function (tabs) {
    _browserTabs = tabs || [];
    var foundActive = false;
    for (var i = 0; i < _browserTabs.length; i++) {
      if (_browserTabs[i].active) {
        _activeTabId = _browserTabs[i].id;
        foundActive = true;
        break;
      }
    }
    if (!foundActive && _browserTabs.length > 0) {
      _activeTabId = _browserTabs[0].id;
    } else if (_browserTabs.length === 0) {
      _activeTabId = null;
    }

    renderBrowserTabs();

    if (_browserMode === 'grid') {
      window.electronAPI.setVisibility(false);
      fetchBrowserGrid();
    } else if (_browserMode === 'focus') {
      syncElectronBrowserBounds();
    }
  });
}

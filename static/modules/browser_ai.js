// ═══════════════════════════════════════════════════════════════
// browser_ai.js — DAON IDE Browser View + BrowserAI Skill Recommendation
// ═══════════════════════════════════════════════════════════════

// ── State ──
var _browserCurrentUrl = '';
var _browserViewVisible = false;
var _browserHistory = [];       // {url, title} stack
var _browserHistoryIdx = -1;    // current position in stack

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
    // Sync Electron browser bounds
    syncElectronBrowserBounds();
    // Show default BrowserAI recommendations
    if (typeof onBrowserUrlChange === 'function') {
      onBrowserUrlChange(_browserCurrentUrl || '');
    }
  } else {
    // Hide browser view
    if (browserWrap) browserWrap.style.display = 'none';
    if (toggleBtn) toggleBtn.classList.remove('active');
    // Restore monaco (default editor view)
    if (monacoContainer) monacoContainer.style.display = 'flex';
    if (welcomeCanvas) {
      // Show welcome only if no file is open
      var activeFile = document.getElementById('activeFilePath');
      welcomeCanvas.style.display = (activeFile && activeFile.textContent !== '파일을 탐색기에서 선택하세요') ? 'none' : 'flex';
    }
    // Hide Electron browser
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
    // Electron mode: navigate via IPC (WebContentsView shared with AI)
    if (frame) frame.style.display = 'none';
    if (placeholder) placeholder.style.display = 'none';
    window.electronAPI.navigate('tab1', url);
    // bounds sync after a short delay to let WebContentsView initialize
    setTimeout(function () {
      syncElectronBrowserBounds();
    }, 150);

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
    window.electronAPI.goBack('tab1');
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
    window.electronAPI.goForward('tab1');
  } else if (_browserHistoryIdx < _browserHistory.length - 1) {
    _browserHistoryIdx++;
    var entry = _browserHistory[_browserHistoryIdx];
    _navigateIframeTo(entry.url);
  }
}

function browserRefresh() {
  if (window.electronAPI) {
    window.electronAPI.reload('tab1');
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
    window.electronAPI.navigate('tab1', 'about:blank');
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
function syncElectronBrowserBounds() {
  if (!window.electronAPI) {
    console.log('[BrowserAI] syncElectronBrowserBounds: electronAPI 없음, 건너뜀');
    return;
  }
  var container = document.getElementById('browserFrameWrap');
  console.log('[BrowserAI] syncElectronBrowserBounds: container=', !!container,
    'offsetParent=', container ? (container.offsetParent !== null) : 'N/A',
    'display=', container ? container.style.display : 'N/A');
  if (container && container.offsetParent !== null) {
    var rect = container.getBoundingClientRect();
    console.log('[BrowserAI] bounds:', rect.x, rect.y, rect.width, rect.height);
    window.electronAPI.setBounds({
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    });
    window.electronAPI.setVisibility(true);
    console.log('[BrowserAI] setBounds + setVisibility(true) 호출됨');
  } else {
    console.log('[BrowserAI] offsetParent null → setVisibility(false)');
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
  setInterval(function () {
    fetch('/api/browser/status')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var pending = data.pending_url || '';
        if (pending && pending !== _lastPending) {
          _lastPending = pending;
          console.log('[BrowserAI] AI requested navigate to:', pending, '- auto-opening browser view');
          // Auto-open browser view if not already visible
          if (!_browserViewVisible) {
            toggleBrowserView();
          }
          // Navigate to the pending URL
          var input = document.getElementById('browserCanvasUrlInput') || document.getElementById('browserUrlInput');
          if (input) input.value = pending;
          browserGoToAddress();
        } else if (!pending) {
          _lastPending = '';
        }
      })
      .catch(function () { /* ignore poll errors */ });
  }, 1000); // Check every 1 second (backend waits up to 10 seconds)
})();

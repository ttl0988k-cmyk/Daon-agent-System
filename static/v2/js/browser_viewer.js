/**
 * DAON Browser Agent Live Viewer & Human-Takeover Module
 * - 에이전트가 브라우저 작업 시 채팅창 내에 실시간 브라우저 카드 팝업 (기본 컴팩트 크기)
 * - WebSocket (/stream) 기반 초저지연 CDP Screencast 화면 송출
 * - 대표님 직접 제어권 가져오기 (Takeover) 지원
 * - 사람이 권한을 넘겨받을 시 시원시원한 대화면 확대 및 줌(Zoom) 기능 지원
 * - 챗 전송 시 강제 닫힘 방지 & 2분(120초) 무활동(Idle) 시에만 자동 정리하여 메모리 반환
 */

const BROWSER_SERVICE_URL = 'http://127.0.0.1:8088';
const IDLE_TIMEOUT_MS = 120000; // 2분 (120초)

function getBrowserWsUrl() {
  const loc = window.location;
  const isHttps = loc.protocol === 'https:';
  // file://·빈 호스트(Electron file 로드 등)면 로컬 8088 직결로 폴백
  if (!loc.hostname || loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') {
    return 'ws://127.0.0.1:8088/stream';
  }
  const proto = isHttps ? 'wss:' : 'ws:';
  return `${proto}//${loc.host}/stream`;
}

function escapeUrl(str) {
  if (!str) return '';
  return String(str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

export class BrowserViewer {
  constructor() {
    this.ws = null;
    this.activeCard = null;
    this.backdropEl = null;
    this.isOpen = false;
    this.isTakeover = false;
    this.isExpanded = false;
    this.zoom = 1.0;
    this.lastUrl = '';
    this.turnCompleted = false;
    this._idleTimer = null;
    this._reconnectTimer = null;
    this._lastTouchTime = 0;
    this._escHandler = null;
    this._dockObserver = null;
    this._boundImg = null;
    this._eventsBound = false;
    this._winUp = null;
    this._winKeyDown = null;
    this._winKeyUp = null;
    this._sendFailNotified = false;
    this._suspended = false;
  }

  /**
   * 브라우저 활동 감지 시 2분 Idle 타이머 리셋
   * - 잦은 타이머 재설정을 방지하기 위해 1초 단위로 쓰로틀링
   */
  _touchIdle() {
    // 절전 중 활동 감지 → 즉시 스트림 재연결 (쓰로틀보다 먼저 처리)
    if (this._suspended) {
      this._suspended = false;
      try { this.connectStream(); } catch (_) {}
    }
    const now = Date.now();
    if (now - this._lastTouchTime < 1000) return;
    this._lastTouchTime = now;

    clearTimeout(this._idleTimer);
    if (!this.isOpen && !this.activeCard) return;

    this._idleTimer = setTimeout(() => {
      // 대표님이 직접 제어 중이거나 대화면으로 보고 계신 중이면 닫지 않고 유지
      if (this.isTakeover || this.isExpanded) {
        this._touchIdle();
        return;
      }
      // 2분 무활동 → 스트림만 일시중단, 백엔드 브라우저는 유지 (다음 조작 시 자동 재연결)
      console.log('[BrowserViewer] ⏰ 2분간 무활동 — 화면 스트림 일시중단 (브라우저 유지 중)');
      this._idleSuspend();
    }, IDLE_TIMEOUT_MS);
  }

  /**
   * 절전 진입: WS 스트림만 끊고 카드·백엔드는 그대로 둔다.
   * (기존 close()는 백엔드까지 죽여서 "가만히 있으면 마우스가 안 먹음" 현상의 범인이었음)
   */
  _idleSuspend() {
    this._suspended = true;
    clearTimeout(this._reconnectTimer);
    if (this.ws) {
      try {
        this.ws.onclose = null; // 재연결 루프 방지 후 닫기
        this.ws.close();
      } catch (_) {}
      this.ws = null;
    }
    const badgeText = this.activeCard?.querySelector('#browser-badge-text');
    if (badgeText) badgeText.textContent = '💤 절전 중 — 클릭하면 깨어남';
  }

  /**
   * 영구 브라우저 도크 확보 (채팅 말풍선 밖 — 최신 메시지 바로 아래, 입력창 근처)
   * - #browser-dock이 없으면 메시지 컨테이너 뒤에 생성
   */
  ensureDock() {
    let dock = document.getElementById('browser-dock');
    if (!dock) {
      const chatContainer = document.getElementById('chat-messages-container');
      dock = document.createElement('div');
      dock.id = 'browser-dock';
      dock.className = 'w-full';
      if (chatContainer && chatContainer.parentNode) {
        chatContainer.parentNode.insertBefore(dock, chatContainer.nextSibling);
      } else {
        document.body.appendChild(dock);
      }
    }
    return dock;
  }

  /**
   * 도크 감시 — 말풍선 innerHTML 갈아엎기로 카드가 DOM에서 떨어지면 도크로 구조
   */
  startDockWatch() {
    if (this._dockObserver) return;
    try {
      this._dockObserver = new MutationObserver(() => {
        if (this.activeCard && !document.contains(this.activeCard)) {
          const dock = this.ensureDock();
          dock.appendChild(this.activeCard);
          this.isOpen = true;
          if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.connectStream();
          }
        }
      });
      this._dockObserver.observe(document.body, { childList: true, subtree: true });
    } catch (_) {}
  }

  /**
   * 브라우저 서비스의 실시간 가동 상태 체크
   */
  async checkStatus() {
    try {
      const res = await fetch(`${BROWSER_SERVICE_URL}/health`, { signal: AbortSignal.timeout(1200) });
      if (!res.ok) return { browser_running: false };
      return await res.json();
    } catch {
      return { browser_running: false };
    }
  }

  /**
   * 에이전트 메시지 버블 내에 실시간 브라우저 뷰어 카드 생성/마운트
   */
  mount(targetContainer, initialUrl = '') {
    // 영구 도크 우선 — 말풍선은 새 채팅/세션 전환에 갈아엎어지므로 카드 본체는 도크에 둔다
    const dock = this.ensureDock();
    this.startDockWatch();
    console.log('[BrowserViewer] 🚀 mount 호출됨, dock 고정, initialUrl:', initialUrl || '(없음)');
    if (initialUrl) this.lastUrl = initialUrl;
    this.turnCompleted = false;

    // 말풍선 자리에는 도크로 안내하는 placeholder만 (카드 ID 중복 방지)
    if (targetContainer && document.contains(targetContainer) && !targetContainer.closest('#browser-dock')) {
      if (!targetContainer.querySelector('.browser-dock-link')) {
        const link = document.createElement('button');
        link.type = 'button';
        link.className = 'browser-dock-link w-full mt-2 px-3 py-2 rounded-lg bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 text-[12px] text-neutral-200 transition-colors flex items-center gap-2 cursor-pointer';
        link.innerHTML = '<span class="material-symbols-outlined text-[15px] text-emerald-400">language</span><span>🌐 브라우저가 메시지 아래 도크에 고정됐어요 — 클릭하면 이동합니다</span>';
        link.addEventListener('click', () => {
          document.getElementById('browser-dock')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        targetContainer.appendChild(link);
      }
    }

    // 기존 카드가 어디 있든(구 말풍선 포함) 도크로 입양
    const existingInDom = document.querySelector('#browser-live-viewer-card');
    const detached = this.activeCard && !document.contains(this.activeCard) ? this.activeCard : null;
    const existing = existingInDom || detached;
    if (existing) {
      this.activeCard = existing;
      if (existing.parentNode !== dock) dock.appendChild(existing);
      this.isOpen = true;
      if (initialUrl) this.updateUrl(initialUrl);
      this.updateBadgeUI();
      this._touchIdle();
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        this.connectStream();
      }
      dock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      return;
    }

    const card = document.createElement('div');
    card.id = 'browser-live-viewer-card';
    card.className = 'w-full my-3 border border-neutral-700/80 rounded-xl bg-neutral-900 text-white overflow-hidden shadow-xl transition-all duration-300 animate-fadeIn';
    card.innerHTML = `
      <!-- 브라우저 헤더 바 -->
      <div class="px-3 py-2 bg-neutral-800 border-b border-neutral-700 flex flex-wrap items-center justify-between gap-2 select-none" id="browser-viewer-header">
        <!-- 좌측: 상태 배지 & URL -->
        <div class="flex items-center gap-2 min-w-0 flex-1">
          <span id="browser-live-badge" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono text-[11px] font-semibold transition-all">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" id="browser-badge-dot"></span>
            <span id="browser-badge-text">LIVE (에이전트)</span>
          </span>
          <div class="flex items-center gap-1 text-[12px] text-neutral-300 font-mono truncate max-w-[280px] sm:max-w-[420px]" id="browser-viewer-url-bar">
            <span class="material-symbols-outlined text-[15px] text-neutral-400 shrink-0">language</span>
            <span class="truncate" id="browser-url-text">${escapeUrl(initialUrl || '연결 준비 중...')}</span>
          </div>
        </div>

        <!-- 우측: 제어권 토글 + 대화면 확대/축소 + 줌 + 닫기 액션 -->
        <div class="flex items-center gap-1.5 shrink-0 flex-wrap">
          <!-- 페이지 탐색 (뒤로/앞으로/새로고침) -->
          <div class="flex items-center bg-neutral-700/80 rounded-lg border border-neutral-600/60 overflow-hidden">
            <button type="button" id="browser-nav-back" class="px-1.5 py-1 text-neutral-300 hover:bg-neutral-600 cursor-pointer transition-colors" title="뒤로 가기">
              <span class="material-symbols-outlined text-[13px] leading-none">arrow_back</span>
            </button>
            <button type="button" id="browser-nav-forward" class="px-1.5 py-1 text-neutral-300 hover:bg-neutral-600 cursor-pointer transition-colors" title="앞으로 가기">
              <span class="material-symbols-outlined text-[13px] leading-none">arrow_forward</span>
            </button>
            <button type="button" id="browser-nav-reload" class="px-1.5 py-1 text-neutral-300 hover:bg-neutral-600 cursor-pointer transition-colors" title="새로고침">
              <span class="material-symbols-outlined text-[13px] leading-none">refresh</span>
            </button>
          </div>
          <!-- 제어권 토글 버튼 -->
          <button type="button" id="browser-takeover-btn"
            class="px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all flex items-center gap-1 cursor-pointer bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/40"
            title="마우스와 키보드로 직접 브라우저를 조작하려면 클릭하세요">
            <span class="material-symbols-outlined text-[14px]">pan_tool</span>
            <span id="browser-takeover-btn-label">직접 제어하기</span>
          </button>

          <!-- 대화면 확대/원래 크기 토글 버튼 -->
          <button type="button" id="browser-expand-btn"
            class="px-2 py-1 bg-neutral-700 hover:bg-neutral-600 active:bg-neutral-500 rounded-lg text-[11px] text-neutral-200 transition-colors flex items-center gap-1 cursor-pointer"
            title="대화면으로 확대하여 편하게 보기">
            <span class="material-symbols-outlined text-[14px]" id="browser-expand-icon">open_in_full</span>
            <span id="browser-expand-btn-label">대화면 확대</span>
          </button>

          <!-- 줌 배율 컨트롤 (100% / 125% / 150%) -->
          <div class="flex items-center bg-neutral-700/80 rounded-lg border border-neutral-600/60 text-[11px] overflow-hidden">
            <button type="button" id="browser-zoom-out-btn" class="px-1.5 py-1 text-neutral-300 hover:bg-neutral-600 cursor-pointer transition-colors" title="화면 글씨 축소">
              <span class="material-symbols-outlined text-[13px] leading-none">zoom_out</span>
            </button>
            <span id="browser-zoom-label" class="px-1.5 font-mono text-neutral-200 text-[10px] select-none min-w-[34px] text-center">100%</span>
            <button type="button" id="browser-zoom-in-btn" class="px-1.5 py-1 text-neutral-300 hover:bg-neutral-600 cursor-pointer transition-colors" title="화면 글씨 확대">
              <span class="material-symbols-outlined text-[13px] leading-none">zoom_in</span>
            </button>
          </div>

          <!-- 외부 기본 브라우저로 열기 버튼 -->
          <button type="button" id="browser-open-external-btn"
            class="px-2 py-1 bg-neutral-700 hover:bg-neutral-600 active:bg-neutral-500 rounded-lg text-[11px] text-neutral-200 transition-colors flex items-center gap-1 cursor-pointer"
            title="현재 페이지를 PC 기본 웹 브라우저(Chrome/Edge)로 직접 띄우기">
            <span class="material-symbols-outlined text-[13px]">open_in_new</span>
            <span class="hidden sm:inline">외부 브라우저</span>
          </button>

          <!-- 닫기 버튼 -->
          <button type="button" id="browser-viewer-close-btn"
            class="px-2 py-1 bg-neutral-700 hover:bg-red-600/80 active:bg-red-700 rounded-lg text-[11px] text-neutral-200 hover:text-white transition-colors flex items-center gap-1 cursor-pointer ml-0.5"
            title="브라우저 뷰어 닫기">
            <span class="material-symbols-outlined text-[13px]">close</span>
            <span>닫기</span>
          </button>
        </div>
      </div>

      <!-- 제어권 안내 바 (직접 제어 모드일 때 눈에 띄게 노출) -->
      <div id="browser-takeover-banner" class="hidden px-3 py-1.5 bg-amber-500/15 border-b border-amber-500/30 text-amber-200 text-[11px] flex items-center justify-between select-none animate-fadeIn">
        <div class="flex items-center gap-1.5">
          <span class="material-symbols-outlined text-[15px] text-amber-400">touch_app</span>
          <span><b>직접 제어 모드 가동 중:</b> 마우스 클릭·스크롤 및 키보드로 페이지를 자유롭게 조작하실 수 있습니다.</span>
        </div>
        <span class="text-neutral-400 text-[10px]">완료 후 '에이전트 제어로 복귀'를 누르시면 됩니다.</span>
      </div>

      <!-- 실시간 뷰포트 영역 (Screencast & Takeover) -->
      <div class="relative w-full aspect-[16/10] max-h-[460px] bg-black flex items-center justify-center overflow-auto select-none transition-all duration-300" id="browser-viewport-box">
        <div id="browser-screencast-wrapper" class="relative flex items-center justify-center transition-transform duration-150 origin-center w-full h-full">
          <!-- 포커스 수신용 히든 인풋: 클릭 시 자동 포커스되어 한글 IME 및 영문 타이핑을 원활하게 수신 -->
          <input id="browser-type-proxy" type="text" autocomplete="off" spellcheck="false" class="absolute opacity-0 pointer-events-none w-1 h-1 -top-10 left-0" aria-hidden="true" />
          <img id="browser-screencast-img" class="w-full h-full object-contain pointer-events-auto cursor-default hidden" draggable="false" alt="" />
        </div>
        <div id="browser-viewer-loading" class="absolute inset-0 flex flex-col items-center justify-center bg-black/75 text-neutral-300 gap-2 z-10 pointer-events-none">
          <span class="material-symbols-outlined text-[28px] animate-spin text-emerald-400">progress_activity</span>
          <span class="text-[12px] font-mono">브라우저 화면 스트림 동기화 중...</span>
        </div>
      </div>

      <!-- 한글 입력창 (헤드리스엔 OS IME가 없어 한/영키 무용 → 완성글을 통째로 전송) -->
      <div id="browser-ime-bar" class="px-3 py-2 bg-neutral-800/90 border-t border-neutral-700 flex items-center gap-2">
        <span class="text-[11px] text-neutral-400 shrink-0">⌨️ 한글입력</span>
        <input id="browser-ime-input" type="text" placeholder="검색창 클릭 후 한글 타이핑 → Enter(입력+검색실행)"
          class="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg bg-neutral-900 border border-neutral-600 text-[13px] text-neutral-100 placeholder:text-neutral-500 outline-none focus:border-emerald-500" />
        <button type="button" id="browser-ime-send"
          class="px-3 py-1.5 rounded-lg text-[12px] font-semibold bg-emerald-600 hover:bg-emerald-500 text-white cursor-pointer shrink-0">전송</button>
      </div>
    `;

    // 이벤트 리스너 등록
    card.querySelector('#browser-viewer-close-btn')?.addEventListener('click', () => {
      this.close();
    });

    card.querySelector('#browser-open-external-btn')?.addEventListener('click', () => {
      const targetUrl = this.lastUrl || 'https://www.google.com';
      if (window.electronAPI && typeof window.electronAPI.openExternal === 'function') {
        window.electronAPI.openExternal(targetUrl);
      } else {
        window.open(targetUrl, '_blank');
      }
    });

    card.querySelector('#browser-takeover-btn')?.addEventListener('click', () => {
      this.toggleTakeover();
    });

    card.querySelector('#browser-expand-btn')?.addEventListener('click', () => {
      this.toggleExpand();
    });

    card.querySelector('#browser-zoom-in-btn')?.addEventListener('click', () => {
      this.adjustZoom(0.25);
    });

    card.querySelector('#browser-zoom-out-btn')?.addEventListener('click', () => {
      this.adjustZoom(-0.25);
    });

    // 페이지 탐색 버튼 (직접제어와 무관 — 카드 크롬이므로 항상 동작)
    card.querySelector('#browser-nav-back')?.addEventListener('click', () => this._sendNav('back'));
    card.querySelector('#browser-nav-forward')?.addEventListener('click', () => this._sendNav('forward'));
    card.querySelector('#browser-nav-reload')?.addEventListener('click', () => this._sendNav('reload'));

    // 한글 입력창 → 완성글 통째 전송 + 원격 Enter (Enter 또는 전송 버튼)
    const imeInput = card.querySelector('#browser-ime-input');
    const imeSend = card.querySelector('#browser-ime-send');
    const flushIme = () => {
      const v = imeInput ? imeInput.value : '';
      if (!v) return false;
      this._sendText(v);
      try { imeInput.value = ''; } catch (_) {}
      return true;
    };
    imeInput?.addEventListener('keydown', (e) => {
      e.stopPropagation(); // window 가로채기 중복 방지 (이중 안전장치)
      if (e.key === 'Enter') {
        e.preventDefault();
        const had = flushIme();
        // 원격 페이지에 Enter키 전달 → 검색 실행 (입력창이 Enter를 먹어서 별도 전송 필요)
        setTimeout(() => { try { this._sendKey('Enter', 'Enter'); } catch (_) {} }, had ? 400 : 0);
      }
    });
    imeSend?.addEventListener('click', () => { flushIme(); imeInput?.focus(); });

    // 카드 본체는 영구 도크에 고정 (말풍선 갈아엎기 영향 없음)
    dock.appendChild(card);
    this.activeCard = card;
    this.isOpen = true;

    // WebSocket 스트림 연결 및 입력 주입 리스너 설정
    this.connectStream();

    // 2분 무활동 자동 정리 타이머 가동
    this._touchIdle();

    // 도크로 스크롤 안착
    try { dock.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (_) {}
    try { this._syncFab(); } catch (_) {}
  }

  updateUrl(url) {
    if (!this.activeCard || !url) return;
    this.lastUrl = url;
    const urlSpan = this.activeCard.querySelector('#browser-url-text');
    if (urlSpan) urlSpan.textContent = url;
    this._touchIdle();
  }

  updateBadgeUI() {
    if (!this.activeCard) return;
    const badge = this.activeCard.querySelector('#browser-live-badge');
    const badgeDot = this.activeCard.querySelector('#browser-badge-dot');
    const badgeText = this.activeCard.querySelector('#browser-badge-text');
    if (!badge || !badgeText) return;

    if (this.isTakeover) {
      // 사람이 직접 제어 중
      badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/25 text-amber-300 border border-amber-500/40 font-mono text-[11px] font-semibold transition-all';
      if (badgeDot) badgeDot.className = 'w-2 h-2 rounded-full bg-amber-400 animate-pulse';
      badgeText.textContent = '🕹️ 직접 제어 중 (개입)';
    } else if (this.turnCompleted) {
      // 탐색 완료 후 대기 상태 (화면 유지)
      badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-neutral-700/80 text-neutral-300 font-mono text-[11px] font-semibold transition-all';
      if (badgeDot) badgeDot.className = 'w-2 h-2 rounded-full bg-emerald-400';
      badgeText.textContent = '✔️ 탐색 완료 (대기 중)';
    } else {
      // 에이전트 작업 중
      badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 font-mono text-[11px] font-semibold transition-all';
      if (badgeDot) badgeDot.className = 'w-2 h-2 rounded-full bg-emerald-400 animate-pulse';
      badgeText.textContent = 'LIVE (에이전트)';
    }
  }

  /**
   * 대표님 직접 제어권 (Human Takeover) 토글
   */
  toggleTakeover() {
    this._touchIdle();
    this.isTakeover = !this.isTakeover;

    const btn = this.activeCard?.querySelector('#browser-takeover-btn');
    const label = this.activeCard?.querySelector('#browser-takeover-btn-label');
    const banner = this.activeCard?.querySelector('#browser-takeover-banner');
    const img = this.activeCard?.querySelector('#browser-screencast-img');

    if (this.isTakeover) {
      if (btn) {
        btn.className = 'px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all flex items-center gap-1 cursor-pointer bg-emerald-500 text-white hover:bg-emerald-600 shadow-md ring-2 ring-emerald-400/50';
      }
      if (label) label.textContent = '🕹️ 에이전트 제어로 복귀';
      if (banner) banner.classList.remove('hidden');
      if (img) img.style.cursor = 'pointer';

      // 켜는 순간 스트림이 죽어 있으면 즉시 재연결 (X로 닫은 뒤 재사용 케이스)
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        this.connectStream();
      }

      // 사람이 권한을 넘겨받으면 시원하게 대화면으로 확대 및 타이핑 포커스 부여!
      if (!this.isExpanded) {
        this.setExpanded(true);
      }
      setTimeout(() => {
        const proxy = this.activeCard?.querySelector('#browser-type-proxy');
        if (proxy) {
          try { proxy.focus({ preventScroll: true }); } catch (_) {}
        }
      }, 50);
    } else {
      if (btn) {
        btn.className = 'px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all flex items-center gap-1 cursor-pointer bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 border border-amber-500/40';
      }
      if (label) label.textContent = '직접 제어하기';
      if (banner) banner.classList.add('hidden');
      if (img) img.style.cursor = 'default';

      if (this.isExpanded) {
        this.setExpanded(false);
      }
    }

    this.updateBadgeUI();
  }

  /**
   * 대화면 확대/축소 모드 토글
   */
  toggleExpand() {
    this._touchIdle();
    this.setExpanded(!this.isExpanded);
  }

  /**
   * 대화면 확대 모달 상태 설정
   */
  setExpanded(expand) {
    if (!this.activeCard) return;
    this._touchIdle();
    this.isExpanded = !!expand;

    const expandBtn = this.activeCard.querySelector('#browser-expand-btn');
    const expandIcon = this.activeCard.querySelector('#browser-expand-icon');
    const expandLabel = this.activeCard.querySelector('#browser-expand-btn-label');
    const viewportBox = this.activeCard.querySelector('#browser-viewport-box');

    if (this.isExpanded) {
      if (!this.backdropEl) {
        this.backdropEl = document.createElement('div');
        this.backdropEl.id = 'browser-viewer-backdrop';
        this.backdropEl.className = 'fixed inset-0 bg-black/80 backdrop-blur-sm z-[9998] transition-opacity duration-300 animate-fadeIn';
        this.backdropEl.addEventListener('click', () => {
          this.setExpanded(false);
        });
        document.body.appendChild(this.backdropEl);
      }

      this.activeCard.classList.remove('w-full', 'my-3');
      this.activeCard.className = 'fixed inset-2 md:inset-6 z-[9999] flex flex-col rounded-2xl bg-neutral-900 border-2 border-primary/60 shadow-2xl overflow-hidden transition-all duration-300 animate-fadeIn';

      if (viewportBox) {
        viewportBox.classList.remove('aspect-[16/10]', 'max-h-[460px]');
        viewportBox.classList.add('flex-1', 'w-full', 'h-full', 'max-h-none');
      }
      const wrapper = this.activeCard.querySelector('#browser-screencast-wrapper');
      if (wrapper) {
        wrapper.classList.add('w-full', 'h-full');
      }

      if (expandIcon) expandIcon.textContent = 'close_fullscreen';
      if (expandLabel) expandLabel.textContent = '원래 크기로';
      if (expandBtn) expandBtn.title = '채팅창 기본 크기로 축소';

      if (!this._escHandler) {
        this._escHandler = (e) => {
          if (e.key === 'Escape' && this.isExpanded) {
            this.setExpanded(false);
          }
        };
        window.addEventListener('keydown', this._escHandler);
      }
    } else {
      if (this.backdropEl) {
        this.backdropEl.remove();
        this.backdropEl = null;
      }

      this.activeCard.className = 'w-full my-3 border border-neutral-700/80 rounded-xl bg-neutral-900 text-white overflow-hidden shadow-xl transition-all duration-300 animate-fadeIn';

      if (viewportBox) {
        viewportBox.classList.remove('flex-1', 'h-full', 'max-h-none');
        viewportBox.classList.add('aspect-[16/10]', 'max-h-[460px]');
      }
      const wrapper = this.activeCard.querySelector('#browser-screencast-wrapper');
      if (wrapper) {
        wrapper.classList.remove('w-full', 'h-full');
      }

      if (expandIcon) expandIcon.textContent = 'open_in_full';
      if (expandLabel) expandLabel.textContent = '대화면 확대';
      if (expandBtn) expandBtn.title = '대화면으로 확대하여 편하게 보기';

      if (this._escHandler) {
        window.removeEventListener('keydown', this._escHandler);
        this._escHandler = null;
      }
    }
  }

  /**
   * 화면 줌 배율 조절 (100% ~ 200%)
   */
  adjustZoom(delta) {
    this._touchIdle();
    const next = Math.max(0.75, Math.min(2.0, Math.round((this.zoom + delta) * 100) / 100));
    this.setZoom(next);
  }

  setZoom(level) {
    this.zoom = level;
    const wrapper = this.activeCard?.querySelector('#browser-screencast-wrapper');
    const label = this.activeCard?.querySelector('#browser-zoom-label');
    if (wrapper) {
      wrapper.style.transform = `scale(${this.zoom})`;
    }
    if (label) {
      label.textContent = `${Math.round(this.zoom * 100)}%`;
    }
  }

  connectStream() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    clearTimeout(this._reconnectTimer);
    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }

    const wsUrl = getBrowserWsUrl();
    try {
      this.ws = new WebSocket(wsUrl);
    } catch (e) {
      console.warn('[BrowserViewer] WS 생성 오류:', e);
      this._scheduleReconnect();
      return;
    }

    const img = this.activeCard?.querySelector('#browser-screencast-img');
    const loading = this.activeCard?.querySelector('#browser-viewer-loading');

    let firstFrameLogged = false;

    this.ws.onopen = () => {
      console.log('[BrowserViewer] ✅ 실시간 화면 스트림 소켓 연결됨:', wsUrl);
      clearTimeout(this._reconnectTimer);
      this._touchIdle();
    };

    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.t === 'frame') {
          const targetImg = document.querySelector('#browser-screencast-img') || img || this.activeCard?.querySelector('#browser-screencast-img');
          const targetLoading = document.querySelector('#browser-viewer-loading') || loading || this.activeCard?.querySelector('#browser-viewer-loading');
          if (targetImg) {
            targetImg.src = 'data:image/jpeg;base64,' + msg.data;
            targetImg.classList.remove('hidden');
          }
          if (targetLoading && !targetLoading.classList.contains('hidden')) {
            targetLoading.classList.add('hidden');
          }
          if (!firstFrameLogged) {
            firstFrameLogged = true;
            console.log('[BrowserViewer] 🖼️ 첫 실시간 프레임 수신 및 렌더링 완료 (스피너 해제됨)');
          }
          this._touchIdle();
        } else if (msg.t === 'nav') {
          if (msg.url) {
            this.updateUrl(msg.url);
          }
        } else if (msg.t === 'closed') {
          // 서버가 브라우저 인스턴스를 닫았다 → 뷰어 카드도 함께 정리하고 재연결 금지.
          // 이 처리가 없으면 카드가 '연결됨' 상태로 남아 계속 재연결을 시도한다.
          console.log('[BrowserViewer] 📴 서버 브라우저 종료 — 뷰어 카드 정리 (reason=' + (msg.reason || 'closed') + ')');
          this._suspended = true;
          clearTimeout(this._reconnectTimer);
          this._reconnectTimer = null;
          clearTimeout(this._idleTimer);
          this._idleTimer = null;
          if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
          if (this.activeCard) {
            try { this.activeCard.remove(); } catch (_) {}
            this.activeCard = null;
          }
          this.isOpen = false;
          this.isTakeover = false;
          this.isExpanded = false;
          this.turnCompleted = false;
          try { this._syncFab(); } catch (_) {}
        }
      } catch (err) {
        console.warn('[BrowserViewer] 프레임 파싱 오류:', err);
      }
    };

    this.ws.onerror = (e) => {
      console.warn('[BrowserViewer] WS 오류 발생:', e);
    };

    this.ws.onclose = () => {
      console.log('[BrowserViewer] WS 연결 닫힘');
      this._scheduleReconnect();
    };

    if (img) {
      this.bindHumanTakeoverEvents(img);
    }
  }

  _scheduleReconnect() {
    clearTimeout(this._reconnectTimer);
    if (this._suspended) return; // 절전 중에는 재연결 금지 (다음 조작 시 _touchIdle이 깨움)
    if (!this.isOpen && !this.activeCard) return;
    this._reconnectTimer = setTimeout(() => {
      if (this.isOpen && this.activeCard && document.contains(this.activeCard)) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
          console.log('[BrowserViewer] 🔄 스트림 재연결 시도 중...');
          this.connectStream();
        }
      }
    }, 1500);
  }

  /**
   * 사람이 마우스/키보드로 직접 개입할 때의 이벤트 주입
   */
  bindHumanTakeoverEvents(img) {
    // 같은 이미지면 중복 바인딩 방지, 새 이미지면 재바인딩 (카드 재생성 시 클릭 먹통 방지)
    if (this._eventsBound && this._boundImg === img) return;
    if (this._winUp) window.removeEventListener('mouseup', this._winUp);
    if (this._winKeyDown) window.removeEventListener('keydown', this._winKeyDown);
    if (this._winKeyUp) window.removeEventListener('keyup', this._winKeyUp);
    this._eventsBound = true;
    this._boundImg = img;

    const send = (data) => {
      this._touchIdle();
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify(data));
          if (this._sendFailNotified) {
            this._sendFailNotified = false;
            this.updateBadgeUI();
          }
        } catch (_) { this._onSendFailed(); }
      } else {
        this._onSendFailed();
      }
    };

    const rel = (e) => {
      const r = img.getBoundingClientRect();
      if (!r.width || !r.height) return { x: 0, y: 0 };

      const naturalAspect = 1280 / 800;
      const boxAspect = r.width / r.height;

      let renderWidth = r.width;
      let renderHeight = r.height;
      let offsetX = 0;
      let offsetY = 0;

      if (boxAspect > naturalAspect) {
        renderWidth = r.height * naturalAspect;
        offsetX = (r.width - renderWidth) / 2;
      } else {
        renderHeight = r.width / naturalAspect;
        offsetY = (r.height - renderHeight) / 2;
      }

      const clientXInImg = e.clientX - (r.left + offsetX);
      const clientYInImg = e.clientY - (r.top + offsetY);

      const x = Math.max(0, Math.min(1, clientXInImg / renderWidth));
      const y = Math.max(0, Math.min(1, clientYInImg / renderHeight));
      return { x, y };
    };

    // 클릭 시 시각적 리플 피드백 (어디를 클릭했는지 명확하게 표시)
    const showClickRipple = (clientX, clientY) => {
      try {
        const ripple = document.createElement('div');
        ripple.style.position = 'fixed';
        ripple.style.left = `${clientX - 12}px`;
        ripple.style.top = `${clientY - 12}px`;
        ripple.style.width = '24px';
        ripple.style.height = '24px';
        ripple.style.borderRadius = '50%';
        ripple.style.border = '2px solid #10b981';
        ripple.style.backgroundColor = 'rgba(16, 185, 129, 0.3)';
        ripple.style.pointerEvents = 'none';
        ripple.style.zIndex = '10000';
        ripple.style.transition = 'transform 0.3s ease-out, opacity 0.3s ease-out';
        ripple.style.transform = 'scale(0.5)';
        ripple.style.opacity = '1';
        document.body.appendChild(ripple);
        requestAnimationFrame(() => {
          ripple.style.transform = 'scale(1.8)';
          ripple.style.opacity = '0';
        });
        setTimeout(() => ripple.remove(), 320);
      } catch (_) {}
    };

    img.addEventListener('contextmenu', e => {
      if (this.isTakeover) e.preventDefault();
    });

    let isMouseDown = false;
    let mouseDownPos = null;
    let moveThrottleTimer = null;
    let pendingMove = null;

    // 히든 타이핑 프록시 인풋 이벤트 바인딩 (한글 IME 및 키보드 직접 타이핑)
    const proxyInput = this.activeCard?.querySelector('#browser-type-proxy');
    if (proxyInput && !proxyInput._boundEvents) {
      proxyInput._boundEvents = true;

      let isComposing = false;

      // 1. 한글 조합 시작
      proxyInput.addEventListener('compositionstart', () => {
        isComposing = true;
      });

      // 2. 한글 음절 조합 완료 시점: 완성된 글자/단어만 원격으로 1회 전송
      proxyInput.addEventListener('compositionend', (e) => {
        isComposing = false;
        if (!this.isOpen || !this.isTakeover) return;
        const text = e.data || proxyInput.value;
        if (text) {
          this._sendText(text);
        }
        proxyInput.value = '';
      });

      // 3. 일반 텍스트 입력 (영문, 숫자, 특수문자 등 비조합 문자)
      proxyInput.addEventListener('input', (e) => {
        if (!this.isOpen || !this.isTakeover) return;
        // 조합 중(한글 자모 작성 중)일 때는 절대로 중간 값을 서버로 보내거나 비우지 않음!
        if (isComposing || e.isComposing) return;
        if (proxyInput.value) {
          this._sendText(proxyInput.value);
          proxyInput.value = '';
        }
      });

      // 4. 제어키(Enter, Backspace, Tab, 방향키 등) 처리
      proxyInput.addEventListener('keydown', (e) => {
        if (!this.isOpen || !this.isTakeover) return;
        if (e.key === 'Process') return;

        // Enter 키: 조합 중인 미완성 글자가 있다면 즉시 확정 전송 후 Enter 키 전달 (검색 실행)
        if (e.key === 'Enter') {
          e.preventDefault();
          if (isComposing || proxyInput.value) {
            const pending = proxyInput.value;
            if (pending) this._sendText(pending);
            proxyInput.value = '';
            isComposing = false;
          }
          send({ t: 'key', kind: 'down', key: 'Enter', code: 'Enter' });
          setTimeout(() => {
            send({ t: 'key', kind: 'up', key: 'Enter', code: 'Enter' });
          }, 60);
          return;
        }

        // Backspace 키: 조합 중이 아닐 때만 원격 글자 삭제
        if (e.key === 'Backspace') {
          if (!isComposing && !proxyInput.value) {
            e.preventDefault();
            send({ t: 'key', kind: 'down', key: 'Backspace', code: 'Backspace' });
            setTimeout(() => {
              send({ t: 'key', kind: 'up', key: 'Backspace', code: 'Backspace' });
            }, 60);
          }
          return;
        }

        const controlKeys = ['Tab', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Home', 'End'];
        if (controlKeys.includes(e.key)) {
          e.preventDefault();
          send({ t: 'key', kind: 'down', key: e.key, code: e.code });
        }
      });

      proxyInput.addEventListener('keyup', (e) => {
        if (!this.isOpen || !this.isTakeover) return;
        const controlKeys = ['Tab', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Home', 'End'];
        if (controlKeys.includes(e.key)) {
          send({ t: 'key', kind: 'up', key: e.key, code: e.code });
        }
      });
    }

    img.addEventListener('mousemove', e => {
      if (!this.isTakeover) return;

      // 마우스를 누른 상태에서 미세 떨림(5px 미만)이면 드래그로 전송하지 않아 클릭을 보장
      if (isMouseDown && mouseDownPos) {
        const dx = Math.abs(e.clientX - mouseDownPos.x);
        const dy = Math.abs(e.clientY - mouseDownPos.y);
        if (dx < 5 && dy < 5) {
          return;
        }
      }

      pendingMove = {
        t: 'mouse',
        kind: 'move',
        down: isMouseDown,
        button: isMouseDown ? 'left' : undefined,
        ...rel(e)
      };
      if (!moveThrottleTimer) {
        moveThrottleTimer = setTimeout(() => {
          moveThrottleTimer = null;
          if (pendingMove) {
            // 현재 마우스 상태를 정확히 반영하여 클릭 중간에 buttons:0으로 리셋되는 버그 차단
            pendingMove.down = isMouseDown;
            if (isMouseDown) pendingMove.button = 'left';
            send(pendingMove);
          }
        }, 25); // 40fps throttled
      }
    });

    img.addEventListener('mousedown', e => {
      if (!this.isTakeover) return;

      // 마우스 누를 때 대기 중이던 move 타이머 즉시 취소 (down: false가 끼어들어 클릭을 삼키는 레이스 컨디션 완벽 차단)
      if (moveThrottleTimer) {
        clearTimeout(moveThrottleTimer);
        moveThrottleTimer = null;
        pendingMove = null;
      }
      isMouseDown = true;
      mouseDownPos = { x: e.clientX, y: e.clientY };

      // 화면 클릭 즉시 타이핑 프록시에 포커스하여 키보드 입력이 즉시 먹히도록 처리
      if (proxyInput) {
        try { proxyInput.focus({ preventScroll: true }); } catch (_) {}
      } else if (document.activeElement && typeof document.activeElement.blur === 'function') {
        document.activeElement.blur();
      }

      showClickRipple(e.clientX, e.clientY);

      const btn = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
      send({
        t: 'mouse',
        kind: 'down',
        button: btn,
        clickCount: e.detail || 1,
        ...rel(e)
      });
    });

    this._winUp = (e) => {
      if (!this.isOpen || !this.isTakeover || !isMouseDown) return;
      isMouseDown = false;
      mouseDownPos = null;
      if (moveThrottleTimer) {
        clearTimeout(moveThrottleTimer);
        moveThrottleTimer = null;
        pendingMove = null;
      }
      const btn = e.button === 2 ? 'right' : (e.button === 1 ? 'middle' : 'left');
      send({
        t: 'mouse',
        kind: 'up',
        button: btn,
        clickCount: e.detail || 1,
        ...rel(e)
      });
    };
    window.addEventListener('mouseup', this._winUp);

    img.addEventListener('wheel', e => {
      if (!this.isTakeover) return;
      e.preventDefault();
      send({ t: 'wheel', ...rel(e), deltaY: e.deltaY, deltaX: e.deltaX });
    }, { passive: false });

    let isHovered = false;
    img.addEventListener('mouseenter', () => {
      isHovered = true;
      this._touchIdle();
    });
    img.addEventListener('mouseleave', () => {
      isHovered = false;
    });

    const keydownHandler = (e) => {
      if (!this.isOpen || !this.isTakeover) return;
      const target = e.target;
      // 채팅창·하단 한글입력창·프록시 인풋에 포커스가 있을 때는 window 가로채기 방지
      if (target && (target.id === 'user-input' || target.id === 'browser-ime-input' || target.id === 'browser-type-proxy' || target.tagName === 'TEXTAREA' || target.classList?.contains?.('chat-input'))) return;
      if (e.key === 'F12' || (e.ctrlKey && e.shiftKey && e.key.toUpperCase() === 'I')) return;

      // 화면에 포커스가 없을 때도 사용자가 키를 누르면 프록시로 포커스 전달
      if (proxyInput && document.activeElement !== proxyInput) {
        try { proxyInput.focus({ preventScroll: true }); } catch (_) {}
      }

      if (e.key === 'Process' || e.isComposing) return;

      e.preventDefault();
      send({
        t: 'key',
        kind: 'down',
        key: e.key,
        code: e.code,
        text: e.key.length === 1 ? e.key : undefined
      });
    };

    const keyupHandler = (e) => {
      if (!this.isOpen || !this.isTakeover) return;
      const target = e.target;
      if (target && (target.id === 'user-input' || target.id === 'browser-ime-input' || target.id === 'browser-type-proxy' || target.tagName === 'TEXTAREA' || target.classList?.contains?.('chat-input'))) return;
      send({ t: 'key', kind: 'up', key: e.key, code: e.code });
    };

    this._winKeyDown = keydownHandler;
    this._winKeyUp = keyupHandler;
    window.addEventListener('keydown', this._winKeyDown);
    window.addEventListener('keyup', this._winKeyUp);
  }

  /**
   * 입력 신호 전송 실패 시: 뱃지에 상태 노출 + 스트림 재연결
   * (기존에는 조용히 버려져서 클릭·키보드가 "먹통"처럼 보였음)
   */
  _onSendFailed() {
    if (!this._sendFailNotified) {
      this._sendFailNotified = true;
      console.warn('[BrowserViewer] ⚠️ 스트림 소켓이 닫혀 있어 입력 신호를 버림 — 재연결 시도');
      const badgeText = this.activeCard?.querySelector('#browser-badge-text');
      if (badgeText) badgeText.textContent = '⚠️ 연결 끊김 — 재연결 중…';
    }
    try { this.connectStream(); } catch (_) {}
  }

  /**
   * 한글 완성 텍스트 전송 (Input.insertText 경로 — OS IME 없이 한글 입력 가능)
   */
  _sendText(text) {
    this._touchIdle();
    if (!text) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ t: 'text', text }));
        if (this._sendFailNotified) {
          this._sendFailNotified = false;
          this.updateBadgeUI();
        }
      } catch (_) { this._onSendFailed(); }
    } else {
      this._onSendFailed();
    }
  }

  /**
   * 원격 키 한 번 누르기 (down→up 한 쌍 — 한글입력창 Enter 후 검색실행용)
   */
  _sendKey(key, code) {
    this._touchIdle();
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) { this._onSendFailed(); return; }
    try {
      this.ws.send(JSON.stringify({ t: 'key', kind: 'down', key, code }));
      setTimeout(() => {
        try {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ t: 'key', kind: 'up', key, code }));
          }
        } catch (_) {}
      }, 120);
    } catch (_) { this._onSendFailed(); }
  }

  /**
   * 페이지 탐색 전송 (뒤로/앞으로/새로고침 — 카드 크롬이라 takeover 없이도 동작)
   */
  _sendNav(action) {
    this._touchIdle();
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) { this._onSendFailed(); return; }
    try {
      this.ws.send(JSON.stringify({ t: 'nav', action }));
    } catch (_) { this._onSendFailed(); }
  }

  /**
   * 에이전트 턴 완료 시 호출:
   * - 화면을 닫지 않고 상태 배지만 전환하며 2분 카운트다운 가동
   */
  onTurnCompleted() {
    this.turnCompleted = true;
    this.updateBadgeUI();
    this._touchIdle();
  }

  scheduleCollapse(delayMs = 2500) {
    this.onTurnCompleted();
  }

  cancelCollapse() {
    // 호환용 no-op
  }

  /**
   * 브라우저 세션 닫기 및 뷰어 정리
   */
  async close() {
    clearTimeout(this._idleTimer);
    clearTimeout(this._reconnectTimer);
    this._idleTimer = null;
    this._reconnectTimer = null;
    this._eventsBound = false;
    this._suspended = false;

    if (this._escHandler) {
      window.removeEventListener('keydown', this._escHandler);
      this._escHandler = null;
    }

    if (this.backdropEl) {
      this.backdropEl.remove();
      this.backdropEl = null;
    }

    if (this.ws) {
      try { this.ws.close(); } catch (_) {}
      this.ws = null;
    }

    // 서버 브라우저 세션 닫기
    fetch(`${BROWSER_SERVICE_URL}/api/session/close`, { method: 'POST' }).catch(() => {});

    if (this.activeCard) {
      this.activeCard.remove();
      this.activeCard = null;
    }

    this.isOpen = false;
    this.isTakeover = false;
    this.isExpanded = false;
    this.turnCompleted = false;
    try { this._syncFab(); } catch (_) {}
  }

  /**
   * 🌐 수동 열기 버튼 (FAB) — 카드가 없을 때 화면 우하단에 상시 노출.
   * 새로고침·세션전환 등으로 카드가 증발해도 대표님이 직접 다시 띄울 수 있다.
   * (JS에서 동적 생성이라 index.html 수정 불필요)
   */
  ensureReopenFab() {
    if (document.getElementById('browser-reopen-fab')) { this._syncFab(); return; }
    const fab = document.createElement('button');
    fab.id = 'browser-reopen-fab';
    fab.type = 'button';
    fab.title = '브라우저 카드를 다시 엽니다 (드래그하여 원하는 위치로 이동 가능)';
    fab.innerHTML = '<span style="font-size:16px;">🌐</span><span class="fab-text" style="font-size:12px;font-weight:600;">브라우저 열기</span>';
    // 입력창(약 130~150px) 위쪽인 bottom: 165px로 안전하게 배치하여 중단/전송 버튼 가림 완벽 방지
    fab.style.cssText = 'position:fixed;right:18px;bottom:165px;z-index:9990;display:flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9999px;background:linear-gradient(135deg,#059669,#10b981);color:#fff;border:1px solid rgba(255,255,255,.3);box-shadow:0 6px 20px rgba(5,150,105,.4), 0 2px 6px rgba(0,0,0,.3);cursor:grab;user-select:none;transition:transform 0.15s, box-shadow 0.15s;';
    
    // 호버 애니메이션
    fab.addEventListener('mouseenter', () => { fab.style.transform = 'translateY(-2px) scale(1.02)'; });
    fab.addEventListener('mouseleave', () => { fab.style.transform = 'translateY(0) scale(1)'; });

    // 간단한 드래그 이동 지원 (대표님이 원하는 편한 위치에 둘 수 있음)
    let isDragging = false, dragMoved = false;
    let startX = 0, startY = 0, initialRight = 18, initialBottom = 165;

    fab.addEventListener('mousedown', (e) => {
      isDragging = true;
      dragMoved = false;
      startX = e.clientX;
      startY = e.clientY;
      const rect = fab.getBoundingClientRect();
      initialRight = window.innerWidth - rect.right;
      initialBottom = window.innerHeight - rect.bottom;
      fab.style.cursor = 'grabbing';
      e.preventDefault();
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const dx = startX - e.clientX;
      const dy = startY - e.clientY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true;
      const newRight = Math.max(10, Math.min(window.innerWidth - 80, initialRight + dx));
      const newBottom = Math.max(20, Math.min(window.innerHeight - 60, initialBottom + dy));
      fab.style.right = `${newRight}px`;
      fab.style.bottom = `${newBottom}px`;
    });

    window.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        fab.style.cursor = 'grab';
      }
    });

    fab.addEventListener('click', (e) => {
      if (dragMoved) { e.preventDefault(); return; } // 드래그했을 땐 열기 취소
      try { this.manualOpen(); } catch (_) {}
    });

    document.body.appendChild(fab);
    this._syncFab();
  }

  _syncFab() {
    const fab = document.getElementById('browser-reopen-fab');
    if (!fab) return;
    const cardVisible = !!(this.activeCard && document.contains(this.activeCard));
    fab.style.display = cardVisible ? 'none' : 'flex';
  }

  /**
   * 수동 열기 — 카드 마운트 + WS 경유 페이지 이동.
   * (WS는 원격 프록시를 타므로 폰에서도 동작. 서버가 꺼져 있으면 스트림 접속 시 자동 기동)
   */
  async manualOpen() {
    this._suspended = false;
    const url = this.lastUrl || 'https://www.google.com';
    this.mount(null, url);
    for (let i = 0; i < 50; i++) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) break;
      await new Promise((r) => setTimeout(r, 100));
    }
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ t: 'open', url }));
      }
    } catch (_) {}
    try { this._syncFab(); } catch (_) {}
  }
}

export const liveBrowser = new BrowserViewer();
window.liveBrowser = liveBrowser;

// 🌐 수동 열기 버튼은 모듈 로드 즉시 생성 (카드 유무와 무관 — 카드 없으면 노출, 있으면 숨김)
try {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { try { liveBrowser.ensureReopenFab(); } catch (_) {} });
  } else {
    liveBrowser.ensureReopenFab();
  }
} catch (_) {}

/**
 * 새로고침 자동복구 — WS 프로브(?probe=1)로 백엔드 생존 확인 후 카드 복원.
 * (구 /health 직접 fetch는 원격 https에서 혼합콘텐츠 차단 + 폰 localhost라 실패했음.
 *  WS는 getBrowserWsUrl()이 원격 프록시(wss://.../stream)를 타므로 어디서든 동작)
 * 백엔드가 꺼져 있으면 조용히 패스 (에이전트가 시동 시 mount로 띄움)
 */
async function __browserViewerRestoreOnLoad() {
  try {
    if (liveBrowser.activeCard && document.contains(liveBrowser.activeCard)) return;
    const base = getBrowserWsUrl();
    const probeUrl = base + (base.includes('?') ? '&' : '?') + 'probe=1';
    let ws = null;
    try { ws = new WebSocket(probeUrl); } catch (_) { return; }
    const hello = await new Promise((resolve) => {
      const timer = setTimeout(() => { try { ws.close(); } catch (_) {} resolve(null); }, 5000);
      ws.onmessage = (e) => {
        try {
          const m = JSON.parse(e.data);
          if (m && m.t === 'hello') { clearTimeout(timer); resolve(m); }
        } catch (_) {}
      };
      ws.onerror = () => { clearTimeout(timer); resolve(null); };
      ws.onclose = () => { clearTimeout(timer); resolve(null); };
    });
    try { ws.close(); } catch (_) {}
    if (hello && hello.browser_running) {
      console.log('[BrowserViewer] 🔄 새로고침 복구 — 백엔드 살아있음, 카드 복원:', hello.current_url || '');
      liveBrowser.mount(null, hello.current_url || '');
    }
    // 백엔드가 죽어 있어도 수동 열기 버튼은 보장
    try { liveBrowser.ensureReopenFab(); } catch (_) {}
  } catch (_) { /* 무시 */ }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => setTimeout(__browserViewerRestoreOnLoad, 800));
} else {
  setTimeout(__browserViewerRestoreOnLoad, 800);
}

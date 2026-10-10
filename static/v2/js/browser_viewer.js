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
  if (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') {
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
  }

  /**
   * 브라우저 활동 감지 시 2분 Idle 타이머 리셋
   * - 잦은 타이머 재설정을 방지하기 위해 1초 단위로 쓰로틀링
   */
  _touchIdle() {
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
      console.log('[BrowserViewer] ⏰ 2분간 무활동(Idle) 감지되어 브라우저 뷰어 및 백엔드 세션을 자동 정리합니다.');
      this.close();
    }, IDLE_TIMEOUT_MS);
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
    let container = targetContainer;
    if (!container || !document.contains(container)) {
      const chatContainer = document.getElementById('chat-messages-container');
      const lastArticle = chatContainer?.lastElementChild;
      const responseBody = lastArticle?.querySelector('.response-body') || lastArticle;
      container = responseBody || chatContainer || document.body;
    }
    console.log('[BrowserViewer] 🚀 mount 호출됨, container:', container, 'initialUrl:', initialUrl || '(없음)');
    if (initialUrl) this.lastUrl = initialUrl;
    this.turnCompleted = false;

    const existingInDom = document.querySelector('#browser-live-viewer-card');
    if (existingInDom && document.contains(existingInDom)) {
      this.activeCard = existingInDom;
      this.isOpen = true;
      if (initialUrl) this.updateUrl(initialUrl);
      this.updateBadgeUI();
      this._touchIdle();
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        this.connectStream();
      }
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
        <div id="browser-screencast-wrapper" class="relative flex items-center justify-center transition-transform duration-150 origin-center">
          <img id="browser-screencast-img" class="max-w-full max-h-full object-contain pointer-events-auto cursor-default hidden" draggable="false" alt="" />
        </div>
        <div id="browser-viewer-loading" class="absolute inset-0 flex flex-col items-center justify-center bg-black/75 text-neutral-300 gap-2 z-10 pointer-events-none">
          <span class="material-symbols-outlined text-[28px] animate-spin text-emerald-400">progress_activity</span>
          <span class="text-[12px] font-mono">브라우저 화면 스트림 동기화 중...</span>
        </div>
      </div>
    `;

    // 이벤트 리스너 등록
    card.querySelector('#browser-viewer-close-btn')?.addEventListener('click', () => {
      this.close();
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

    // 타겟 컨테이너에 추가 (에이전트 메시지 본문)
    container.appendChild(card);
    this.activeCard = card;
    this.isOpen = true;

    // WebSocket 스트림 연결 및 입력 주입 리스너 설정
    this.connectStream();

    // 2분 무활동 자동 정리 타이머 가동
    this._touchIdle();

    // 부드러운 스크롤 안착
    if (typeof window.scrollChatToBottom === 'function') {
      window.scrollChatToBottom(true);
    }
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
        btn.className = 'px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all flex items-center gap-1 cursor-pointer bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30 border border-emerald-500/50';
      }
      if (label) label.textContent = '에이전트 제어로 복귀';
      if (banner) banner.classList.remove('hidden');
      if (img) img.style.cursor = 'crosshair';

      // 사람이 권한을 넘겨받으면 시원하게 대화면으로 확대!
      if (!this.isExpanded) {
        this.setExpanded(true);
      }
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
    const send = (data) => {
      this._touchIdle();
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify(data));
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

    img.addEventListener('contextmenu', e => e.preventDefault());
    img.addEventListener('mousemove', e => send({ t: 'mouse', kind: 'move', ...rel(e) }));
    img.addEventListener('mousedown', e => send({ t: 'mouse', kind: 'down', ...rel(e) }));
    window.addEventListener('mouseup', e => {
      if (this.isOpen) send({ t: 'mouse', kind: 'up', ...rel(e) });
    });

    img.addEventListener('wheel', e => {
      e.preventDefault();
      send({ t: 'wheel', ...rel(e), deltaY: e.deltaY });
    }, { passive: false });

    let isHovered = false;
    img.addEventListener('mouseenter', () => { isHovered = true; this._touchIdle(); });
    img.addEventListener('mouseleave', () => { isHovered = false; });

    const keydownHandler = (e) => {
      if (!this.isOpen || (!isHovered && !this.isTakeover)) return;
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
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
      if (!this.isOpen || (!isHovered && !this.isTakeover)) return;
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      send({ t: 'key', kind: 'up', key: e.key, code: e.code });
    };

    window.addEventListener('keydown', keydownHandler);
    window.addEventListener('keyup', keyupHandler);
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
  }
}

export const liveBrowser = new BrowserViewer();
window.liveBrowser = liveBrowser;

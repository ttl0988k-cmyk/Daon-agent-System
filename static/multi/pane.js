/**
 * DAON Multi-Agent Orchestrator — pane.js
 * Single Pane Component Renderer and Controller.
 * Manages independent session, EventSource stream, reasoning accordion, and tool HUD.
 * Features:
 *  - Auto-interrupt & send while working (Never locks input)
 *  - Prominent stop button (Header + Input bar dynamic toggle)
 *  - Full session loading & history restoration from Classic View
 *  - Bidirectional session synchronization with localStorage
 */

import { api } from './api.js';
import { AgentStream } from './stream.js';

export class Pane {
  constructor(options = {}) {
    this.id = options.id || `pane-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
    this.profile = options.profile || 'raon';
    this.profiles = options.profiles || [];
    this.models = options.models || [];
    this.modelData = options.modelData || null;
    this.workspace = options.workspace || 'C:\\daon';
    this.sessionId = options.sessionId || null;

    // Auto-detect model from profile metadata if available
    const profMeta = (this.profiles || []).find(p => p.name === this.profile);
    const defaultFromProf = profMeta && profMeta.model ? profMeta.model : '';
    let chosen = options.model || defaultFromProf || 'MiniMax-M3';
    if (chosen.toLowerCase().startsWith('auto')) {
      chosen = 'MiniMax-M3';
    }
    this.currentModel = chosen;

    this.container = options.container;
    this.currentStreamId = null;
    this.stream = null;
    this.status = 'idle'; // idle | working | blocked | waiting | error
    this.autoScroll = true;

    // Callbacks
    this.onStatusChange = options.onStatusChange || (() => {});
    this.onDestroy = options.onDestroy || (() => {});
    this.onDispatch = options.onDispatch || (() => {});
    this.onSessionChange = options.onSessionChange || (() => {});
    this.getRoomContext = options.getRoomContext || null;

    // Active message stream buffers
    this.activeAssistantMsgEl = null;
    this.activeAssistantRawText = '';
    this.activeThoughtContentEl = null;
    this.activeThoughtDetailsEl = null;
    this.activeThoughtRaw = '';
    this.activeToolHudEl = null;

    // Track stream IDs to prevent duplicate attaches or cancelled stream replay loops
    this._cancelledStreams = new Set();
    this._attachedStreams = new Set();

    // Init gate: resolves when init() completes so external callers can await it
    this._initDone = false;
    this._initReady = new Promise(resolve => { this._resolveInit = resolve; });

    // Defer poller start until init finishes to avoid premature attach
    this._initReady.then(() => {
      this.pollTimer = setInterval(() => this.checkExternalStream(), 1800);
    });

    this.init();
  }

  getAgentMeta(profileName) {
    const p = (profileName || '').toLowerCase();
    if (p.includes('raon') || p.includes('라온')) return { icon: '👑', role: '총괄사령관', colorClass: 'raon' };
    if (p.includes('bill') || p.includes('빌')) return { icon: '🔨', role: '개발구현', colorClass: 'bill' };
    if (p.includes('sherlock') || p.includes('셜록')) return { icon: '🔍', role: '품질검수', colorClass: 'sherlock' };
    if (p.includes('tony') || p.includes('토니')) return { icon: '💡', role: '전략기획', colorClass: 'tony' };
    if (p.includes('prada') || p.includes('프라다')) return { icon: '🎨', role: '디자인감성', colorClass: 'prada' };
    if (p.includes('codex') || p.includes('코덱스')) return { icon: '⚡', role: '코딩워커 (Codex)', colorClass: 'codex' };
    if (p.includes('claude') || p.includes('클로드')) return { icon: '🔮', role: '코딩워커 (Claude)', colorClass: 'claude' };
    return { icon: '🤖', role: '전문워커', colorClass: 'default' };
  }

  isWorkerProfile(p) {
    const s = (p || '').toLowerCase();
    return s.includes('codex') || s.includes('코덱스') || s.includes('claude') || s.includes('클로드');
  }

  getWorkerName(p) {
    const s = (p || '').toLowerCase();
    if (s.includes('claude') || s.includes('클로드')) return 'worker-claude';
    return 'worker-codex';
  }

  async init() {
    this.render();
    this.bindEvents();
    if (this.isWorkerProfile(this.profile)) {
      await this.initWorkerMode();
    } else {
      if (this.sessionId) {
        const ok = await this.loadSession(this.sessionId);
        if (!ok) await this.setupNewSession();
      } else {
        await this.setupNewSession();
      }
    }
    // Signal that init is fully complete — unblocks attachToStream / checkExternalStream
    this._initDone = true;
    this._resolveInit();
  }

  render() {
    const meta = this.getAgentMeta(this.profile);
    const el = document.createElement('div');
    el.className = `pane ${meta.colorClass}`;
    el.id = this.id;
    el.dataset.profile = this.profile;

    // Generate Profile Options
    const profileOptionsHtml = this.profiles.map(p => {
      const selected = p.name === this.profile ? 'selected' : '';
      return `<option value="${p.name}" ${selected}>${p.name}</option>`;
    }).join('');

    // Generate Model Options (grouped by Provider)
    let modelOptionsHtml = '';
    if (this.modelData && this.modelData.groups && this.modelData.groups.length > 0) {
      modelOptionsHtml = this.modelData.groups.map(group => {
        const opts = (group.models || []).map(m => {
          const selected = m.id === this.currentModel ? 'selected' : '';
          return `<option value="${m.id}" ${selected}>${m.label || m.id}</option>`;
        }).join('');
        return `<optgroup label="[${group.provider}]">${opts}</optgroup>`;
      }).join('');
    } else {
      modelOptionsHtml = this.models.map(m => {
        const selected = m === this.currentModel ? 'selected' : '';
        return `<option value="${m}" ${selected}>${m}</option>`;
      }).join('');
    }

    el.innerHTML = `
      <!-- Pane Header -->
      <div class="pane-header">
        <div class="pane-meta">
          <span class="agent-icon" id="${this.id}-icon">${meta.icon}</span>
          <select class="profile-select" id="${this.id}-profile-select" title="에이전트 프로파일 선택">
            ${profileOptionsHtml || `<option value="${this.profile}">${this.profile}</option>`}
          </select>
          <span class="pane-badge-status idle" id="${this.id}-badge">
            <span class="status-indicator-dot"></span>
            <span class="status-text">IDLE</span>
          </span>
          <select class="model-select" id="${this.id}-model-select" title="에이전트 실행 모델 변경">
            ${modelOptionsHtml}
          </select>
        </div>
        <div class="pane-tools">
          <button class="pane-tool-btn new-sess-btn" id="${this.id}-new-sess-btn" title="새 대화 세션 시작">＋</button>
          <button class="pane-tool-btn cancel-btn" id="${this.id}-cancel-btn" title="작업 중단 (Stop)" style="display:none;">■ 중지</button>
          <button class="pane-tool-btn close-btn" id="${this.id}-close-btn" title="창 닫기">✕</button>
        </div>
      </div>

      <!-- Pane Scrollable Message Body -->
      <div class="pane-body" id="${this.id}-body">
        <div class="msg assistant system-welcome">
          <div class="msg-sender">${meta.icon} ${this.profile} (${meta.role})</div>
          <div class="msg-bubble">
            <strong>${this.profile}</strong> 세션을 준비하는 중입니다...
          </div>
        </div>
      </div>

      <!-- Pane Footer / Input Bar -->
      <div class="pane-footer">
        <div class="pane-quick-dispatch" id="${this.id}-quick-dispatch">
          <span class="dispatch-label">⚡ 빠른전달:</span>
          <span class="dispatch-tag" data-target="라온">@라온</span>
          <span class="dispatch-tag" data-target="빌">@빌</span>
          <span class="dispatch-tag" data-target="셜록">@셜록</span>
          <span class="dispatch-tag" data-target="토니">@토니</span>
          <span class="dispatch-tag" data-target="코덱스">@코덱스</span>
        </div>
        <div class="pane-input-bar">
          <textarea 
            class="pane-textarea" 
            id="${this.id}-input" 
            placeholder="${this.profile}에게 지시 입력... (Enter: 전송, Shift+Enter: 줄바꿈)"
            rows="1"></textarea>
          <button class="pane-send-btn" id="${this.id}-send-btn" title="전송 (Enter)">
            ▶
          </button>
        </div>
      </div>
    `;

    this.container.appendChild(el);
    this.el = el;
    this.bodyEl = el.querySelector(`#${this.id}-body`);
    this.inputEl = el.querySelector(`#${this.id}-input`);
    this.sendBtn = el.querySelector(`#${this.id}-send-btn`);
    this.cancelBtn = el.querySelector(`#${this.id}-cancel-btn`);
    this.badgeEl = el.querySelector(`#${this.id}-badge`);
    this.profileSelect = el.querySelector(`#${this.id}-profile-select`);
    this.modelSelect = el.querySelector(`#${this.id}-model-select`);
    this.iconEl = el.querySelector(`#${this.id}-icon`);
  }

  bindEvents() {
    // 0. Focus / Click sync to Classic active session
    this.el.addEventListener('click', () => {
      if (this.sessionId) {
        try { localStorage.setItem('daon_active_session_id', this.sessionId); } catch (_) {}
      }
    });

    // 0-1. New Session button
    const newSessBtn = this.el.querySelector(`#${this.id}-new-sess-btn`);
    if (newSessBtn) {
      newSessBtn.addEventListener('click', async () => {
        if (this.status === 'working') {
          if (!confirm('현재 작업이 진행 중입니다. 새 세션을 시작하시겠습니까?')) return;
          await this.handleCancel();
        }
        await this.setupNewSession();
      });
    }

    // 1. Profile Hot-Swap
    this.profileSelect.addEventListener('change', async (e) => {
      const newProfile = e.target.value;
      if (newProfile === this.profile) return;
      await this.switchProfile(newProfile);
    });

    // 1-1. Model Selection Hot-Swap
    if (this.modelSelect) {
      this.modelSelect.addEventListener('change', async (e) => {
        const newModel = e.target.value;
        this.currentModel = newModel;
        if (this.isWorker) {
          this.appendMessage('assistant', `🔄 워커 두뇌를 **${newModel}**(으)로 전환 중입니다...`);
          this.setStatus('waiting');
          try {
            const res = await api.setWorkerModel(this.workerName, newModel);
            const displayModel = res.worker?.model || newModel;
            this.appendMessage('assistant', `✅ 워커 두뇌가 **${displayModel}**(으)로 성공적으로 전환되었습니다.`);
            this.setStatus('idle');
          } catch (err) {
            console.error('setWorkerModel error:', err);
            this.appendMessage('assistant', `⚠️ 모델 전환 중 오류가 발생했습니다: ${err.message}`);
            this.setStatus('idle');
          }
        } else {
          this.appendMessage('assistant', `🤖 실행 모델이 **${newModel}**(으)로 변경되었습니다.`);
        }
      });
    }

    // 2. Send Button Click
    this.sendBtn.addEventListener('click', () => {
      const isWorking = (this.status === 'working' || this.status === 'blocked');
      const hasText = (this.inputEl.value || '').trim().length > 0;
      if (isWorking && !hasText) {
        this.handleCancel();
      } else {
        this.handleSendMessage();
      }
    });

    // 3. Textarea Keydown (Enter to send, Shift+Enter for newline)
    this.inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.handleSendMessage();
      }
    });

    // 4. Auto-grow Textarea & dynamic send button icon
    this.inputEl.addEventListener('input', () => {
      this.inputEl.style.height = 'auto';
      this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px';
      this.updateSendButtonState();
    });

    // 5. Cancel Work (■)
    this.cancelBtn.addEventListener('click', () => this.handleCancel());

    // 6. Close Pane (✕)
    this.el.querySelector(`#${this.id}-close-btn`).addEventListener('click', () => this.destroy());

    // 7. Quick Dispatch Tag Clicks
    const tags = this.el.querySelectorAll('.dispatch-tag');
    tags.forEach(tag => {
      tag.addEventListener('click', () => {
        const target = tag.dataset.target;
        this.inputEl.value = `@${target} ` + this.inputEl.value;
        this.inputEl.focus();
        this.updateSendButtonState();
      });
    });
  }

  updateSendButtonState() {
    const isWorking = (this.status === 'working' || this.status === 'blocked');
    const hasText = (this.inputEl.value || '').trim().length > 0;

    if (isWorking) {
      if (hasText) {
        this.sendBtn.className = 'pane-send-btn working-interrupt';
        this.sendBtn.innerHTML = '⚡';
        this.sendBtn.title = '이전 작업 중단하고 새 지시 즉시 실행 (Enter)';
        this.sendBtn.disabled = false;
      } else {
        this.sendBtn.className = 'pane-send-btn working-cancel';
        this.sendBtn.innerHTML = '■';
        this.sendBtn.title = '작업 중단 (Stop)';
        this.sendBtn.disabled = false;
      }
    } else {
      this.sendBtn.className = 'pane-send-btn';
      this.sendBtn.innerHTML = '▶';
      this.sendBtn.title = '전송 (Enter)';
      this.sendBtn.disabled = false;
    }
  }

  setStatus(status) {
    this.status = status;
    const badgeText = this.badgeEl.querySelector('.status-text');
    this.badgeEl.className = `pane-badge-status ${status}`;
    if (badgeText) badgeText.textContent = status.toUpperCase();

    // ★ CRITICAL FIX: NEVER disable inputEl! User must be able to type anytime
    this.inputEl.disabled = false;

    const isWorking = (status === 'working' || status === 'blocked');
    this.cancelBtn.style.display = isWorking ? 'inline-flex' : 'none';
    this.updateSendButtonState();

    this.onStatusChange(this.id, status, this.profile);
  }

  async loadSession(sessionId) {
    try {
      this.setStatus('waiting');
      const res = await api.getSession(sessionId);
      if (!res || !res.session) throw new Error('세션 데이터를 찾을 수 없습니다.');
      const s = res.session;

      this.sessionId = s.session_id;
      this.workspace = s.workspace || this.workspace;

      if (s.profile && s.profile !== this.profile) {
        this.profile = s.profile;
        this.el.dataset.profile = s.profile;
        const meta = this.getAgentMeta(this.profile);
        this.iconEl.textContent = meta.icon;
        if (this.profileSelect) this.profileSelect.value = this.profile;
      }

      if (s.model) {
        this.currentModel = s.model;
        if (this.modelSelect) this.modelSelect.value = s.model;
      }

      // If a live stream is already attached (peer dispatch arrived before
      // loadSession finished), do NOT clear the DOM or reset status — the
      // stream handler owns the body now.
      if (this.status !== 'working' && !this.currentStreamId) {
        this.renderHistory(s.messages || []);
        this.setStatus('idle');
      } else {
        console.log(`[Pane ${this.profile}] loadSession skipped DOM reset — active stream ${this.currentStreamId} takes priority`);
      }
      try { localStorage.setItem('daon_active_session_id', this.sessionId); } catch (_) {}
      if (this.onSessionChange) this.onSessionChange(this.id, this.sessionId);
      return true;
    } catch (err) {
      console.warn(`[Pane ${this.profile}] loadSession failed for ${sessionId}:`, err);
      return false;
    }
  }

  async setupNewSession() {
    try {
      this.setStatus('waiting');
      const res = await api.createSession(this.profile, this.workspace, this.currentModel);
      if (res && res.session) {
        this.sessionId = res.session.session_id;
        const meta = this.getAgentMeta(this.profile);
        this.bodyEl.innerHTML = `
          <div class="msg assistant system-welcome">
            <div class="msg-sender">${meta.icon} ${this.profile} (${meta.role})</div>
            <div class="msg-bubble">
              <strong>${this.profile}</strong> 새 대화 세션이 준비되었습니다. 명령을 입력하세요.
            </div>
          </div>
        `;
        this.setStatus('idle');
        try { localStorage.setItem('daon_active_session_id', this.sessionId); } catch (_) {}
        if (this.onSessionChange) this.onSessionChange(this.id, this.sessionId);
      } else {
        throw new Error('Invalid session response');
      }
    } catch (err) {
      console.error(`[Pane ${this.profile}] setupNewSession failed:`, err);
      this.setStatus('error');
      this.appendMessage('assistant', `⚠️ 세션 생성 실패: ${err.message}`);
    }
  }

  // ── Worker Mode Handlers (Herdr Integration) ──

  async initWorkerMode() {
    this.isWorker = true;
    this.workerName = this.getWorkerName(this.profile);
    this._workerTurnsRendered = [];
    this._isWorkerRendering = false;
    const meta = this.getAgentMeta(this.profile);

    this.setStatus('waiting');
    this.bodyEl.innerHTML = `
      <div class="msg assistant system-welcome">
        <div class="msg-sender">${meta.icon} ${this.profile} (${meta.role})</div>
        <div class="msg-bubble">
          <strong>${this.profile}</strong> Herdr PTY 워커 세션에 연결 중입니다...
        </div>
      </div>
    `;

    try {
      const res = await api.startWorker(this.workerName, this.workerName.includes('claude') ? 'claude' : 'codex', this.workspace);
      const worker = res.worker || (await api.getWorkers()).workers?.find(w => w.name === this.workerName);
      if (worker) {
        this.renderWorkerState(worker);
      } else {
        this.setStatus('idle');
      }
    } catch (err) {
      console.error(`[Pane ${this.profile}] initWorkerMode error:`, err);
      this.setStatus('error');
      this.appendMessage('assistant', `⚠️ 워커 연결 실패: ${err.message}`);
    }

    this.connectWorkerStream();
  }

  renderWorkerState(worker) {
    if (!worker) return;
    const meta = this.getAgentMeta(this.profile);
    const turns = worker.turns || [];

    if (this._isWorkerRendering) return;
    this._isWorkerRendering = true;

    try {
      if (!this._workerTurnsRendered) {
        this._workerTurnsRendered = [];
      }

      if (turns.length === 0 && this.bodyEl.children.length === 0) {
        this.bodyEl.innerHTML = `
          <div class="msg assistant system-welcome">
            <div class="msg-sender">${meta.icon} ${this.profile} (${meta.role})</div>
            <div class="msg-bubble">
              <strong>${this.profile}</strong> 워커가 준비되었습니다. (두뇌: <strong>${worker.model || 'MiniMax-M3'}</strong>)<br>
              <span style="font-size:12px; color:var(--text-dim); display:inline-block; margin-top:4px;">
                ✨ 상단 모델 선택창에서 언제든 원하는 프로바이더/모델로 즉시 변경할 수 있습니다.
              </span>
            </div>
          </div>
        `;
      } else if (turns.length > 0) {
        // Remove welcome placeholder if exists
        const welcome = this.bodyEl.querySelector('.system-welcome');
        if (welcome) welcome.remove();

        // Check each turn in worker.turns: only append if not already in _workerTurnsRendered
        turns.forEach((turn, idx) => {
          const isLatest = (idx === turns.length - 1);
          const sig = `${turn.role}:${(turn.content || '').trim()}`;
          const alreadyRendered = this._workerTurnsRendered.includes(sig);

          if (!alreadyRendered) {
            this._workerTurnsRendered.push(sig);
            if (turn.role === 'user') {
              this.appendMessage('user', turn.content);
            } else {
              this.appendWorkerMessage(turn.content, isLatest ? worker.raw_terminal : null);
            }
          }
        });
      }

      // Handle approval card cleanly
      if (worker.blocked_info) {
        this.renderApprovalCard(worker.blocked_info);
      } else {
        const existing = document.getElementById(`${this.id}-approval-card`);
        if (existing) existing.remove();
      }

      this.setStatus(worker.status || 'idle');
    } finally {
      this._isWorkerRendering = false;
    }
  }

  appendWorkerMessage(cleanContent, rawTerminal = null) {
    const meta = this.getAgentMeta(this.profile);
    const msgEl = document.createElement('div');
    msgEl.className = 'msg assistant';

    let formattedText = typeof marked !== 'undefined' ? marked.parse(cleanContent) : cleanContent.replace(/\n/g, '<br>');
    let terminalDetailsHtml = '';

    if (rawTerminal) {
      const lineCount = rawTerminal.split('\n').length;
      terminalDetailsHtml = `
        <details class="worker-terminal-details">
          <summary>🔍 터미널 실행 상세/도구 로그 접기/펼치기 (${lineCount}줄)</summary>
          <pre class="worker-terminal-raw">${this.escapeHtml(rawTerminal)}</pre>
        </details>
      `;
    }

    msgEl.innerHTML = `
      <div class="msg-sender">${meta.icon} ${this.profile}</div>
      <div class="msg-bubble">
        <div class="msg-content">${formattedText}</div>
        ${terminalDetailsHtml}
      </div>
    `;

    this.bodyEl.appendChild(msgEl);
    this.scrollToBottom();
  }

  renderApprovalCard(blockedInfo) {
    const existing = document.getElementById(`${this.id}-approval-card`);
    if (existing) existing.remove();

    const card = document.createElement('div');
    card.className = 'worker-approval-card';
    card.id = `${this.id}-approval-card`;

    const title = blockedInfo.type === 'folder_trust' ? '작업 폴더 신뢰 승인 요청' : '도구 / 명령 실행 승인 요청';
    card.innerHTML = `
      <div class="approval-header">⚠️ ${title}</div>
      <div class="approval-question">${this.escapeHtml(blockedInfo.question)}</div>
      <div class="approval-actions">
        <button class="btn-approve" id="${this.id}-btn-approve">승인 (Enter/Y)</button>
        <button class="btn-reject" id="${this.id}-btn-reject">거절 (Esc/N)</button>
      </div>
    `;

    this.bodyEl.appendChild(card);
    this.scrollToBottom();

    const btnApprove = card.querySelector(`#${this.id}-btn-approve`);
    if (btnApprove) {
      btnApprove.addEventListener('click', async () => {
        card.remove();
        this.setStatus('working');
        try {
          await api.approveWorker(this.workerName, 'approve');
        } catch (err) {
          console.error('Approve failed:', err);
        }
      });
    }

    const btnReject = card.querySelector(`#${this.id}-btn-reject`);
    if (btnReject) {
      btnReject.addEventListener('click', async () => {
        card.remove();
        this.setStatus('working');
        try {
          await api.approveWorker(this.workerName, 'reject');
        } catch (err) {
          console.error('Reject failed:', err);
        }
      });
    }
  }

  connectWorkerStream() {
    if (this._workerEventSource) {
      try { this._workerEventSource.close(); } catch (_) {}
      this._workerEventSource = null;
    }

    try {
      this._workerEventSource = new EventSource('/api/workers/stream');
      this._workerEventSource.addEventListener('worker_status', (e) => {
        const d = JSON.parse(e.data);
        if (d.name === this.workerName) {
          this.setStatus(d.status);
        }
      });
      this._workerEventSource.addEventListener('worker_done', (e) => {
        const d = JSON.parse(e.data);
        if (d.worker && d.worker.name === this.workerName) {
          this.renderWorkerState(d.worker);
        }
      });
      this._workerEventSource.addEventListener('worker_approved', (e) => {
        const d = JSON.parse(e.data);
        if (d.worker && d.worker.name === this.workerName) {
          this.renderWorkerState(d.worker);
        }
      });
      this._workerEventSource.onerror = () => {
        // SSE disconnected, fallback to active status poll
        this.startWorkerStatusPoll();
      };
    } catch (err) {
      console.warn(`[Pane ${this.profile}] Worker SSE stream error:`, err);
      this.startWorkerStatusPoll();
    }
  }

  startWorkerStatusPoll(startTurnCount = null) {
    if (this._workerPollTimer) clearInterval(this._workerPollTimer);
    const initialCount = startTurnCount !== null ? startTurnCount : (this._workerTurnsRendered ? this._workerTurnsRendered.length : 0);
    const startTime = Date.now();
    const maxPollTime = 180000; // 3 minutes timeout

    this._workerPollTimer = setInterval(async () => {
      if (!this.isWorker) {
        clearInterval(this._workerPollTimer);
        this._workerPollTimer = null;
        return;
      }
      try {
        const res = await api.getWorkers();
        const w = res.workers?.find(x => x.name === this.workerName);
        if (!w) return;

        // Render current state incrementally
        this.renderWorkerState(w);

        const currentCount = this._workerTurnsRendered ? this._workerTurnsRendered.length : 0;
        const hasNewTurn = currentCount > initialCount;
        const isNotWorking = (w.status !== 'working');
        const isBlockedWithCard = (w.status === 'blocked' && !!w.blocked_info);
        const timedOut = (Date.now() - startTime) > maxPollTime;

        // Terminate polling ONLY when:
        // 1) A new turn has arrived AND worker is not working (idle/done)
        // 2) Worker is legitimately blocked waiting for user approval (card is displayed)
        // 3) Timeout
        if ((hasNewTurn && isNotWorking) || isBlockedWithCard || timedOut) {
          clearInterval(this._workerPollTimer);
          this._workerPollTimer = null;
          this.setStatus(w.status || 'idle');
        } else {
          this.setStatus('working');
        }
      } catch (e) {
        console.warn('Worker poll check error:', e);
      }
    }, 1200);
  }

  renderHistory(messages) {
    this.bodyEl.innerHTML = '';
    const meta = this.getAgentMeta(this.profile);

    if (!messages || messages.length === 0) {
      this.bodyEl.innerHTML = `
        <div class="msg assistant system-welcome">
          <div class="msg-sender">${meta.icon} ${this.profile} (${meta.role})</div>
          <div class="msg-bubble">
            <strong>${this.profile}</strong> 세션이 준비되었습니다. (${this.sessionId ? this.sessionId.slice(0, 8) + '…' : ''})
          </div>
        </div>
      `;
      return;
    }

    messages.forEach(m => {
      if (!m || !m.role || m.role === 'tool') return;

      if (m.role === 'user') {
        const text = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
        this.appendMessage('user', text);
      } else if (m.role === 'assistant') {
        const msgEl = document.createElement('div');
        msgEl.className = 'msg assistant';
        let inner = `<div class="msg-sender">${meta.icon} ${this.profile}</div><div class="msg-bubble">`;

        // Reasoning / Thinking
        const reasoning = m.reasoning_content || m.thinking;
        if (reasoning) {
          inner += `
            <details class="thought-block">
              <summary class="thought-summary">💭 사고 과정 (Reasoning)</summary>
              <div class="thought-content">${this.escapeHtml(reasoning)}</div>
            </details>
          `;
        }

        // Assistant Content
        const contentStr = typeof m.content === 'string' ? m.content : (m.content ? JSON.stringify(m.content) : '');
        if (contentStr) {
          inner += `<div class="stream-content">${this.renderMarkdown(contentStr)}</div>`;
        }

        // Tool calls
        if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
          m.tool_calls.forEach(tc => {
            const fname = (tc.function && tc.function.name) || tc.name || 'tool';
            inner += `
              <div class="tool-hud done" style="margin-top:6px;">
                <span>✓</span>
                <span>도구 실행 완료: <strong>${this.escapeHtml(fname)}</strong></span>
              </div>
            `;
          });
        }

        inner += `</div>`;
        msgEl.innerHTML = inner;
        this.bodyEl.appendChild(msgEl);
      }
    });

    this.scrollToBottom();
  }

  async switchProfile(newProfile) {
    if (this.status === 'working') {
      if (!confirm('현재 작업이 진행 중입니다. 프로파일을 변경하면 작업이 중단됩니다. 변경하시겠습니까?')) {
        this.profileSelect.value = this.profile;
        return;
      }
      await this.handleCancel();
    }

    const wasWorker = this.isWorker;
    const isNowWorker = this.isWorkerProfile(newProfile);

    this.profile = newProfile;
    this.el.dataset.profile = newProfile;
    const meta = this.getAgentMeta(newProfile);
    this.iconEl.textContent = meta.icon;
    this.inputEl.placeholder = `${newProfile}에게 지시 입력... (Enter: 전송)`;

    if (isNowWorker) {
      await this.initWorkerMode();
      this.onStatusChange(this.id, this.status, this.profile);
      return;
    }

    if (wasWorker && !isNowWorker) {
      this.isWorker = false;
      if (this._workerEventSource) {
        try { this._workerEventSource.close(); } catch (_) {}
      }
    }

    // Auto-sync preferred model for the profile
    const profMeta = (this.profiles || []).find(p => p.name === newProfile);
    if (profMeta && profMeta.model) {
      this.currentModel = profMeta.model;
      if (this.modelSelect) this.modelSelect.value = profMeta.model;
    }
    
    // Reset session and re-create
    await this.setupNewSession();
    this.appendMessage('assistant', `🔄 프로파일이 **${newProfile}**(${meta.role})로 변경되었습니다.`);
    this.onStatusChange(this.id, this.status, this.profile);
  }

  async handleSendMessage(forcedText = null) {
    const text = (forcedText || this.inputEl.value).trim();
    if (!text) return;

    // Reset input
    if (!forcedText) {
      this.inputEl.value = '';
      this.inputEl.style.height = 'auto';
      this.updateSendButtonState();
    }

    if (this.isWorker) {
      // ⚡ Worker Mode Prompt
      if (!this._workerTurnsRendered) this._workerTurnsRendered = [];
      const userSig = `user:${text.trim()}`;
      this._workerTurnsRendered.push(userSig);
      this.appendMessage('user', text);

      this.setStatus('working');
      try {
        const initialTurns = this._workerTurnsRendered.length;
        await api.promptWorker(this.workerName, text);
        this.startWorkerStatusPoll(initialTurns);
      } catch (err) {
        console.error(`[Pane ${this.profile}] promptWorker failed:`, err);
        this.setStatus('error');
        this.appendMessage('assistant', `⚠️ 지시 전송 실패: ${err.message}`);
      }
      return;
    }

    // 1. Render User Message for Standard Agents
    this.appendMessage('user', text);

    // ⚡ Auto-interrupt if already working (Classic view feature!)
    if (this.status === 'working' || this.status === 'blocked' || this.currentStreamId) {
      console.log(`[Pane ${this.profile}] Auto-cancelling in-progress stream before new prompt`);
      const oldStreamId = this.currentStreamId;
      const oldSessionId = this.sessionId;
      if (oldStreamId) {
        this._cancelledStreams.add(oldStreamId);
      }
      if (this.stream) {
        try { this.stream.close(); } catch (_) {}
        this.stream = null;
      }
      this.currentStreamId = null;
      api.cancelChat(oldStreamId, oldSessionId).catch(e => console.warn('Cancel failed:', e));
      this.appendMessage('assistant', '⏹️ 이전 작업을 중지하고 새 지시를 실행합니다.');
    }

    // Sync active session for Classic view
    if (this.sessionId) {
      try { localStorage.setItem('daon_active_session_id', this.sessionId); } catch (_) {}
    }

    // 2. Inter-agent mention check (e.g. "@빌")
    this.checkInterAgentDispatch(text);

    // 3. Prepare Assistant Stream Placeholder
    this.prepareAssistantResponse();

    // 4. Start Chat Execution via Engine
    try {
      this.setStatus('working');
      let backendMessage = text;
      if (this.getRoomContext) {
        const roomCtx = this.getRoomContext(this.id);
        if (roomCtx) {
          backendMessage = `${text}\n\n${roomCtx}`;
        }
      }
      const res = await api.startChat(this.sessionId, backendMessage, this.workspace, this.currentModel);
      this.currentStreamId = res.stream_id;
      if (this.currentStreamId) {
        this._attachedStreams.add(this.currentStreamId);
      }

      // 5. Connect Pane's Own EventSource
      this.stream = new AgentStream(this.currentStreamId, {
        onToken: (token) => this.handleToken(token),
        onReasoning: (chunk) => this.handleReasoning(chunk),
        onJob: (job) => this.handleJob(job),
        onTool: (tool) => this.handleTool(tool),
        onApproval: (approval) => this.handleApproval(approval),
        onStatusChange: (status) => this.setStatus(status),
        onDone: () => this.handleDone(),
        onCancel: () => this.handleCancelDone(),
        onError: (err) => this.handleError(err)
      });
    } catch (err) {
      this.setStatus('error');
      this.appendMessage('assistant', `❌ 실행 요청 오류: ${err.message}`);
    }
  }

  injectMessage(text, autoSend = true) {
    if (!text) return;
    this.inputEl.value = text;
    this.inputEl.style.height = 'auto';
    this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px';
    this.updateSendButtonState();
    if (autoSend) {
      setTimeout(() => {
        this.handleSendMessage(text);
      }, 150);
    }
  }

  checkInterAgentDispatch(text) {
    const match = text.match(/@(빌|라온|셜록|토니|프라다|코덱스|클로드|bill|raon|sherlock|tony|prada|codex|claude)/i);
    if (match) {
      const targetAgent = match[1];
      this.onDispatch({
        from: this.profile,
        to: targetAgent,
        message: text
      });
    }
  }

  prepareAssistantResponse() {
    const meta = this.getAgentMeta(this.profile);
    const msgEl = document.createElement('div');
    msgEl.className = 'msg assistant';
    msgEl.innerHTML = `
      <div class="msg-sender">${meta.icon} ${this.profile}</div>
      <div class="msg-bubble">
        <div class="stream-content"></div>
      </div>
    `;
    this.bodyEl.appendChild(msgEl);
    this.activeAssistantMsgEl = msgEl.querySelector('.stream-content');
    this.activeAssistantRawText = '';
    this.activeThoughtContentEl = null;
    this.activeThoughtDetailsEl = null;
    this.activeThoughtRaw = '';
    this.activeToolHudEl = null;
    this.scrollToBottom();
  }

  handleToken(token) {
    if (!this.activeAssistantMsgEl) return;
    this.activeAssistantRawText = (this.activeAssistantRawText || '') + token;
    this.activeAssistantMsgEl.innerHTML = this.renderMarkdown(this.activeAssistantRawText);
    this.scrollToBottom();
  }

  handleReasoning(chunk) {
    if (!this.activeThoughtDetailsEl) {
      const details = document.createElement('details');
      details.className = 'thought-block';
      details.open = true;
      details.innerHTML = `
        <summary class="thought-summary">💭 사고 과정 (Reasoning)</summary>
        <div class="thought-content"></div>
      `;
      this.activeAssistantMsgEl.parentElement.insertBefore(details, this.activeAssistantMsgEl);
      this.activeThoughtDetailsEl = details;
      this.activeThoughtContentEl = details.querySelector('.thought-content');
      this.activeThoughtRaw = '';
    }
    if (this.activeThoughtContentEl) {
      this.activeThoughtRaw = (this.activeThoughtRaw || '') + chunk;
      this.activeThoughtContentEl.textContent = this.activeThoughtRaw;
      this.scrollToBottom();
    }
  }

  renderMarkdown(text) {
    if (!text) return '';
    let out = text;
    // Code blocks
    out = out.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, (match, lang, code) => {
      return `<pre class="md-code"><code class="lang-${lang}">${this.escapeHtml(code)}</code></pre>`;
    });
    // Inline code
    out = out.replace(/`([^`]+)`/g, (match, code) => {
      return `<code class="md-inline">${this.escapeHtml(code)}</code>`;
    });
    // Bold & Italic
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Line breaks
    out = out.replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>');
    return out;
  }

  handleJob(job) {
    const type = job.type || 'start';
    const toolName = job.tool || job.name || 'tool';

    if (type === 'start') {
      const hud = document.createElement('div');
      hud.className = 'tool-hud';
      hud.innerHTML = `
        <span class="tool-spinner"></span>
        <span class="tool-label">실행 중: <strong>${this.escapeHtml(toolName)}</strong></span>
      `;
      this.activeAssistantMsgEl.parentElement.insertBefore(hud, this.activeAssistantMsgEl);
      this.activeToolHudEl = hud;
    } else if (this.activeToolHudEl) {
      this.activeToolHudEl.className = 'tool-hud done';
      this.activeToolHudEl.innerHTML = `
        <span>✓</span>
        <span>완료: <strong>${this.escapeHtml(toolName)}</strong></span>
      `;
    }
    this.scrollToBottom();
  }

  handleTool(tool) {
    if (this.activeToolHudEl) {
      this.activeToolHudEl.className = 'tool-hud done';
      this.activeToolHudEl.innerHTML = `
        <span>✓</span>
        <span>완료: <strong>${this.escapeHtml(tool.tool || '도구 실행')}</strong></span>
      `;
    }
  }

  handleApproval(approval) {
    const card = document.createElement('div');
    card.className = 'approval-block';
    card.innerHTML = `
      <div class="approval-title">⚠️ 명령 실행 승인 대기 (Blocked)</div>
      <div class="approval-desc">${this.escapeHtml(approval.command || approval.message || '시스템 변경 작업 승인이 필요합니다.')}</div>
      <div class="approval-actions">
        <button class="approval-btn allow" id="${this.id}-allow-btn">승인 (Allow)</button>
        <button class="approval-btn reject" id="${this.id}-reject-btn">거부 (Deny)</button>
      </div>
    `;
    this.bodyEl.appendChild(card);
    this.scrollToBottom();

    card.querySelector(`#${this.id}-allow-btn`).addEventListener('click', async () => {
      await api.respondApproval(this.sessionId, true);
      card.remove();
      this.setStatus('working');
    });

    card.querySelector(`#${this.id}-reject-btn`).addEventListener('click', async () => {
      await api.respondApproval(this.sessionId, false);
      card.remove();
      this.setStatus('idle');
    });
  }

  async checkExternalStream() {
    if (!this._initDone || this.status === 'working' || !this.sessionId) return;
    try {
      const res = await api.getStreamStatus(this.sessionId);
      if (res && res.active && res.stream_id && res.stream_id !== this.currentStreamId) {
        if (this._cancelledStreams.has(res.stream_id) || this._attachedStreams.has(res.stream_id)) {
          return;
        }
        console.log(`[Pane ${this.profile}] External stream detected for session ${this.sessionId}: ${res.stream_id}`);
        this.attachToStream(res.stream_id);
      }
    } catch (_) {}
  }

  async attachToStream(streamId) {
    if (!streamId) return;
    if (this._cancelledStreams.has(streamId)) {
      console.log(`[Pane ${this.profile}] Skipping cancelled stream: ${streamId}`);
      return;
    }
    if (this.currentStreamId === streamId) return;
    if (this._attachedStreams.has(streamId)) {
      console.log(`[Pane ${this.profile}] Skipping already attached stream: ${streamId}`);
      return;
    }
    this._attachedStreams.add(streamId);

    // Wait for init() to finish so loadSession doesn't clobber our DOM afterwards
    await this._initReady;
    this.currentStreamId = streamId;
    this.setStatus('working');

    // Prepare assistant stream placeholder
    this.prepareAssistantResponse();
    this.activeAssistantMsgEl.innerHTML = `<em>동료 에이전트의 지시를 수신하여 작업을 실행 중입니다...</em>`;

    // Connect AgentStream
    this.stream = new AgentStream(this.currentStreamId, {
      onToken: (token) => this.handleToken(token),
      onReasoning: (chunk) => this.handleReasoning(chunk),
      onJob: (job) => this.handleJob(job),
      onTool: (tool) => this.handleTool(tool),
      onApproval: (approval) => this.handleApproval(approval),
      onStatusChange: (status) => this.setStatus(status),
      onDone: () => this.handleDone(),
      onCancel: () => this.handleCancelDone(),
      onError: (err) => this.handleError(err)
    });
  }

  async handleDone() {
    if (this.currentStreamId) {
      this._attachedStreams.add(this.currentStreamId);
    }
    this.currentStreamId = null;
    this.setStatus('idle');
    if (this.activeThoughtDetailsEl) {
      this.activeThoughtDetailsEl.open = false;
    }
    this.activeAssistantMsgEl = null;
    this.activeAssistantRawText = '';
    // Reload full session history so prompt sent by peer and result are both fully rendered
    if (this.sessionId) {
      try {
        const res = await api.getSession(this.sessionId);
        if (res && res.session && res.session.messages) {
          this.renderHistory(res.session.messages);
        }
      } catch (_) {}
    }
    this.scrollToBottom();
  }

  async handleCancel() {
    console.log(`[Pane ${this.profile}] Cancelling work...`);
    const streamToCancel = this.currentStreamId;
    const sessionToCancel = this.sessionId;

    if (streamToCancel) {
      this._cancelledStreams.add(streamToCancel);
    }

    if (this.stream) {
      try { this.stream.close(); } catch (_) {}
      this.stream = null;
    }
    this.currentStreamId = null;
    this.setStatus('idle');

    // Clean up active assistant UI without creating duplicate bubbles
    if (this.activeAssistantMsgEl) {
      if (this.activeAssistantRawText) {
        this.activeAssistantMsgEl.innerHTML = this.renderMarkdown(this.activeAssistantRawText) +
          `<div class="stream-cancelled-notice" style="margin-top:8px;font-size:0.85em;color:var(--text-muted,#888);font-style:italic;">⏹️ 작업이 사용자에 의해 중단되었습니다.</div>`;
      } else {
        this.activeAssistantMsgEl.innerHTML = `<span style="color:var(--text-muted,#888);font-style:italic;">⏹️ 작업이 사용자에 의해 중단되었습니다.</span>`;
      }
      this.activeAssistantMsgEl = null;
      this.activeAssistantRawText = '';
    } else {
      this.appendMessage('assistant', `⏹️ 작업이 사용자에 의해 중단되었습니다.`);
    }

    if (this.activeThoughtDetailsEl) {
      this.activeThoughtDetailsEl.open = false;
      this.activeThoughtDetailsEl = null;
    }
    if (this.activeToolHudEl) {
      this.activeToolHudEl.className = 'tool-hud done';
      this.activeToolHudEl.innerHTML = `<span>⏹️</span><span>작업 중단됨</span>`;
      this.activeToolHudEl = null;
    }

    if (streamToCancel || sessionToCancel) {
      await api.cancelChat(streamToCancel, sessionToCancel);
    }
  }

  handleCancelDone() {
    if (this.currentStreamId) {
      this._cancelledStreams.add(this.currentStreamId);
    }
    if (this.stream) {
      try { this.stream.close(); } catch (_) {}
      this.stream = null;
    }
    this.currentStreamId = null;
    this.setStatus('idle');
    if (this.activeThoughtDetailsEl) {
      this.activeThoughtDetailsEl.open = false;
      this.activeThoughtDetailsEl = null;
    }
    if (this.activeAssistantMsgEl) {
      this.activeAssistantMsgEl = null;
      this.activeAssistantRawText = '';
    }
  }

  handleError(err) {
    this.currentStreamId = null;
    this.setStatus('error');
    this.appendMessage('assistant', `⚠️ 오류 발생: ${err.error || JSON.stringify(err)}`);
  }

  appendMessage(role, text) {
    const meta = this.getAgentMeta(this.profile);
    const msgEl = document.createElement('div');
    msgEl.className = `msg ${role}`;
    const sender = role === 'user' ? '👤 나 (대표님)' : `${meta.icon} ${this.profile}`;

    msgEl.innerHTML = `
      <div class="msg-sender">${sender}</div>
      <div class="msg-bubble">${this.renderMarkdown(text)}</div>
    `;
    this.bodyEl.appendChild(msgEl);
    this.scrollToBottom();
  }

  scrollToBottom() {
    if (!this.autoScroll) return;
    this.bodyEl.scrollTop = this.bodyEl.scrollHeight;
  }

  escapeHtml(str) {
    return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
  }

  injectMessage(text) {
    this.inputEl.value = text;
    this.inputEl.focus();
    this.updateSendButtonState();
    this.el.classList.add('highlight-pulse');
    setTimeout(() => this.el.classList.remove('highlight-pulse'), 1200);
  }

  destroy() {
    if (this._workerEventSource) {
      try { this._workerEventSource.close(); } catch (_) {}
      this._workerEventSource = null;
    }
    if (this._workerPollTimer) {
      clearInterval(this._workerPollTimer);
      this._workerPollTimer = null;
    }
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.stream) {
      try { this.stream.close(); } catch (_) {}
      this.stream = null;
    }
    if (this.el) {
      this.el.remove();
    }
    this.onDestroy(this.id);
  }
}

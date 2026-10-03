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

    const cleanedText = this.cleanWorkerText(cleanContent);
    let formattedText = this.renderMarkdown(cleanedText);
    let terminalDetailsHtml = '';

    if (rawTerminal) {
      const cleanedTerm = this.stripAnsi(rawTerminal).trim();
      if (cleanedTerm) {
        const lineCount = cleanedTerm.split('\n').length;
        terminalDetailsHtml = `
          <details class="tool-card tool-group-card" style="margin-top:8px;">
            <summary>
              <span class="tool-group-icon">💻</span>
              <span class="tool-group-label">터미널 실행 로그 (${lineCount}줄)</span>
              <span class="tool-group-chevron">▶</span>
            </summary>
            <div class="terminal-live-card" style="margin:4px 8px 8px 8px;">
              <pre class="terminal-live-output">${this.escapeHtml(cleanedTerm)}</pre>
            </div>
          </details>
        `;
      }
    }

    msgEl.innerHTML = `
      <div class="msg-sender">${meta.icon} ${this.profile}</div>
      <div class="msg-bubble">
        <div class="stream-content">${formattedText}</div>
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
    card.className = 'inline-approval-card';
    card.id = `${this.id}-approval-card`;

    const title = blockedInfo.type === 'folder_trust' ? '작업 폴더 신뢰 승인 요청' : '도구 / 터미널 명령 실행 승인 요청';
    const question = blockedInfo.question || '작업을 진행하기 위해 승인이 필요합니다.';

    card.innerHTML = `
      <div class="inline-approval-card-inner">
        <div class="inline-approval-card-header">
          <span class="inline-approval-card-icon">🛡️</span>
          <span class="inline-approval-card-title">⚠️ ${title}</span>
        </div>
        <div class="inline-approval-card-body">
          <code>${this.escapeHtml(question)}</code>
        </div>
        <div class="inline-approval-card-actions">
          <button class="approval-btn ia-approve-btn" id="${this.id}-btn-approve">✅ 승인 (Enter/Y)</button>
          <button class="approval-btn ia-reject-btn" id="${this.id}-btn-reject">❌ 거절 (Esc/N)</button>
        </div>
      </div>
    `;

    this.bodyEl.appendChild(card);
    this.scrollToBottom();

    const btnApprove = card.querySelector(`#${this.id}-btn-approve`);
    if (btnApprove) {
      btnApprove.addEventListener('click', async () => {
        card.className = 'inline-approval-card resolved approved';
        card.querySelector('.inline-approval-card-header').innerHTML = `
          <span class="inline-approval-card-icon">✅</span>
          <span class="inline-approval-card-title">워커 작업이 승인되었습니다 (작업 진행 중)</span>
        `;
        const actions = card.querySelector('.inline-approval-card-actions');
        if (actions) actions.remove();
        this.setStatus('working');
        try {
          await api.approveWorker(this.workerName, 'approve');
        } catch (err) {
          console.error('Worker approve failed:', err);
        }
      });
    }

    const btnReject = card.querySelector(`#${this.id}-btn-reject`);
    if (btnReject) {
      btnReject.addEventListener('click', async () => {
        card.className = 'inline-approval-card resolved rejected';
        card.querySelector('.inline-approval-card-header').innerHTML = `
          <span class="inline-approval-card-icon">❌</span>
          <span class="inline-approval-card-title">워커 작업이 거부되었습니다</span>
        `;
        const actions = card.querySelector('.inline-approval-card-actions');
        if (actions) actions.remove();
        this.setStatus('idle');
        try {
          await api.approveWorker(this.workerName, 'reject');
        } catch (err) {
          console.error('Worker reject failed:', err);
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

        // Reasoning / Thinking (Clean collapsed card)
        const reasoning = m.reasoning_content || m.thinking;
        if (reasoning) {
          inner += `
            <details class="tool-card reasoning-card">
              <summary>
                <span class="tool-group-label">💭 사고 과정 (클릭하여 보기)</span>
                <span class="tool-group-chevron">▶</span>
              </summary>
              <div class="tool-card-body">
                <pre class="reasoning-text">${this.escapeHtml(reasoning)}</pre>
              </div>
            </details>
          `;
        }

        // Tool calls (Clean collapsed tool-group-card)
        if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
          let itemsHtml = '';
          m.tool_calls.forEach(tc => {
            const fname = (tc.function && tc.function.name) || tc.name || 'tool';
            let argsStr = '';
            try {
              const parsed = typeof tc.function?.arguments === 'string' ? JSON.parse(tc.function.arguments) : (tc.function?.arguments || tc.args);
              if (parsed) {
                argsStr = parsed.command || parsed.path || parsed.filename || parsed.query || Object.values(parsed)[0] || '';
              }
            } catch (_) {}
            itemsHtml += `
              <div class="tool-group-item">
                <span class="tgi-icon">✅</span>
                <span class="tgi-name">${this.escapeHtml(fname)}</span>
                <span class="tgi-args" title="${this.escapeHtml(String(argsStr))}">${this.escapeHtml(String(argsStr))}</span>
                <span class="tgi-status">완료</span>
              </div>
            `;
          });

          inner += `
            <details class="tool-card tool-group-card">
              <summary>
                <span class="tool-group-icon">🔧</span>
                <span class="tool-group-label">도구 실행 완료 (${m.tool_calls.length}개)</span>
                <span class="tool-group-counter">${m.tool_calls.length}</span>
                <span class="tool-group-chevron">▶</span>
              </summary>
              <div class="tool-group-items">
                ${itemsHtml}
              </div>
            </details>
          `;
        }

        // Assistant Content
        const contentStr = typeof m.content === 'string' ? m.content : (m.content ? JSON.stringify(m.content) : '');
        if (contentStr) {
          inner += `<div class="stream-content">${this.renderMarkdown(contentStr)}</div>`;
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
    this.activeReasoningCard = null;
    this.activeReasoningBody = null;
    this.activeReasoningRaw = '';
    this.activeToolCard = null;
    this.activeToolItemsEl = null;
    this.activeToolMap = {};
    this.activeToolCount = 0;
    this.activeToolDone = 0;
    this.scrollToBottom();
  }

  handleToken(token) {
    if (!this.activeAssistantMsgEl) return;

    // Auto-collapse reasoning card when final answer generation begins
    if (this.activeReasoningCard && this.activeReasoningCard.open) {
      this.activeReasoningCard.open = false;
      const spinner = this.activeReasoningCard.querySelector('.reasoning-spinner');
      if (spinner) spinner.style.display = 'none';
      const label = this.activeReasoningCard.querySelector('.reasoning-summary-title');
      if (label) label.textContent = '💭 사고 과정 완료 (클릭하여 보기)';
    }

    this.activeAssistantRawText = (this.activeAssistantRawText || '') + token;
    this.activeAssistantMsgEl.innerHTML = this.renderMarkdown(this.activeAssistantRawText);
    this.scrollToBottom();
  }

  handleReasoning(chunk) {
    if (!this.activeReasoningCard) {
      const card = document.createElement('details');
      card.className = 'tool-card reasoning-card';
      card.open = true;
      card.innerHTML = `
        <summary>
          <span class="tool-group-spinner reasoning-spinner"></span>
          <span class="tool-group-label reasoning-summary-title">💭 생각 중... (사고 과정)</span>
          <span class="tool-group-chevron">▶</span>
        </summary>
        <div class="tool-card-body">
          <pre class="reasoning-text"></pre>
        </div>
      `;
      this.activeAssistantMsgEl.parentElement.insertBefore(card, this.activeAssistantMsgEl);
      this.activeReasoningCard = card;
      this.activeReasoningBody = card.querySelector('.reasoning-text');
      this.activeReasoningRaw = '';
    }
    if (this.activeReasoningBody) {
      this.activeReasoningRaw = (this.activeReasoningRaw || '') + chunk;
      this.activeReasoningBody.textContent = this.activeReasoningRaw;
      this.scrollToBottom();
    }
  }

  _ensureToolCard() {
    if (this.activeReasoningCard && this.activeReasoningCard.open) {
      this.activeReasoningCard.open = false;
      const spinner = this.activeReasoningCard.querySelector('.reasoning-spinner');
      if (spinner) spinner.style.display = 'none';
      const label = this.activeReasoningCard.querySelector('.reasoning-summary-title');
      if (label) label.textContent = '💭 사고 과정 완료 (클릭하여 보기)';
    }

    if (!this.activeToolCard) {
      const card = document.createElement('details');
      card.className = 'tool-card tool-group-card';
      card.open = true;
      card.innerHTML = `
        <summary>
          <span class="tool-group-icon">🔧</span>
          <span class="tool-group-spinner"></span>
          <span class="tool-group-label">도구 작업 중...</span>
          <span class="tool-group-counter">0</span>
          <span class="tool-group-chevron">▶</span>
        </summary>
        <div class="tool-group-items"></div>
      `;
      this.activeAssistantMsgEl.parentElement.insertBefore(card, this.activeAssistantMsgEl);
      this.activeToolCard = card;
      this.activeToolItemsEl = card.querySelector('.tool-group-items');
      this.activeToolMap = {};
      this.activeToolCount = 0;
      this.activeToolDone = 0;
    }
    return this.activeToolCard;
  }

  handleJob(job) {
    this.handleToolOrJob(job);
  }

  handleTool(tool) {
    this.handleToolOrJob(tool);
  }

  handleToolOrJob(data) {
    if (!this.activeAssistantMsgEl) return;
    this._ensureToolCard();

    const isStart = data.event === 'tool.started' || data.type === 'start';
    const isDone = data.event === 'tool.completed' || data.type === 'done';
    const tName = data.name || data.tool || '도구 작업';
    const tid = data.tool_call_id || (tName + '_' + this.activeToolCount);

    let argSummary = '';
    if (data.args) {
      if (typeof data.args === 'string') argSummary = data.args;
      else if (data.args.command) argSummary = data.args.command;
      else if (data.args.path) argSummary = data.args.path;
      else if (data.args.filename) argSummary = data.args.filename;
      else if (data.args.query) argSummary = data.args.query;
      else {
        const keys = Object.keys(data.args);
        if (keys.length > 0) argSummary = String(data.args[keys[0]]);
      }
    } else if (data.desc) {
      argSummary = data.desc;
    }

    if (isStart) {
      this.activeToolCount++;
      const item = document.createElement('div');
      item.className = 'tool-group-item';
      item.dataset.tid = tid;
      item.innerHTML = `
        <span class="tgi-icon">⏳</span>
        <span class="tgi-name">${this.escapeHtml(tName)}</span>
        <span class="tgi-args" title="${this.escapeHtml(argSummary)}">${this.escapeHtml(argSummary)}</span>
        <span class="tgi-status">실행 중</span>
      `;
      this.activeToolMap[tid] = item;
      this.activeToolItemsEl.appendChild(item);

      // Live terminal preview if command execution
      if (/terminal|execute_command|run_command|bash|cmd|powershell/i.test(tName)) {
        let termCard = this.activeToolCard.querySelector(`.terminal-live-card[data-tid="${tid}"]`);
        if (!termCard) {
          termCard = document.createElement('div');
          termCard.className = 'terminal-live-card';
          termCard.dataset.tid = tid;
          termCard.innerHTML = `
            <div class="terminal-live-header">
              <span class="terminal-live-indicator">● LIVE</span>
              <span class="terminal-cmd">$ ${this.escapeHtml(argSummary || tName)}</span>
            </div>
            <pre class="terminal-live-output">명령을 실행하는 중입니다...</pre>
          `;
          this.activeToolItemsEl.appendChild(termCard);
        }
      }
    } else if (isDone) {
      this.activeToolDone++;
      let item = this.activeToolMap[tid];
      if (!item) {
        for (const k in this.activeToolMap) {
          const el = this.activeToolMap[k];
          if (el.querySelector('.tgi-name')?.textContent === tName && el.querySelector('.tgi-status')?.textContent === '실행 중') {
            item = el;
            break;
          }
        }
      }
      if (item) {
        const ic = item.querySelector('.tgi-icon');
        const st = item.querySelector('.tgi-status');
        if (ic) ic.textContent = '✅';
        if (st) st.textContent = '완료';
      }

      // Update terminal card output
      const termCard = this.activeToolCard?.querySelector(`.terminal-live-card[data-tid="${tid}"]`);
      if (termCard) {
        const ind = termCard.querySelector('.terminal-live-indicator');
        if (ind) {
          ind.textContent = '● DONE';
          ind.className = 'terminal-live-indicator done';
        }
        if (data.result) {
          const out = termCard.querySelector('.terminal-live-output');
          if (out) out.textContent = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
        }
      }
    }

    const label = this.activeToolCard.querySelector('.tool-group-label');
    const counter = this.activeToolCard.querySelector('.tool-group-counter');
    const spinner = this.activeToolCard.querySelector('.tool-group-spinner');
    const running = Math.max(0, this.activeToolCount - this.activeToolDone);

    if (counter) counter.textContent = String(this.activeToolCount);
    if (label) {
      if (running > 0) {
        label.textContent = `도구 작업 중... (${this.activeToolDone}/${this.activeToolCount} 완료)`;
        if (spinner) spinner.style.display = 'inline-block';
      } else {
        label.textContent = `도구 실행 완료 (${this.activeToolCount}개)`;
        if (spinner) spinner.style.display = 'none';
      }
    }
    this.scrollToBottom();
  }

  handleApproval(approval) {
    const card = document.createElement('div');
    card.className = 'inline-approval-card';
    card.id = `${this.id}-approval-card`;

    const title = approval.type === 'dangerous_command' ? '⚠️ 위험 명령 실행 승인 대기' : '🛡️ 시스템 명령 승인 대기 (Approval Required)';
    const cmd = approval.command || (approval.args ? JSON.stringify(approval.args) : '');
    const desc = approval.message || approval.reason || '시스템 보안 설정에 따라 사용자의 확인 및 승인이 필요합니다.';

    card.innerHTML = `
      <div class="inline-approval-card-inner">
        <div class="inline-approval-card-header">
          <span class="inline-approval-card-icon">🛡️</span>
          <span class="inline-approval-card-title">${title}</span>
        </div>
        <div class="inline-approval-card-body">
          ${cmd ? `<code>${this.escapeHtml(cmd)}</code>` : ''}
          <div class="ia-desc">${this.escapeHtml(desc)}</div>
        </div>
        <div class="inline-approval-card-actions">
          <button class="approval-btn ia-approve-btn" id="${this.id}-btn-allow">✅ 승인 (Allow)</button>
          <button class="approval-btn ia-reject-btn" id="${this.id}-btn-deny">❌ 거부 (Deny)</button>
        </div>
      </div>
    `;

    this.bodyEl.appendChild(card);
    this.scrollToBottom();

    card.querySelector(`#${this.id}-btn-allow`).addEventListener('click', async () => {
      card.className = 'inline-approval-card resolved approved';
      card.querySelector('.inline-approval-card-header').innerHTML = `
        <span class="inline-approval-card-icon">✅</span>
        <span class="inline-approval-card-title">명령 실행이 승인되었습니다 (작업 진행 중)</span>
      `;
      const actions = card.querySelector('.inline-approval-card-actions');
      if (actions) actions.remove();
      this.setStatus('working');
      try {
        await api.respondApproval(this.sessionId, true);
      } catch (err) {
        console.error('Approval failed:', err);
      }
    });

    card.querySelector(`#${this.id}-btn-deny`).addEventListener('click', async () => {
      card.className = 'inline-approval-card resolved rejected';
      card.querySelector('.inline-approval-card-header').innerHTML = `
        <span class="inline-approval-card-icon">❌</span>
        <span class="inline-approval-card-title">명령 실행이 거부되었습니다</span>
      `;
      const actions = card.querySelector('.inline-approval-card-actions');
      if (actions) actions.remove();
      this.setStatus('idle');
      try {
        await api.respondApproval(this.sessionId, false);
      } catch (err) {
        console.error('Reject failed:', err);
      }
    });
  }

  stripAnsi(str) {
    if (!str) return '';
    return str
      .replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n');
  }

  cleanWorkerText(str) {
    if (!str) return '';
    let clean = this.stripAnsi(str);
    clean = clean.replace(/↓\s*Back to bottom\s*·\s*esc/gi, '');
    clean = clean.replace(/New activity\s*·\s*↓\s*Back to bottom\s*·\s*esc/gi, '');
    clean = clean.replace(/Type a prompt to continue/gi, '');
    return clean.trim();
  }

  renderMarkdown(text) {
    if (!text) return '';
    let out = String(text);

    // 1. Code blocks with copy button
    out = out.replace(/```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g, (match, lang, code) => {
      const l = lang || 'code';
      const encoded = encodeURIComponent(code);
      return `
        <div class="md-code-wrapper">
          <div class="md-code-header">
            <span>${this.escapeHtml(l)}</span>
            <button class="code-copy-btn" onclick="navigator.clipboard.writeText(decodeURIComponent('${encoded}')).then(()=>{this.textContent='✅ 복사됨!';setTimeout(()=>this.textContent='복사',1500);})">복사</button>
          </div>
          <pre class="md-code"><code class="lang-${l}">${this.escapeHtml(code)}</code></pre>
        </div>
      `;
    });

    // 2. Inline code
    out = out.replace(/`([^`]+)`/g, (match, code) => {
      return `<code class="md-inline">${this.escapeHtml(code)}</code>`;
    });

    // 3. Blockquotes
    out = out.replace(/^>\s?(.*)$/gm, '<blockquote class="md-quote">$1</blockquote>');

    // 4. Bullet lists
    out = out.replace(/^[\*\-]\s+(.*)$/gm, '<div style="display:flex;gap:6px;margin:2px 0;"><span style="color:var(--accent-raon,#a855f7);flex-shrink:0;">•</span><span>$1</span></div>');

    // 5. Bold & Italic & Strikethrough
    out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    out = out.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // 6. Links
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:underline;">$1</a>');

    // 7. Line breaks
    out = out.replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>');
    return out;
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
    if (this.activeReasoningCard) {
      this.activeReasoningCard.open = false;
      const spinner = this.activeReasoningCard.querySelector('.reasoning-spinner');
      if (spinner) spinner.style.display = 'none';
    }
    if (this.activeToolCard) {
      const spinner = this.activeToolCard.querySelector('.tool-group-spinner');
      if (spinner) spinner.style.display = 'none';
      const label = this.activeToolCard.querySelector('.tool-group-label');
      if (label && this.activeToolCount > 0) {
        label.textContent = `도구 실행 완료 (${this.activeToolCount}개)`;
      }
    }
    this.activeAssistantMsgEl = null;
    this.activeAssistantRawText = '';
    this.activeReasoningCard = null;
    this.activeToolCard = null;

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

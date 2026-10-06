/**
 * DAON Multi-Agent Orchestrator — multi.js
 * Main Entry Point and Global State Coordinator.
 * Bootstraps panes, manages layout presets (2/3/4/6), and binds inter-agent event bus.
 * Features:
 *  - Classic View active session inheritance (never resets ongoing chat)
 *  - Multi-pane session state persistence via localStorage
 *  - Auto-fill and intelligent companion agent session selection
 */

import { api } from './api.js';
import { Pane } from './pane.js';
import { AgentBoard } from './board.js';
import { AgentFlow } from './flow.js';

class MultiApp {
  constructor() {
    this.gridEl = document.getElementById('grid');
    this.boardBadgesEl = document.getElementById('boardBadges');
    this.pipelineTagsEl = document.getElementById('pipelineTags');
    this.layoutControls = document.getElementById('layoutControls');
    this.addAgentBtn = document.getElementById('addAgentBtn');
    this.autoScrollToggle = document.getElementById('autoScrollToggle');

    this.panes = new Map(); // id -> Pane instance
    this.profiles = [];
    this.models = [];
    this.sessions = [];
    this.activeProfile = 'raon';
    this.globalAutoScroll = true;

    this.board = new AgentBoard(this.boardBadgesEl);
    this.flow = new AgentFlow(this.pipelineTagsEl);

    this.MAX_PANES = 6;
  }

  async start() {
    console.log('[DAON Multi] Booting Multi-Agent Orchestrator...');

    // 1. Load System Metadata & Sessions
    await this.loadMetadata();

    // 2. Bind Header Events
    this.bindGlobalEvents();

    // 3. Initialize Panes (Restore from Classic or Saved State)
    await this.setupDefaultPanes();

    // 4. Background listener for active peer streams (auto-spawns split panes when unmounted peers are called!)
    setInterval(() => this.checkActiveStreams(), 1800);

    console.log('[DAON Multi] Cockpit ready with synchronized panes.');
  }

  async loadMetadata() {
    try {
      const [profilesRes, modelsRes, sessionsRes] = await Promise.all([
        api.getProfiles(),
        api.getModels(),
        api.getSessions()
      ]);

      this.profiles = profilesRes.profiles || [];
      // Register external Herdr coding workers in profile choices
      if (!this.profiles.some(p => (p.name || '').toLowerCase().includes('codex'))) {
        this.profiles.push({ name: '코덱스 (Codex)', model: 'MiniMax-M3', isWorker: true });
      }
      if (!this.profiles.some(p => (p.name || '').toLowerCase().includes('claude'))) {
        this.profiles.push({ name: '클로드 (Claude)', model: 'deepseek-v4.1-flash', isWorker: true });
      }

      this.activeProfile = profilesRes.active || 'raon';
      this.modelData = modelsRes || {};
      this.models = [];
      this.sessions = sessionsRes.sessions || [];

      if (modelsRes && modelsRes.groups) {
        // Prioritize reliable custom providers (MiniMax, OpenCode Go, OpenRouter) before Omniroute
        modelsRes.groups.sort((a, b) => {
          const aOmni = (a.provider || '').toLowerCase().includes('omni') ? 1 : 0;
          const bOmni = (b.provider || '').toLowerCase().includes('omni') ? 1 : 0;
          return aOmni - bOmni;
        });
        modelsRes.groups.forEach(g => {
          (g.models || []).forEach(m => this.models.push(m.id));
        });
      }
      this.defaultModel = 'MiniMax-M3';
      if (this.models.length === 0) {
        this.models = ['MiniMax-M3', 'deepseek-v4.1-flash'];
      }

      console.log(`[DAON Multi] Loaded ${this.profiles.length} profiles, ${this.models.length} models, ${this.sessions.length} existing sessions.`);
    } catch (err) {
      console.warn('[DAON Multi] Metadata load warning:', err);
    }
  }

  bindGlobalEvents() {
    // Layout switcher buttons
    const layoutBtns = this.layoutControls.querySelectorAll('.layout-btn');
    layoutBtns.forEach(btn => {
      btn.addEventListener('click', async () => {
        const layout = btn.dataset.layout;
        await this.setLayout(layout, true);
      });
    });

    // Add Agent Button
    this.addAgentBtn.addEventListener('click', () => this.handleAddAgent());

    // Add Worker Button (Codex / Herdr)
    const addWorkerBtn = document.getElementById('addWorkerBtn');
    if (addWorkerBtn) {
      addWorkerBtn.addEventListener('click', () => this.handleAddWorker('코덱스 (Codex)'));
    }

    // Add Claude Worker Button (Claude Code / Herdr)
    const addClaudeWorkerBtn = document.getElementById('addClaudeWorkerBtn');
    if (addClaudeWorkerBtn) {
      addClaudeWorkerBtn.addEventListener('click', () => this.handleAddWorker('클로드 (Claude)'));
    }

    // Auto-Scroll Toggle
    this.autoScrollToggle.addEventListener('click', () => {
      this.globalAutoScroll = !this.globalAutoScroll;
      this.autoScrollToggle.classList.toggle('active', this.globalAutoScroll);
      this.autoScrollToggle.innerHTML = this.globalAutoScroll
        ? '<span class="btn-icon">⟳</span> 자동스크롤 ON'
        : '<span class="btn-icon">⏸</span> 자동스크롤 OFF';

      this.panes.forEach(pane => {
        pane.autoScroll = this.globalAutoScroll;
      });
    });
  }

  async setLayout(layout, autoFill = true) {
    this.gridEl.dataset.layout = layout;
    const targetCount = parseInt(layout, 10);
    console.log(`[DAON Multi] Layout set to: ${layout} panes.`);

    // Sync button active style
    const layoutBtns = this.layoutControls.querySelectorAll('.layout-btn');
    layoutBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.layout === layout);
    });

    // Auto-fill missing agents so there is no empty space
    if (autoFill && !isNaN(targetCount)) {
      while (this.panes.size < targetCount && this.panes.size < this.MAX_PANES) {
        const nextProfile = this.getUnusedProfile();
        // Check if there is an existing session for this profile
        const existingSess = this.sessions.find(s => 
          (s.profile || '').toLowerCase() === nextProfile.toLowerCase() &&
          !Array.from(this.panes.values()).some(p => p.sessionId === s.session_id)
        );
        await this.createPane(nextProfile, existingSess ? existingSess.session_id : null);
      }
    }
  }

  async setupDefaultPanes() {
    const activeSid = localStorage.getItem('daon_active_session_id');
    const savedPanesStr = localStorage.getItem('daon_multi_panes');

    let restored = false;

    // Check Case 1: Saved Multi Panes from previous multi session
    if (savedPanesStr) {
      try {
        const savedPanes = JSON.parse(savedPanesStr);
        if (Array.isArray(savedPanes) && savedPanes.length > 0) {
          const validPanes = savedPanes.filter(p => this.sessions.some(s => s.session_id === p.sessionId));
          if (validPanes.length > 0) {
            for (const sp of validPanes) {
              await this.createPane(sp.profile, sp.sessionId, sp.model);
            }
            this.setLayout(String(Math.min(validPanes.length, this.MAX_PANES)), false);
            restored = true;
          }
        }
      } catch (e) {
        console.warn('[DAON Multi] Failed to restore saved multi panes:', e);
      }
    }

    if (restored) return;

    // Check Case 2: Classic Active Session Inheritance
    let activeSess = null;
    if (activeSid) {
      activeSess = this.sessions.find(s => s.session_id === activeSid);
    }

    if (activeSess) {
      // Pane 1: Inherit Classic Active Session!
      const p1Profile = activeSess.profile || this.findProfileByName(['raon', '라온']) || 'raon';
      await this.createPane(p1Profile, activeSess.session_id, activeSess.model);

      // Pane 2: Pick companion agent (Bill or Raon)
      const companionProfile = (p1Profile.toLowerCase().includes('bill') || p1Profile.toLowerCase().includes('빌'))
        ? (this.findProfileByName(['raon', '라온']) || 'raon')
        : (this.findProfileByName(['bill', '빌', '빌(개발)']) || 'bill');

      // Find existing companion session if any
      const companionSess = this.sessions.find(s => 
        (s.profile || '').toLowerCase().includes(companionProfile.toLowerCase()) && 
        s.session_id !== activeSid
      );
      await this.createPane(companionProfile, companionSess ? companionSess.session_id : null);

      this.setLayout('2', false);
      this.savePanesState();
      return;
    }

    // Check Case 3: Default Setup (Raon + Bill)
    const raonProfile = this.findProfileByName(['raon', '라온']) || 'raon';
    const latestRaon = this.sessions.find(s => (s.profile || '').toLowerCase().includes('raon') || (s.profile || '').includes('라온'));
    await this.createPane(raonProfile, latestRaon ? latestRaon.session_id : null);

    const billProfile = this.findProfileByName(['bill', '빌', '빌(개발)']) || (this.profiles[1] ? this.profiles[1].name : 'raon');
    const latestBill = this.sessions.find(s => (s.profile || '').toLowerCase().includes('bill') || (s.profile || '').includes('빌'));
    await this.createPane(billProfile, latestBill ? latestBill.session_id : null);

    this.setLayout('2', false);
    this.savePanesState();
  }

  findProfileByName(candidates) {
    for (const name of candidates) {
      const match = this.profiles.find(p => p.name.toLowerCase() === name.toLowerCase());
      if (match) return match.name;
    }
    return null;
  }

  getUnusedProfile() {
    const activeProfiles = Array.from(this.panes.values()).map(p => p.profile);
    const candidates = ['셜록', '토니', '프라다', 'sherlock', 'tony', 'prada'];
    
    // Check priority candidates
    for (const c of candidates) {
      const found = this.profiles.find(p => p.name.toLowerCase().includes(c.toLowerCase()));
      if (found && !activeProfiles.includes(found.name)) {
        return found.name;
      }
    }

    // Check any unused profile
    const anyUnused = this.profiles.find(p => !activeProfiles.includes(p.name));
    if (anyUnused) return anyUnused.name;

    // Fallback to active
    return this.activeProfile || 'raon';
  }

  findProfileByName(keywords) {
    const kwList = Array.isArray(keywords) ? keywords : [keywords];
    for (const kw of kwList) {
      const lower = String(kw || '').toLowerCase();
      if (!lower) continue;
      if (lower.includes('코덱스') || lower.includes('codex')) return '코덱스 (Codex)';
      if (lower.includes('클로드') || lower.includes('claude')) return '클로드 (Claude)';
      const p = this.profiles.find(x => x.name.toLowerCase().includes(lower));
      if (p) return p.name;
    }
    return null;
  }

  async handleAddAgent() {
    if (this.panes.size >= this.MAX_PANES) {
      alert(`⚠️ 안정적인 실시간 스트리밍(HTTP/1.1)을 위해 동시에 열 수 있는 최대 창은 ${this.MAX_PANES}개입니다.`);
      return;
    }

    const nextProfile = this.getUnusedProfile();
    // Check if there is an existing session for this profile
    const existingSess = this.sessions.find(s => 
      (s.profile || '').toLowerCase() === nextProfile.toLowerCase() &&
      !Array.from(this.panes.values()).some(p => p.sessionId === s.session_id)
    );
    await this.createPane(nextProfile, existingSess ? existingSess.session_id : null);

    // Auto-adjust layout preset if needed
    const count = this.panes.size;
    if (count === 3) this.setLayout('3', false);
    else if (count === 4) this.setLayout('4', false);
    else if (count >= 5) this.setLayout('6', false);
  }

  async handleAddWorker(profileName = '코덱스 (Codex)') {
    if (this.panes.size >= this.MAX_PANES) {
      alert(`⚠️ 안정적인 실시간 처리를 위해 동시에 열 수 있는 최대 창은 ${this.MAX_PANES}개입니다.`);
      return;
    }

    // Check if worker pane is already open; if so, highlight it
    const isClaude = (profileName || '').toLowerCase().includes('claude') || (profileName || '').toLowerCase().includes('클로드');
    const existing = Array.from(this.panes.values()).find(p => {
      const pLower = (p.profile || '').toLowerCase();
      return isClaude
        ? (pLower.includes('claude') || pLower.includes('클로드'))
        : (pLower.includes('codex') || pLower.includes('코덱스'));
    });
    if (existing) {
      const el = document.getElementById(existing.id);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth' });
        el.classList.add('highlight-pulse');
        setTimeout(() => el.classList.remove('highlight-pulse'), 1200);
      }
      return;
    }

    await this.createPane(profileName);

    const count = this.panes.size;
    if (count === 3) this.setLayout('3', false);
    else if (count === 4) this.setLayout('4', false);
    else if (count >= 5) this.setLayout('6', false);
  }

  async createPane(profileName, sessionId = null, model = null) {
    const pane = new Pane({
      profile: profileName,
      profiles: this.profiles,
      models: this.models,
      modelData: this.modelData,
      sessionId: sessionId,
      model: model,
      container: this.gridEl,
      onStatusChange: (paneId, status, profile) => {
        this.board.updateAgent(paneId, status, profile);
      },
      onSessionChange: () => {
        this.savePanesState();
      },
      onDestroy: (paneId) => {
        this.panes.delete(paneId);
        this.board.removeAgent(paneId);
        this.savePanesState();
        // Auto-scale layout if panes reduced
        if (this.panes.size <= 2) this.setLayout('2', false);
        else if (this.panes.size === 3) this.setLayout('3', false);
        else if (this.panes.size === 4) this.setLayout('4', false);
      },
      onDispatch: (data) => {
        this.handleDispatch(data);
      },
      getRoomContext: (callerPaneId) => {
        const peers = [];
        for (const [id, p] of this.panes.entries()) {
          if (id !== callerPaneId) {
            if (p.isWorker) {
              peers.push(`- ${p.profile} (코딩 워커): delegate_to_worker(worker_name="${p.workerName}", prompt="...") 도구로 위임`);
            } else if (p.sessionId) {
              peers.push(`- ${p.profile} (협업 에이전트): delegate_to_agent(agent_name="${p.profile}", task="...") 도구로 위임`);
            }
          }
        }
        if (peers.length === 0) return '';
        return `[오케스트레이션 회의실 안내]\n현재 대표님 화면에 열려있는 동료 에이전트:\n${peers.join('\n')}\n* 전문 동료 에이전트(빌/셜록/토니/프라다 등)에게 작업을 맡길 때는 반드시 delegate_to_agent(agent_name="...", task="...") 도구를 사용하여 직접 지시하세요. 대표님 화면의 분할 창에서 실시간으로 일하는 모습이 스트리밍되며, 작업이 끝나면 결과가 자동으로 보고됩니다.\n* 코딩 워커(코덱스/클로드)에게 실제 파일 생성이나 터미널 빌드/테스트를 시킬 때는 delegate_to_worker(worker_name="...", prompt="...") 도구를 사용하세요.`;
      }
    });

    this.panes.set(pane.id, pane);
    pane.autoScroll = this.globalAutoScroll;
    // Wait for pane init (loadSession / setupNewSession) to fully complete
    // before returning, so callers can safely attachToStream without race.
    await pane._initReady;
    this.savePanesState();
    return pane;
  }

  savePanesState() {
    const state = Array.from(this.panes.values()).map(p => ({
      profile: p.profile,
      sessionId: p.sessionId,
      model: p.currentModel
    }));
    try {
      localStorage.setItem('daon_multi_panes', JSON.stringify(state));
      if (state.length > 0 && state[0].sessionId) {
        localStorage.setItem('daon_active_session_id', state[0].sessionId);
      }
    } catch (_) {}
  }

  async checkActiveStreams() {
    try {
      const res = await api.getActiveStreams();
      const activeList = res.active_streams || [];
      for (const act of activeList) {
        // Skip if this stream has been cancelled in any pane
        const isCancelled = Array.from(this.panes.values()).some(p => p._cancelledStreams && p._cancelledStreams.has(act.stream_id));
        if (isCancelled) continue;

        // 1. Check if this session is already loaded in one of our panes
        const existingPane = Array.from(this.panes.values()).find(p => p.sessionId === act.session_id);
        if (existingPane) {
          if (existingPane._cancelledStreams && existingPane._cancelledStreams.has(act.stream_id)) continue;
          if (existingPane._attachedStreams && existingPane._attachedStreams.has(act.stream_id)) continue;
          continue; // Handled by pane's own checkExternalStream
        }

        // 2. Check if a pane of the same profile is open and currently idle
        const idleProfilePane = Array.from(this.panes.values()).find(p => 
          p.profile.toLowerCase() === act.profile.toLowerCase() && p.status === 'idle'
        );
        if (idleProfilePane) {
          if (idleProfilePane._cancelledStreams && idleProfilePane._cancelledStreams.has(act.stream_id)) continue;
          if (idleProfilePane._attachedStreams && idleProfilePane._attachedStreams.has(act.stream_id)) continue;
          console.log(`[DAON Multi] Auto-switching idle pane for [${act.profile}] to active session ${act.session_id}`);
          await idleProfilePane.loadSession(act.session_id);
          idleProfilePane.attachToStream(act.stream_id);
          continue;
        }

        // 3. Neither session nor profile is open! Auto-spawn a new pane!
        if (this.panes.size < this.MAX_PANES && act.profile) {
          const isAttached = Array.from(this.panes.values()).some(p => p._attachedStreams && p._attachedStreams.has(act.stream_id));
          if (isAttached) continue;

          console.log(`[DAON Multi] 🚀 Auto-spawning new pane for active peer [${act.profile}] (session: ${act.session_id})`);
          const newPane = await this.createPane(act.profile, act.session_id);
          newPane.attachToStream(act.stream_id);

          // Auto-adjust layout (e.g. 2 -> 3, 3 -> 4)
          const count = this.panes.size;
          if (count === 3) this.setLayout('3', false);
          else if (count === 4) this.setLayout('4', false);
          else if (count >= 5) this.setLayout('6', false);
        }
      }
    } catch (e) {
      console.warn('[DAON Multi] checkActiveStreams error:', e);
    }
  }

  async handleDispatch({ from, to, message }) {
    // 1. Highlight visual flow
    this.flow.triggerDispatch(from, to, message);

    // 2. Find target pane
    const targetLower = (to || '').toLowerCase();
    for (const [id, pane] of this.panes.entries()) {
      if (pane.profile.toLowerCase().includes(targetLower)) {
        // Clean up text removing @mention prefix
        const cleanMessage = message.replace(new RegExp(`@${to}\\s*`, 'i'), '').trim();
        pane.injectMessage(cleanMessage);
        return;
      }
    }

    // 3. If target pane is NOT open, auto-spawn it!
    if (this.panes.size < this.MAX_PANES) {
      console.log(`[DAON Multi] Target pane [${to}] not open. Auto-spawning...`);
      const targetProfile = this.findProfileByName([to]) || to;
      const existingSess = this.sessions.find(s => (s.profile || '').toLowerCase().includes(to.toLowerCase()));
      const newPane = await this.createPane(targetProfile, existingSess ? existingSess.session_id : null);

      // Auto-scale layout
      const count = this.panes.size;
      if (count === 3) this.setLayout('3', false);
      else if (count === 4) this.setLayout('4', false);
      else if (count >= 5) this.setLayout('6', false);

      const cleanMessage = message.replace(new RegExp(`@${to}\\s*`, 'i'), '').trim();
      newPane.injectMessage(cleanMessage);
    }
  }
}

// Start on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const app = new MultiApp();
  app.start();
  window.__daonMultiApp = app; // For debugging
});

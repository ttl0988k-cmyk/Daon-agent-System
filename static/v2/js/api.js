/**
 * DAON Workspace v2.5 - Core Backend API Client
 * Seamlessly interfaces with DAON's Python ThreadingHTTPServer & EventSource SSE.
 */

export const DaonAPI = {
  baseUrl: '',

  /**
   * Health & System Status
   */
  async getHealth() {
    const res = await fetch(`${this.baseUrl}/health`);
    if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
    return await res.json();
  },

  async getSystemStatus() {
    const res = await fetch(`${this.baseUrl}/api/system/status`);
    if (!res.ok) throw new Error(`System status failed: ${res.status}`);
    return await res.json();
  },

  /**
   * Sessions
   */
  async getSessions() {
    const res = await fetch(`${this.baseUrl}/api/sessions`);
    if (!res.ok) throw new Error(`Failed to fetch sessions: ${res.status}`);
    const data = await res.json();
    return data.sessions || [];
  },

  async createSession(title = '새 세션', profile = null) {
    const payload = { title };
    if (profile) payload.profile = profile;
    const res = await fetch(`${this.baseUrl}/api/session/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(`Failed to create session: ${res.status}`);
    return await res.json();
  },

  async getSession(sessionId) {
    const res = await fetch(`${this.baseUrl}/api/session?session_id=${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`Failed to load session: ${res.status}`);
    const data = await res.json();
    return data.session || null;
  },

  async deleteSession(sessionId) {
    const res = await fetch(`${this.baseUrl}/api/session/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId })
    });
    return res.ok;
  },

  async updateSession(sessionId, updates) {
    try {
      const res = await fetch(`${this.baseUrl}/api/session/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, ...updates })
      });
      return res.ok;
    } catch (e) {
      console.warn('Failed to update session:', e);
      return false;
    }
  },

  async deleteAllSessions() {
    try {
      const sessions = await this.getSessions();
      for (const s of sessions) {
        if (s.session_id) {
          await this.deleteSession(s.session_id);
        }
      }
      return true;
    } catch (e) {
      console.warn('Failed to delete all sessions:', e);
      return false;
    }
  },


  /**
   * File Upload
   */
  async uploadFile(sessionId, file) {
    const fd = new FormData();
    fd.append('session_id', sessionId);
    fd.append('file', file, file.name);

    const res = await fetch(`${this.baseUrl}/api/upload`, {
      method: 'POST',
      body: fd
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err || `Upload failed: ${res.status}`);
    }
    return await res.json(); // { filename, path, size }
  },

  /**
   * Providers & Models
   */
  async getProviders() {
    try {
      const res = await fetch(`${this.baseUrl}/api/providers`);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      console.warn('Could not fetch providers:', e);
      return null;
    }
  },

  async getModels() {
    try {
      const res = await fetch(`${this.baseUrl}/api/models`);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      console.warn('Could not fetch models:', e);
      return null;
    }
  },

  /**
   * Chat & SSE Streaming
   */
  async startChat({ sessionId, message, model, profile, workspace, attachments = [], reasoningEffort, autonomousMode, approvalPolicy, agentPersona }) {
    const body = {
      session_id: sessionId,
      message,
      model: model || undefined,
      profile: profile || agentPersona || undefined,
      workspace: workspace || undefined,
      attachments: (attachments && attachments.length > 0) ? attachments : undefined,
      reasoning_effort: reasoningEffort || undefined,
      autonomous_mode: autonomousMode !== false,
      approval_policy: approvalPolicy || (autonomousMode !== false ? 'scope' : 'step')
    };

    const res = await fetch(`${this.baseUrl}/api/chat/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Chat start failed: ${res.status}`);
    }

    return await res.json(); // { stream_id, session_id }
  },

  async getActiveStreams() {
    try {
      const res = await fetch(`${this.baseUrl}/api/chat/active`);
      if (!res.ok) return { active_streams: [] };
      return await res.json();
    } catch {
      return { active_streams: [] };
    }
  },

  connectSSE(streamId, callbacks = {}) {
    const { onToken, onReasoning, onStep, onToolCall, onToolResult, onDone, onError, onCompressed, onBoardroomSlot, onBoardroomReply, onBoardroomDone, onHarnessDone, onHarnessStarted, onSecretRequest } = callbacks;
    const url = `${this.baseUrl}/api/chat/stream?stream_id=${encodeURIComponent(streamId)}`;
    const es = new EventSource(url);

    es.addEventListener('token', (e) => {
      try {
        const data = JSON.parse(e.data);
        onToken?.(data.token ?? data.text ?? e.data);
      } catch {
        onToken?.(e.data);
      }
    });

    es.addEventListener('reasoning', (e) => {
      try {
        const data = JSON.parse(e.data);
        onReasoning?.(data.text ?? data.reasoning ?? e.data);
      } catch {
        onReasoning?.(e.data);
      }
    });

    es.addEventListener('step', (e) => {
      try {
        const data = JSON.parse(e.data);
        onStep?.(data);
      } catch {
        onStep?.({ step: e.data });
      }
    });

    es.addEventListener('tool', (e) => {
      try {
        const data = JSON.parse(e.data);
        onToolCall?.(data);
      } catch {
        onToolCall?.({ raw: e.data });
      }
    });

    es.addEventListener('tool_call', (e) => {
      try {
        const data = JSON.parse(e.data);
        onToolCall?.(data);
      } catch {
        onToolCall?.({ raw: e.data });
      }
    });

    es.addEventListener('tool_result', (e) => {
      try {
        const data = JSON.parse(e.data);
        onToolResult?.(data);
      } catch {
        onToolResult?.({ raw: e.data });
      }
    });

    es.addEventListener('apperror', (e) => {
      try {
        const data = JSON.parse(e.data);
        onError?.(new Error(data.message || data.error || '애플리케이션 에러 발생'));
      } catch {
        onError?.(new Error(e.data || '오류 발생'));
      }
    });

    // ── Model Attribution & Fallback Notifications ─────────────────────
    es.addEventListener('model_info', (e) => {
      try {
        callbacks.onModelInfo?.(JSON.parse(e.data));
      } catch {
        callbacks.onModelInfo?.({ model: e.data });
      }
    });

    es.addEventListener('model_fallback', (e) => {
      try {
        callbacks.onModelFallback?.(JSON.parse(e.data));
      } catch {
        callbacks.onModelFallback?.({ fallback: e.data });
      }
    });

    // ── Context Compression: session ID rotated ──────────────────────────
    // 컨텍스트가 임계치를 넘으면 백엔드(streaming.py)가 새 세션을 만들고
    // 파일을 리네임한 뒤 compressed 이벤트로 old/new session_id를 알린다.
    // 이 리스너가 없으면 프론트는 옛 session_id에 묶여 다음 메시지가
    // "Session not found"로 실패하고, 사용자는 안내 없이 무응답을 겪는다.
    es.addEventListener('compressed', (e) => {
      try {
        onCompressed?.(JSON.parse(e.data));
      } catch {
        onCompressed?.({});
      }
    });

    // ── Boardroom: 8-slot agent meeting events ───────────────────────────
    es.addEventListener('boardroom_slot', (e) => {
      try { callbacks.onBoardroomSlot?.(JSON.parse(e.data)); } catch { callbacks.onBoardroomSlot?.({}); }
    });

    es.addEventListener('boardroom_reply', (e) => {
      try { callbacks.onBoardroomReply?.(JSON.parse(e.data)); } catch { callbacks.onBoardroomReply?.({}); }
    });

    es.addEventListener('boardroom_done', (e) => {
      try { callbacks.onBoardroomDone?.(JSON.parse(e.data)); } catch { callbacks.onBoardroomDone?.({}); }
    });

    // 회의실 오케스트레이터(2026-10-15) — 라운드 헤더와 사회자 취합.
    // 이 둘이 빠지면 탭 직접 소집 시 '몇 라운드인지'와 '라온이 무엇을 취합했는지'가
    // 화면에 안 남는다(reply·conclusion 만 보임). live 폴링 경로와 대칭.
    es.addEventListener('boardroom_round_start', (e) => {
      try { callbacks.onBoardroomRoundStart?.(JSON.parse(e.data)); } catch { callbacks.onBoardroomRoundStart?.({}); }
    });

    es.addEventListener('boardroom_moderation', (e) => {
      try { callbacks.onBoardroomModeration?.(JSON.parse(e.data)); } catch { callbacks.onBoardroomModeration?.({}); }
    });

    // ── Dynamic Harness: 백그라운드 잡 완료 알림 ─────────────────────────
    // 챗에서 execute_dynamic_harness(background=True)로 시작한 하네스가 끝나면
    // 해당 턴이 아직 살아있을 때 결과가 push된다. 턴이 이미 끝나 SSE가 닫혔으면
    // 이 이벤트는 오지 않는다 — 그 경우 하네스 탭 폴링이 결과를 보여준다.
    // ── Dynamic Harness: 백그라운드 잡 시작 알림 (run_id 전달) ───────────
    // 챗에서 execute_dynamic_harness(background=True)가 시작되면 run_id가
    // 즉시 push된다. 프론트는 이 run_id로 하네스 탭 폴링(startHarnessPolling)을
    // 시작해 챗에서 비동기로 돌린 하네스의 진행상황을 탭에서 실시간으로 본다.
    es.addEventListener('harness_started', (e) => {
      try { callbacks.onHarnessStarted?.(JSON.parse(e.data)); } catch { callbacks.onHarnessStarted?.({}); }
    });

    es.addEventListener('harness_done', (e) => {
      try { callbacks.onHarnessDone?.(JSON.parse(e.data)); } catch { callbacks.onHarnessDone?.({}); }
    });

    // ── Secrets: 시크릿 요청 push (값 없음 — id/name/purpose/kind만) ─────────
    // 백엔드가 secret_request 이벤트를 보내면 폴링보다 먼저 카드가 뜬다.
    // 미지원 서버에서도 5초 폴링이 동일 카드를 렌더하므로 이중 안전.
    es.addEventListener('secret_request', (e) => {
      let detail = {};
      try { detail = JSON.parse(e.data); } catch { detail = {}; }
      try { onSecretRequest?.(detail); } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('daon:secret-request', { detail })); } catch (_) {}
    });

    es.addEventListener('done', (e) => {
      es.close();
      try {
        onDone?.(JSON.parse(e.data));
      } catch {
        onDone?.(e.data);
      }
    });

    es.addEventListener('error', (e) => {
      // EventSource fires error on EOF/close as well
      es.close();
      onError?.(e);
    });

    return es;
  },

  async cancelChat(streamId, sessionId) {
    if (!streamId) return false;
    try {
      const res = await fetch(`${this.baseUrl}/api/chat/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stream_id: streamId,
          session_id: sessionId || ''
        })
      });
      return res.ok;
    } catch (e) {
      console.warn('Chat cancel failed:', e);
      return false;
    }
  },

  /**
   * Dynamic Harness API
   */
  async runDynamicHarness({ task, autoApprove = true }) {
    const res = await fetch(`${this.baseUrl}/api/dynamic/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, auto_approve: autoApprove })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Dynamic run failed: ${res.status}`);
    }
    return await res.json(); // { ok: true, run_id, status }
  },

  async getDynamicStatus(runId, logCursor = 0) {
    const res = await fetch(`${this.baseUrl}/api/dynamic/status?run_id=${encodeURIComponent(runId)}&log_cursor=${logCursor}`);
    if (!res.ok) throw new Error(`Dynamic status failed: ${res.status}`);
    return await res.json();
  },

  async cancelDynamicRun(runId) {
    const res = await fetch(`${this.baseUrl}/api/dynamic/cancel/${encodeURIComponent(runId)}`, {
      method: 'POST'
    });
    return res.ok;
  },

  /** 의도 확인(clarifying) 질문에 대한 답변을 제출한다. */
  async submitDynamicAnswer(runId, answers) {
    const res = await fetch(`${this.baseUrl}/api/dynamic/answer/${encodeURIComponent(runId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Answer submit failed: ${res.status}`);
    }
    return await res.json();
  },

  /** 승인 대기(awaiting_approval) 상태의 작업을 승인/거부한다. */
  async approveDynamicRun(runId, action = 'approve') {
    const res = await fetch(`${this.baseUrl}/api/dynamic/approve/${encodeURIComponent(runId)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Approval failed: ${res.status}`);
    }
    return await res.json();
  },

  /**
   * Background Wake — 백그라운드 작업 완료 기상 폴러용.
   * poll은 조회만 하고, ack는 자동 기상 직전에 호출해 중복 기상을 막는다.
   */
  async wakePoll(sessionId) {
    const res = await fetch(`${this.baseUrl}/api/wake/poll?session_id=${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`Wake poll failed: ${res.status}`);
    return await res.json(); // { ok, active, completions }
  },

  async wakeAck(ids) {
    const res = await fetch(`${this.baseUrl}/api/wake/ack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids })
    });
    if (!res.ok) throw new Error(`Wake ack failed: ${res.status}`);
    return await res.json(); // { ok, acked }
  },

  /**
   * MCP Servers & Presets
   */
  async getMCPServers() {
    const res = await fetch(`${this.baseUrl}/api/mcp/servers`);
    if (!res.ok) throw new Error(`MCP servers failed: ${res.status}`);
    const data = await res.json();
    return data.servers || [];
  },

  async getMCPPresets() {
    const res = await fetch(`${this.baseUrl}/api/mcp/presets`);
    if (!res.ok) throw new Error(`MCP presets failed: ${res.status}`);
    const data = await res.json();
    return data.presets || {};
  },

  async connectMCPServer(serverId) {
    const res = await fetch(`${this.baseUrl}/api/mcp/servers/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_id: serverId })
    });
    return await res.json();
  },

  async disconnectMCPServer(serverId) {
    const res = await fetch(`${this.baseUrl}/api/mcp/servers/disconnect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_id: serverId })
    });
    return await res.json();
  },

  async addMCPFromPreset(presetId) {
    const res = await fetch(`${this.baseUrl}/api/mcp/servers/add-preset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset_id: presetId })
    });
    return await res.json();
  },

  /**
   * Boardroom & Multi-Agent Meeting API
   */
  async broadcastBoardroom({ task, sessionId = null, models = null, slots = null }) {
    const res = await fetch(`${this.baseUrl}/api/boardroom/broadcast`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task,
        session_id: sessionId || '',
        models: models || {},
        slots: Array.isArray(slots) ? slots : null
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Boardroom broadcast failed: ${res.status}`);
    }
    return await res.json(); // { ok, stream_id, slots, slot_ids }
  },

  /**
   * 회의실 탭의 참여 선택(슬롯 목록 + 슬롯별 모델)을 서버에 저장/조회한다.
   * 서버가 기억해야 채팅에서 convene_boardroom으로 소집할 때도 같은 참여자가 쓰인다.
   * 실패해도 회의실 UI는 계속 동작해야 하므로 예외를 던지지 않고 null을 돌려준다.
   */
  async getBoardroomSelection() {
    try {
      const res = await fetch(`${this.baseUrl}/api/boardroom/selection`);
      if (!res.ok) return null;
      return await res.json(); // { ok, saved, slots, models }
    } catch (e) {
      return null;
    }
  },

  async saveBoardroomSelection({ slots = null, models = null } = {}) {
    try {
      const body = {};
      if (Array.isArray(slots)) body.slots = slots;
      if (models && typeof models === 'object') body.models = models;
      const res = await fetch(`${this.baseUrl}/api/boardroom/selection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!res.ok) return null;
      return await res.json(); // { ok, saved, slots, models }
    } catch (e) {
      return null;
    }
  },

  async startBoardroomMeeting({ sessionId, topic, models = ['deepseek-v4.1-flash', 'glm-5.3-flash'], maxTurns = 8 }) {
    const res = await fetch(`${this.baseUrl}/api/debate/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        topic,
        mode: 'meeting',
        models,
        max_turns: maxTurns
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Meeting start failed: ${res.status}`);
    }
    return await res.json(); // { ok: true, stream_id, session_id }
  },

  /**
   * Skills Registry
   */
  async getSkills() {
    const res = await fetch(`${this.baseUrl}/api/skills`);
    if (!res.ok) throw new Error(`Skills failed: ${res.status}`);
    const data = await res.json();
    return data.skills || [];
  },

  /**
   * Workspaces (Project Folders)
   */
  async getWorkspaces() {
    const res = await fetch(`${this.baseUrl}/api/workspaces`);
    if (!res.ok) return { workspaces: [], last: '' };
    return await res.json();
  },

  async setActiveWorkspace(path) {
    const res = await fetch(`${this.baseUrl}/api/workspaces/set-active`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Failed to set workspace: ${res.status}`);
    }
    return await res.json();
  },

  async selectWorkspaceDialog() {
    const res = await fetch(`${this.baseUrl}/api/workspaces/select`);
    if (!res.ok) throw new Error(`Select dialog failed: ${res.status}`);
    return await res.json();
  },

  /**
   * Providers & Custom Models Management
   */
  async getProviders() {
    const res = await fetch(`${this.baseUrl}/api/providers`);
    if (!res.ok) throw new Error(`Providers failed: ${res.status}`);
    return await res.json();
  },

  async addProvider(providerData) {
    const res = await fetch(`${this.baseUrl}/api/providers/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(providerData)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Failed to add provider: ${res.status}`);
    }
    return await res.json();
  },

  async deleteProvider(name) {
    const res = await fetch(`${this.baseUrl}/api/providers/delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    return res.ok;
  },

  async fetchProviderModels({ name, key, url, api_key, base_url, preset }) {
    const finalKey = (key || api_key || '').trim();
    const finalUrl = (url || base_url || '').trim();
    const res = await fetch(`${this.baseUrl}/api/providers/fetch-models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || 'temp',
        api_key: finalKey,
        key: finalKey,
        base_url: finalUrl,
        url: finalUrl,
        preset
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Fetch models failed: ${res.status}`);
    }
    return await res.json();
  },

  async refreshProviderModels(name) {
    const res = await fetch(`${this.baseUrl}/api/providers/refresh-models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Refresh models failed: ${res.status}`);
    }
    return await res.json();
  },

  /**
   * Agent Profiles (정적 에이전트: 토니, 빌, 셜록, 프라다, 라온 / 다온응대 제외)
   */
  async getProfiles() {
    try {
      const res = await fetch(`${this.baseUrl}/api/profiles`);
      if (!res.ok) return { profiles: [], active: 'raon' };
      const data = await res.json();
      if (Array.isArray(data.profiles)) {
        // 대표님 요구사항: '다온응대'는 필요 없으므로 완전히 필터링
        data.profiles = data.profiles.filter(p => {
          const n = (p.name || '').toLowerCase();
          return !n.includes('다온') && !n.includes('daon');
        });
      }
      return data;
    } catch (_) {
      return { profiles: [], active: 'raon' };
    }
  },

  async switchProfile(name) {
    try {
      const res = await fetch(`${this.baseUrl}/api/profile/switch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      return res.ok;
    } catch (e) {
      console.warn('switchProfile error:', e);
      return false;
    }
  },

  async createProfile(name) {
    const res = await fetch(`${this.baseUrl}/api/profile/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `프로필 생성 실패: ${res.status}`);
    }
    return await res.json();
  },

  /**
   * Secrets (시크릿카드) — 값은 절대 응답에 포함되지 않는다.
   * value는 setSecret 전송 시에만 사용하고, 어떤 경로로도 렌더/로그하지 않는다.
   */
  async getSecrets() {
    const res = await fetch(`${this.baseUrl}/api/secrets`);
    if (!res.ok) throw new Error(`Secrets failed: ${res.status}`);
    const data = await res.json();
    return data.secrets || [];
  },

  async getSecretsPending(sessionId = '') {
    const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    const res = await fetch(`${this.baseUrl}/api/secrets/pending${qs}`);
    if (!res.ok) throw new Error(`Secrets pending failed: ${res.status}`);
    const data = await res.json();
    return data.pending || [];
  },

  async setSecret({ name, value, purpose = '', scope = 'all' }) {
    const res = await fetch(`${this.baseUrl}/api/secrets/set`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, value, purpose, scope })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Secret 저장 실패: ${res.status}`);
    }
    return await res.json();
  },

  async allowSecret(name, sessionId) {
    const res = await fetch(`${this.baseUrl}/api/secrets/allow`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, session_id: sessionId })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Secret 허용 실패: ${res.status}`);
    }
    return await res.json();
  },

  async cancelSecretRequest(id) {
    const res = await fetch(`${this.baseUrl}/api/secrets/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Secret 요청 취소 실패: ${res.status}`);
    }
    return await res.json();
  },

  async updateSecretScope(name, scope) {
    const res = await fetch(`${this.baseUrl}/api/secrets/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, scope })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Secret scope 변경 실패: ${res.status}`);
    }
    return await res.json();
  },

  async deleteSecret(name) {
    const res = await fetch(`${this.baseUrl}/api/secrets/${encodeURIComponent(name)}`, {
      method: 'DELETE'
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `Secret 삭제 실패: ${res.status}`);
    }
    return await res.json();
  }
};

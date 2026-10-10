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
    const { onToken, onReasoning, onStep, onToolCall, onToolResult, onDone, onError, onCompressed, onBoardroomSlot, onBoardroomReply, onBoardroomDone } = callbacks;
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
  async broadcastBoardroom({ task, sessionId = null }) {
    const res = await fetch(`${this.baseUrl}/api/boardroom/broadcast`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task, session_id: sessionId || '' })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Boardroom broadcast failed: ${res.status}`);
    }
    return await res.json(); // { ok, stream_id, slots }
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
  }
};

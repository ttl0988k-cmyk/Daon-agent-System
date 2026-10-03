/**
 * DAON Multi-Agent Orchestrator — api.js
 * REST API client wrapper for headless Daon engine (server.exe:9090)
 */

export const api = {
  /**
   * Fetch all registered profiles and active profile
   */
  async getProfiles() {
    try {
      const res = await fetch('/api/profiles');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('[API] getProfiles failed:', err);
      return { profiles: [], active: 'raon' };
    }
  },

  /**
   * Fetch available models
   */
  async getModels() {
    try {
      const res = await fetch('/api/models');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn('[API] getModels failed, fallback to defaults:', err);
      return { models: ['deepseek-v4.1-flash', 'claude-3-5-sonnet-20241022'], default: 'deepseek-v4.1-flash' };
    }
  },

  /**
   * Create a new isolated session for a specific profile
   * @param {string} profile - Target profile name (e.g. 'raon', '빌(개발)')
   * @param {string} [workspace] - Target workspace directory
   * @param {string} [model] - Optional LLM model
   */
  async createSession(profile, workspace = 'C:\\daon', model = '') {
    try {
      const payload = { profile, workspace };
      if (model) payload.model = model;

      const res = await fetch('/api/session/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`Session create failed (${res.status}): ${errText}`);
      }
      return await res.json();
    } catch (err) {
      console.error('[API] createSession failed:', err);
      throw err;
    }
  },

  /**
   * Start chat execution (generates stream_id)
   * ⚠️ CAUTION: Starting chat on a busy session will cancel previous execution!
   */
  async startChat(sessionId, message, workspace = 'C:\\daon', model = '') {
    try {
      const payload = {
        session_id: sessionId,
        message,
        workspace
      };
      if (model) payload.model = model;

      const res = await fetch('/api/chat/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`Chat start failed (${res.status}): ${errText}`);
      }
      return await res.json(); // { stream_id, session_id }
    } catch (err) {
      console.error('[API] startChat failed:', err);
      throw err;
    }
  },

  /**
   * Fetch all existing sessions
   */
  async getSessions() {
    try {
      const res = await fetch('/api/sessions');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('[API] getSessions failed:', err);
      return { sessions: [] };
    }
  },

  /**
   * Cancel ongoing stream
   */
  async cancelChat(streamId, sessionId = null) {
    if (!streamId && !sessionId) return { ok: false };
    try {
      const payload = { stream_id: streamId || '' };
      if (sessionId) payload.session_id = sessionId;

      const res = await fetch('/api/chat/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      return await res.json();
    } catch (err) {
      console.error('[API] cancelChat failed:', err);
      return { ok: false, error: err.message };
    }
  },

  /**
   * Fetch session message history
   */
  async getSession(sessionId) {
    try {
      const res = await fetch(`/api/session?session_id=${encodeURIComponent(sessionId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('[API] getSession failed:', err);
      return null;
    }
  },

  /**
   * Check if a stream or session is actively running
   */
  async getStreamStatus(sessionIdOrStreamId) {
    if (!sessionIdOrStreamId) return { active: false };
    try {
      const param = sessionIdOrStreamId.length === 32 ? `stream_id=${sessionIdOrStreamId}` : `session_id=${sessionIdOrStreamId}`;
      const res = await fetch(`/api/chat/stream/status?${param}`);
      if (!res.ok) return { active: false };
      return await res.json();
    } catch (_) {
      return { active: false };
    }
  },

  /**
   * Return all currently active streams across all sessions
   */
  async getActiveStreams() {
    try {
      const res = await fetch('/api/chat/active');
      if (!res.ok) return { active_streams: [] };
      return await res.json();
    } catch (_) {
      return { active_streams: [] };
    }
  },

  /**
   * Respond to pending approval (laya/permission guard)
   */
  async respondApproval(sessionId, approved = true) {
    try {
      const res = await fetch('/api/approval/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, approved })
      });
      return await res.json();
    } catch (err) {
      console.error('[API] respondApproval failed:', err);
      return { ok: false };
    }
  },

  /**
   * ── Herdr External Workers API ──
   */
  async getWorkers() {
    try {
      const res = await fetch('/api/workers');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.warn('[API] getWorkers failed:', err);
      return { ok: false, workers: [] };
    }
  },

  async startWorker(name = 'worker-codex', kind = 'codex', cwd = 'C:\\daon') {
    const res = await fetch('/api/workers/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, kind, cwd })
    });
    return await res.json();
  },

  async promptWorker(name, prompt) {
    const res = await fetch('/api/workers/prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, prompt })
    });
    return await res.json();
  },

  async approveWorker(name, decision = 'approve', key = null) {
    const res = await fetch('/api/workers/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, decision, key })
    });
    return await res.json();
  },

  async setWorkerModel(name, model) {
    const res = await fetch('/api/workers/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, model })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return await res.json();
  }
};


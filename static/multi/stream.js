/**
 * DAON Multi-Agent Orchestrator — stream.js
 * Independent SSE (EventSource) Stream Manager for each Pane.
 * Handles 21 Daon engine events with state transitions (working, blocked, idle).
 */

export class AgentStream {
  constructor(streamId, callbacks = {}) {
    this.streamId = streamId;
    this.callbacks = Object.assign({
      onToken: () => {},
      onReasoning: () => {},
      onJob: () => {},
      onTool: () => {},
      onApproval: () => {},
      onDone: () => {},
      onCancel: () => {},
      onError: () => {},
      onStatusChange: () => {},
    }, callbacks);

    this.es = null;
    this.closed = false;
    this.init();
  }

  init() {
    if (!this.streamId) return;
    const url = `/api/chat/stream?stream_id=${encodeURIComponent(this.streamId)}`;
    this.es = new EventSource(url);
    this.callbacks.onStatusChange('working');

    // 1. Core Token Stream
    this.es.addEventListener('token', (e) => {
      if (this.closed) return;
      try {
        const data = JSON.parse(e.data);
        const text = data.text !== undefined ? data.text : (data.token || data.content || (typeof data === 'string' ? data : ''));
        this.callbacks.onToken(text);
      } catch {
        this.callbacks.onToken(e.data || '');
      }
    });

    // 2. Reasoning / Thinking Stream
    this.es.addEventListener('reasoning', (e) => {
      if (this.closed) return;
      try {
        const data = JSON.parse(e.data);
        const text = data.text !== undefined ? data.text : (data.reasoning || data.token || (typeof data === 'string' ? data : ''));
        this.callbacks.onReasoning(text);
      } catch {
        this.callbacks.onReasoning(e.data || '');
      }
    });

    // 3. Tool Job Execution (job { type: 'start' | 'progress' })
    this.es.addEventListener('job', (e) => {
      if (this.closed) return;
      try {
        const data = JSON.parse(e.data);
        this.callbacks.onJob(data);
      } catch (err) {
        console.warn('[Stream] job parse error:', err);
      }
    });

    // 4. Tool Output / Execution Complete
    this.es.addEventListener('tool', (e) => {
      if (this.closed) return;
      try {
        const data = JSON.parse(e.data);
        this.callbacks.onTool(data);
      } catch (err) {
        console.warn('[Stream] tool parse error:', err);
      }
    });

    // 5. Approval / Gatekeeper (Triggers BLOCKED status)
    this.es.addEventListener('approval', (e) => {
      if (this.closed) return;
      try {
        const data = JSON.parse(e.data);
        this.callbacks.onStatusChange('blocked');
        this.callbacks.onApproval(data);
      } catch (err) {
        console.warn('[Stream] approval parse error:', err);
      }
    });

    // 6. Work Complete (Done -> Idle)
    this.es.addEventListener('done', (e) => {
      if (this.closed) return;
      this.callbacks.onStatusChange('idle');
      this.callbacks.onDone();
      this.close();
    });

    // 7. Work Cancelled (Cancel -> Idle)
    this.es.addEventListener('cancel', (e) => {
      if (this.closed) return;
      this.callbacks.onStatusChange('idle');
      this.callbacks.onCancel();
      this.close();
    });

    // 8. Errors
    const handleError = (e) => {
      if (this.closed) return;
      console.warn(`[Stream ${this.streamId}] Error event:`, e);
      try {
        const data = e.data ? JSON.parse(e.data) : { error: 'Unknown stream error' };
        this.callbacks.onError(data);
      } catch {
        this.callbacks.onError({ error: e.data || 'Connection error' });
      }
    };

    this.es.addEventListener('apperror', handleError);
    this.es.addEventListener('apierror', handleError);

    // 9. Raw EventSource Error / Connection drop
    this.es.onerror = (e) => {
      if (this.closed) return;
      if (this.es.readyState === EventSource.CLOSED) {
        this.callbacks.onStatusChange('idle');
        this.callbacks.onDone();
        this.close();
      }
    };
  }

  /**
   * Safely close and cleanup SSE to prevent connection leak
   */
  close() {
    if (this.closed) return;
    this.closed = true;
    if (this.es) {
      try {
        this.es.close();
      } catch (err) {
        console.warn('[Stream] Error closing EventSource:', err);
      }
      this.es = null;
    }
  }
}

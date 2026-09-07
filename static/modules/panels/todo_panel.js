// @ts-check
/**
 * DAON Panel Module: todo_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

function loadTodos() {

  const panel = $('todoPanel');

  if (!panel) return;

  // Parse the most recent todo state from message history

  let todos = [];

  for (let i = S.messages.length - 1; i >= 0; i--) {

    const m = S.messages[i];

    if (m && m.role === 'tool') {

      try {

        const d = JSON.parse(typeof m.content === 'string' ? m.content : JSON.stringify(m.content));

        if (d && Array.isArray(d.todos) && d.todos.length) {

          todos = d.todos;

          break;

        }

      } catch (e) { }

    }

  }

  if (!todos.length) {

    panel.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:4px 0">No active task list in this session.</div>';

    return;

  }

  const statusIcon = { pending: '○', in_progress: '◉', completed: '✓', cancelled: '✗' };

  const statusColor = { pending: 'var(--muted)', in_progress: 'var(--blue)', completed: 'rgba(100,200,100,.8)', cancelled: 'rgba(200,100,100,.5)' };

  panel.innerHTML = todos.map(t => `

    <div style="display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);">

      <span style="font-size:14px;flex-shrink:0;margin-top:1px;color:${statusColor[t.status] || 'var(--muted)'}">${statusIcon[t.status] || '○'}</span>

      <div style="flex:1;min-width:0">

        <div style="font-size:13px;color:${t.status === 'completed' ? 'var(--muted)' : t.status === 'in_progress' ? 'var(--text)' : 'var(--text)'};${t.status === 'completed' ? 'text-decoration:line-through;opacity:.5' : ''};line-height:1.4">${esc(t.content)}</div>

        <div style="font-size:10px;color:var(--muted);margin-top:2px;opacity:.6">${esc(t.id)} · ${esc(t.status)}</div>

      </div>

    </div>`).join('');

}

async function clearConversation() {

  if (!S.session) return;

  if (!confirm('Clear all messages in this conversation? This cannot be undone.')) return;

  try {

    const data = await api('/api/session/clear', {
      method: 'POST',

      body: JSON.stringify({ session_id: S.session.session_id })
    });

    S.session = data.session;

    S.messages = [];

    S.toolCalls = [];

    syncTopbar();

    renderMessages();

    showToast('대화 내용 삭제됨');

  } catch (e) { setStatus('Clear failed: ' + e.message); }

}

// ── Skills panel ──

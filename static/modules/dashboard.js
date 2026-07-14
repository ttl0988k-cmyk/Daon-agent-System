// ── Token Usage & Cost Dashboard ──

/**
 * Fetch and render the dashboard with aggregated token/cost metrics.
 * Called on panel switch to 'dashboard' and via the refresh button.
 */
async function loadDashboard() {
    const els = {
        totalTokens: $('dashTotalTokens'),
        totalCost: $('dashTotalCost'),
        runCount: $('dashRunCount'),
        sessionCount: $('dashSessionCount'),
        modelBreakdown: $('dashModelBreakdown'),
        agentBreakdown: $('dashAgentBreakdown'),
        recentRuns: $('dashRecentRuns'),
    };

    // Show loading state
    for (const el of Object.values(els)) {
        if (el) el.innerHTML = '<span style="color:var(--muted)">불러오는 중...</span>';
    }

    try {
        const data = await api('/api/dashboard/metrics');
        if (!data) {
            for (const el of Object.values(els)) {
                if (el) el.innerHTML = '<span style="color:var(--danger)">데이터를 불러오지 못했습니다.</span>';
            }
            return;
        }

        renderSummaryCards(data, els);
        renderModelBreakdown(data, els);
        renderAgentBreakdown(data, els);
        renderRecentRuns(data, els);
    } catch (err) {
        console.error('[Dashboard] Failed to load metrics:', err);
        for (const el of Object.values(els)) {
            if (el) el.innerHTML = '<span style="color:var(--danger)">오류: ' + esc(err.message) + '</span>';
        }
    }
}

function renderSummaryCards(data, els) {
    const t = data.total || {};
    const s = data.session_usage || {};

    els.totalTokens.innerHTML = formatTokenCount(t.input_tokens + t.output_tokens);
    els.totalCost.innerHTML = '$' + (t.estimated_cost || 0).toFixed(4);
    els.runCount.innerHTML = `${t.total_runs || 0}회 <span style="font-size:9px;color:var(--muted)">(성공:${t.successful_runs || 0} 실패:${t.failed_runs || 0})</span>`;
    els.sessionCount.innerHTML = `${s.session_count || 0} 세션 <span style="font-size:9px;color:var(--muted)">(${formatTokenCount(s.total_input_tokens + s.total_output_tokens)})</span>`;
}

function renderModelBreakdown(data, els) {
    const byModel = data.by_model || {};
    const entries = Object.entries(byModel).sort((a, b) => b[1].estimated_cost - a[1].estimated_cost);

    if (entries.length === 0) {
        els.modelBreakdown.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">아직 실행 데이터가 없습니다.</div>';
        return;
    }

    const maxCost = entries[0][1].estimated_cost || 1;

    let html = '';
    for (const [model, info] of entries) {
        const pct = maxCost > 0 ? (info.estimated_cost / maxCost * 100) : 0;
        const shortName = model.length > 28 ? model.substring(0, 26) + '..' : model;
        html += `
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 7px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
          <span style="font-size:10px;font-weight:600;color:var(--text)" title="${esc(model)}">${esc(shortName)}</span>
          <span style="font-size:9px;color:var(--accent);font-weight:600">$${info.estimated_cost.toFixed(4)}</span>
        </div>
        <div style="background:var(--border);border-radius:3px;height:4px;margin-bottom:2px">
          <div style="background:var(--accent);height:4px;border-radius:3px;width:${pct}%;transition:width .3s"></div>
        </div>
        <div style="font-size:8px;color:var(--muted);display:flex;gap:8px">
          <span>입력 ${formatTokenCount(info.input_tokens)}</span>
          <span>출력 ${formatTokenCount(info.output_tokens)}</span>
          <span>실행 ${info.run_count}회</span>
        </div>
      </div>`;
    }
    els.modelBreakdown.innerHTML = html;
}

function renderAgentBreakdown(data, els) {
    const byAgent = data.by_agent || {};
    const entries = Object.entries(byAgent).sort((a, b) => b[1].estimated_cost - a[1].estimated_cost);

    if (entries.length === 0) {
        els.agentBreakdown.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">아직 에이전트 실행 데이터가 없습니다.</div>';
        return;
    }

    let html = '';
    for (const [agent, info] of entries) {
        html += `
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 7px;display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-size:10px;font-weight:600;color:var(--text)">${esc(agent)}</span>
          <span style="font-size:8px;color:var(--muted);margin-left:4px">${esc(info.model || '')}</span>
        </div>
        <div style="text-align:right">
          <div style="font-size:10px;font-weight:600;color:var(--accent)">$${info.estimated_cost.toFixed(4)}</div>
          <div style="font-size:8px;color:var(--muted)">${formatTokenCount(info.input_tokens + info.output_tokens)}</div>
        </div>
      </div>`;
    }
    els.agentBreakdown.innerHTML = html;
}

function renderRecentRuns(data, els) {
    const runs = data.recent_runs || [];

    if (runs.length === 0) {
        els.recentRuns.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">아직 실행 기록이 없습니다.</div>';
        return;
    }

    let html = '';
    for (const run of runs) {
        const statusIcon = run.status === 'success' ? '✅' : '❌';
        const statusColor = run.status === 'success' ? 'var(--success)' : 'var(--danger)';
        const timeStr = run.start_time ? new Date(run.start_time * 1000).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '?';
        const taskPreview = run.task || '작업 없음';

        html += `
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:5px 7px;margin-bottom:3px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1px">
          <span style="font-size:9px;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%">${esc(taskPreview)}</span>
          <span style="font-size:9px;color:${statusColor}">${statusIcon} ${run.status}</span>
        </div>
        <div style="font-size:8px;color:var(--muted);display:flex;gap:8px">
          <span>${timeStr}</span>
          <span>${formatTokenCount(run.input_tokens + run.output_tokens)}</span>
          <span>$${(run.estimated_cost || 0).toFixed(4)}</span>
          <span>노드 ${run.node_count}개</span>
        </div>
        ${run.error ? `<div style="font-size:8px;color:var(--danger);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(run.error)}</div>` : ''}
      </div>`;
    }
    els.recentRuns.innerHTML = html;
}

/**
 * Format a token count into a human-readable string.
 * e.g. 1234567 -> "1.23M"
 */
function formatTokenCount(n) {
    if (n == null) return '0';
    n = Number(n);
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return String(n);
}

// ── Auto-load is handled by switchPanel in panels.js ──

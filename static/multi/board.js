/**
 * DAON Multi-Agent Orchestrator — board.js
 * Real-time Agent Status Board Manager.
 * Aggregates state across all active panes and renders live indicator chips in the footer.
 */

export class AgentBoard {
  constructor(containerEl) {
    this.container = containerEl;
    this.agents = new Map(); // paneId -> { profile, status, lastUpdate }
  }

  updateAgent(paneId, status, profile) {
    this.agents.set(paneId, {
      profile,
      status,
      lastUpdate: Date.now()
    });
    this.render();
  }

  removeAgent(paneId) {
    this.agents.delete(paneId);
    this.render();
  }

  getIcon(profile) {
    const p = (profile || '').toLowerCase();
    if (p.includes('raon') || p.includes('라온')) return '👑';
    if (p.includes('bill') || p.includes('빌')) return '🔨';
    if (p.includes('sherlock') || p.includes('셜록')) return '🔍';
    if (p.includes('tony') || p.includes('토니')) return '💡';
    if (p.includes('prada') || p.includes('프라다')) return '🎨';
    if (p.includes('codex') || p.includes('코덱스')) return '⚡';
    if (p.includes('claude') || p.includes('클로드')) return '🔮';
    return '🤖';
  }

  getStatusColor(status) {
    switch (status) {
      case 'working': return '#10b981'; // Green
      case 'blocked': return '#ef4444'; // Red
      case 'waiting': return '#eab308'; // Yellow
      case 'idle': default: return '#64748b'; // Gray
    }
  }

  render() {
    if (!this.container) return;
    if (this.agents.size === 0) {
      this.container.innerHTML = '<span style="color:var(--text-dim); font-size:11px;">활성 에이전트 없음</span>';
      return;
    }

    const chips = Array.from(this.agents.entries()).map(([paneId, data]) => {
      const icon = this.getIcon(data.profile);
      const color = this.getStatusColor(data.status);
      const pulseStyle = data.status === 'working' ? 'box-shadow: 0 0 8px #10b981;' : '';
      const blockedStyle = data.status === 'blocked' ? 'border-color: #ef4444; color: #f87171;' : '';

      return `
        <div class="board-agent-chip" data-pane="${paneId}" style="${blockedStyle}" title="클릭하여 ${data.profile} 창으로 이동">
          <span>${icon}</span>
          <strong>${data.profile}</strong>
          <span style="display:inline-block; width:6px; height:6px; border-radius:50%; background:${color}; ${pulseStyle}"></span>
          <span style="font-size:10px; opacity:0.8;">${data.status.toUpperCase()}</span>
        </div>
      `;
    }).join('');

    this.container.innerHTML = chips;

    // Click chip to focus pane
    this.container.querySelectorAll('.board-agent-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const paneId = chip.dataset.pane;
        const targetEl = document.getElementById(paneId);
        if (targetEl) {
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          targetEl.classList.add('highlight-pulse');
          setTimeout(() => targetEl.classList.remove('highlight-pulse'), 1200);
        }
      });
    });
  }
}

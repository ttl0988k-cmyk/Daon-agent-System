/**
 * DAON Multi-Agent Orchestrator — flow.js
 * Visual Pipeline and Inter-Agent Dispatch Manager.
 * Highlights dispatch flows between Commander (Raon) and Specialists (Bill, Sherlock, Tony).
 */

export class AgentFlow {
  constructor(pipelineContainerEl) {
    this.container = pipelineContainerEl;
  }

  /**
   * Trigger a visual pulse along the pipeline
   * @param {string} fromProfile 
   * @param {string} toProfile 
   * @param {string} message 
   */
  triggerDispatch(fromProfile, toProfile, message = '') {
    if (!this.container) return;

    // Normalize names
    const from = (fromProfile || '').toLowerCase();
    const to = (toProfile || '').toLowerCase();

    const badges = this.container.querySelectorAll('.pipeline-badge');
    badges.forEach(badge => {
      const text = badge.textContent.toLowerCase();
      if (text.includes(to)) {
        badge.style.transform = 'scale(1.15)';
        badge.style.boxShadow = '0 0 16px rgba(6, 182, 212, 0.8)';
        badge.style.borderColor = '#00e5ff';
        badge.style.transition = 'all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)';

        setTimeout(() => {
          badge.style.transform = '';
          badge.style.boxShadow = '';
          badge.style.borderColor = '';
        }, 1500);
      }
    });

    console.log(`[Flow] Dispatched task from [${fromProfile}] to [${toProfile}]: ${message.substring(0, 40)}...`);
  }
}

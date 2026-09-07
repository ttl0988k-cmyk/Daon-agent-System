// @ts-check
/**
 * DAON Panel Module: memory_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

async function loadMemory(force) {

  const panel = $('memoryPanel');

  try {

    const data = await api('/api/memory');

    _memoryData = data;  // cache for edit form

    const fmtTime = ts => ts ? new Date(ts * 1000).toLocaleString() : '';

    panel.innerHTML = `

      <div class="memory-section">

        <div class="memory-section-title">

          &#129504; My Notes

          <span class="memory-mtime">${fmtTime(data.memory_mtime)}</span>

        </div>

        ${data.memory

        ? `<div class="memory-content preview-md">${renderMd(data.memory)}</div>`

        : '<div class="memory-empty">아직 노트가 없습니다.</div>'}

      </div>

      <div class="memory-section">

        <div class="memory-section-title">

          &#128100; User Profile

          <span class="memory-mtime">${fmtTime(data.user_mtime)}</span>

        </div>

        ${data.user

        ? `<div class="memory-content preview-md">${renderMd(data.user)}</div>`

        : '<div class="memory-empty">아직 프로필이 없습니다.</div>'}

      </div>`;
    loadMemoryStore(panel);
  } catch (e) { panel.innerHTML = `<div style="color:var(--accent);font-size:12px">오류: ${esc(e.message)}</div>`; }
}

// ── DAON 기억 시스템 (자동 추출 facts / 자동 프로필 / 세션 요약) ──
async function loadMemoryStore(panel) {
  let store = panel.querySelector('#memoryStorePanel');
  if (!store) {
    store = document.createElement('div');
    store.id = 'memoryStorePanel';
    panel.appendChild(store);
  }
  store.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px;">기억 불러오는 중...</div>';
  const fmt = ts => ts ? new Date(ts * 1000).toLocaleString() : '';
  try {
    const [factsRes, profRes, sumRes, statsRes] = await Promise.all([
      api('/api/memory/facts?limit=50'),
      api('/api/memory/profile'),
      api('/api/memory/summaries?limit=20'),
      api('/api/memory/store/stats'),
    ]);
    const facts = (factsRes && factsRes.facts) || [];
    const profile = (profRes && profRes.profile) || {};
    const summaries = (sumRes && sumRes.summaries) || [];
    const stats = statsRes || {};

    const profileKeys = Object.keys(profile);
    const profileHtml = profileKeys.length
      ? profileKeys.map(k =>
        `<div style="display:flex;gap:8px;font-size:11px;padding:2px 0;">
             <span style="color:var(--muted);min-width:90px;">${esc(k)}</span>
             <span style="color:var(--text);">${esc(profile[k] || '')}</span>
           </div>`).join('')
      : '<div class="memory-empty">아직 자동 학습된 프로필이 없습니다.</div>';

    const factsHtml = facts.length
      ? facts.map(f =>
        `<div style="display:flex;gap:6px;align-items:flex-start;padding:4px 0;border-bottom:1px solid var(--border);">
             <span style="font-size:9px;padding:1px 5px;border-radius:8px;background:var(--bg3);color:var(--muted);white-space:nowrap;">${esc(f.category || 'general')}</span>
             <span style="font-size:11px;color:var(--text);flex:1;">${esc(f.content)}</span>
             <button class="memstore-del" data-id="${f.id}" title="삭제"
               style="border:none;background:none;color:var(--muted);cursor:pointer;font-size:11px;padding:0 2px;">✕</button>
           </div>`).join('')
      : '<div class="memory-empty">대화가 쌓이면 자동으로 기억을 추출합니다.</div>';

    const sumHtml = summaries.length
      ? summaries.map(s =>
        `<div style="padding:6px 0;border-bottom:1px solid var(--border);">
             <div style="font-size:11px;font-weight:600;color:var(--text);">${esc(s.title || 'Untitled')}
               <span class="memory-mtime">${fmt(s.created_at)}</span></div>
             <div style="font-size:11px;color:var(--muted);margin-top:2px;">${esc(s.summary)}</div>
           </div>`).join('')
      : '<div class="memory-empty">아직 세션 요약이 없습니다.</div>';

    store.innerHTML = `
      <div class="memory-section">
        <div class="memory-section-title">&#129302; 자동 프로필 <span class="memory-mtime">${stats.profile_keys || 0}개 항목</span></div>
        <div class="memory-content">${profileHtml}</div>
      </div>
      <div class="memory-section">
        <div class="memory-section-title">&#128161; 자동 추출 기억 <span class="memory-mtime">${stats.facts || 0}건</span></div>
        <div class="memory-content">${factsHtml}</div>
      </div>
      <div class="memory-section">
        <div class="memory-section-title">&#128221; 세션 요약 <span class="memory-mtime">${stats.summaries || 0}건</span></div>
        <div class="memory-content">${sumHtml}</div>
      </div>`;

    store.querySelectorAll('.memstore-del').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.getAttribute('data-id');
        try {
          await api('/api/memory/fact/delete', { method: 'POST', body: JSON.stringify({ id: Number(id) }) });
          loadMemoryStore(panel);
        } catch (e) { /* ignore */ }
      });
    });
  } catch (e) {
    store.innerHTML = '<div style="color:var(--muted);font-size:11px;">기억 시스템을 불러오지 못했습니다.</div>';
  }
}

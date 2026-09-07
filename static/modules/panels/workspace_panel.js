// @ts-check
/**
 * DAON Panel Module: workspace_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

async function loadWorkspaceList() {

  try {

    const data = await api('/api/workspaces');

    _workspaceList = data.workspaces || [];

    // Refresh sidebar display if we have a current session

    if (S.session && S.session.workspace) {

      const sidebarName = $('sidebarWsName');

      const sidebarPath = $('sidebarWsPath');

      if (sidebarName) sidebarName.textContent = getWorkspaceFriendlyName(S.session.workspace);

      if (sidebarPath) sidebarPath.textContent = S.session.workspace;

    }

    return data;

  } catch (e) { return { workspaces: [], last: '' }; }

}

function renderWorkspaceDropdown(workspaces, currentWs) {

  const dd = $('wsDropdown');

  if (!dd) return;

  dd.innerHTML = '';

  for (const w of workspaces) {

    const opt = document.createElement('div');

    opt.className = 'ws-opt' + (w.path === currentWs ? ' active' : '');

    opt.innerHTML = `<span class="ws-opt-name">${esc(w.name)}</span><span class="ws-opt-path">${esc(w.path)}</span>`;

    opt.onclick = async () => {

      closeWsDropdown();

      if (!S.session || w.path === S.session.workspace) return;

      await api('/api/session/update', {
        method: 'POST', body: JSON.stringify({

          session_id: S.session.session_id, workspace: w.path, model: S.session.model

        })
      });

      S.session.workspace = w.path;

      syncTopbar();

      await loadDir('.');

      showToast(`Switched to ${w.name}`);

    };

    dd.appendChild(opt);

  }

  // Divider + Manage link

  const div = document.createElement('div'); div.className = 'ws-divider'; dd.appendChild(div);

  const mgmt = document.createElement('div'); mgmt.className = 'ws-opt ws-manage';

  mgmt.innerHTML = '&#9881; Manage workspaces';

  mgmt.onclick = () => { closeWsDropdown(); switchPanel('workspaces'); };

  dd.appendChild(mgmt);

}

function toggleWsDropdown() {

  const dd = $('wsDropdown');

  if (!dd) return;

  const open = dd.classList.contains('open');

  if (open) { closeWsDropdown(); }

  else {

    closeProfileDropdown(); // close profile dropdown if open

    loadWorkspaceList().then(data => {

      renderWorkspaceDropdown(data.workspaces, S.session ? S.session.workspace : '');

      dd.classList.add('open');

    });

  }

}

function closeWsDropdown() {

  const dd = $('wsDropdown');

  if (dd) dd.classList.remove('open');

}

document.addEventListener('click', e => {

  if (!e.target.closest('#sidebarWsDisplay') && !e.target.closest('#wsDropdown')) closeWsDropdown();

});

async function loadWorkspacesPanel() {

  const panel = $('workspacesPanel');

  if (!panel) return;

  const data = await loadWorkspaceList();

  renderWorkspacesPanel(data.workspaces);

}

function renderWorkspacesPanel(workspaces) {

  const panel = $('workspacesPanel');

  panel.innerHTML = '';

  for (const w of workspaces) {

    const row = document.createElement('div'); row.className = 'ws-row';

    row.innerHTML = `

      <div class="ws-row-info">

        <div class="ws-row-name">${esc(w.name)}</div>

        <div class="ws-row-path">${esc(w.path)}</div>

      </div>

      <div class="ws-row-actions">

        <button class="ws-action-btn" title="현재 세션에서 사용" onclick="switchToWorkspace('${esc(w.path)}','${esc(w.name)}')">&#8594; 사용</button>

        <button class="ws-action-btn danger" title="삭제" onclick="removeWorkspace('${esc(w.path)}')">&#10005;</button>

      </div>`;

    panel.appendChild(row);

  }

  const addRow = document.createElement('div'); addRow.className = 'ws-add-row';

  addRow.innerHTML = `
    <button class="ws-action-btn" onclick="pickWorkspaceFolder()" style="flex:1;display:flex;align-items:center;justify-content:center;gap:6px;padding:9px 12px;">
      <span style="font-size:16px;">📁</span> 폴더 선택하여 추가
    </button>`;

  panel.appendChild(addRow);

  const hint = document.createElement('div');
  hint.style.cssText = 'font-size:11px;color:var(--muted);padding:4px 0 8px';
  hint.textContent = '폴더 선택 대화상자에서 작업공간 디렉터리를 선택하세요.';
  panel.appendChild(hint);

}

async function pickWorkspaceFolder() {
  if (typeof openWebExplorer !== 'function') {
    showToast('폴더 탐색기를 사용할 수 없습니다.');
    return;
  }
  openWebExplorer({
    type: 'dir',
    title: '작업공간 폴더 선택',
    onSelect: async (selectedPath) => {
      if (!selectedPath) return;
      try {
        const data = await api('/api/workspaces/add', { method: 'POST', body: JSON.stringify({ path: selectedPath }) });
        _workspaceList = data.workspaces;
        renderWorkspacesPanel(data.workspaces);
        showToast('작업공간을 추가했습니다: ' + selectedPath);
      } catch (e) { showToast('추가 실패: ' + e.message); }
    }
  });
}

async function removeWorkspace(path) {

  if (!confirm(`작업공간 "${path}"를 삭제할까요?`)) return;

  try {

    const data = await api('/api/workspaces/remove', { method: 'POST', body: JSON.stringify({ path }) });

    _workspaceList = data.workspaces;

    renderWorkspacesPanel(data.workspaces);

    showToast('작업공간을 삭제했습니다');

  } catch (e) { setStatus('삭제 실패: ' + e.message); }

}

async function switchToWorkspace(path, name) {

  if (!S.session) return;

  try {

    await api('/api/session/update', {
      method: 'POST', body: JSON.stringify({

        session_id: S.session.session_id, workspace: path, model: S.session.model

      })
    });

    S.session.workspace = path;

    syncTopbar();

    await loadDir('.');

    showToast(`전환 완료: ${name}`);

  } catch (e) { setStatus('전환 실패: ' + e.message); }

}

// ── Profile panel + dropdown ──

var _profilesCache = null;

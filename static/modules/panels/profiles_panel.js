// @ts-check
/**
 * DAON Panel Module: profiles_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

async function loadProfilesPanel() {

  const panel = $('profilesPanel');

  if (!panel) return;

  try {

    const data = await api('/api/profiles');

    _profilesCache = data;

    panel.innerHTML = '';

    if (!data.profiles || !data.profiles.length) {

      panel.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:12px">프로필이 없습니다.</div>';

      return;

    }

    for (const p of data.profiles) {

      const card = document.createElement('div');

      card.className = 'profile-card';

      const meta = [];

      if (p.model) meta.push(p.model.split('/').pop());

      if (p.provider) meta.push(p.provider);

      if (p.skill_count) meta.push(p.skill_count + ' skill' + (p.skill_count !== 1 ? 's' : ''));

      if (p.has_env) meta.push('API keys configured');

      const gwDot = p.gateway_running

        ? '<span class="profile-opt-badge running" title="Gateway running"></span>'

        : '<span class="profile-opt-badge stopped" title="Gateway stopped"></span>';

      const isActive = p.name === data.active;

      const activeBadge = isActive ? '<span style="color:var(--link);font-size:10px;font-weight:600;margin-left:6px">ACTIVE</span>' : '';

      card.innerHTML = `

        <div class="profile-card-header">

          <div style="min-width:0;flex:1">

            <div class="profile-card-name${isActive ? ' is-active' : ''}">${gwDot}${esc(p.name)}${p.is_default ? ' <span style="opacity:.5">(default)</span>' : ''}${activeBadge}</div>

            ${meta.length ? `<div class="profile-card-meta">${esc(meta.join(' \u00b7 '))}</div>` : '<div class="profile-card-meta">설정 없음</div>'}

          </div>

          <div class="profile-card-actions">

            ${!isActive ? `<button class="ws-action-btn" onclick="switchToProfile('${esc(p.name)}')" title="이 프로필로 전환">사용</button>` : ''}

            ${!p.is_default ? `<button class="ws-action-btn danger" onclick="deleteProfile('${esc(p.name)}')" title="이 프로필 삭제">&#10005;</button>` : ''}

          </div>

        </div>`;

      panel.appendChild(card);

    }

  } catch (e) {

    panel.innerHTML = `<div style="color:var(--accent);font-size:12px;padding:12px">Error: ${esc(e.message)}</div>`;

  }

}

function renderProfileDropdown(data) {

  const dd = $('profileDropdown');

  if (!dd) return;

  dd.innerHTML = '';

  const profiles = data.profiles || [];

  const active = data.active || 'default';

  for (const p of profiles) {

    const opt = document.createElement('div');

    opt.className = 'profile-opt' + (p.name === active ? ' active' : '');

    const meta = [];

    if (p.model) meta.push(p.model.split('/').pop());

    if (p.skill_count) meta.push(p.skill_count + ' skills');

    const gwDot = `<span class="profile-opt-badge ${p.gateway_running ? 'running' : 'stopped'}"></span>`;

    const checkmark = p.name === active ? ' <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--link)" stroke-width="3" style="vertical-align:-1px"><polyline points="20 6 9 17 4 12"/></svg>' : '';

    opt.innerHTML = `<div class="profile-opt-name">${gwDot}${esc(p.name)}${p.is_default ? ' <span style="opacity:.5;font-weight:400">(default)</span>' : ''}${checkmark}</div>` +

      (meta.length ? `<div class="profile-opt-meta">${esc(meta.join(' \u00b7 '))}</div>` : '');

    opt.onclick = async () => {

      closeProfileDropdown();

      if (p.name === active) return;

      await switchToProfile(p.name);

    };

    dd.appendChild(opt);

  }

  // Divider + Manage link

  const div = document.createElement('div'); div.className = 'ws-divider'; dd.appendChild(div);

  const mgmt = document.createElement('div'); mgmt.className = 'profile-opt ws-manage';

  mgmt.innerHTML = '&#9881; 프로필 관리';

  mgmt.onclick = () => { closeProfileDropdown(); switchPanel('profiles'); };

  dd.appendChild(mgmt);

}

function toggleProfileDropdown() {

  const dd = $('profileDropdown');

  if (!dd) return;

  if (dd.classList.contains('open')) { closeProfileDropdown(); return; }

  closeWsDropdown(); // close workspace dropdown if open

  api('/api/profiles').then(data => {

    renderProfileDropdown(data);

    dd.classList.add('open');

  }).catch(e => { showToast('프로필을 불러오지 못했습니다'); });

}

function closeProfileDropdown() {

  const dd = $('profileDropdown');

  if (dd) dd.classList.remove('open');

}

document.addEventListener('click', e => {

  if (!e.target.closest('#profileChipWrap')) closeProfileDropdown();

});

async function switchToProfile(name) {

  if (S.busy) { showToast('에이전트가 실행 중일 때는 프로필을 전환할 수 없습니다'); return; }

  // Determine whether the current session has any messages.

  // A session with messages is "in progress" and belongs to the current profile —

  // we must not retag it.  We'll start a fresh session for the new profile instead.

  const sessionInProgress = S.session && S.messages && S.messages.length > 0;

  try {

    const data = await api('/api/profile/switch', { method: 'POST', body: JSON.stringify({ name }) });

    S.activeProfile = data.active || name;

    // ── Model ──────────────────────────────────────────────────────────────

    localStorage.removeItem('hermes-webui-model');

    _skillsData = null;

    await populateModelDropdown();

    if (data.default_model) {

      const sel = $('modelSelect');

      const resolved = _applyModelToDropdown(data.default_model, sel);

      const modelToUse = resolved || data.default_model;

      S._pendingProfileModel = modelToUse;

      // Only patch the in-memory session model if we're NOT about to replace the session

      if (S.session && !sessionInProgress) {

        S.session.model = modelToUse;

      }

    }

    // ── Workspace ──────────────────────────────────────────────────────────

    _workspaceList = null;

    await loadWorkspaceList();

    if (data.default_workspace) {

      // Always store the profile default for new sessions

      S._profileDefaultWorkspace = data.default_workspace;

      if (S.session && !sessionInProgress) {

        // Empty session (no messages yet) — safe to update it in place

        try {

          await api('/api/session/update', {
            method: 'POST', body: JSON.stringify({

              session_id: S.session.session_id,

              workspace: data.default_workspace,

              model: S.session.model,

            })
          });

          S.session.workspace = data.default_workspace;

        } catch (_) { }

      }

    }

    // ── Session ────────────────────────────────────────────────────────────

    _showAllProfiles = false;

    if (sessionInProgress) {

      // The current session has messages and belongs to the previous profile.

      // Start a new session for the new profile so nothing gets cross-tagged.

      await newSession(false);

      await renderSessionList();

      showToast('프로필 전환 완료: ' + name + ' — 새 대화를 시작했습니다');

    } else {

      // No messages yet — just refresh the list and topbar in place

      await renderSessionList();

      syncTopbar();

      showToast('프로필 전환 완료: ' + name);

    }

    // ── Sidebar panels ─────────────────────────────────────────────────────

    if (_currentPanel === 'skills') await loadSkills();

    if (_currentPanel === 'memory') await loadMemory();

    if (_currentPanel === 'tasks') await loadCrons();

    if (_currentPanel === 'profiles') await loadProfilesPanel();

    if (_currentPanel === 'workspaces') await loadWorkspacesPanel();

  } catch (e) { showToast('전환 실패: ' + e.message); }

}

function toggleProfileForm() {

  const form = $('profileCreateForm');

  if (!form) return;

  form.style.display = form.style.display === 'none' ? '' : 'none';

  if (form.style.display !== 'none') {

    $('profileFormName').value = '';

    $('profileFormClone').checked = false;

    const errEl = $('profileFormError');

    if (errEl) errEl.style.display = 'none';

    $('profileFormName').focus();

  }

}

async function submitProfileCreate() {

  const name = ($('profileFormName').value || '').trim().toLowerCase();

  const cloneConfig = $('profileFormClone').checked;

  const errEl = $('profileFormError');

  if (!name) { errEl.textContent = '이름이 필요합니다'; errEl.style.display = ''; return; }

  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(name)) { errEl.textContent = '소문자, 숫자, 하이픈, 밑줄만 사용할 수 있습니다'; errEl.style.display = ''; return; }

  try {

    await api('/api/profile/create', { method: 'POST', body: JSON.stringify({ name, clone_config: cloneConfig }) });

    toggleProfileForm();

    await loadProfilesPanel();

    showToast('프로필을 만들었습니다: ' + name);

  } catch (e) { errEl.textContent = e.message || '생성 실패'; errEl.style.display = ''; }

}

async function deleteProfile(name) {

  if (!confirm(`프로필 "${name}"를 삭제할까요? 이 프로필의 설정, 스킬, 기억, 세션이 모두 제거됩니다.`)) return;

  try {

    await api('/api/profile/delete', { method: 'POST', body: JSON.stringify({ name }) });

    await loadProfilesPanel();

    showToast('프로필을 삭제했습니다: ' + name);

  } catch (e) { showToast('삭제 실패: ' + e.message); }

}

// ── Memory panel ──

// @ts-check
/**
 * DAON Panel Module: skills_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

async function loadSkills() {

  if (_skillsData) { renderSkills(_skillsData); return; }

  const box = $('skillsList');

  try {

    const data = await api('/api/skills');

    _skillsData = data.skills || [];

    renderSkills(_skillsData);

  } catch (e) { box.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">Error: ${esc(e.message)}</div>`; }

}

function renderSkills(skills) {

  const query = ($('skillsSearch').value || '').toLowerCase();

  const filtered = query ? skills.filter(s =>

    (s.name || '').toLowerCase().includes(query) ||

    (s.description || '').toLowerCase().includes(query) ||

    (s.category || '').toLowerCase().includes(query)

  ) : skills;

  // Group by category

  const cats = {};

  for (const s of filtered) {

    const cat = s.category || '(general)';

    if (!cats[cat]) cats[cat] = [];

    cats[cat].push(s);

  }

  const box = $('skillsList');

  box.innerHTML = '';

  if (!filtered.length) { box.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">No skills match.</div>'; return; }

  for (const [cat, items] of Object.entries(cats).sort()) {

    const sec = document.createElement('div');

    sec.className = 'skills-category';

    sec.innerHTML = `<div class="skills-cat-header">&#128193; ${esc(cat)} <span style="opacity:.5">(${items.length})</span></div>`;

    for (const skill of items.sort((a, b) => a.name.localeCompare(b.name))) {

      const el = document.createElement('div');

      el.className = 'skill-item';

      const _skChecked = (State.harnessSelectedSkills || []).indexOf(skill.name) !== -1;
      el.innerHTML = `<input type="checkbox" class="skill-harness-cb" data-skill="${esc(skill.name)}" ${_skChecked ? 'checked' : ''} style="accent-color:var(--accent);cursor:pointer;flex-shrink:0;" title="하네스 실행 시 이 스킬 사용"><span class="skill-name">${esc(skill.label || skill.name)}</span><span class="skill-desc">${esc(skill.description || '')}</span>`;

      el.querySelector('.skill-harness-cb').addEventListener('change', function (e) {
        e.stopPropagation();
        if (typeof onHarnessSkillToggle === 'function') onHarnessSkillToggle(skill.name, this.checked);
      });
      el.onclick = (e) => { if (e.target.classList.contains('skill-harness-cb')) return; openSkill(skill.name, el); };

      sec.appendChild(el);

    }

    box.appendChild(sec);

  }

}

function filterSkills() {

  if (_skillsData) renderSkills(_skillsData);

}

async function openSkill(name, el) {

  // Highlight active skill

  document.querySelectorAll('.skill-item').forEach(e => e.classList.remove('active'));

  if (el) el.classList.add('active');

  try {

    const data = await api(`/api/skills/content?name=${encodeURIComponent(name)}`);

    // Show skill content in right panel preview

    $('previewPathText').textContent = name + '.md';

    $('previewBadge').textContent = 'skill';

    $('previewBadge').className = 'preview-badge md';

    showPreview('md');

    let html = renderMd(data.content || '(no content)');

    // Render linked files section if present

    const lf = data.linked_files || {};

    const categories = Object.entries(lf).filter(([, files]) => files && files.length > 0);

    if (categories.length) {

      html += '<div class="skill-linked-files"><div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Linked Files</div>';

      for (const [cat, files] of categories) {

        html += `<div class="skill-linked-section"><h4>${esc(cat)}</h4>`;

        for (const f of files) {

          html += `<a class="skill-linked-file" href="#" data-skill-name="${esc(name)}" data-skill-file="${esc(f)}">${esc(f)}</a>`;

        }

        html += '</div>';

      }

      html += '</div>';

    }

    $('previewMd').innerHTML = html;

    // Wire linked-file clicks via data attributes (avoids inline JS XSS with apostrophes)

    $('previewMd').querySelectorAll('.skill-linked-file').forEach(a => {

      a.addEventListener('click', e => { e.preventDefault(); openSkillFile(a.dataset.skillName, a.dataset.skillFile); });

    });

    $('previewArea').classList.add('visible');

    $('fileTree').style.display = 'none';

  } catch (e) { setStatus('Could not load skill: ' + e.message); }

}

async function openSkillFile(skillName, filePath) {

  try {

    const data = await api(`/api/skills/content?name=${encodeURIComponent(skillName)}&file=${encodeURIComponent(filePath)}`);

    $('previewPathText').textContent = skillName + ' / ' + filePath;

    $('previewBadge').textContent = filePath.split('.').pop() || 'file';

    $('previewBadge').className = 'preview-badge code';

    const ext = filePath.split('.').pop() || '';

    if (['md', 'markdown'].includes(ext)) {

      showPreview('md');

      $('previewMd').innerHTML = renderMd(data.content || '');

    } else {

      showPreview('code');

      $('previewCode').textContent = data.content || '';

      requestAnimationFrame(() => highlightCode());

    }

  } catch (e) { setStatus('Could not load file: ' + e.message); }

}

// ── Skill create/edit form ──

var _editingSkillName = null;

function toggleSkillForm(prefillName, prefillCategory, prefillContent) {

  const form = $('skillCreateForm');

  if (!form) return;

  const open = form.style.display !== 'none';

  if (open) { form.style.display = 'none'; _editingSkillName = null; return; }

  $('skillFormName').value = prefillName || '';

  $('skillFormCategory').value = prefillCategory || '';

  $('skillFormContent').value = prefillContent || '';

  $('skillFormError').style.display = 'none';

  _editingSkillName = prefillName || null;

  form.style.display = '';

  $('skillFormName').focus();

}

async function submitSkillSave() {

  const name = ($('skillFormName').value || '').trim().toLowerCase().replace(/\s+/g, '-');

  const category = ($('skillFormCategory').value || '').trim();

  const content = $('skillFormContent').value;

  const errEl = $('skillFormError');

  errEl.style.display = 'none';

  if (!name) { errEl.textContent = 'Skill name is required'; errEl.style.display = ''; return; }

  if (!content.trim()) { errEl.textContent = 'Content is required'; errEl.style.display = ''; return; }

  try {

    await api('/api/skills/save', { method: 'POST', body: JSON.stringify({ name, category: category || undefined, content }) });

    showToast(_editingSkillName ? 'Skill updated ✓' : 'Skill created ✓');

    _skillsData = null;

    toggleSkillForm();

    await loadSkills();

  } catch (e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = ''; }

}

// ── Memory inline edit ──

var _memoryData = null;

function toggleMemoryEdit() {

  const form = $('memoryEditForm');

  if (!form) return;

  const open = form.style.display !== 'none';

  if (open) { form.style.display = 'none'; return; }

  $('memEditSection').textContent = 'memory (notes)';

  $('memEditContent').value = _memoryData ? (_memoryData.memory || '') : '';

  $('memEditError').style.display = 'none';

  form.style.display = '';

}

function closeMemoryEdit() {

  const form = $('memoryEditForm');

  if (form) form.style.display = 'none';

}

async function submitMemorySave() {

  const content = $('memEditContent').value;

  const errEl = $('memEditError');

  errEl.style.display = 'none';

  try {

    await api('/api/memory/write', { method: 'POST', body: JSON.stringify({ section: 'memory', content }) });

    showToast('메모리 저장됨 ✓');

    closeMemoryEdit();

    await loadMemory(true);

  } catch (e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = ''; }

}

// ── Workspace management ──

var _workspaceList = [];  // cached from /api/workspaces

function getWorkspaceFriendlyName(path) {

  // Look up the friendly name from the workspace list cache, fallback to last path segment

  if (_workspaceList && _workspaceList.length) {

    const match = _workspaceList.find(w => w.path === path);

    if (match && match.name) return match.name;

  }

  return path.split('/').filter(Boolean).pop() || path;

}

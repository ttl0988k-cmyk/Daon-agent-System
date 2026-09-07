// @ts-check
/**
 * DAON Panel Module: cron_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

async function loadCrons() {

  const box = $('cronList');

  try {

    const data = await api('/api/crons');

    if (!data.jobs || !data.jobs.length) {

      box.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:12px">No scheduled jobs found.</div>';

      return;

    }

    box.innerHTML = '';

    for (const job of data.jobs) {

      const item = document.createElement('div');

      item.className = 'cron-item';

      item.id = 'cron-' + job.id;

      const statusClass = job.enabled === false ? 'disabled' : job.state === 'paused' ? 'paused' : job.last_status === 'error' ? 'error' : 'active';

      const statusLabel = job.enabled === false ? 'off' : job.state === 'paused' ? 'paused' : job.last_status === 'error' ? 'error' : 'active';

      const nextRun = job.next_run_at ? new Date(job.next_run_at).toLocaleString() : 'N/A';

      const lastRun = job.last_run_at ? new Date(job.last_run_at).toLocaleString() : 'never';

      item.innerHTML = `

        <div class="cron-header" onclick="toggleCron('${job.id}')">

          <span class="cron-name" title="${esc(job.name)}">${esc(job.name)}</span>

          <span class="cron-status ${statusClass}">${statusLabel}</span>

        </div>

        <div class="cron-body" id="cron-body-${job.id}">

          <div class="cron-schedule">&#128337; ${esc(job.schedule_display || job.schedule?.expression || '')} &nbsp;|&nbsp; Next: ${esc(nextRun)} &nbsp;|&nbsp; Last: ${esc(lastRun)}</div>

          <div class="cron-prompt">${esc((job.prompt || '').slice(0, 300))}${(job.prompt || '').length > 300 ? '…' : ''}</div>

          <div class="cron-actions">

            <button class="cron-btn run" onclick="cronRun('${job.id}')">&#9654; Run now</button>

            ${statusLabel === 'paused'

          ? `<button class="cron-btn" onclick="cronResume('${job.id}')">&#9654;&#9474; Resume</button>`

          : `<button class="cron-btn pause" onclick="cronPause('${job.id}')">&#9646;&#9646; Pause</button>`}

            <button class="cron-btn" onclick="cronEditOpen('${job.id}',${JSON.stringify(job).replace(/"/g, '&quot;')})">&#9998; Edit</button>

            <button class="cron-btn" style="border-color:rgba(201,168,76,.3);color:var(--accent)" onclick="cronDelete('${job.id}')">&#128465; Delete</button>

          </div>

          <!-- Inline edit form, hidden by default -->

          <div id="cron-edit-${job.id}" style="display:none;margin-top:8px;border-top:1px solid var(--border);padding-top:8px">

            <input id="cron-edit-name-${job.id}" placeholder="Job name" style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--border2);border-radius:6px;color:var(--text);padding:5px 8px;font-size:12px;outline:none;margin-bottom:5px;box-sizing:border-box">

            <input id="cron-edit-schedule-${job.id}" placeholder="Schedule" style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--border2);border-radius:6px;color:var(--text);padding:5px 8px;font-size:12px;outline:none;margin-bottom:5px;box-sizing:border-box">

            <textarea id="cron-edit-prompt-${job.id}" rows="3" placeholder="Prompt" style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--border2);border-radius:6px;color:var(--text);padding:5px 8px;font-size:12px;outline:none;resize:none;font-family:inherit;margin-bottom:5px;box-sizing:border-box"></textarea>

            <div id="cron-edit-err-${job.id}" style="font-size:11px;color:var(--accent);display:none;margin-bottom:5px"></div>

            <div style="display:flex;gap:6px">

              <button class="cron-btn run" style="flex:1" onclick="cronEditSave('${job.id}')">Save</button>

              <button class="cron-btn" style="flex:1" onclick="cronEditClose('${job.id}')">Cancel</button>

            </div>

          </div>

          <div id="cron-output-${job.id}">

            <div class="cron-last-header" style="display:flex;align-items:center;justify-content:space-between">

              <span>Last output</span>

              <button class="cron-btn" style="padding:1px 8px;font-size:10px" onclick="loadCronHistory('${job.id}',this)">All runs</button>

            </div>

            <div class="cron-last" id="cron-out-text-${job.id}" style="color:var(--muted);font-size:11px">Loading…</div>

            <div id="cron-history-${job.id}" style="display:none"></div>

          </div>

        </div>`;

      box.appendChild(item);

      // Eagerly load last output for visible items

      loadCronOutput(job.id);

    }

  } catch (e) { box.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">Error: ${esc(e.message)}</div>`; }

}

var _cronSelectedSkills = [];

var _cronSkillsCache = null;

function toggleCronForm() {

  const form = $('cronCreateForm');

  if (!form) return;

  const open = form.style.display !== 'none';

  form.style.display = open ? 'none' : '';

  if (!open) {

    $('cronFormVibe').value = '';

    $('cronFormName').value = '';

    $('cronFormSchedule').value = '';

    $('cronFormPrompt').value = '';

    $('cronFormDeliver').value = 'local';

    $('cronFormError').style.display = 'none';

    _cronSelectedSkills = [];

    _renderCronSkillTags();

    const search = $('cronFormSkillSearch');

    if (search) search.value = '';

    // Pre-fetch skills for the picker

    if (!_cronSkillsCache) {

      api('/api/skills').then(d => { _cronSkillsCache = d.skills || []; }).catch(() => { });

    }

    $('cronFormName').focus();

  }

}

function _renderCronSkillTags() {

  const wrap = $('cronFormSkillTags');

  if (!wrap) return;

  wrap.innerHTML = '';

  for (const name of _cronSelectedSkills) {

    const tag = document.createElement('span');

    tag.className = 'skill-tag';

    tag.dataset.skill = name;

    const rm = document.createElement('span');

    rm.className = 'remove-tag'; rm.textContent = '×';

    rm.onclick = () => { _cronSelectedSkills = _cronSelectedSkills.filter(s => s !== name); tag.remove(); };

    tag.appendChild(document.createTextNode(name));

    tag.appendChild(rm);

    wrap.appendChild(tag);

  }

}

function _guessCronNameFromPrompt(prompt) {

  const p = (prompt || '').replace(/해줘|해주세요|보내줘|보내줘요|알려줘|정리해줘/g, '').trim();

  return (p || '예약 작업').slice(0, 32);

}

function _cronPreviewHtml(parsed) {

  if (!parsed) return '';

  const deliverLabel = parsed.deliver === 'telegram' ? 'Telegram' : parsed.deliver === 'discord' ? 'Discord' : '로컬 저장';

  return `

    <div class="cron-preview-row"><span class="cron-preview-label">이름</span><span class="cron-preview-value">${esc(parsed.name || '예약 작업')}</span></div>

    <div class="cron-preview-row"><span class="cron-preview-label">스케줄</span><span class="cron-preview-value"><code>${esc(parsed.schedule || '')}</code></span></div>

    <div class="cron-preview-row"><span class="cron-preview-label">전달</span><span class="cron-preview-value">${esc(deliverLabel)}</span></div>

    <div class="cron-preview-row multi"><span class="cron-preview-label">프롬프트 초안</span><span class="cron-preview-value">${esc(parsed.prompt || '')}</span></div>

  `;

}

function _renderCronPreview(parsed) {

  const box = $('cronFormPreview');

  if (!box) return;

  if (!parsed) {

    box.style.display = 'none';

    box.innerHTML = '';

    return;

  }

  box.innerHTML = `<div class="cron-preview-title">미리보기</div>${_cronPreviewHtml(parsed)}`;

  box.style.display = '';

}

function validateCronDraft(draft) {

  const issues = [];

  if (!draft) return ['요청을 먼저 입력해 주세요'];

  if (!draft.schedule) issues.push('스케줄을 해석하지 못했습니다');

  if (!draft.prompt || draft.prompt.length < 6) issues.push('프롬프트가 너무 짧습니다');

  if (draft.prompt && !/[가-힣A-Za-z0-9]/.test(draft.prompt)) issues.push('프롬프트에 실제 작업 내용이 필요합니다');

  return issues;

}

function _parseNaturalCronRequest(text) {

  const raw = (text || '').trim();

  if (!raw) return null;

  let schedule = '';

  let prompt = raw;

  let deliver = 'local';

  if (/텔레그램/.test(raw)) deliver = 'telegram';

  else if (/디스코드|discord/i.test(raw)) deliver = 'discord';

  const dayMap = { '일': 0, '월': 1, '화': 2, '수': 3, '목': 4, '금': 5, '토': 6 };

  const timeMatch = raw.match(/(오전|아침|새벽|점심|오후|저녁|밤)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?/);

  let hour = 9, minute = 0;

  if (timeMatch) {

    hour = parseInt(timeMatch[2], 10);

    minute = timeMatch[3] ? parseInt(timeMatch[3], 10) : 0;

    const part = timeMatch[1] || '';

    if (/오후|저녁|밤/.test(part) && hour < 12) hour += 12;

    if (/새벽/.test(part) && hour === 12) hour = 0;

    if (/점심/.test(part) && hour < 12) hour = Math.max(12, hour);

  } else {

    if (/아침|오전/.test(raw)) hour = 8;

    else if (/점심/.test(raw)) hour = 12;

    else if (/오후/.test(raw)) hour = 15;

    else if (/저녁|밤/.test(raw)) hour = 20;

  }

  const weekdayMatch = raw.match(/매주\s*([일월화수목금토])요일?/);

  if (/매시간/.test(raw)) schedule = '0 * * * *';

  else if (/매일|매일마다/.test(raw)) schedule = `${minute} ${hour} * * *`;

  else if (/평일/.test(raw)) schedule = `${minute} ${hour} * * 1-5`;

  else if (/주말/.test(raw)) schedule = `${minute} ${hour} * * 0,6`;

  else if (weekdayMatch) schedule = `${minute} ${hour} * * ${dayMap[weekdayMatch[1]]}`;

  else if (/매주/.test(raw)) schedule = `${minute} ${hour} * * 1`;

  else if (/매달/.test(raw)) schedule = `${minute} ${hour} 1 * *`;

  else if (/(2시간마다|두 시간마다)/.test(raw)) schedule = '0 */2 * * *';

  else if (/(3시간마다|세 시간마다)/.test(raw)) schedule = '0 */3 * * *';

  else if (/(4시간마다|네 시간마다)/.test(raw)) schedule = '0 */4 * * *';

  else if (/(1시간마다|한 시간마다)/.test(raw)) schedule = '0 * * * *';

  else schedule = `${minute} ${hour} * * *`;

  prompt = prompt

    .replace(/매일마다?|평일마다?|주말마다?|매주\s*[일월화수목금토]요일?|매주|매달/g, '')

    .replace(/(오전|아침|새벽|점심|오후|저녁|밤)?\s*\d{1,2}시(?:\s*\d{1,2}분)?/g, '')

    .replace(/(1시간마다|한 시간마다|2시간마다|두 시간마다|3시간마다|세 시간마다|4시간마다|네 시간마다|매시간)/g, '')

    .replace(/텔레그램으로|텔레그램에|디스코드로|디스코드에/gi, '')

    .replace(/^마다\s*/, '')

    .replace(/\s+/g, ' ')

    .trim();

  if (!prompt) prompt = raw;

  return {

    schedule,

    prompt,

    deliver,

    name: _guessCronNameFromPrompt(prompt),

  };

}

function fillCronExample(kind) {

  const vibe = $('cronFormVibe');

  if (!vibe) return;

  if (kind === 'daily') vibe.value = '매일 아침 8시에 AI 뉴스 요약해서 텔레그램으로 보내줘';

  else if (kind === 'weekly') vibe.value = '매주 월요일 오전 9시에 이번 주 해야 할 일 정리해서 로컬에 저장해줘';

  autofillCronFromVibe();

}

function autofillCronFromVibe() {

  const vibe = ($('cronFormVibe').value || '').trim();

  const errEl = $('cronFormError');

  errEl.style.display = 'none';

  const parsed = _parseNaturalCronRequest(vibe);

  _renderCronPreview(parsed);

  if (!parsed) {

    errEl.textContent = '자유롭게 요청을 먼저 입력해 주세요';

    errEl.style.display = '';

    return;

  }

  const issues = validateCronDraft(parsed);

  if (issues.length) {

    errEl.textContent = '점검: ' + issues.join(' / ');

    errEl.style.display = '';

  }

  if (!$('cronFormName').value.trim()) $('cronFormName').value = parsed.name;

  $('cronFormSchedule').value = parsed.schedule;

  $('cronFormPrompt').value = parsed.prompt;

  $('cronFormDeliver').value = parsed.deliver;

  showToast('요청을 바탕으로 예약 작업 초안을 채웠습니다');

}

// Skill search input handler

(function () {

  const setup = () => {

    const search = $('cronFormSkillSearch');

    const dropdown = $('cronFormSkillDropdown');

    if (!search || !dropdown) return;

    search.oninput = () => {

      const q = search.value.trim().toLowerCase();

      if (!q || !_cronSkillsCache) { dropdown.style.display = 'none'; return; }

      const matches = _cronSkillsCache.filter(s =>

        !_cronSelectedSkills.includes(s.name) &&

        (s.name.toLowerCase().includes(q) || (s.category || '').toLowerCase().includes(q))

      ).slice(0, 8);

      if (!matches.length) { dropdown.style.display = 'none'; return; }

      dropdown.innerHTML = '';

      for (const s of matches) {

        const opt = document.createElement('div');

        opt.className = 'skill-opt';

        opt.textContent = s.name + (s.category ? ' (' + s.category + ')' : '');

        opt.onclick = () => {

          _cronSelectedSkills.push(s.name);

          _renderCronSkillTags();

          search.value = '';

          dropdown.style.display = 'none';

        };

        dropdown.appendChild(opt);

      }

      dropdown.style.display = '';

    };

    search.onblur = () => setTimeout(() => { dropdown.style.display = 'none'; }, 150);

  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);

  else setTimeout(setup, 0);

})();

async function submitCronCreate() {

  let name = $('cronFormName').value.trim();

  let schedule = $('cronFormSchedule').value.trim();

  let prompt = $('cronFormPrompt').value.trim();

  let deliver = $('cronFormDeliver').value;

  const vibe = ($('cronFormVibe').value || '').trim();

  const errEl = $('cronFormError');

  errEl.style.display = 'none';

  if (vibe && (!schedule || !prompt)) {

    const parsed = _parseNaturalCronRequest(vibe);

    if (parsed) {

      _renderCronPreview(parsed);

      if (!name) name = parsed.name;

      if (!schedule) schedule = parsed.schedule;

      if (!prompt) prompt = parsed.prompt;

      if (deliver === 'local') deliver = parsed.deliver;

      $('cronFormName').value = name;

      $('cronFormSchedule').value = schedule;

      $('cronFormPrompt').value = prompt;

      $('cronFormDeliver').value = deliver;

    }

  }

  const issues = validateCronDraft({ name, schedule, prompt, deliver });

  if (!schedule) { errEl.textContent = '스케줄이 필요합니다 (예: "0 9 * * *" 또는 "every 1h")'; errEl.style.display = ''; return; }

  if (!prompt) { errEl.textContent = '프롬프트가 필요합니다'; errEl.style.display = ''; return; }

  if (issues.length) {

    errEl.textContent = '점검: ' + issues.join(' / ');

    errEl.style.display = '';

    return;

  }

  try {

    const body = { schedule, prompt, deliver };

    if (name) body.name = name;

    if (_cronSelectedSkills.length) body.skills = _cronSelectedSkills;

    await api('/api/crons/create', { method: 'POST', body: JSON.stringify(body) });

    toggleCronForm();

    showToast('예약 작업을 만들었습니다 ✓');

    await loadCrons();

  } catch (e) {

    errEl.textContent = '오류: ' + e.message; errEl.style.display = '';

  }

}

function _cronOutputSnippet(content) {

  // Extract the response body from a cron output .md file

  const lines = content.split('\n');

  const responseIdx = lines.findIndex(l => l.startsWith('## Response') || l.startsWith('# Response'));

  const body = (responseIdx >= 0 ? lines.slice(responseIdx + 1) : lines).join('\n').trim();

  return body.slice(0, 600) || '(empty)';

}

async function loadCronOutput(jobId) {

  try {

    const data = await api(`/api/crons/output?job_id=${encodeURIComponent(jobId)}&limit=1`);

    const el = $('cron-out-text-' + jobId);

    if (!el) return;

    if (!data.outputs || !data.outputs.length) { el.textContent = '(no runs yet)'; return; }

    const out = data.outputs[0];

    const ts = out.filename.replace('.md', '').replace(/_/g, ' ');

    el.textContent = ts + '\n\n' + _cronOutputSnippet(out.content);

  } catch (e) { /* ignore */ }

}

async function loadCronHistory(jobId, btn) {

  const histEl = $('cron-history-' + jobId);

  if (!histEl) return;

  // Toggle: if already open, close it

  if (histEl.style.display !== 'none') {

    histEl.style.display = 'none';

    if (btn) btn.textContent = 'All runs';

    return;

  }

  if (btn) btn.textContent = 'Loading…';

  try {

    const data = await api(`/api/crons/output?job_id=${encodeURIComponent(jobId)}&limit=20`);

    if (!data.outputs || !data.outputs.length) {

      histEl.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:4px 0">(no runs yet)</div>';

    } else {

      histEl.innerHTML = data.outputs.map((out, i) => {

        const ts = out.filename.replace('.md', '').replace(/_/g, ' ');

        const snippet = _cronOutputSnippet(out.content);

        const id = `cron-hist-run-${jobId}-${i}`;

        return `<div style="border-top:1px solid var(--border);padding:6px 0">

          <div style="display:flex;align-items:center;justify-content:space-between;cursor:pointer" onclick="document.getElementById('${id}').style.display=document.getElementById('${id}').style.display==='none'?'':'none'">

            <span style="font-size:11px;font-weight:600;color:var(--muted)">${esc(ts)}</span>

            <span style="font-size:10px;color:var(--muted);opacity:.6">▸</span>

          </div>

          <div id="${id}" style="display:none;font-size:11px;color:var(--muted);white-space:pre-wrap;line-height:1.5;margin-top:4px;max-height:200px;overflow-y:auto">${esc(snippet)}</div>

        </div>`;

      }).join('');

    }

    histEl.style.display = '';

    if (btn) btn.textContent = 'Hide runs';

  } catch (e) {

    if (btn) btn.textContent = 'All runs';

  }

}

function toggleCron(id) {

  const body = $('cron-body-' + id);

  if (body) body.classList.toggle('open');

}

async function cronRun(id) {

  try {

    await api('/api/crons/run', { method: 'POST', body: JSON.stringify({ job_id: id }) });

    showToast('작업 실행됨 ✓');

    setTimeout(() => loadCronOutput(id), 5000);

  } catch (e) { showToast('실행 실패: ' + e.message, 4000); }

}

async function cronPause(id) {

  try {

    await api('/api/crons/pause', { method: 'POST', body: JSON.stringify({ job_id: id }) });

    showToast('작업 일시중지됨');

    await loadCrons();

  } catch (e) { showToast('일시중지 실패: ' + e.message, 4000); }

}

async function cronResume(id) {

  try {

    await api('/api/crons/resume', { method: 'POST', body: JSON.stringify({ job_id: id }) });

    showToast('작업 재개됨 ✓');

    await loadCrons();

  } catch (e) { showToast('재개 실패: ' + e.message, 4000); }

}

function cronEditOpen(id, job) {

  const form = $('cron-edit-' + id);

  if (!form) return;

  $('cron-edit-name-' + id).value = job.name || '';

  $('cron-edit-schedule-' + id).value = job.schedule_display || (job.schedule && job.schedule.expression) || job.schedule || '';

  $('cron-edit-prompt-' + id).value = job.prompt || '';

  const errEl = $('cron-edit-err-' + id);

  if (errEl) errEl.style.display = 'none';

  form.style.display = '';

}

function cronEditClose(id) {

  const form = $('cron-edit-' + id);

  if (form) form.style.display = 'none';

}

async function cronEditSave(id) {

  const name = $('cron-edit-name-' + id).value.trim();

  const schedule = $('cron-edit-schedule-' + id).value.trim();

  const prompt = $('cron-edit-prompt-' + id).value.trim();

  const errEl = $('cron-edit-err-' + id);

  if (!schedule) { errEl.textContent = 'Schedule is required'; errEl.style.display = ''; return; }

  if (!prompt) { errEl.textContent = 'Prompt is required'; errEl.style.display = ''; return; }

  try {

    const updates = { job_id: id, schedule, prompt };

    if (name) updates.name = name;

    await api('/api/crons/update', { method: 'POST', body: JSON.stringify(updates) });

    showToast('작업 업데이트됨 ✓');

    await loadCrons();

  } catch (e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = ''; }

}

async function cronDelete(id) {

  if (!confirm('Delete this cron job? This cannot be undone.')) return;

  try {

    await api('/api/crons/delete', { method: 'POST', body: JSON.stringify({ job_id: id }) });

    showToast('작업 삭제됨');

    await loadCrons();

  } catch (e) { showToast('Delete failed: ' + e.message, 4000); }

}

// ── Cron Notification & Badge Polling ──

var _cronPollSince = Date.now() / 1000;
var _cronPollTimer = null;
var _cronUnreadCount = 0;

function startCronPolling() {
  if (_cronPollTimer) return;
  _cronPollTimer = setInterval(async () => {
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      const data = await api(`/api/crons/recent?since=${_cronPollSince}`);
      if (data.completions && data.completions.length > 0) {
        for (const c of data.completions) {
          const icon = c.status === 'error' ? '❌' : '✅';
          showToast(`${icon} 예약 작업 "${c.name}" ${c.status === 'error' ? '실패' : '완료'}`, 4000);
          _cronPollSince = Math.max(_cronPollSince, c.completed_at);
        }
        _cronUnreadCount += data.completions.length;
        updateCronBadge();
      }
    } catch (e) {}
  }, 30000);
}

function updateCronBadge() {
  if (typeof document === 'undefined') return;
  const tab = document.querySelector('.nav-tab[data-panel="tasks"]');
  if (!tab) return;
  let badge = tab.querySelector('.cron-badge');
  if (_cronUnreadCount > 0) {
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'cron-badge';
      tab.style.position = 'relative';
      tab.appendChild(badge);
    }
    badge.textContent = _cronUnreadCount > 9 ? '9+' : _cronUnreadCount;
    badge.style.display = '';
  } else if (badge) {
    badge.style.display = 'none';
  }
}

function clearCronBadge() {
  _cronUnreadCount = 0;
  updateCronBadge();
}

if (typeof window !== 'undefined') {
  startCronPolling();
}

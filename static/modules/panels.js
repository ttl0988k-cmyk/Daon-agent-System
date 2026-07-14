var _currentPanel = 'chat';



var _skillsData = null; // cached skills list







async function switchPanel(name) {
  _currentPanel = name;
  // Update nav tabs (original sidebar tabs)
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.panel === name));
  // Update panel views
  document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
  const panelEl = $('panel' + name.charAt(0).toUpperCase() + name.slice(1));
  if (panelEl) panelEl.classList.add('active');

  if (name === 'tasks') await loadCrons();
  if (name === 'skills') { await loadSkills(); setTimeout(loadSkillsHubPanel, 150); }
  if (name === 'memory') await loadMemory();
  if (name === 'workspaces') await loadWorkspacesPanel();
  if (name === 'profiles') await loadProfilesPanel();
  if (name === 'todos') loadTodos();
  if (name === 'artifacts' && typeof renderArtifactListSidebar === 'function') renderArtifactListSidebar();
  if (name === 'setup') { if (typeof renderSetupPackHistorySidebar === 'function') renderSetupPackHistorySidebar(); setTimeout(loadSetupPanel, 100); }
  if (name === 'checks' && typeof renderPreflightResultSidebar === 'function') renderPreflightResultSidebar('note');
  if (name === 'dashboard') { setTimeout(loadDashboard, 50); setTimeout(loadConfigScore, 100); }
  if (name === 'git') { cleanupGitPanel(); setTimeout(loadGitPanel, 50); }
  if (name === 'browser') { loadBrowserPanel(); }
  if (name === 'docs') { loadDocsPanel(); }
  if (name === 'integrations') { loadIntegrationsPanel(); }
  if (name === 'mcp') { loadMcpPanel(); }
}




// ── Cron panel ──



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



    showToast('Job triggered ✓');



    setTimeout(() => loadCronOutput(id), 5000);



  } catch (e) { showToast('Run failed: ' + e.message, 4000); }



}







async function cronPause(id) {



  try {



    await api('/api/crons/pause', { method: 'POST', body: JSON.stringify({ job_id: id }) });



    showToast('Job paused');



    await loadCrons();



  } catch (e) { showToast('Pause failed: ' + e.message, 4000); }



}







async function cronResume(id) {



  try {



    await api('/api/crons/resume', { method: 'POST', body: JSON.stringify({ job_id: id }) });



    showToast('Job resumed ✓');



    await loadCrons();



  } catch (e) { showToast('Resume failed: ' + e.message, 4000); }



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



    showToast('Job updated ✓');



    await loadCrons();



  } catch (e) { errEl.textContent = 'Error: ' + e.message; errEl.style.display = ''; }



}







async function cronDelete(id) {



  if (!confirm('Delete this cron job? This cannot be undone.')) return;



  try {



    await api('/api/crons/delete', { method: 'POST', body: JSON.stringify({ job_id: id }) });



    showToast('Job deleted');



    await loadCrons();



  } catch (e) { showToast('Delete failed: ' + e.message, 4000); }



}







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



    showToast('Conversation cleared');



  } catch (e) { setStatus('Clear failed: ' + e.message); }



}







// ── Skills panel ──



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



      el.innerHTML = `<span class="skill-name">${esc(skill.name)}</span><span class="skill-desc">${esc(skill.description || '')}</span>`;



      el.onclick = () => openSkill(skill.name, el);



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



    showToast('Memory saved ✓');



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



  } catch (e) { panel.innerHTML = `<div style="color:var(--accent);font-size:12px">오류: ${esc(e.message)}</div>`; }



}







// Drag and drop



const wrap = $('composerWrap'); let dragCounter = 0;



document.addEventListener('dragover', e => e.preventDefault());



document.addEventListener('dragenter', e => { e.preventDefault(); if (e.dataTransfer.types.includes('Files')) { dragCounter++; wrap.classList.add('drag-over'); } });



document.addEventListener('dragleave', e => { dragCounter--; if (dragCounter <= 0) { dragCounter = 0; wrap.classList.remove('drag-over'); } });



document.addEventListener('drop', e => { e.preventDefault(); dragCounter = 0; wrap.classList.remove('drag-over'); const files = Array.from(e.dataTransfer.files); if (files.length) { addFiles(files); $('msg').focus(); } });







// ── Settings panel ───────────────────────────────────────────────────────────







var _settingsDirty = false;



var _settingsThemeOnOpen = null; // track theme at open time for discard revert







function toggleSettings() {



  const overlay = $('settingsOverlay');



  if (!overlay) return;



  if (overlay.style.display === 'none') {



    _settingsDirty = false;



    _settingsThemeOnOpen = document.documentElement.dataset.theme || 'dark';



    overlay.style.display = '';



    loadSettingsPanel();



  } else {



    _closeSettingsPanel();



  }



}







// Close with unsaved-changes check. If dirty, show a confirm dialog.



function _closeSettingsPanel() {



  if (!_settingsDirty) {



    // Nothing changed -- revert any live preview and close



    _revertSettingsPreview();



    $('settingsOverlay').style.display = 'none';



    return;



  }



  // Dirty -- show inline confirm bar



  _showSettingsUnsavedBar();



}







// Revert live DOM/localStorage to what they were when the panel opened



function _revertSettingsPreview() {



  if (_settingsThemeOnOpen) {



    document.documentElement.dataset.theme = _settingsThemeOnOpen;



    localStorage.setItem('hermes-theme', _settingsThemeOnOpen);



  }



}







// Show the "Unsaved changes" bar inside the settings panel



function _showSettingsUnsavedBar() {



  let bar = $('settingsUnsavedBar');



  if (bar) { bar.style.display = ''; return; }



  // Create it



  bar = document.createElement('div');



  bar.id = 'settingsUnsavedBar';



  bar.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;background:rgba(233,69,96,.12);border:1px solid rgba(233,69,96,.3);border-radius:8px;padding:10px 14px;margin:0 0 12px;font-size:13px;';



  bar.innerHTML = '<span style="color:var(--text)">저장되지 않은 변경 사항이 있습니다.</span>'



    + '<span style="display:flex;gap:8px">'



    + '<button onclick="_discardSettings()" style="padding:5px 12px;border-radius:6px;border:1px solid var(--border2);background:rgba(255,255,255,.06);color:var(--muted);cursor:pointer;font-size:12px;font-weight:600">버리기</button>'



    + '<button onclick="saveSettings(true)" style="padding:5px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px;font-weight:600">저장</button>'



    + '</span>';



  const body = document.querySelector('.settings-body') || document.querySelector('.settings-panel');



  if (body) body.prepend(bar);



}







function _discardSettings() {



  _revertSettingsPreview();



  _settingsDirty = false;



  $('settingsOverlay').style.display = 'none';



}







// Mark settings as dirty whenever anything changes



function _markSettingsDirty() {



  _settingsDirty = true;



}







async function loadSettingsPanel() {



  try {



    const settings = await api('/api/settings');



    const botNameInput = $('settingsBotName');



    if (botNameInput) { botNameInput.value = settings.bot_name || 'Hermes'; botNameInput.addEventListener('input', _markSettingsDirty, { once: false }); }



    // Populate model dropdown from /api/models



    const modelSel = $('settingsModel');



    if (modelSel) {



      modelSel.innerHTML = '';



      try {



        const models = await api('/api/models');



        for (const g of (models.groups || [])) {



          const og = document.createElement('optgroup');



          og.label = g.provider;



          for (const m of g.models) {



            const opt = document.createElement('option');



            opt.value = m.id; opt.textContent = m.label;



            og.appendChild(opt);



          }



          modelSel.appendChild(og);



        }



      } catch (e) { }



      modelSel.value = settings.default_model || '';



      modelSel.addEventListener('change', _markSettingsDirty, { once: false });



    }



    // Populate workspace dropdown from /api/workspaces



    const wsSel = $('settingsWorkspace');



    if (wsSel) {



      wsSel.innerHTML = '';



      try {



        const wsData = await api('/api/workspaces');



        for (const w of (wsData.workspaces || [])) {



          const opt = document.createElement('option');



          opt.value = w.path; opt.textContent = w.name || w.path;



          wsSel.appendChild(opt);



        }



      } catch (e) { }



      wsSel.value = settings.default_workspace || '';



      wsSel.addEventListener('change', _markSettingsDirty, { once: false });



    }



    // Send key preference



    const sendKeySel = $('settingsSendKey');



    if (sendKeySel) { sendKeySel.value = settings.send_key || 'enter'; sendKeySel.addEventListener('change', _markSettingsDirty, { once: false }); }



    // Theme preference



    const themeSel = $('settingsTheme');



    if (themeSel) { themeSel.value = settings.theme || 'dark'; themeSel.addEventListener('change', _markSettingsDirty, { once: false }); }



    const showUsageCb = $('settingsShowTokenUsage');



    if (showUsageCb) { showUsageCb.checked = !!settings.show_token_usage; showUsageCb.addEventListener('change', _markSettingsDirty, { once: false }); }



    const showCliCb = $('settingsShowCliSessions');



    if (showCliCb) { showCliCb.checked = !!settings.show_cli_sessions; showCliCb.addEventListener('change', _markSettingsDirty, { once: false }); }



    const syncCb = $('settingsSyncInsights');



    if (syncCb) { syncCb.checked = !!settings.sync_to_insights; syncCb.addEventListener('change', _markSettingsDirty, { once: false }); }



    // Password field: always blank (we don't send hash back)



    const pwField = $('settingsPassword');



    if (pwField) { pwField.value = ''; pwField.addEventListener('input', _markSettingsDirty, { once: false }); }



    // Show auth buttons only when auth is active



    try {



      const authStatus = await api('/api/auth/status');



      const active = authStatus.auth_enabled;



      const signOutBtn = $('btnSignOut');



      if (signOutBtn) signOutBtn.style.display = active ? '' : 'none';



      const disableBtn = $('btnDisableAuth');



      if (disableBtn) disableBtn.style.display = active ? '' : 'none';



    } catch (e) { }



  } catch (e) {



    showToast('설정을 불러오지 못했습니다: ' + e.message);



  }



}

// ── Provider Management ──

async function loadProviderManagement() {
  const list = $('settingsProvidersList');
  if (!list) return;

  try {
    const data = await api('/api/providers');
    const presets = data.presets || {};
    const providers = data.providers || {};

    const presetSel = $('settingsProviderPreset');
    if (presetSel) {
      const currentVal = presetSel.value;
      presetSel.innerHTML = '<option value="">-- Manual Entry --</option>';
      for (const [key, cfg] of Object.entries(presets)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = cfg.label + ' (' + cfg.base_url + ')';
        presetSel.appendChild(opt);
      }
      presetSel.value = currentVal;
    }

    const providerKeys = Object.keys(providers);
    if (providerKeys.length === 0) {
      list.innerHTML = '<div style="color:var(--muted);font-size:11px;text-align:center;padding:8px;">No custom providers added yet. Click "+ Add Provider" to add one.</div>';
    } else {
      list.innerHTML = providerKeys.map(function (name) {
        const cfg = providers[name];
        const models = cfg.models || [];
        const modelList = models.map(function (m) { return m.id || m.model; }).join(', ') || 'No models';
        return '<div class="settings-provider-card">' +
          '<div class="provider-info">' +
          '<span class="provider-name">' + esc(name) + '</span>' +
          '<span class="provider-models" title="' + esc(modelList) + '">' + esc(modelList) + '</span>' +
          '</div>' +
          '<div class="provider-actions">' +
          '<button class="provider-btn" onclick="editProvider(\'' + esc(name) + '\')" title="Edit">✎</button>' +
          '<button class="provider-btn danger" onclick="deleteProvider(\'' + esc(name) + '\')" title="Delete">✕</button>' +
          '</div>' +
          '</div>';
      }).join('');
    }
  } catch (e) {
    list.innerHTML = '<div style="color:var(--danger);font-size:11px;text-align:center;padding:8px;">Failed to load providers: ' + esc(e.message) + '</div>';
  }
}

function showAddProviderForm() {
  const form = $('settingsAddProviderForm');
  const title = $('settingsProviderFormTitle');
  if (form) form.style.display = '';
  if (title) title.textContent = 'Add Provider';
  const nameEl = $('settingsProviderName');
  const keyEl = $('settingsProviderKey');
  const urlEl = $('settingsProviderUrl');
  const presetEl = $('settingsProviderPreset');
  const resultEl = $('settingsProviderFetchResult');
  if (nameEl) { nameEl.value = ''; nameEl.readOnly = false; nameEl.style.opacity = '1'; }
  if (keyEl) keyEl.value = '';
  if (urlEl) urlEl.value = '';
  if (presetEl) presetEl.value = '';
  if (resultEl) { resultEl.style.display = 'none'; resultEl.innerHTML = ''; }
  const saveBtn = $('settingsProviderSaveBtn');
  if (saveBtn) { saveBtn.textContent = '💾 Save Provider'; saveBtn.onclick = saveProvider; }
  _editingProviderName = null;
}

function hideAddProviderForm() {
  const form = $('settingsAddProviderForm');
  if (form) form.style.display = 'none';
  _editingProviderName = null;
}

let _editingProviderName = null;

async function editProvider(name) {
  try {
    const data = await api('/api/providers');
    const cfg = (data.providers || {})[name];
    if (!cfg) { showToast('Provider not found'); return; }

    showAddProviderForm();
    const title = $('settingsProviderFormTitle');
    const nameEl = $('settingsProviderName');
    const keyEl = $('settingsProviderKey');
    const urlEl = $('settingsProviderUrl');
    const presetEl = $('settingsProviderPreset');
    const saveBtn = $('settingsProviderSaveBtn');

    if (title) title.textContent = 'Edit Provider: ' + name;
    if (nameEl) { nameEl.value = name; nameEl.readOnly = true; nameEl.style.opacity = '0.7'; }
    if (keyEl) { keyEl.value = ''; keyEl.placeholder = 'Leave empty to keep existing key'; }
    if (urlEl) urlEl.value = cfg.base_url || '';
    if (presetEl) presetEl.value = '';
    if (saveBtn) { saveBtn.textContent = '💾 Update Provider'; saveBtn.onclick = saveProvider; }
    _editingProviderName = name;
  } catch (e) {
    showToast('Failed to load provider: ' + e.message);
  }
}

async function saveProvider() {
  const name = ($('settingsProviderName') || {}).value.trim();
  const key = ($('settingsProviderKey') || {}).value.trim();
  const url = ($('settingsProviderUrl') || {}).value.trim();

  if (!name) { showToast('Provider name is required'); return; }
  if (!key) { showToast('API key is required'); return; }

  const isEdit = _editingProviderName !== null;

  try {
    const saveBtn = $('settingsProviderSaveBtn');
    if (saveBtn) { saveBtn.textContent = '⏳ Saving...'; saveBtn.disabled = true; }

    const result = await api('/api/providers/add', {
      method: 'POST',
      body: { name: name, api_key: key, base_url: url }
    });

    if (result.success) {
      const models = result.models || [];
      showToast(isEdit ? 'Provider updated: ' + name : 'Provider added: ' + name + (models.length ? ' (' + models.length + ' models)' : ''));
      await loadProviderManagement();
      await refreshAllModelSelects();
    } else {
      showToast('Failed: ' + (result.error || 'Unknown error'));
    }
  } catch (e) {
    showToast('Save failed: ' + e.message);
  } finally {
    const saveBtn = $('settingsProviderSaveBtn');
    if (saveBtn) { saveBtn.textContent = _editingProviderName ? '💾 Update Provider' : '💾 Save Provider'; saveBtn.disabled = false; }
  }
}

async function deleteProvider(name) {
  if (!confirm('Delete provider "' + name + '" and all its models? This cannot be undone.')) return;

  try {
    const result = await api('/api/providers/delete', {
      method: 'POST',
      body: { name: name }
    });

    if (result.success) {
      showToast('Provider deleted: ' + name);
      await loadProviderManagement();
      await refreshAllModelSelects();
    } else {
      showToast('Delete failed: ' + (result.error || 'Unknown error'));
    }
  } catch (e) {
    showToast('Delete failed: ' + e.message);
  }
}

function onProviderPresetChange() {
  const presetKey = ($('settingsProviderPreset') || {}).value;
  if (!presetKey) return;

  const sel = $('settingsProviderPreset');
  const presetText = sel.options[sel.selectedIndex].textContent;
  const match = presetText.match(/\((.+)\)$/);
  const baseUrl = match ? match[1] : '';

  const urlEl = $('settingsProviderUrl');
  const nameEl = $('settingsProviderName');

  if (urlEl && baseUrl) urlEl.value = baseUrl;
  if (nameEl && !nameEl.value) {
    nameEl.value = presetKey;
  }
}

async function fetchProviderModels() {
  const name = ($('settingsProviderName') || {}).value.trim();
  const key = ($('settingsProviderKey') || {}).value.trim();
  const url = ($('settingsProviderUrl') || {}).value.trim();
  const resultEl = $('settingsProviderFetchResult');
  const fetchBtn = $('settingsProviderFetchBtn');

  if (!url) { showToast('Base URL is required for auto-detection'); return; }
  if (!key) { showToast('API key is required for auto-detection'); return; }

  if (fetchBtn) { fetchBtn.textContent = '⏳ Detecting...'; fetchBtn.disabled = true; }
  if (resultEl) { resultEl.style.display = ''; resultEl.innerHTML = '<div style="color:var(--muted);">Fetching models from ' + esc(url) + '/models ...</div>'; }

  try {
    const data = await api('/api/providers/fetch-models', {
      method: 'POST',
      body: { name: name || 'temp', api_key: key, base_url: url }
    });

    if (data.success && data.models && data.models.length > 0) {
      var modelHtml = '<div style="color:var(--success);font-weight:600;margin-bottom:4px;">✅ Found ' + data.models.length + ' models:</div>' +
        '<div style="display:flex;flex-wrap:wrap;gap:4px;">' +
        data.models.map(function (m) { return '<span style="background:var(--bg2);padding:2px 6px;border-radius:3px;font-size:10px;">' + esc(m.id || m) + '</span>'; }).join('') +
        '</div>' +
        '<div style="margin-top:8px;font-size:10px;color:var(--muted);">These models will be saved when you click "Save Provider".</div>';
      if (resultEl) resultEl.innerHTML = modelHtml;
    } else {
      if (resultEl) resultEl.innerHTML = '<div style="color:var(--warning);">⚠️ No models returned from provider. You can still save and manually specify models later.</div>';
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = '<div style="color:var(--danger);">❌ Auto-detection failed: ' + esc(e.message) + '. You can still save the provider and add models manually.</div>';
  } finally {
    if (fetchBtn) { fetchBtn.textContent = '🔍 Auto-Detect Models'; fetchBtn.disabled = false; }
  }
}

async function refreshAllModelSelects() {
  try {
    const models = await api('/api/models');
    const settingsSel = $('settingsDefaultModel');
    if (settingsSel) {
      const currentVal = settingsSel.value;
      settingsSel.innerHTML = '';
      for (var gi = 0; gi < (models.groups || []).length; gi++) {
        var g = models.groups[gi];
        const og = document.createElement('optgroup');
        og.label = g.provider;
        for (var mi = 0; mi < g.models.length; mi++) {
          var m = g.models[mi];
          const opt = document.createElement('option');
          opt.value = m.id;
          opt.textContent = m.label;
          og.appendChild(opt);
        }
        settingsSel.appendChild(og);
      }
      settingsSel.value = currentVal || '';
    }
    if (typeof populateModelSelect === 'function') {
      populateModelSelect();
    }
  } catch (e) {
    // Silently fail
  }
}







async function saveSettings(andClose) {



  const botName = (($('settingsBotName') || {}).value || '').trim();



  const model = ($('settingsModel') || {}).value;



  const workspace = ($('settingsWorkspace') || {}).value;



  const sendKey = ($('settingsSendKey') || {}).value;



  const showTokenUsage = !!($('settingsShowTokenUsage') || {}).checked;



  const showCliSessions = !!($('settingsShowCliSessions') || {}).checked;



  const pw = ($('settingsPassword') || {}).value;



  const theme = ($('settingsTheme') || {}).value || 'dark';



  const body = {};



  if (botName) body.bot_name = botName;



  if (model) body.default_model = model;



  if (workspace) body.default_workspace = workspace;



  if (sendKey) body.send_key = sendKey;



  body.theme = theme;



  body.show_token_usage = showTokenUsage;



  body.show_cli_sessions = showCliSessions;



  body.sync_to_insights = !!($('settingsSyncInsights') || {}).checked;



  // Password: only act if the field has content; blank = leave auth unchanged



  if (pw && pw.trim()) {



    try {



      await api('/api/settings', { method: 'POST', body: JSON.stringify({ ...body, _set_password: pw.trim() }) });



      window._sendKey = sendKey || 'enter';



      window._showTokenUsage = showTokenUsage;



      showToast('설정을 저장했습니다 (비밀번호가 설정되어 다시 로그인해야 합니다)');



      _settingsDirty = false; _settingsThemeOnOpen = theme;



      const bar = $('settingsUnsavedBar'); if (bar) bar.style.display = 'none';



      $('settingsOverlay').style.display = 'none';



      return;



    } catch (e) { showToast('저장 실패: ' + e.message); return; }



  }



  try {



    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });



    window._sendKey = sendKey || 'enter';



    window._showTokenUsage = showTokenUsage;



    window._showCliSessions = showCliSessions;



    _settingsDirty = false; _settingsThemeOnOpen = theme;



    const bar = $('settingsUnsavedBar'); if (bar) bar.style.display = 'none';



    renderMessages();



    if (typeof renderSessionList === 'function') renderSessionList();



    showToast('설정을 저장했습니다');



    $('settingsOverlay').style.display = 'none';



  } catch (e) {



    showToast('저장 실패: ' + e.message);



  }



}







async function signOut() {



  try {



    await api('/api/auth/logout', { method: 'POST', body: '{}' });



    window.location.href = '/login';



  } catch (e) {



    showToast('로그아웃 실패: ' + e.message);



  }



}







async function disableAuth() {



  if (!confirm('비밀번호 보호를 끌까요? 누구나 이 인스턴스에 접근할 수 있게 됩니다.')) return;



  try {



    await api('/api/settings', { method: 'POST', body: JSON.stringify({ _clear_password: true }) });



    showToast('인증을 비활성화했습니다 — 비밀번호 보호가 제거되었습니다');



    // Hide both auth buttons since auth is now off



    const disableBtn = $('btnDisableAuth');



    if (disableBtn) disableBtn.style.display = 'none';



    const signOutBtn = $('btnSignOut');



    if (signOutBtn) signOutBtn.style.display = 'none';



  } catch (e) {



    showToast('인증 비활성화 실패: ' + e.message);



  }



}







// Close settings on overlay click (not panel click) -- with unsaved-changes check



document.addEventListener('click', e => {



  const overlay = $('settingsOverlay');



  if (overlay && e.target === overlay) _closeSettingsPanel();



});







// ── Cron completion alerts ────────────────────────────────────────────────────







var _cronPollSince = Date.now() / 1000;  // track from page load



var _cronPollTimer = null;



var _cronUnreadCount = 0;







function startCronPolling() {



  if (_cronPollTimer) return;



  _cronPollTimer = setInterval(async () => {



    if (document.hidden) return;  // don't poll when tab is in background



    try {



      const data = await api(`/api/crons/recent?since=${_cronPollSince}`);



      if (data.completions && data.completions.length > 0) {



        for (const c of data.completions) {



          const icon = c.status === 'error' ? '\u274c' : '\u2705';



          showToast(`${icon} 예약 작업 "${c.name}" ${c.status === 'error' ? '실패' : '완료'}`, 4000);



          _cronPollSince = Math.max(_cronPollSince, c.completed_at);



        }



        _cronUnreadCount += data.completions.length;



        updateCronBadge();



      }



    } catch (e) { }



  }, 30000);



}







function updateCronBadge() {



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







// Clear cron badge when Tasks tab is opened



const _origSwitchPanel = switchPanel;



switchPanel = async function (name) {



  if (name === 'tasks') { _cronUnreadCount = 0; updateCronBadge(); }



  return _origSwitchPanel(name);



};







// Start polling on page load



startCronPolling();







// ── Background agent error tracking ──────────────────────────────────────────







const _backgroundErrors = [];  // {session_id, title, message, ts}







function trackBackgroundError(sessionId, title, message) {



  // Only track if user is NOT currently viewing this session



  if (S.session && S.session.session_id === sessionId) return;



  _backgroundErrors.push({ session_id: sessionId, title: title || 'Untitled', message, ts: Date.now() });



  showErrorBanner();



}







function showErrorBanner() {



  let banner = $('bgErrorBanner');



  if (!banner) {



    banner = document.createElement('div');



    banner.id = 'bgErrorBanner';



    banner.className = 'bg-error-banner';



    const msgs = document.querySelector('.messages');



    if (msgs) msgs.parentNode.insertBefore(banner, msgs);



    else document.body.appendChild(banner);



  }



  const latest = _backgroundErrors[0];  // FIFO: show oldest (first) error



  if (!latest) { banner.style.display = 'none'; return; }



  const count = _backgroundErrors.length;



  banner.innerHTML = `<span>\u26a0 ${count > 1 ? count + '개의 세션에서' : '"' + esc(latest.title) + '" 세션에서'} 오류가 발생했습니다</span><div style="display:flex;gap:6px;flex-shrink:0"><button class="reconnect-btn" onclick="navigateToErrorSession()">보기</button><button class="reconnect-btn" onclick="dismissErrorBanner()">닫기</button></div>`;



  banner.style.display = '';



}







function navigateToErrorSession() {



  const latest = _backgroundErrors.shift();  // FIFO: show oldest error first



  if (latest) {



    loadSession(latest.session_id); renderSessionList();



  }



  if (_backgroundErrors.length === 0) dismissErrorBanner();



  else showErrorBanner();



}







function dismissErrorBanner() {



  _backgroundErrors.length = 0;



  const banner = $('bgErrorBanner');



  if (banner) banner.style.display = 'none';



}







// --- Demo to Skill ---

var _demoSessionId = null;
var _demoPollTimer = null;
var _demoGeneratedSkill = null;

/* -- helpers: which DOM ids to use (modal OR inline panel) -- */
function _d(id) {
  // try modal ID first, fall back to inline panel ID
  var el = document.getElementById('demoModal' + id.charAt(0).toUpperCase() + id.slice(1));
  if (!el) el = document.getElementById('demo' + id.charAt(0).toUpperCase() + id.slice(1));
  return el;
}

function _demoModalOpen() {
  var m = document.getElementById('demoSkillModal');
  if (m) m.style.display = 'flex';
  resetDemoUI();
}

function _demoModalClose() {
  var m = document.getElementById('demoSkillModal');
  if (m) m.style.display = 'none';
  if (_demoPollTimer) { clearInterval(_demoPollTimer); _demoPollTimer = null; }
}

document.addEventListener('DOMContentLoaded', function () {
  var closeBtn = document.getElementById('closeDemoSkillBtn');
  if (closeBtn) closeBtn.addEventListener('click', function () {
    if (!_demoSessionId) { _demoModalClose(); return; }
    if (confirm('녹화 세션이 활성 상태입니다. 정말 닫으시겠습니까?')) { cancelDemoRecording(); _demoModalClose(); }
  });
  // click outside modal to close
  var overlay = document.getElementById('demoSkillModal');
  if (overlay) overlay.addEventListener('click', function (e) {
    if (e.target === overlay) {
      if (!_demoSessionId) { _demoModalClose(); }
      else if (confirm('녹화 세션이 활성 상태입니다. 정말 닫으시겠습니까?')) { cancelDemoRecording(); _demoModalClose(); }
    }
  });
});

function toggleDemoSkill() {
  var body = document.getElementById('demoSkillBody');
  var header = document.getElementById('demoSkillHeader');
  var icon = document.getElementById('demoToggleIcon');
  if (!body) return;
  if (body.style.display === 'none' || !body.style.display) {
    body.style.display = '';
    if (header) header.classList.add('open');
    if (icon) icon.textContent = '\u25BC';
  } else {
    body.style.display = 'none';
    if (header) header.classList.remove('open');
    if (icon) icon.textContent = '\u25B6';
  }
}

function openDemoSkill() {
  _demoModalOpen();
}

function resetDemoUI() {
  var ids = ['Idle', 'Recording', 'Analyzing', 'Result', 'TextMode'];
  for (var i = 0; i < ids.length; i++) {
    var mel = document.getElementById('demoModal' + ids[i]);
    var iel = document.getElementById('demo' + ids[i]);
    if (mel) mel.style.display = (ids[i] === 'Idle') ? '' : 'none';
    if (iel) iel.style.display = (ids[i] === 'Idle') ? '' : 'none';
  }
  // hide text error
  var me = document.getElementById('demoModalTextError');
  var ie = document.getElementById('demoTextError');
  if (me) me.style.display = 'none';
  if (ie) ie.style.display = 'none';
}

async function startDemoRecording() {
  try {
    var data = await api('/api/demo/start', {
      method: 'POST',
      body: JSON.stringify({ source: 'cdp', name: 'demo-' + Date.now() })
    });
    if (!data.ok) { showToast('\uB179\uD654 \uC2DC\uC791 \uC2E4\uD328: ' + (data.message || '')); return; }
    _demoSessionId = data.session_id;
    _demoModalOpen();
    var idle = document.getElementById('demoModalIdle');
    var rec = document.getElementById('demoModalRecording');
    if (idle) idle.style.display = 'none';
    if (rec) rec.style.display = '';
    showToast('\uB179\uD654\uAC00 \uC2DC\uC791\uB418\uC5C8\uC2B5\uB2C8\uB2E4. \uBE0C\uB77C\uC6B0\uC800\uC5D0\uC11C \uB3D9\uC791\uC744 \uC218\uD589\uD558\uC138\uC694.');
    _demoPollTimer = setInterval(pollDemoEvents, 2000);
    pollDemoEvents();
  } catch (e) {
    showToast('\uB179\uD654 \uC2DC\uC791 \uC624\uB958: ' + e.message);
  }
}

async function pollDemoEvents() {
  if (!_demoSessionId) return;
  try {
    var data = await api('/api/demo/events?session_id=' + encodeURIComponent(_demoSessionId));
    if (data.event_count !== undefined) {
      var el = document.getElementById('demoModalEventCount');
      if (el) el.textContent = data.event_count + ' \uC774\uBCA4\uD2B8';
    }
  } catch (e) { /* ignore */ }
}

async function stopDemoRecording() {
  if (!_demoSessionId) return;
  if (_demoPollTimer) { clearInterval(_demoPollTimer); _demoPollTimer = null; }
  var rec = document.getElementById('demoModalRecording');
  var ana = document.getElementById('demoModalAnalyzing');
  if (rec) rec.style.display = 'none';
  if (ana) ana.style.display = '';
  try {
    var data = await api('/api/demo/stop', {
      method: 'POST',
      body: JSON.stringify({ session_id: _demoSessionId })
    });
    if (!data.ok) { showToast('\uBD84\uC11D \uC2DC\uC791 \uC2E4\uD328'); resetDemoUI(); return; }
    showToast('LLM \uBD84\uC11D\uC774 \uC2DC\uC791\uB418\uC5C8\uC2B5\uB2C8\uB2E4...');
    _demoPollTimer = setInterval(pollDemoStatus, 3000);
  } catch (e) {
    showToast('\uC911\uC9C0 \uC624\uB958: ' + e.message);
    resetDemoUI();
  }
}

async function pollDemoStatus() {
  if (!_demoSessionId) return;
  try {
    var data = await api('/api/demo/status?session_id=' + encodeURIComponent(_demoSessionId));
    var session = data.session;
    if (!session) return;
    if (session.status === 'completed') {
      if (_demoPollTimer) { clearInterval(_demoPollTimer); _demoPollTimer = null; }
      var ana = document.getElementById('demoModalAnalyzing');
      var res = document.getElementById('demoModalResult');
      var content = document.getElementById('demoModalResultContent');
      if (ana) ana.style.display = 'none';
      if (res) res.style.display = '';
      _demoGeneratedSkill = session.skill_name || '';
      var skillPath = session.skill_path || '';
      if (content) content.innerHTML = '<div class="demo-hint" style="color:var(--success);">\uC2A4\uD0AC \uC0DD\uC131 \uC644\uB8CC!</div>' +
        '<div style="font-size:12px;padding:4px 0;"><strong>\uC2A4\uD0AC:</strong> ' + esc(_demoGeneratedSkill) + '</div>' +
        (skillPath ? '<div style="font-size:10px;color:var(--muted);">\uACBD\uB85C: ' + esc(skillPath) + '</div>' : '');
      showToast('\uC2A4\uD0AC\uC774 \uC0DD\uC131\uB418\uC5C8\uC2B5\uB2C8\uB2E4! \uC2B9\uC778 \uB610\uB294 \uAC70\uC808\uD574\uC8FC\uC138\uC694.');
      _skillsData = null;
      setTimeout(function () { loadSkills(); }, 500);
    } else if (session.status === 'error') {
      if (_demoPollTimer) { clearInterval(_demoPollTimer); _demoPollTimer = null; }
      resetDemoUI();
      showToast('\uBD84\uC11D \uC2E4\uD328: ' + (session.error || '\uC54C \uC218 \uC5C6\uB294 \uC624\uB958'));
    }
  } catch (e) { /* ignore */ }
}

async function cancelDemoRecording() {
  if (_demoPollTimer) { clearInterval(_demoPollTimer); _demoPollTimer = null; }
  if (_demoSessionId) {
    try {
      await api('/api/demo/cancel', {
        method: 'POST',
        body: JSON.stringify({ session_id: _demoSessionId })
      });
    } catch (e) { /* ignore */ }
    _demoSessionId = null;
  }
  _demoGeneratedSkill = null;
  _demoModalClose();
  resetDemoUI();
  showToast('\uB179\uD654\uAC00 \uCDE8\uC18C\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
}

function toggleDemoTextMode() {
  var textMode = document.getElementById('demoModalTextMode');
  var idle = document.getElementById('demoModalIdle');
  if (!textMode) return;
  if (textMode.style.display === 'none' || !textMode.style.display) {
    textMode.style.display = '';
    if (idle) idle.style.display = 'none';
    var sn = document.getElementById('demoModalSkillName');
    var ds = document.getElementById('demoModalDescription');
    var er = document.getElementById('demoModalTextError');
    if (sn) sn.value = '';
    if (ds) ds.value = '';
    if (er) er.style.display = 'none';
  } else {
    textMode.style.display = 'none';
    if (idle) idle.style.display = '';
  }
}

async function submitDemoTextWorkflow() {
  var descEl = document.getElementById('demoModalDescription');
  var nameEl = document.getElementById('demoModalSkillName');
  var errEl = document.getElementById('demoModalTextError');
  var description = (descEl ? descEl.value : '').trim();
  var skillName = (nameEl ? nameEl.value : '').trim();
  if (!description) {
    if (errEl) { errEl.textContent = '\uC6CC\uD06C\uD50C\uB85C\uC6B0 \uC124\uBBAE\uC744 \uC785\uB825\uD574\uC8FC\uC138\uC694.'; errEl.style.display = ''; }
    return;
  }
  if (errEl) errEl.style.display = 'none';
  var textMode = document.getElementById('demoModalTextMode');
  var ana = document.getElementById('demoModalAnalyzing');
  if (textMode) textMode.style.display = 'none';
  if (ana) ana.style.display = '';
  try {
    var body = { description: description };
    if (skillName) body.skill_name = skillName;
    var data = await api('/api/demo/text-workflow', {
      method: 'POST',
      body: JSON.stringify(body)
    });
    if (!data.ok) { showToast('\uD14D\uC2A4\uD2B8 \uBD84\uC11D \uC2E4\uD328: ' + (data.message || '')); resetDemoUI(); return; }
    showToast('\uD14D\uC2A4\uD2B8 \uC6CC\uD06C\uD50C\uB85C\uC6B0 \uBD84\uC11D\uC774 \uC2DC\uC791\uB418\uC5C8\uC2B5\uB2C8\uB2E4...');
    setTimeout(async function () {
      if (ana) ana.style.display = 'none';
      _demoModalClose();
      _skillsData = null;
      await loadSkills();
      showToast('\uC2A4\uD0AC \uC0DD\uC131 \uC644\uB8CC! \uC2A4\uD0AC \uBAA9\uB85D\uC744 \uD655\uC778\uD558\uC138\uC694.');
    }, 8000);
  } catch (e) {
    showToast('\uD14D\uC2A4\uD2B8 \uBD84\uC11D \uC624\uB958: ' + e.message);
    resetDemoUI();
  }
}

async function approveDemoSkill() {
  if (!_demoGeneratedSkill) { showToast('\uC2B9\uC778\uD560 \uC2A4\uD0AC\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.'); return; }
  try {
    var data = await api('/api/demo/skill/approve', {
      method: 'POST',
      body: JSON.stringify({ skill_name: _demoGeneratedSkill })
    });
    if (data.ok) {
      showToast('\uC2A4\uD0AC\uC774 \uC2B9\uC778\uB418\uC5C8\uC2B5\uB2C8\uB2E4 \u2713');
      _demoGeneratedSkill = null;
      _demoModalClose();
      resetDemoUI();
      _skillsData = null;
      await loadSkills();
    } else {
      showToast('\uC2B9\uC778 \uC2E4\uD328: ' + (data.message || ''));
    }
  } catch (e) {
    showToast('\uC2B9\uC778 \uC624\uB958: ' + e.message);
  }
}

async function rejectDemoSkill() {
  if (!_demoGeneratedSkill) { showToast('\uAC70\uC808\uD560 \uC2A4\uD0AC\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.'); return; }
  try {
    var data = await api('/api/demo/skill/reject', {
      method: 'POST',
      body: JSON.stringify({ skill_name: _demoGeneratedSkill })
    });
    if (data.ok) {
      showToast('\uC2A4\uD0AC\uC774 \uAC70\uC808\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
      _demoGeneratedSkill = null;
      _demoModalClose();
      resetDemoUI();
      _skillsData = null;
      await loadSkills();
    } else {
      showToast('\uAC70\uC808 \uC2E4\uD328: ' + (data.message || ''));
    }
  } catch (e) {
    showToast('\uAC70\uC808 \uC624\uB958: ' + e.message);
  }
}

// Event wiring




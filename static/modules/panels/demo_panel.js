// @ts-check
/**
 * DAON Panel Module: demo_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */
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

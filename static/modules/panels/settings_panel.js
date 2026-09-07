// @ts-check
/**
 * DAON Panel Module: settings_panel.js
 * Extracted from monolithic panels.js (Phase 4: Frontend Modularization)
 */

var _settingsDirty = false;
var _settingsThemeOnOpen = null; // track theme at open time for discard revert
var _settingsAbortController = null;

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
  if (_settingsAbortController) {
    _settingsAbortController.abort();
    _settingsAbortController = null;
  }
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
  if (_settingsAbortController) {
    _settingsAbortController.abort();
  }
  _settingsAbortController = new AbortController();
  const signal = _settingsAbortController.signal;

  try {
    const settings = await api('/api/settings');

    const botNameInput = $('settingsBotName');
    if (botNameInput) {
      botNameInput.value = settings.bot_name || 'Hermes';
      botNameInput.addEventListener('input', _markSettingsDirty, { signal });
    }

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
      modelSel.addEventListener('change', _markSettingsDirty, { signal });
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
      wsSel.addEventListener('change', _markSettingsDirty, { signal });
    }

    // Send key preference
    const sendKeySel = $('settingsSendKey');
    if (sendKeySel) {
      sendKeySel.value = settings.send_key || 'enter';
      sendKeySel.addEventListener('change', _markSettingsDirty, { signal });
    }

    // Theme preference
    const themeSel = $('settingsTheme');
    if (themeSel) {
      themeSel.value = settings.theme || 'dark';
      themeSel.addEventListener('change', _markSettingsDirty, { signal });
    }

    const showUsageCb = $('settingsShowTokenUsage');
    if (showUsageCb) {
      showUsageCb.checked = !!settings.show_token_usage;
      showUsageCb.addEventListener('change', _markSettingsDirty, { signal });
    }

    const showCliCb = $('settingsShowCliSessions');
    if (showCliCb) {
      showCliCb.checked = !!settings.show_cli_sessions;
      showCliCb.addEventListener('change', _markSettingsDirty, { signal });
    }

    const syncCb = $('settingsSyncInsights');
    if (syncCb) {
      syncCb.checked = !!settings.sync_to_insights;
      syncCb.addEventListener('change', _markSettingsDirty, { signal });
    }

    // Password field: always blank (we don't send hash back)
    const pwField = $('settingsPassword');
    if (pwField) {
      pwField.value = '';
      pwField.addEventListener('input', _markSettingsDirty, { signal });
    }

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

    // 이벤트 위임은 1회만 바인딩 (list 요소 자체는 innerHTML 재렌더링에도 유지됨).
    // 기존 인라인 onclick="editProvider('이름')" 방식은 이름에 특수문자(' 등)가 들어가면
    // HTML 디코딩 후 JS 문자열 리터럴이 깨져 클릭이 아예 무효화되는 문제가 있었다.
    if (!list._providerDelegationBound) {
      list._providerDelegationBound = true;
      list.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('button[data-provider-action]') : null;
        if (!btn) return;
        var pname = btn.getAttribute('data-provider-name') || '';
        if (btn.getAttribute('data-provider-action') === 'edit') editProvider(pname);
        else if (btn.getAttribute('data-provider-action') === 'delete') deleteProvider(pname);
        else if (btn.getAttribute('data-provider-action') === 'refresh') refreshProviderModels(pname, btn);
      });
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
          '<button class="provider-btn" data-provider-action="refresh" data-provider-name="' + esc(name) + '" title="저장된 키로 /models 재호출해 모델 목록 갱신">🔄</button>' +
          '<button class="provider-btn" data-provider-action="edit" data-provider-name="' + esc(name) + '" title="편집">✎</button>' +
          '<button class="provider-btn danger" data-provider-action="delete" data-provider-name="' + esc(name) + '" title="삭제">✕</button>' +
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
  if (title) title.textContent = '제공자 추가';
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
  if (saveBtn) { saveBtn.textContent = '💾 제공자 저장'; saveBtn.onclick = saveProvider; }
  _editingProviderName = null;
  _selectedProviderModels = null;
}

function hideAddProviderForm() {
  const form = $('settingsAddProviderForm');
  if (form) form.style.display = 'none';
  _editingProviderName = null;
  _selectedProviderModels = null;
}

let _editingProviderName = null;
let _selectedProviderModels = null; // 자동감지 후 선택된 모델 목록

async function editProvider(name) {
  try {
    const data = await api('/api/providers');
    const cfg = (data.providers || {})[name];
    if (!cfg) { showToast('제공자를 찾을 수 없습니다'); return; }

    showAddProviderForm();
    const title = $('settingsProviderFormTitle');
    const nameEl = $('settingsProviderName');
    const keyEl = $('settingsProviderKey');
    const urlEl = $('settingsProviderUrl');
    const presetEl = $('settingsProviderPreset');
    const saveBtn = $('settingsProviderSaveBtn');

    if (title) title.textContent = '제공자 편집: ' + name;
    if (nameEl) { nameEl.value = name; nameEl.readOnly = true; nameEl.style.opacity = '0.7'; }
    if (keyEl) { keyEl.value = ''; keyEl.placeholder = '기존 키 유지하려면 빈 칸으로 두세요'; }
    if (urlEl) urlEl.value = cfg.base_url || '';
    if (presetEl) presetEl.value = '';
    if (saveBtn) { saveBtn.textContent = '💾 제공자 업데이트'; saveBtn.onclick = saveProvider; }
    _editingProviderName = name;
  } catch (e) {
    showToast('제공자 정보 로드 실패: ' + e.message);
  }
}

// 프로바이더 추가/삭제 진행 중 재진입 잠금 (더블클릭·연타로 인한 중복 API 호출 방지)
let _providerActionBusy = false;

// 네이티브 confirm() 대체용 인페이지 확인 모달.
// Electron에서 네이티브 대화상자를 닫은 직후 첫 클릭이 윈도우 포커스 복원으로
// 소모되어 "두 번 세번 눌러야 동작"하는 현상의 주원인이었다.
function _confirmModalAsync(message) {
  return new Promise(function (resolve) {
    var overlay = $('providerConfirmModal');
    if (!overlay) { resolve(window.confirm(message)); return; }
    var msgEl = $('providerConfirmMessage');
    var okBtn = $('providerConfirmOk');
    var cancelBtn = $('providerConfirmCancel');
    if (msgEl) msgEl.textContent = message;
    overlay.style.display = 'flex';

    var settled = false;
    function finish(val) {
      if (settled) return;
      settled = true;
      document.removeEventListener('keydown', escHandler, true);
      overlay.style.display = 'none';
      resolve(val);
    }
    function escHandler(e) {
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    }
    if (okBtn) okBtn.onclick = function () { finish(true); };
    if (cancelBtn) cancelBtn.onclick = function () { finish(false); };
    document.addEventListener('keydown', escHandler, true);
    setTimeout(function () { if (okBtn) okBtn.focus(); }, 0);
  });
}

async function saveProvider() {
  if (_providerActionBusy) return; // 저장 진행 중 재클릭 무시
  const name = ($('settingsProviderName') || {}).value.trim();
  const key = ($('settingsProviderKey') || {}).value.trim();
  const url = ($('settingsProviderUrl') || {}).value.trim();

  if (!name) { showToast('제공자 이름이 필요합니다'); return; }
  if (!key) { showToast('API 키가 필요합니다'); return; }

  const isEdit = _editingProviderName !== null;
  _providerActionBusy = true;

  try {
    const saveBtn = $('settingsProviderSaveBtn');
    if (saveBtn) { saveBtn.textContent = '⏳ 저장 중...'; saveBtn.disabled = true; }

    const bodyPayload = { name: name, api_key: key, base_url: url };
    // 체크박스에서 선택된 모델만 수집.
    // Bugfix: 체크박스가 그려져 있다면(자동 감지/수동 추가 완료 상태) 체크 결과를 그대로 존중한다.
    // 이전에는 체크 0개일 때 _selectedProviderModels(전체 목록)로 폴백되어
    // "전체 해제" 후에도 300개 모델이 전부 저장되는 문제가 있었다.
    var checkedModels = _collectCheckedProviderModels();
    if (checkedModels !== null) {
      bodyPayload.models = checkedModels;
    } else if (_selectedProviderModels && _selectedProviderModels.length > 0) {
      bodyPayload.models = _selectedProviderModels;
    }
    // 백엔드는 models 미지정 시 /models 자동 감지(urllib 타임아웃 15초)를 수행하므로
    // 프론트 기본 타임아웃(15초)과 경합하지 않도록 30초로 연장.
    const result = await api('/api/providers/add', {
      method: 'POST',
      body: bodyPayload,
      timeout: 30000
    });

    if (result.success) {
      const models = result.models || [];
      if (result.merged_into) {
        // 같은 base_url의 기존 프로바이더에 모델이 병합된 경우 — 중복 항목을 만들지 않는다.
        showToast('기존 제공자 "' + result.merged_into + '"에 모델 ' + (result.added_count || 0) + '개 추가됨 (중복 등록 방지)');
      } else {
        showToast(isEdit ? '제공자 업데이트됨: ' + name : '제공자 추가됨: ' + name + (models.length ? ' (' + models.length + '개 모델)' : ''));
      }
      // 직렬 await 대신 병렬 갱신 — 목록 갱신이 늦어 "안 된 것처럼" 보이는 현상 완화
      await Promise.all([loadProviderManagement(), refreshAllModelSelects()]);
    } else {
      showToast('실패: ' + (result.error || '알 수 없는 오류'));
    }
  } catch (e) {
    showToast('저장 실패: ' + e.message);
  } finally {
    _providerActionBusy = false;
    const saveBtn = $('settingsProviderSaveBtn');
    if (saveBtn) { saveBtn.textContent = _editingProviderName ? '💾 제공자 업데이트' : '💾 제공자 저장'; saveBtn.disabled = false; }
  }
}

async function deleteProvider(name) {
  if (_providerActionBusy) return; // 삭제 진행 중 재클릭 무시 (더블클릭 시 두 번 DELETE 방지)
  _providerActionBusy = true;

  try {
    // 네이티브 confirm() 대신 인페이지 모달 사용 — 닫은 직후 첫 클릭이 포커스 복원으로 소모되는 문제 제거
    var ok = await _confirmModalAsync('제공자 "' + name + '"와 그 모든 모델을 삭제하시겠습니까? 되돌릴 수 없습니다.');
    if (!ok) return;

    const result = await api('/api/providers/delete', {
      method: 'POST',
      body: { name: name },
      timeout: 30000
    });

    if (result.success) {
      showToast('제공자 삭제됨: ' + name);
      await Promise.all([loadProviderManagement(), refreshAllModelSelects()]);
    } else {
      showToast('삭제 실패: ' + (result.error || '알 수 없는 오류'));
    }
  } catch (e) {
    showToast('삭제 실패: ' + e.message);
  } finally {
    _providerActionBusy = false;
  }
}

async function refreshProviderModels(name, btn) {
  if (_providerActionBusy) return;
  _providerActionBusy = true;
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }

  try {
    const data = await api('/api/providers/refresh-models', {
      method: 'POST',
      body: { name: name },
      timeout: 60000
    });

    if (data.success) {
      showToast('✅ ' + name + ': 모델 ' + (data.count || (data.models || []).length) + '개 갱신됨');
      await Promise.all([loadProviderManagement(), refreshAllModelSelects()]);
    } else {
      showToast('모델 갱신 실패: ' + (data.error || '알 수 없는 오류'));
    }
  } catch (e) {
    showToast('모델 갱신 실패: ' + e.message);
  } finally {
    _providerActionBusy = false;
    if (btn) { btn.disabled = false; btn.textContent = '🔄'; }
  }
}

function onProviderPresetChange() {
  const presetKey = ($('settingsProviderPreset') || {}).value;
  if (!presetKey) return;

  const sel = $('settingsProviderPreset');
  const presetText = sel.options[sel.selectedIndex].textContent;
  const match = presetText.match(/\(([^()]+)\)$/);
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

  if (!url) { showToast('모델 자동 감지에는 기본 URL이 필요합니다'); return; }
  if (!key) { showToast('모델 자동 감지에는 API 키가 필요합니다'); return; }

  if (fetchBtn) { fetchBtn.textContent = '⏳ 감지 중...'; fetchBtn.disabled = true; }
  if (resultEl) { resultEl.style.display = ''; resultEl.innerHTML = '<div style="color:var(--muted);">' + esc(url) + '/models 에서 모델 목록 가져오는 중...</div>'; }

  try {
    const data = await api('/api/providers/fetch-models', {
      method: 'POST',
      body: { name: name || 'temp', api_key: key, base_url: url }
    });

    if (data.success && data.models && data.models.length > 0) {
      _selectedProviderModels = data.models.map(function (m) { return { id: m.id || m, label: m.label || m.id || m, type: m.type || 'chat' }; });
      _renderProviderModelList('✅ ' + data.models.length + '개 모델 발견 — 저장할 모델을 선택하세요:', 'var(--success)');
    } else {
      _selectedProviderModels = null;
      if (resultEl) resultEl.innerHTML = '<div style="color:var(--warning);">⚠️ 제공자로부터 모델이 반환되지 않았습니다. 저장 후 수동으로 모델을 지정할 수 있습니다.</div>';
    }
  } catch (e) {
    if (resultEl) resultEl.innerHTML = '<div style="color:var(--danger);">❌ 모델 자동 감지 실패: ' + esc(e.message) + ' — 위 "모델 직접 입력"으로 수동 추가할 수 있습니다.</div>';
  } finally {
    if (fetchBtn) { fetchBtn.textContent = '🔍 모델 자동 감지'; fetchBtn.disabled = false; }
  }
}

function _collectCheckedProviderModels() {
  var resultEl = $('settingsProviderFetchResult');
  if (!resultEl || !_selectedProviderModels) return null;
  var cbs = resultEl.querySelectorAll('.provider-model-cb');
  if (!cbs.length) return null;
  var selected = [];
  cbs.forEach(function (cb) {
    if (cb.checked) {
      var idx = parseInt(cb.getAttribute('data-idx'), 10);
      if (idx >= 0 && idx < _selectedProviderModels.length) {
        selected.push(_selectedProviderModels[idx]);
      }
    }
  });
  return selected;
}

function _providerModelSelectAll(checked) {
  var resultEl = $('settingsProviderFetchResult');
  if (!resultEl) return;
  resultEl.querySelectorAll('.provider-model-cb').forEach(function (cb) { cb.checked = checked; });
  _updateProviderModelCount();
}

function _updateProviderModelCount() {
  var countEl = $('providerModelCount');
  var resultEl = $('settingsProviderFetchResult');
  if (!countEl || !resultEl) return;
  var cbs = resultEl.querySelectorAll('.provider-model-cb');
  var checked = 0;
  cbs.forEach(function (cb) { if (cb.checked) checked++; });
  countEl.textContent = checked + '/' + cbs.length + '개 선택됨';
}

// ── 모델 목록 렌더링 (자동 감지 + 수동 추가 공용) ──────────────────────────
// 전체 선택/해제 버튼은 스크롤 영역 "위"에 고정 배치해서 항상 보이게 한다.
// checkedIds: null이면 기본값(tts/audio 계열 제외 전부 체크), 객체면 해당 id만 체크.
function _renderProviderModelList(headerText, headerColor, checkedIds) {
  var resultEl = $('settingsProviderFetchResult');
  if (!resultEl || !_selectedProviderModels) return;
  var _typeColors = { chat: 'var(--muted)', image: '#e879f9', video: '#38bdf8' };
  var html = '<div style="font-weight:600;margin-bottom:4px;color:' + (headerColor || 'var(--success)') + ';">' + headerText + '</div>' +
    '<div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;">' +
    '<button onclick="_providerModelSelectAll(true)" style="font-size:10px;padding:1px 6px;cursor:pointer;">전체 선택</button>' +
    '<button onclick="_providerModelSelectAll(false)" style="font-size:10px;padding:1px 6px;cursor:pointer;">전체 해제</button>' +
    '<span id="providerModelCount" style="font-size:10px;color:var(--muted);"></span>' +
    '</div>' +
    '<div style="display:flex;flex-direction:column;gap:3px;max-height:220px;overflow-y:auto;">' +
    _selectedProviderModels.map(function (m, i) {
      var mid = m.id || '';
      var mtype = m.type || 'chat';
      var isTts = /tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid);
      var isChecked = checkedIds ? !!checkedIds[mid] : !isTts;
      return '<div style="display:flex;align-items:center;gap:4px;background:var(--bg2);padding:3px 6px;border-radius:4px;font-size:10px;' + (isTts ? 'opacity:0.5;' : '') + '">' +
        '<input type="checkbox" class="provider-model-cb" data-idx="' + i + '"' + (isChecked ? ' checked' : '') + ' style="width:12px;height:12px;margin:0;flex-shrink:0;">' +
        '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(mid) + '">' + esc(mid) + '</span>' +
        '<select class="provider-model-type" data-idx="' + i + '" style="font-size:9px;padding:0 2px;border-radius:3px;border:1px solid var(--border);background:var(--bg);color:' + (_typeColors[mtype] || 'var(--muted)') + ';cursor:pointer;flex-shrink:0;">' +
        '<option value="chat"' + (mtype === 'chat' ? ' selected' : '') + '>💬 chat</option>' +
        '<option value="image"' + (mtype === 'image' ? ' selected' : '') + '>🖼 image</option>' +
        '<option value="video"' + (mtype === 'video' ? ' selected' : '') + '>🎬 video</option>' +
        '</select></div>';
    }).join('') +
    '</div>' +
    '<div style="margin-top:6px;font-size:10px;color:var(--muted);">타입을 확인/변경 후 "제공자 저장" 버튼을 누르면 선택한 모델만 저장됩니다. (전체 해제 후 저장하면 모델 없이 저장됩니다)</div>';
  resultEl.style.display = '';
  resultEl.innerHTML = html;
  _updateProviderModelCount();
  // 체크박스 변경 이벤트
  resultEl.querySelectorAll('.provider-model-cb').forEach(function (cb) {
    cb.addEventListener('change', function () { _updateProviderModelCount(); });
  });
  // 타입 셀렉트 변경 이벤트
  resultEl.querySelectorAll('.provider-model-type').forEach(function (sel) {
    sel.addEventListener('change', function () {
      var idx = parseInt(sel.getAttribute('data-idx'), 10);
      if (idx >= 0 && idx < _selectedProviderModels.length) {
        _selectedProviderModels[idx].type = sel.value;
      }
      var colors = { chat: 'var(--muted)', image: '#e879f9', video: '#38bdf8' };
      sel.style.color = colors[sel.value] || 'var(--muted)';
    });
  });
}

// 현재 체크된 모델 id 집합을 반환 (목록이 안 그려져 있으면 null)
function _getCheckedProviderModelIds() {
  var resultEl = $('settingsProviderFetchResult');
  if (!resultEl || !_selectedProviderModels) return null;
  var cbs = resultEl.querySelectorAll('.provider-model-cb');
  if (!cbs.length) return null;
  var ids = {};
  cbs.forEach(function (cb) {
    if (cb.checked) {
      var idx = parseInt(cb.getAttribute('data-idx'), 10);
      if (idx >= 0 && idx < _selectedProviderModels.length) ids[_selectedProviderModels[idx].id] = true;
    }
  });
  return ids;
}

// ── 모델 직접 입력: 자동 감지 없이 모델 ID를 수동 추가 ──────────────────────
// 이미 목록에 있는 모델을 입력하면 "이미 추가됨" 경고 대신 해당 항목을
// 자동으로 체크하고 그 행으로 스크롤/하이라이트한다 (자동 감지 목록이 수백 개여도
// 일일이 찾을 필요 없음).
function _addManualProviderModels() {
  var inputEl = $('settingsProviderManualModel');
  var typeEl = $('settingsProviderManualType');
  if (!inputEl) return;
  var raw = (inputEl.value || '').trim();
  if (!raw) { showToast('추가할 모델 ID를 입력하세요'); return; }
  var mtype = typeEl ? typeEl.value : 'chat';
  var ids = raw.split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s.length > 0; });
  if (!ids.length) return;

  // 재렌더링 시 기존 체크 상태가 날아가지 않도록 미리 보존
  var prevChecked = _getCheckedProviderModelIds();

  if (!_selectedProviderModels) _selectedProviderModels = [];
  var existing = {};
  var existingLower = {}; // 대소문자 무시 조회: lower -> 실제 목록의 id
  _selectedProviderModels.forEach(function (m) {
    existing[m.id] = true;
    existingLower[String(m.id || '').toLowerCase()] = m.id;
  });
  var added = 0;
  var checkedExisting = []; // 이미 목록에 있어서 체크만 켠 항목들
  ids.forEach(function (id) {
    if (existing[id]) {
      if (prevChecked) prevChecked[id] = true;
      checkedExisting.push(id);
      return;
    }
    // 대소문자만 다른 입력은 같은 모델로 간주해 기존 항목을 체크한다
    var actualId = existingLower[id.toLowerCase()];
    if (actualId) {
      if (prevChecked) prevChecked[actualId] = true;
      checkedExisting.push(actualId);
      return;
    }
    existing[id] = true;
    existingLower[id.toLowerCase()] = id;
    _selectedProviderModels.push({ id: id, label: id, type: mtype });
    if (prevChecked) prevChecked[id] = true; // 새 모델은 기본 체크
    added++;
  });
  inputEl.value = '';
  var headerExtra = checkedExisting.length ? (added ? ', 기존 ' + checkedExisting.length + '개 체크' : '기존 ' + checkedExisting.length + '개 체크') : '';
  _renderProviderModelList('📝 모델 ' + _selectedProviderModels.length + '개 (직접 입력 ' + added + '개 추가됨' + headerExtra + ') — 저장할 모델을 선택하세요:', 'var(--accent)', prevChecked);

  if (checkedExisting.length) {
    // 기존 항목 체크 + 해당 행으로 스크롤/하이라이트
    _highlightProviderModels(checkedExisting);
    showToast('기존 모델 ' + checkedExisting.length + '개에 체크했습니다' + (added ? ' · 신규 ' + added + '개 추가' : ''));
  } else if (added === 0) {
    showToast('추가하거나 체크할 모델이 없습니다');
  }
}

// 직접 입력한 모델이 이미 목록에 있을 때: 해당 체크박스를 켜고 행을 하이라이트한 뒤 스크롤한다.
function _highlightProviderModels(ids) {
  var resultEl = $('settingsProviderFetchResult');
  if (!resultEl || !_selectedProviderModels) return;
  var want = {};
  ids.forEach(function (id) { want[id] = true; });
  var firstRow = null;
  resultEl.querySelectorAll('.provider-model-cb').forEach(function (cb) {
    var idx = parseInt(cb.getAttribute('data-idx'), 10);
    if (isNaN(idx) || idx < 0 || idx >= _selectedProviderModels.length) return;
    if (!want[_selectedProviderModels[idx].id]) return;
    cb.checked = true;
    var row = cb.closest('div');
    if (row) {
      row.style.background = 'rgba(56,189,248,0.22)';
      row.style.outline = '1px solid var(--accent)';
      (function (r) {
        setTimeout(function () { r.style.background = ''; r.style.outline = ''; }, 2500);
      })(row);
      if (!firstRow) firstRow = row;
    }
  });
  if (firstRow && firstRow.scrollIntoView) {
    try { firstRow.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) { firstRow.scrollIntoView(); }
  }
  _updateProviderModelCount();
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

import re
from pathlib import Path

api_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\api.js")
app_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")

# 1. Update api.js
with open(api_js_path, "r", encoding="utf-8") as f:
    api_code = f.read()

# Ensure fetchProviderModels sends all variant field names and refreshProviderModels exists
old_fetch_block = """  async fetchProviderModels({ name, key, url, preset }) {"""
if old_fetch_block in api_code:
    new_fetch_block = """  async fetchProviderModels({ name, key, url, api_key, base_url, preset }) {
    const finalKey = (key || api_key || '').trim();
    const finalUrl = (url || base_url || '').trim();
    const res = await fetch(`${this.baseUrl}/api/providers/fetch-models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || 'temp',
        api_key: finalKey,
        key: finalKey,
        base_url: finalUrl,
        url: finalUrl,
        preset
      })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Fetch models failed: ${res.status}`);
    }
    return await res.json();
  },

  async refreshProviderModels(name) {
    const res = await fetch(`${this.baseUrl}/api/providers/refresh-models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(err.error || `Refresh models failed: ${res.status}`);
    }
    return await res.json();
  },"""
    # Find up to next method
    idx = api_code.find(old_fetch_block)
    end_idx = api_code.find("async getProfiles()", idx)
    api_code = api_code[:idx] + new_fetch_block + "\n\n  /**\n   * Agent Profiles\n   */\n  " + api_code[end_idx:]
    with open(api_js_path, "w", encoding="utf-8") as f:
        f.write(api_code)
    print("api.js updated.")


# 2. Update app.js
with open(app_js_path, "r", encoding="utf-8") as f:
    app_code = f.read()

# Replace the entire initSettingsModal function with the classic full-featured logic
old_init_settings_pattern = re.compile(r"async function initSettingsModal\(\)\s*\{[\s\S]*?\n\}\n\n// ── 4\. Agent Persona Management", re.MULTILINE)

new_init_settings = """async function initSettingsModal() {
  const openBtn = document.getElementById('sidebar-settings-btn');
  const modal = document.getElementById('settings-modal');
  const closeBtn = document.getElementById('settings-modal-close');
  const doneBtn = document.getElementById('settings-modal-done');
  const providersList = document.getElementById('settings-providers-list');
  const addBtn = document.getElementById('add-new-provider-btn');
  const formCard = document.getElementById('provider-form-card');
  const formCloseBtn = document.getElementById('provider-form-close-btn');
  const formCancelBtn = document.getElementById('provider-form-cancel-btn');
  const formSaveBtn = document.getElementById('provider-form-save-btn');
  const presetSelect = document.getElementById('provider-preset-select');
  const nameInput = document.getElementById('provider-name-input');
  const keyInput = document.getElementById('provider-key-input');
  const keyToggleBtn = document.getElementById('provider-key-toggle-btn');
  const urlInput = document.getElementById('provider-url-input');
  const manualModelsInput = document.getElementById('provider-manual-models-input');
  const fetchModelsBtn = document.getElementById('provider-fetch-models-btn');
  const fetchResult = document.getElementById('provider-fetch-result');

  // 구 버전 custom_providers.json의 14개 공식 제공자 프리셋 + 기본 모델 목록
  const FALLBACK_PRESETS = {
    'openai': { label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'o1', 'o3-mini'] },
    'anthropic': { label: 'Anthropic', base_url: 'https://api.anthropic.com/v1', models: ['claude-3-7-sonnet-20250219', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022'] },
    'google': { label: 'Google Gemini', base_url: 'https://generativelanguage.googleapis.com/v1beta', models: ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash'] },
    'deepseek': { label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
    'minimax': { label: 'MiniMax', base_url: 'https://api.minimax.io/v1', models: ['MiniMax-Text-01', 'abab6.5s-chat'] },
    'openrouter': { label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', models: ['deepseek/deepseek-r1', 'deepseek/deepseek-chat', 'anthropic/claude-3.5-sonnet', 'openai/gpt-4o'] },
    'together': { label: 'Together AI', base_url: 'https://api.together.xyz/v1', models: ['meta-llama/Llama-3.3-70B-Instruct-Turbo', 'deepseek-ai/DeepSeek-R1'] },
    'groq': { label: 'Groq', base_url: 'https://api.groq.com/openai/v1', models: ['llama-3.3-70b-versatile', 'deepseek-r1-distill-llama-70b'] },
    'xai': { label: 'xAI (Grok)', base_url: 'https://api.x.ai/v1', models: ['grok-2-1212', 'grok-beta'] },
    'zhipu': { label: 'ZhipuAI (GLM)', base_url: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-plus', 'glm-4-flash'] },
    'dashscope': { label: 'Alibaba Cloud (DashScope/Qwen)', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max', 'qwen-turbo'] },
    'qwen-token-plan': { label: 'Alibaba Cloud (Token Plan)', base_url: 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max'] },
    'opencode-go': {
      label: 'OpenCode Go',
      base_url: 'https://opencode.ai/zen/go/v1',
      models: ['deepseek-v4.1-flash', 'deepseek-v4-flash', 'deepseek-v4-pro', 'glm-5.3-flash', 'gpt-6-luna', 'gpt-5.6-luna', 'qwen3.8-max', 'mimo-v2.6-flash']
    },
    'opencode-zen': {
      label: 'OpenCode Zen',
      base_url: 'https://opencode.ai/zen/v1',
      models: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-4-5', 'gpt-6-luna', 'gpt-5.6-terra', 'gemini-3.8-flash', 'gemini-3.1-pro', 'deepseek-v4.1-flash', 'muse-spark-1.3', 'kimi-k3', 'glm-5.3']
    }
  };

  let activePresets = { ...FALLBACK_PRESETS };
  let editingProviderName = null;
  let detectedProviderModels = []; // 자동 감지된 모델 목록

  function openModal() {
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    loadProvidersList();
  }

  function closeModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    if (formCard) formCard.classList.add('hidden');
  }

  // 프리셋 옵션 채우기 (서버 프리셋 + 폴백 모델 딥 병합)
  function populatePresetSelect(serverPresets) {
    if (!presetSelect) return;
    activePresets = {};
    for (const [key, cfg] of Object.entries(FALLBACK_PRESETS)) {
      activePresets[key] = { ...cfg };
    }
    if (serverPresets) {
      for (const [key, cfg] of Object.entries(serverPresets)) {
        const existingModels = activePresets[key]?.models || [];
        const newModels = (cfg.models && cfg.models.length > 0) ? cfg.models : existingModels;
        activePresets[key] = {
          ...(activePresets[key] || {}),
          ...cfg,
          models: newModels
        };
      }
    }

    const curVal = presetSelect.value;
    presetSelect.innerHTML = '<option value="">-- 직접 입력 (Manual Entry) --</option>';
    
    for (const [key, cfg] of Object.entries(activePresets)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = `${cfg.label || key} (${cfg.base_url || ''})`;
      presetSelect.appendChild(opt);
    }
    if (curVal && activePresets[curVal]) {
      presetSelect.value = curVal;
    }
  }

  // 프리셋 변경 시 자동 필드 세팅 (구 버전 로직 그대로)
  if (presetSelect) {
    presetSelect.addEventListener('change', () => {
      const val = presetSelect.value;
      if (!val || !activePresets[val]) return;

      const pcfg = activePresets[val];
      // 편집 모드가 아니면 이름을 프리셋 키로 설정
      if (!editingProviderName) {
        if (nameInput) nameInput.value = val;
      }
      if (urlInput) {
        urlInput.value = pcfg.base_url || '';
      }
      const modelsList = (pcfg.models && pcfg.models.length > 0) ? pcfg.models : (FALLBACK_PRESETS[val]?.models || []);
      if (manualModelsInput && modelsList.length > 0) {
        manualModelsInput.value = modelsList.map(m => m.id || m.name || m).join(', ');
      }
      if (fetchResult) {
        fetchResult.classList.add('hidden');
        fetchResult.innerHTML = '';
      }
      detectedProviderModels = [];

      // API Key 입력 필드로 자동 포커스
      if (keyInput) keyInput.focus();
    });
  }

  async function loadProvidersList() {
    if (!providersList) return;
    try {
      const data = await DaonAPI.getProviders();
      populatePresetSelect(data.presets);

      const providers = data.providers || {};
      const entries = Object.entries(providers);
      if (entries.length === 0) {
        providersList.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">등록된 제공자가 없습니다. 새 제공자를 추가해보세요.</div>';
        return;
      }

      providersList.innerHTML = entries.map(([pName, pData]) => {
        const maskedKey = pData.api_key ? (pData.api_key.substring(0, 4) + '••••••••' + pData.api_key.slice(-3)) : '미등록';
        const modelCount = (pData.models || []).length;
        return `
          <div class="border border-black/10 rounded-[8px] p-3 bg-surface flex items-center justify-between hover:border-black/25 transition-colors">
            <div class="flex flex-col gap-0.5 truncate">
              <div class="flex items-center gap-2">
                <span class="font-label-md font-semibold text-[13px] text-on-surface">${escapeHtml(pName)}</span>
                <span class="text-[10px] font-code px-1.5 py-0.5 rounded bg-black/[0.05] text-on-surface-variant">${modelCount}개 모델</span>
              </div>
              <div class="flex items-center gap-2 text-[11px] font-code text-on-surface-variant truncate">
                <span>Key: ${escapeHtml(maskedKey)}</span>
                <span>·</span>
                <span class="truncate">${escapeHtml(pData.base_url || '기본 URL')}</span>
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button class="px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 refresh-provider-btn cursor-pointer flex items-center gap-1" data-name="${escapeHtml(pName)}" title="저장된 API 키로 최신 모델 목록 다시 불러오기">
                <span class="material-symbols-outlined text-[13px]">refresh</span>
                <span>갱신</span>
              </button>
              <button class="px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 edit-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">수정</button>
              <button class="px-2 py-1 text-[11px] border border-black/15 text-rose-600 rounded-[6px] hover:bg-rose-50 delete-provider-btn cursor-pointer" data-name="${escapeHtml(pName)}">삭제</button>
            </div>
          </div>
        `;
      }).join('');

      // Refresh button (구 버전 refreshProviderModels 로직)
      providersList.querySelectorAll('.refresh-provider-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.getAttribute('data-name');
          const oldHtml = btn.innerHTML;
          btn.disabled = true;
          btn.innerHTML = '<span class="material-symbols-outlined text-[13px] animate-spin">refresh</span><span>갱신 중...</span>';
          try {
            const res = await DaonAPI.refreshProviderModels(name);
            const count = res.count || (res.models ? res.models.length : 0);
            alert(`✅ '${name}' 제공자: 모델 ${count}개가 성공적으로 갱신되었습니다!`);
            await loadProvidersList();
            try { await loadLiveProviders(); } catch (_) {}
          } catch (err) {
            alert(`모델 갱신 실패: ${err.message}`);
          } finally {
            btn.disabled = false;
            btn.innerHTML = oldHtml;
          }
        });
      });

      // Edit button
      providersList.querySelectorAll('.edit-provider-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const name = btn.getAttribute('data-name');
          const p = providers[name];
          if (p) editProvider(name, p);
        });
      });

      // Delete button
      providersList.querySelectorAll('.delete-provider-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.getAttribute('data-name');
          if (confirm(`'${name}' 제공자와 등록된 모델을 삭제하시겠습니까?`)) {
            await DaonAPI.deleteProvider(name);
            await loadProvidersList();
            try { await loadLiveProviders(); } catch (_) {}
          }
        });
      });
    } catch (_) {
      providersList.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">제공자 목록을 불러오지 못했습니다.</div>';
    }
  }

  function editProvider(name, data) {
    if (!formCard) return;
    editingProviderName = name;
    formCard.classList.remove('hidden');
    document.getElementById('provider-form-title').textContent = `'${name}' 제공자 수정`;
    if (nameInput) { nameInput.value = name; nameInput.disabled = true; }
    if (keyInput) { keyInput.value = ''; keyInput.placeholder = '기존 키 유지 시 빈 칸으로 두세요'; }
    if (urlInput) urlInput.value = data.base_url || '';
    if (manualModelsInput) {
      manualModelsInput.value = (data.models || []).map(m => m.id || m.name || m).join(', ');
    }
    if (presetSelect) presetSelect.value = '';
    if (fetchResult) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
    }
    detectedProviderModels = [];
  }

  function resetForm() {
    editingProviderName = null;
    if (nameInput) { nameInput.value = ''; nameInput.disabled = false; }
    if (keyInput) { keyInput.value = ''; keyInput.placeholder = 'sk-...'; }
    if (urlInput) urlInput.value = '';
    if (manualModelsInput) manualModelsInput.value = '';
    if (presetSelect) presetSelect.value = '';
    if (fetchResult) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
    }
    detectedProviderModels = [];
    document.getElementById('provider-form-title').textContent = '제공자 추가';
  }

  if (openBtn) openBtn.addEventListener('click', openModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  if (doneBtn) doneBtn.addEventListener('click', closeModal);

  if (addBtn) {
    addBtn.addEventListener('click', () => {
      resetForm();
      formCard.classList.remove('hidden');
    });
  }

  if (formCloseBtn) formCloseBtn.addEventListener('click', () => formCard.classList.add('hidden'));
  if (formCancelBtn) formCancelBtn.addEventListener('click', () => formCard.classList.add('hidden'));

  if (keyToggleBtn && keyInput) {
    keyToggleBtn.addEventListener('click', () => {
      const isPass = keyInput.type === 'password';
      keyInput.type = isPass ? 'text' : 'password';
      keyToggleBtn.querySelector('span').textContent = isPass ? 'visibility_off' : 'visibility';
    });
  }

  // ── 구 버전 모델 자동 감지 및 체크박스 선택 UI (구 버전 그대로) ──────────
  function renderDetectedModelList(models) {
    if (!fetchResult) return;
    detectedProviderModels = models || [];
    if (detectedProviderModels.length === 0) {
      fetchResult.classList.remove('hidden');
      fetchResult.innerHTML = '<div class="text-amber-600">⚠️ 제공자로부터 모델이 반환되지 않았습니다. 저장 후 수동으로 모델을 지정할 수 있습니다.</div>';
      return;
    }

    fetchResult.classList.remove('hidden');
    let html = `
      <div class="flex items-center justify-between pb-1.5 border-b border-black/10">
        <span class="font-semibold text-emerald-600 text-[12px]">✅ ${detectedProviderModels.length}개 모델 발견</span>
        <div class="flex items-center gap-1.5">
          <button id="select-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 선택</button>
          <button id="deselect-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 해제</button>
          <span id="detected-model-count-label" class="text-[10px] font-code text-on-surface-variant"></span>
        </div>
      </div>
      <div class="flex flex-col gap-1 max-h-[180px] overflow-y-auto mt-2 pr-1" id="detected-models-scroll">
    `;

    detectedProviderModels.forEach((m, idx) => {
      const mid = m.id || m.name || m;
      const mtype = m.type || 'chat';
      const isSpecial = /tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid);
      const isChecked = !isSpecial;

      html += `
        <div class="flex items-center justify-between p-1 rounded hover:bg-black/[0.03] text-[11px] font-code ${isSpecial ? 'opacity-50' : ''}">
          <label class="flex items-center gap-2 cursor-pointer truncate flex-1 pr-2">
            <input type="checkbox" class="model-check-box rounded border-black/20" data-idx="${idx}" ${isChecked ? 'checked' : ''} />
            <span class="truncate" title="${escapeHtml(mid)}">${escapeHtml(mid)}</span>
          </label>
          <select class="model-type-select text-[10px] px-1 py-0.5 rounded border border-black/10 bg-surface outline-none" data-idx="${idx}">
            <option value="chat" ${mtype === 'chat' ? 'selected' : ''}>💬 chat</option>
            <option value="image" ${mtype === 'image' ? 'selected' : ''}>🖼 image</option>
            <option value="video" ${mtype === 'video' ? 'selected' : ''}>🎬 video</option>
          </select>
        </div>
      `;
    });

    html += `</div>
      <div class="mt-2 text-[10px] text-on-surface-variant/80 border-t border-black/10 pt-1">
        타입을 확인/선택 후 [제공자 저장] 버튼을 누르면 체크된 모델만 저장됩니다.
      </div>
    `;

    fetchResult.innerHTML = html;

    function updateCount() {
      const cbs = fetchResult.querySelectorAll('.model-check-box');
      let checked = 0;
      const checkedIds = [];
      cbs.forEach(cb => {
        if (cb.checked) {
          checked++;
          const idx = parseInt(cb.getAttribute('data-idx'), 10);
          const m = detectedProviderModels[idx];
          if (m) checkedIds.push(m.id || m.name || m);
        }
      });
      const lbl = document.getElementById('detected-model-count-label');
      if (lbl) lbl.textContent = `${checked}/${cbs.length}개 선택됨`;
      if (manualModelsInput) manualModelsInput.value = checkedIds.join(', ');
    }

    document.getElementById('select-all-models-btn')?.addEventListener('click', () => {
      fetchResult.querySelectorAll('.model-check-box').forEach(cb => cb.checked = true);
      updateCount();
    });

    document.getElementById('deselect-all-models-btn')?.addEventListener('click', () => {
      fetchResult.querySelectorAll('.model-check-box').forEach(cb => cb.checked = false);
      updateCount();
    });

    fetchResult.querySelectorAll('.model-check-box').forEach(cb => {
      cb.addEventListener('change', updateCount);
    });

    fetchResult.querySelectorAll('.model-type-select').forEach(sel => {
      sel.addEventListener('change', (e) => {
        const idx = parseInt(e.target.getAttribute('data-idx'), 10);
        if (detectedProviderModels[idx]) {
          detectedProviderModels[idx].type = e.target.value;
        }
      });
    });

    updateCount();
  }

  // 모델 자동 감지 버튼 클릭
  if (fetchModelsBtn) {
    fetchModelsBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();
      if (!url) { alert('Base URL(엔드포인트)을 먼저 입력해주세요.'); return; }
      if (!key && !editingProviderName) { alert('API 키를 입력해주세요.'); return; }

      fetchModelsBtn.disabled = true;
      fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>감지 중...</span>';
      try {
        const res = await DaonAPI.fetchProviderModels({ name: name || 'temp', key, url, preset: presetSelect.value });
        const detected = res.models || [];
        renderDetectedModelList(detected);
      } catch (err) {
        if (fetchResult) {
          fetchResult.classList.remove('hidden');
          fetchResult.innerHTML = `<div class="text-rose-600 text-[11px]">감지 실패: ${escapeHtml(err.message)}</div>`;
        }
      } finally {
        fetchModelsBtn.disabled = false;
        fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">sync</span><span>모델 자동 감지</span>';
      }
    });
  }

  // 제공자 저장 버튼 (구 버전 로직 그대로)
  if (formSaveBtn) {
    formSaveBtn.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      const key = keyInput.value.trim();
      const url = urlInput.value.trim();

      if (!name) { alert('제공자 식별명을 입력해주세요.'); return; }
      if (!key && !editingProviderName) { alert('API 키를 입력해주세요.'); return; }
      if (!url) { alert('Base URL을 입력해주세요.'); return; }

      // 수동 입력 필드 또는 체크박스에서 모델 목록 추출
      let finalModels = [];
      const cbs = fetchResult?.querySelectorAll('.model-check-box');
      if (cbs && cbs.length > 0) {
        cbs.forEach(cb => {
          if (cb.checked) {
            const idx = parseInt(cb.getAttribute('data-idx'), 10);
            const m = detectedProviderModels[idx];
            if (m) {
              const sel = fetchResult.querySelector(`.model-type-select[data-idx="${idx}"]`);
              finalModels.push({
                id: m.id || m.name || m,
                label: m.label || m.id || m.name || m,
                type: sel ? sel.value : (m.type || 'chat')
              });
            }
          }
        });
      } else {
        const modelsStr = manualModelsInput?.value.trim() || '';
        if (modelsStr) {
          finalModels = modelsStr.split(',').map(m => m.trim()).filter(Boolean).map(id => ({ id, label: id, type: 'chat' }));
        }
      }

      try {
        formSaveBtn.disabled = true;
        formSaveBtn.textContent = '저장 중...';

        const payload = {
          name,
          base_url: url,
          models: finalModels
        };
        if (key) payload.api_key = key;

        const result = await DaonAPI.addProvider(payload);
        const addedCount = result.models ? result.models.length : finalModels.length;
        alert(`'${name}' 제공자가 성공적으로 저장되었습니다! (${addedCount}개 모델)`);
        formCard.classList.add('hidden');
        await loadProvidersList();
        try { await loadLiveProviders(); } catch (_) {}
      } catch (err) {
        alert(`저장 실패: ${err.message}`);
      } finally {
        formSaveBtn.disabled = false;
        formSaveBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">save</span><span>제공자 저장</span>';
      }
    });
  }

  // 초기 로드 시 프리셋 세팅
  populatePresetSelect(FALLBACK_PRESETS);
}

// ── 4. Agent Persona Management"""

if old_init_settings_pattern.search(app_code):
    app_code = old_init_settings_pattern.sub(new_init_settings, app_code)
    print("app.js initSettingsModal updated!")
else:
    print("WARNING: old_init_settings_pattern not matched in app.js")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(app_code)

print("Patching complete.")

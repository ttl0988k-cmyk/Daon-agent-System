import re
from pathlib import Path

index_path = Path(r"c:\daon\Daon agent System\index.html")
app_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")

# 1. Update index.html
with open(index_path, "r", encoding="utf-8") as f:
    index_html = f.read()

old_manual_models_html = """          <div>
            <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">모델 수동 등록 (쉼표로 구분)</label>
            <div class="flex items-center gap-2">
              <input id="provider-manual-models-input" type="text" placeholder="예: deepseek-v4.1-flash, gpt-4o" class="flex-1 bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />
              <button id="provider-fetch-models-btn" type="button" class="px-2.5 py-1.5 bg-surface-container-high hover:bg-surface-dim rounded-[6px] text-[11px] font-label-md text-on-surface flex items-center gap-1 cursor-pointer shrink-0">
                <span class="material-symbols-outlined text-[14px]">sync</span>
                <span>모델 자동 감지</span>
              </button>
            </div>
            <div id="provider-fetch-result" class="hidden text-[11px] p-2 mt-1.5 rounded-[6px] bg-surface-container-high text-on-surface-variant"></div>
          </div>"""

new_manual_models_html = """          <!-- 모델 자동 감지 & 수동 등록 (구 버전 로직 그대로 복원) -->
          <div class="flex flex-col gap-2 pt-1 border-t border-black/10">
            <div class="flex items-center justify-between">
              <label class="block font-label-sm text-[11px] font-semibold text-on-surface">모델 등록 및 관리</label>
              <button id="provider-fetch-models-btn" type="button" class="px-2.5 py-1 bg-surface-container-high hover:bg-surface-dim rounded-[6px] text-[11px] font-label-md text-on-surface flex items-center gap-1 cursor-pointer transition-colors" title="제공자의 /models 엔드포인트에서 지원 모델을 자동으로 가져옵니다">
                <span class="material-symbols-outlined text-[14px]">sync</span>
                <span>🔍 모델 자동 감지</span>
              </button>
            </div>

            <!-- 모델 직접 입력 (수동 등록) -->
            <div class="flex flex-col gap-1">
              <div class="flex items-center gap-1.5">
                <input id="provider-manual-model-input" type="text" placeholder="예: openai/gpt-4o (쉼표로 여러 개 입력 가능, Enter 지원)" class="flex-1 bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />
                <select id="provider-manual-type-select" class="text-[11px] font-label-sm px-2 py-1.5 rounded-[6px] border border-black/15 bg-surface-container-lowest text-on-surface outline-none cursor-pointer shrink-0">
                  <option value="chat">💬 chat</option>
                  <option value="image">🖼 image</option>
                  <option value="video">🎬 video</option>
                </select>
                <button id="provider-add-manual-model-btn" type="button" class="px-3 py-1.5 bg-surface-container-highest hover:bg-black/10 border border-black/10 rounded-[6px] text-[11px] font-label-md font-semibold text-on-surface flex items-center gap-1 cursor-pointer shrink-0 active:scale-95 transition-all">
                  <span class="material-symbols-outlined text-[14px]">add</span>
                  <span>+ 추가</span>
                </button>
              </div>
              <span class="text-[10px] text-on-surface-variant/70 leading-tight">
                모델 ID를 직접 추가합니다 (Enter 키 지원). 이미 목록에 있는 모델을 입력하면 자동으로 체크하고 그 위치로 이동합니다.
              </span>
            </div>

            <!-- 모델 목록 표시 컨테이너 (자동 감지 + 직접 입력 공용) -->
            <div id="provider-fetch-result" class="hidden text-[11px] p-2.5 rounded-[8px] bg-surface-container-high text-on-surface-variant border border-black/10 flex flex-col gap-1.5 mt-0.5"></div>
          </div>"""

if old_manual_models_html in index_html:
    index_html = index_html.replace(old_manual_models_html, new_manual_models_html)
    print("index.html updated with manual models input & type selector & add button.")
else:
    print("WARNING: old_manual_models_html not matched in index.html")

# Update cache buster
index_html = index_html.replace('app.js?v=20261008_2358', 'app.js?v=20261009_0018')

with open(index_path, "w", encoding="utf-8") as f:
    f.write(index_html)


# 2. Update app.js
with open(app_js_path, "r", encoding="utf-8") as f:
    app_code = f.read()

# Replace the entire initSettingsModal with full manual model add logic
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
  const manualModelInput = document.getElementById('provider-manual-model-input');
  const manualTypeSelect = document.getElementById('provider-manual-type-select');
  const addManualModelBtn = document.getElementById('provider-add-manual-model-btn');
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
  let selectedProviderModels = []; // 현재 모델 목록 { id, label, type, checked }

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
      if (!editingProviderName) {
        if (nameInput) nameInput.value = val;
      }
      if (urlInput) {
        urlInput.value = pcfg.base_url || '';
      }
      const modelsList = (pcfg.models && pcfg.models.length > 0) ? pcfg.models : (FALLBACK_PRESETS[val]?.models || []);
      if (modelsList && modelsList.length > 0) {
        selectedProviderModels = modelsList.map(m => {
          const mid = m.id || m.name || m;
          return { id: mid, label: mid, type: m.type || 'chat', checked: true };
        });
        renderProviderModelList('📦 프리셋 기본 모델 목록 (' + selectedProviderModels.length + '개)');
      } else {
        selectedProviderModels = [];
        if (fetchResult) {
          fetchResult.classList.add('hidden');
          fetchResult.innerHTML = '';
        }
      }

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
    if (presetSelect) presetSelect.value = '';

    const models = data.models || [];
    selectedProviderModels = models.map(m => {
      const mid = typeof m === 'string' ? m : (m.id || m.name || '');
      const mtype = typeof m === 'object' && m.type ? m.type : 'chat';
      return { id: mid, label: mid, type: mtype, checked: true };
    });

    if (selectedProviderModels.length > 0) {
      renderProviderModelList(`📋 '${name}' 등록 모델 목록 (${selectedProviderModels.length}개)`);
    } else {
      if (fetchResult) {
        fetchResult.classList.add('hidden');
        fetchResult.innerHTML = '';
      }
    }
  }

  function resetForm() {
    editingProviderName = null;
    if (nameInput) { nameInput.value = ''; nameInput.disabled = false; }
    if (keyInput) { keyInput.value = ''; keyInput.placeholder = 'sk-...'; }
    if (urlInput) urlInput.value = '';
    if (manualModelInput) manualModelInput.value = '';
    if (presetSelect) presetSelect.value = '';
    selectedProviderModels = [];
    if (fetchResult) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
    }
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

  // ── 구 버전 모델 목록 렌더링 (자동 감지 + 직접 수동 입력 공용) ──────────
  function renderProviderModelList(headerTitle) {
    if (!fetchResult) return;
    if (selectedProviderModels.length === 0) {
      fetchResult.classList.add('hidden');
      fetchResult.innerHTML = '';
      return;
    }

    fetchResult.classList.remove('hidden');
    let html = `
      <div class="flex items-center justify-between pb-1.5 border-b border-black/10">
        <span class="font-semibold text-emerald-700 text-[12px]">${escapeHtml(headerTitle || '선택된 모델 목록')}</span>
        <div class="flex items-center gap-1.5">
          <button id="select-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 선택</button>
          <button id="deselect-all-models-btn" type="button" class="px-2 py-0.5 bg-black/[0.05] hover:bg-black/10 rounded text-[10px] font-medium cursor-pointer">전체 해제</button>
          <span id="detected-model-count-label" class="text-[10px] font-code text-on-surface-variant font-medium"></span>
        </div>
      </div>
      <div class="flex flex-col gap-1 max-h-[190px] overflow-y-auto mt-2 pr-1" id="detected-models-scroll">
    `;

    selectedProviderModels.forEach((m, idx) => {
      const mid = m.id || m.label || '';
      const mtype = m.type || 'chat';
      const isSpecial = /tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid);
      const isChecked = m.checked !== false;

      html += `
        <div class="flex items-center justify-between p-1 rounded hover:bg-black/[0.03] text-[11px] font-code model-row-item transition-colors ${isSpecial ? 'opacity-60' : ''}" id="model-row-${idx}">
          <label class="flex items-center gap-2 cursor-pointer truncate flex-1 pr-2">
            <input type="checkbox" class="model-check-box rounded border-black/20" data-idx="${idx}" ${isChecked ? 'checked' : ''} />
            <span class="truncate model-id-text" title="${escapeHtml(mid)}">${escapeHtml(mid)}</span>
          </label>
          <div class="flex items-center gap-1 shrink-0">
            <select class="model-type-select text-[10px] px-1.5 py-0.5 rounded border border-black/10 bg-surface outline-none cursor-pointer" data-idx="${idx}">
              <option value="chat" ${mtype === 'chat' ? 'selected' : ''}>💬 chat</option>
              <option value="image" ${mtype === 'image' ? 'selected' : ''}>🖼 image</option>
              <option value="video" ${mtype === 'video' ? 'selected' : ''}>🎬 video</option>
            </select>
            <button class="remove-model-btn p-0.5 text-on-surface-variant hover:text-red-600 rounded cursor-pointer" data-idx="${idx}" type="button" title="이 모델 제거">
              <span class="material-symbols-outlined text-[14px]">close</span>
            </button>
          </div>
        </div>
      `;
    });

    html += `</div>
      <div class="mt-2 text-[10px] text-on-surface-variant/80 border-t border-black/10 pt-1 flex items-center justify-between">
        <span>타입을 확인/선택 후 [제공자 저장] 버튼을 누르면 체크된 모델만 저장됩니다.</span>
      </div>
    `;

    fetchResult.innerHTML = html;

    function updateCount() {
      const cbs = fetchResult.querySelectorAll('.model-check-box');
      let checked = 0;
      cbs.forEach(cb => {
        const idx = parseInt(cb.getAttribute('data-idx'), 10);
        if (selectedProviderModels[idx]) {
          selectedProviderModels[idx].checked = cb.checked;
        }
        if (cb.checked) checked++;
      });
      const lbl = document.getElementById('detected-model-count-label');
      if (lbl) lbl.textContent = `${checked}/${cbs.length}개 선택됨`;
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
        if (selectedProviderModels[idx]) {
          selectedProviderModels[idx].type = e.target.value;
        }
      });
    });

    fetchResult.querySelectorAll('.remove-model-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.getAttribute('data-idx'), 10);
        selectedProviderModels.splice(idx, 1);
        renderProviderModelList(headerTitle);
      });
    });

    updateCount();
  }

  // ── 구 버전 _addManualProviderModels 로직 그대로 구현 ─────────────────────
  function addManualModels() {
    if (!manualModelInput) return;
    const raw = (manualModelInput.value || '').trim();
    if (!raw) { alert('추가할 모델 ID를 입력해주세요.'); return; }

    const mtype = manualTypeSelect ? manualTypeSelect.value : 'chat';
    const ids = raw.split(',').map(s => s.trim()).filter(Boolean);
    if (ids.length === 0) return;

    const existing = {};
    const existingLower = {};
    selectedProviderModels.forEach((m, idx) => {
      existing[m.id] = idx;
      existingLower[String(m.id || '').toLowerCase()] = idx;
    });

    let addedCount = 0;
    const checkedExistingIndices = [];

    ids.forEach(id => {
      if (existing[id] !== undefined) {
        const idx = existing[id];
        selectedProviderModels[idx].checked = true;
        checkedExistingIndices.push(idx);
        return;
      }
      const actualIdx = existingLower[id.toLowerCase()];
      if (actualIdx !== undefined) {
        selectedProviderModels[actualIdx].checked = true;
        checkedExistingIndices.push(actualIdx);
        return;
      }
      // 신규 모델 추가
      selectedProviderModels.push({ id, label: id, type: mtype, checked: true });
      addedCount++;
    });

    manualModelInput.value = '';
    renderProviderModelList(`📝 모델 목록 (직접 입력 ${addedCount}개 추가됨)`);

    // 기존 모델이 입력된 경우 해당 행으로 스크롤 및 하이라이트 효과
    if (checkedExistingIndices.length > 0) {
      checkedExistingIndices.forEach(idx => {
        const row = document.getElementById(`model-row-${idx}`);
        if (row) {
          row.classList.add('bg-primary/15');
          row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          setTimeout(() => row.classList.remove('bg-primary/15'), 2000);
        }
      });
    }
    manualModelInput.focus();
  }

  // 추가 버튼 클릭 이벤트
  if (addManualModelBtn) {
    addManualModelBtn.addEventListener('click', addManualModels);
  }

  // 직접 입력 인풋 엔터키 이벤트
  if (manualModelInput) {
    manualModelInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addManualModels();
      }
    });
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
        if (detected.length === 0) {
          alert('제공자로부터 모델이 반환되지 않았습니다. 수동으로 모델을 직접 추가해주세요.');
          return;
        }

        // 기존에 등록된 모델이 있다면 합치기
        const existingMap = {};
        selectedProviderModels.forEach(m => { existingMap[m.id] = m; });
        detected.forEach(m => {
          const mid = m.id || m.name || m;
          if (!existingMap[mid]) {
            selectedProviderModels.push({
              id: mid,
              label: m.label || mid,
              type: m.type || 'chat',
              checked: !(/tts|speech|audio|whisper|embed|rerank|moderation/i.test(mid))
            });
          }
        });

        renderProviderModelList(`✅ 자동 감지 완료 (${detected.length}개 발견)`);
      } catch (err) {
        alert('모델 자동 감지 실패: ' + err.message);
      } finally {
        fetchModelsBtn.disabled = false;
        fetchModelsBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">sync</span><span>🔍 모델 자동 감지</span>';
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

      // 체크된 모델만 수집 (구 버전 Bugfix 반영)
      const finalModels = selectedProviderModels
        .filter(m => m.checked !== false)
        .map(m => ({
          id: m.id,
          label: m.label || m.id,
          type: m.type || 'chat'
        }));

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
    print("app.js initSettingsModal replaced with full manual model features!")
else:
    print("WARNING: old_init_settings_pattern not matched in app.js")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(app_code)

print("Patch manual model feature complete.")

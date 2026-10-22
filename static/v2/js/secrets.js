/**
 * DAON Workspace v2.5 — Secrets (시크릿카드) UI
 *
 * - 채팅 인라인 카드: pending 요청이 있으면 #chat-messages-container 끝에 카드 렌더.
 *   fill kind → secure input(type=password) + [저장]/[취소], allow kind → [허용]/[취소].
 * - Settings 모달에 Secrets 섹션을 동적 주입 (index.html 수정 불필요): 목록(이름·purpose·scope만) + 삭제 + 저장 폼.
 * - pending 감지: 5초 폴링(plugins.js 컨벤션) + SSE secret_request push(daon:secret-request 이벤트).
 *
 * 보안 규칙:
 * - 값(value)은 어떤 경로로도 렌더하지 않는다 (DOM textContent/innerHTML에 값 삽입 금지).
 * - 값은 console.log/warn/error에 절대 남기지 않는다.
 * - 전송 직후 입력 필드를 즉시 clear하고 지역 변수도 null로 비운다.
 */

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtScope(scope) {
  if (scope === 'all') return '전체 세션';
  if (Array.isArray(scope)) return scope.length ? esc(scope.join(', ')) : '없음';
  return esc(scope || '전체 세션');
}

const _st = {
  api: null,
  getSessionId: () => '',
  timer: null,
  rendered: new Set(), // 렌더 중인 pending request id
  settingsLoaded: false,
  polling: false,
};

function currentSessionId() {
  try {
    const v = _st.getSessionId();
    return v ? String(v) : '';
  } catch (_) {
    return '';
  }
}

/** 입력 엘리먼트의 값을 안전하게 읽고 즉시 비운다. 반환 후 DOM에는 값이 남지 않는다. */
function takeAndClear(input) {
  if (!input) return '';
  const v = input.value;
  input.value = '';
  try { input.setAttribute('value', ''); } catch (_) {}
  return v;
}

// ── 채팅 인라인 카드 ──────────────────────────────────────────────────────

function chatContainer() {
  return document.getElementById('chat-messages-container');
}

function removeStaleCards(liveIds) {
  const box = chatContainer();
  if (!box) return;
  box.querySelectorAll('[data-secret-req-id]').forEach((el) => {
    const id = el.getAttribute('data-secret-req-id');
    if (!liveIds.has(id)) {
      el.remove();
      _st.rendered.delete(id);
    }
  });
}

function scrollChatToBottom() {
  try {
    if (typeof window.scrollChatToBottom === 'function') { window.scrollChatToBottom(); return; }
    const box = chatContainer();
    if (box) box.lastElementChild?.scrollIntoView({ block: 'nearest' });
  } catch (_) {}
}

function buildCard(req) {
  const id = req.id || '';
  const name = req.name || '';
  const purpose = req.purpose || '';
  const kind = req.kind === 'allow' ? 'allow' : 'fill';
  if (!id || !name) return null;

  const card = document.createElement('div');
  card.setAttribute('data-secret-req-id', id);
  card.className = 'secret-req-card w-full border border-amber-500/40 bg-amber-50 rounded-[10px] p-3 flex flex-col gap-2 my-1';
  card.dataset.kind = kind;

  const head = document.createElement('div');
  head.className = 'flex items-center gap-2';
  head.innerHTML =
    '<span class="material-symbols-outlined text-[18px] text-amber-600">lock</span>' +
    '<span class="font-semibold text-[13px] text-on-surface">시크릿 요청: ' + esc(name) + '</span>' +
    '<span class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-700 border border-amber-500/30">' +
    (kind === 'allow' ? '권한 허용' : '값 입력') + '</span>';
  card.appendChild(head);

  if (purpose) {
    const p = document.createElement('div');
    p.className = 'text-[12px] text-on-surface-variant';
    p.textContent = purpose; // textContent로만 (값 아님 — 서버가 준 설명문)
    card.appendChild(p);
  }

  const note = document.createElement('div');
  note.className = 'text-[11px] text-on-surface-variant/80';
  note.textContent = '이 값은 에이전트에게 보이지 않습니다. 저장 후 “[secret:' + name + ']”로만 참조됩니다.';
  card.appendChild(note);

  const row = document.createElement('div');
  row.className = 'flex items-center gap-2';

  if (kind === 'fill') {
    const input = document.createElement('input');
    input.type = 'password';
    input.autocomplete = 'off';
    input.autocapitalize = 'off';
    input.spellcheck = false;
    input.placeholder = name + ' 값 입력';
    input.setAttribute('aria-label', name + ' 값 입력');
    input.className = 'flex-1 bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-amber-500';
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); saveBtn.click(); }
    });
    row.appendChild(input);

    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'px-3 py-1.5 bg-primary text-on-primary rounded-[6px] text-[12px] font-medium hover:bg-black/80 cursor-pointer shrink-0';
    saveBtn.textContent = '저장';
    saveBtn.addEventListener('click', async () => {
      // 값은 이 핸들러 지역 변수에만 잠시 존재하고, 전송 즉시 clear + null 처리한다.
      let pendingValue = takeAndClear(input);
      if (!pendingValue || !pendingValue.trim()) {
        statusEl.textContent = '값을 입력해 주세요.';
        pendingValue = null;
        input.focus();
        return;
      }
      saveBtn.disabled = true;
      statusEl.textContent = '저장 중...';
      try {
        await _st.api.setSecret({ name, value: pendingValue, purpose, scope: 'all' });
        pendingValue = null;
        statusEl.textContent = '저장 완료.';
        card.remove();
        _st.rendered.delete(id);
        await refreshAll();
      } catch (err) {
        pendingValue = null;
        statusEl.textContent = '저장 실패: ' + (err && err.message ? err.message : '오류');
        saveBtn.disabled = false;
      }
    });
    row.appendChild(saveBtn);
  } else {
    const allowBtn = document.createElement('button');
    allowBtn.type = 'button';
    allowBtn.className = 'px-3 py-1.5 bg-primary text-on-primary rounded-[6px] text-[12px] font-medium hover:bg-black/80 cursor-pointer shrink-0';
    allowBtn.textContent = '허용';
    allowBtn.addEventListener('click', async () => {
      allowBtn.disabled = true;
      statusEl.textContent = '처리 중...';
      try {
        await _st.api.allowSecret(name, currentSessionId());
        statusEl.textContent = '허용 완료.';
        card.remove();
        _st.rendered.delete(id);
        await refreshAll();
      } catch (err) {
        statusEl.textContent = '허용 실패: ' + (err && err.message ? err.message : '오류');
        allowBtn.disabled = false;
      }
    });
    row.appendChild(allowBtn);
  }

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'px-3 py-1.5 border border-black/15 rounded-[6px] text-[12px] text-on-surface hover:bg-black/5 cursor-pointer shrink-0';
  cancelBtn.textContent = '취소';
  cancelBtn.addEventListener('click', async () => {
    // fill 카드의 입력값도 함께 폐기
    const pw = row.querySelector('input[type="password"]');
    if (pw) takeAndClear(pw);
    cancelBtn.disabled = true;
    statusEl.textContent = '취소 중...';
    try {
      await _st.api.cancelSecretRequest(id);
    } catch (_) {
      // 취소 실패해도 카드는 제거 (다음 폴링에서 정합)
    }
    card.remove();
    _st.rendered.delete(id);
    await refreshAll();
  });
  row.appendChild(cancelBtn);
  card.appendChild(row);

  const statusEl = document.createElement('div');
  statusEl.className = 'secret-req-status text-[11px] text-on-surface-variant min-h-[14px]';
  statusEl.setAttribute('role', 'status');
  card.appendChild(statusEl);

  return card;
}

function renderPendingCards(pending) {
  const box = chatContainer();
  const list = Array.isArray(pending) ? pending.filter((r) => r && r.status !== 'cancelled' && r.status !== 'saved') : [];
  const liveIds = new Set(list.map((r) => r.id).filter(Boolean));
  if (!box) return;
  removeStaleCards(liveIds);
  let added = false;
  for (const req of list) {
    if (!req.id || !req.name || _st.rendered.has(req.id)) continue;
    if (box.querySelector(`[data-secret-req-id="${CSS.escape(req.id)}"]`)) {
      _st.rendered.add(req.id);
      continue;
    }
    const card = buildCard(req);
    if (card) {
      box.appendChild(card);
      _st.rendered.add(req.id);
      added = true;
    }
  }
  if (added) scrollChatToBottom();
}

// ── Settings 섹션 (동적 주입 — index.html 수정 불필요) ─────────────────────

function settingsScrollBox() {
  const modal = document.getElementById('settings-modal');
  if (!modal) return null;
  return modal.querySelector('.flex-1.overflow-y-auto');
}

function ensureSettingsSection() {
  const scroll = settingsScrollBox();
  if (!scroll || document.getElementById('secrets-settings-section')) return;
  const sec = document.createElement('div');
  sec.id = 'secrets-settings-section';
  sec.className = 'flex flex-col gap-2 border-t border-black/10 pt-4 mt-1';
  sec.innerHTML =
    '<div class="flex items-center justify-between">' +
      '<span class="font-label-md font-semibold text-[13px] text-on-surface flex items-center gap-1.5">' +
        '<span class="material-symbols-outlined text-[16px]">lock</span>' +
        '<span>시크릿 (Secrets)</span>' +
      '</span>' +
      '<button id="secrets-refresh-btn" type="button" class="px-2.5 py-1 border border-black/15 rounded-[6px] text-[11px] text-on-surface hover:bg-black/5 cursor-pointer flex items-center gap-1">' +
        '<span class="material-symbols-outlined text-[14px]">refresh</span><span>새로고침</span>' +
      '</button>' +
    '</div>' +
    '<div class="text-[11px] text-on-surface-variant">값은 표시되지 않습니다. 이름·용도·범위만 관리합니다.</div>' +
    '<div id="secrets-settings-list" class="flex flex-col gap-2 mt-1">' +
      '<div class="text-[12px] text-on-surface-variant p-3 text-center">불러오는 중...</div>' +
    '</div>' +
    '<div class="border border-black/15 rounded-[8px] p-3 bg-surface flex flex-col gap-2">' +
      '<div class="text-[12px] font-semibold text-on-surface">새 시크릿 저장</div>' +
      '<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">' +
        '<input id="secrets-new-name" type="text" placeholder="이름 (대문자·숫자·밑줄)" autocomplete="off" autocapitalize="characters" spellcheck="false" class="bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />' +
        '<input id="secrets-new-purpose" type="text" placeholder="용도 (선택)" autocomplete="off" class="bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] text-on-surface outline-none focus:border-black" />' +
      '</div>' +
      '<div class="relative flex items-center">' +
        '<input id="secrets-new-value" type="password" placeholder="값 (입력 후 즉시 전송·삭제)" autocomplete="new-password" autocapitalize="off" spellcheck="false" class="w-full bg-surface-container-lowest border border-black/15 rounded-[6px] pl-2.5 pr-8 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />' +
        '<button id="secrets-new-value-toggle" type="button" class="absolute right-2 text-on-surface-variant hover:text-on-surface cursor-pointer"><span class="material-symbols-outlined text-[16px]">visibility</span></button>' +
      '</div>' +
      '<div class="flex items-center justify-end gap-2">' +
        '<span id="secrets-new-status" class="text-[11px] text-on-surface-variant mr-auto" role="status"></span>' +
        '<button id="secrets-new-save" type="button" class="px-4 py-1 bg-primary text-on-primary rounded-[6px] text-[12px] font-medium hover:bg-black/80 cursor-pointer">저장</button>' +
      '</div>' +
    '</div>';
  scroll.appendChild(sec);

  sec.querySelector('#secrets-refresh-btn')?.addEventListener('click', () => loadSecretsList());
  const valInput = sec.querySelector('#secrets-new-value');
  sec.querySelector('#secrets-new-value-toggle')?.addEventListener('click', (e) => {
    const btn = e.currentTarget;
    const isPass = valInput.type === 'password';
    valInput.type = isPass ? 'text' : 'password';
    btn.querySelector('span').textContent = isPass ? 'visibility_off' : 'visibility';
  });
  const doSave = async () => {
    const nameEl = sec.querySelector('#secrets-new-name');
    const purposeEl = sec.querySelector('#secrets-new-purpose');
    const statusEl = sec.querySelector('#secrets-new-status');
    const saveBtn = sec.querySelector('#secrets-new-save');
    const rawName = (nameEl.value || '').trim();
    // 값은 지역 변수에만 두고 전송 즉시 clear + null 처리
    let pendingValue = takeAndClear(valInput);
    if (!rawName) { statusEl.textContent = '이름을 입력해 주세요.'; pendingValue = null; return; }
    if (!pendingValue || !pendingValue.trim()) { statusEl.textContent = '값을 입력해 주세요.'; pendingValue = null; return; }
    const purpose = (purposeEl.value || '').trim();
    saveBtn.disabled = true;
    statusEl.textContent = '저장 중...';
    try {
      await _st.api.setSecret({ name: rawName, value: pendingValue, purpose, scope: 'all' });
      pendingValue = null;
      nameEl.value = '';
      purposeEl.value = '';
      statusEl.textContent = '저장 완료.';
      await loadSecretsList();
    } catch (err) {
      pendingValue = null;
      statusEl.textContent = '저장 실패: ' + (err && err.message ? err.message : '오류');
    } finally {
      saveBtn.disabled = false;
    }
  };
  sec.querySelector('#secrets-new-save')?.addEventListener('click', doSave);
  valInput?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); doSave(); }
  });
}

async function loadSecretsList() {
  const listEl = document.getElementById('secrets-settings-list');
  if (!listEl || !_st.api) return;
  try {
    const secrets = await _st.api.getSecrets();
    if (!Array.isArray(secrets) || secrets.length === 0) {
      listEl.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">저장된 시크릿이 없습니다.</div>';
      return;
    }
    // innerHTML에 들어가는 것은 이름·용도·범위만 (값 필드가 와도 절대 사용하지 않음)
    listEl.innerHTML = secrets.map((s) => {
      const nm = s && s.name ? String(s.name) : '';
      const pp = s && s.purpose ? String(s.purpose) : '';
      const sc = s && ('scope' in s) ? s.scope : 'all';
      return (
        '<div class="border border-black/10 rounded-[8px] p-3 bg-surface flex items-center justify-between gap-2 hover:border-black/25 transition-colors" data-secret-name="' + esc(nm) + '">' +
          '<div class="flex flex-col gap-0.5 truncate">' +
            '<div class="flex items-center gap-2">' +
              '<span class="font-label-md font-semibold text-[13px] font-code text-on-surface">' + esc(nm) + '</span>' +
              '<span class="text-[10px] font-code px-1.5 py-0.5 rounded bg-black/[0.05] text-on-surface-variant">' + fmtScope(sc) + '</span>' +
            '</div>' +
            (pp ? '<div class="text-[11px] text-on-surface-variant truncate">' + esc(pp) + '</div>' : '') +
          '</div>' +
          '<div class="flex items-center gap-1.5 shrink-0">' +
            '<button type="button" class="secret-scope-btn px-2 py-1 text-[11px] border border-black/15 rounded-[6px] text-on-surface hover:bg-black/5 cursor-pointer" data-name="' + esc(nm) + '" title="이 세션에 허용">허용</button>' +
            '<button type="button" class="secret-delete-btn px-2 py-1 text-[11px] border border-black/15 text-rose-600 rounded-[6px] hover:bg-rose-50 cursor-pointer" data-name="' + esc(nm) + '">삭제</button>' +
          '</div>' +
        '</div>'
      );
    }).join('');

    listEl.querySelectorAll('.secret-delete-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const nm = btn.getAttribute('data-name');
        if (!nm) return;
        const ok = window.daonConfirm
          ? await window.daonConfirm(`'${nm}' 시크릿을 삭제하시겠습니까?`, { title: '시크릿 삭제', confirmText: '삭제', danger: true })
          : confirm(`'${nm}' 시크릿을 삭제하시겠습니까?`);
        if (!ok) return;
        btn.disabled = true;
        try {
          await _st.api.deleteSecret(nm);
          await loadSecretsList();
        } catch (err) {
          alert('삭제 실패: ' + (err && err.message ? err.message : '오류'));
          btn.disabled = false;
        }
      });
    });
    listEl.querySelectorAll('.secret-scope-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const nm = btn.getAttribute('data-name');
        const sid = currentSessionId();
        if (!nm || !sid) {
          alert(sid ? '시크릿 이름이 없습니다.' : '활성 세션이 없어 허용할 수 없습니다.');
          return;
        }
        btn.disabled = true;
        try {
          await _st.api.allowSecret(nm, sid);
          await loadSecretsList();
        } catch (err) {
          alert('허용 실패: ' + (err && err.message ? err.message : '오류'));
        } finally {
          btn.disabled = false;
        }
      });
    });
  } catch (_) {
    listEl.innerHTML = '<div class="text-[12px] text-on-surface-variant p-3 text-center">시크릿 목록을 불러오지 못했습니다.</div>';
  }
}

// ── 폴링 + SSE ────────────────────────────────────────────────────────────

async function pollPending() {
  if (_st.polling || !_st.api) return;
  _st.polling = true;
  try {
    const pending = await _st.api.getSecretsPending(currentSessionId());
    renderPendingCards(pending);
  } catch (_) {
    // 폴링 실패는 조용히 무시 (다음 틱에 재시도)
  } finally {
    _st.polling = false;
  }
}

function startPolling() {
  if (_st.timer) return;
  // plugins.js 컨벤션: 5초 간격
  _st.timer = setInterval(pollPending, 5000);
}

async function refreshAll() {
  await pollPending();
  try { await loadSecretsList(); } catch (_) {}
}

export function initSecretsUI(api, opts = {}) {
  if (!api) return;
  _st.api = api;
  if (typeof opts.getSessionId === 'function') _st.getSessionId = opts.getSessionId;

  ensureSettingsSection();
  // Settings 모달이 늦게 열려도 섹션이 주입되도록 open 버튼에 훅
  document.getElementById('sidebar-settings-btn')?.addEventListener('click', () => {
    ensureSettingsSection();
    loadSecretsList();
  });

  // SSE push 수신 → 즉시 폴링으로 카드 렌더 (detail에 값이 와도 절대 사용하지 않음)
  window.addEventListener('daon:secret-request', () => { pollPending(); });

  startPolling();
  pollPending();
  if (!_st.settingsLoaded) {
    _st.settingsLoaded = true;
    loadSecretsList();
  }

  window.DaonSecrets = {
    refresh: refreshAll,
    pollPending,
    loadList: loadSecretsList,
  };
}

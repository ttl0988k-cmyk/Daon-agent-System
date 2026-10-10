import re
from pathlib import Path

app_js_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")

with open(app_js_path, "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add STATIC_PERSONAS and getPersonaIcon near the top (before loadSessions or after state)
persona_constants = """
// ── Static Agent Personas (토니, 빌, 셜록, 프라다, 라온 / 다온응대 제외) ───────────
const STATIC_PERSONAS = [
  { id: 'raon', name: '라온', icon: '🤖', role: '종합 오케스트레이터', desc: '전체 작업 조율, 멀티 에이전트 총괄 및 완주 관리' },
  { id: '토니(기획)', name: '토니', icon: '💡', role: '기획·설계·전략', desc: '프로젝트 기획, 아키텍처 설계, 비즈니스 전략' },
  { id: '빌(개발)', name: '빌', icon: '🔨', role: '풀스택·백엔드·API 구현', desc: '시스템 구현, 코드 작성, 풀스택 개발 및 서버 구축' },
  { id: '셜록(검수)', name: '셜록', icon: '🔍', role: '코드리뷰·QA·검수·디버깅', desc: '품질 검수, 디버깅, 테스트, 코드 취약점 분석' },
  { id: '프라다(디자인)', name: '프라다', icon: '🎨', role: 'UI·UX·아트 디렉터', desc: 'UI/UX 디자인, 인터페이스 감성 설계, 프론트 스타일링' }
];

function getPersonaIcon(pName) {
  if (!pName) return '🤖';
  const n = String(pName).toLowerCase();
  if (n.includes('tony') || n.includes('토니')) return '💡';
  if (n.includes('bill') || n.includes('빌')) return '🔨';
  if (n.includes('sherlock') || n.includes('셜록')) return '🔍';
  if (n.includes('prada') || n.includes('프라다')) return '🎨';
  return '🤖';
}
"""

if "const STATIC_PERSONAS =" not in code:
    code = code.replace("// Global UI State", persona_constants + "\n// Global UI State")
    print("STATIC_PERSONAS added.")

# 2. Update loadSessions to display agent icon and pass currentAgentPersona on delete/create
old_load_sessions = """async function loadSessions() {
  const container = document.getElementById('recent-threads-list');
  if (!container) return;

  // Bind Delete All button once
  const delAllBtn = document.getElementById('delete-all-sessions-btn');
  if (delAllBtn && !delAllBtn._bound) {
    delAllBtn._bound = true;
    delAllBtn.addEventListener('click', async () => {
      if (confirm('모든 대화 세션을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.')) {
        await DaonAPI.deleteAllSessions();
        state.currentSessionId = null;
        renderSessionMessages([]);
        const res = await DaonAPI.createSession('새 세션');
        const sid = res?.session_id || res?.session?.session_id;
        state.currentSessionId = sid;
        await loadSessions();
      }
    });
  }

  try {
    const sessions = await DaonAPI.getSessions();
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="px-space-sm text-[12px] text-on-surface-variant/60">진행된 세션이 없습니다.</div>';
      return;
    }

    container.innerHTML = '';
    sessions.slice(0, 20).forEach((sess) => {
      const a = document.createElement('a');
      a.className = `group h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm transition-colors cursor-pointer ${
        sess.session_id === state.currentSessionId ? 'bg-black/[0.06] text-on-surface font-medium' : 'text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface'
      }`;
      a.title = sess.title || '세션';
      
      const span = document.createElement('span');
      span.className = 'truncate flex-1 pr-1';
      span.textContent = sess.title || `세션 #${sess.session_id.slice(0, 6)}`;
      a.appendChild(span);

      // Trash button for individual session delete — 상시 노출 & 누르기 쉬운 크기
      const delBtn = document.createElement('button');
      delBtn.className = 'w-6 h-6 flex items-center justify-center rounded-[6px] text-on-surface-variant/50 hover:text-red-600 hover:bg-red-500/10 active:scale-95 transition-all shrink-0 ml-1 cursor-pointer';
      delBtn.title = '이 세션 삭제';
      delBtn.type = 'button';
      delBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">delete</span>';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (confirm(`'${sess.title || '선택한 세션'}'을(를) 삭제하시겠습니까?`)) {
          await DaonAPI.deleteSession(sess.session_id);
          if (state.currentSessionId === sess.session_id) {
            state.currentSessionId = null;
          }
          await loadSessions();
        }
      });
      a.appendChild(delBtn);

      a.addEventListener('click', async () => {
        await switchSession(sess.session_id);
      });

      container.appendChild(a);
    });

    // Select the first session if none selected
    if (!state.currentSessionId && sessions.length > 0) {
      await switchSession(sessions[0].session_id);
    }
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}"""

new_load_sessions = """async function loadSessions() {
  const container = document.getElementById('recent-threads-list');
  if (!container) return;

  // Bind Delete All button once
  const delAllBtn = document.getElementById('delete-all-sessions-btn');
  if (delAllBtn && !delAllBtn._bound) {
    delAllBtn._bound = true;
    delAllBtn.addEventListener('click', async () => {
      if (confirm('모든 대화 세션을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.')) {
        await DaonAPI.deleteAllSessions();
        state.currentSessionId = null;
        renderSessionMessages([]);
        const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
        const sid = res?.session_id || res?.session?.session_id;
        state.currentSessionId = sid;
        await loadSessions();
      }
    });
  }

  try {
    const sessions = await DaonAPI.getSessions();
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="px-space-sm text-[12px] text-on-surface-variant/60">진행된 세션이 없습니다.</div>';
      return;
    }

    container.innerHTML = '';
    sessions.slice(0, 20).forEach((sess) => {
      const a = document.createElement('a');
      a.className = `group h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm transition-colors cursor-pointer ${
        sess.session_id === state.currentSessionId ? 'bg-black/[0.06] text-on-surface font-medium' : 'text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface'
      }`;
      a.title = sess.title || '세션';
      
      const span = document.createElement('span');
      span.className = 'truncate flex-1 pr-1 flex items-center gap-1.5';
      const pIcon = getPersonaIcon(sess.profile);
      span.innerHTML = `<span class="shrink-0 text-[12px] opacity-75" title="${escapeHtml(sess.profile || '라온')}">${pIcon}</span><span class="truncate">${escapeHtml(sess.title || `세션 #${sess.session_id.slice(0, 6)}`)}</span>`;
      a.appendChild(span);

      // Trash button for individual session delete
      const delBtn = document.createElement('button');
      delBtn.className = 'w-6 h-6 flex items-center justify-center rounded-[6px] text-on-surface-variant/50 hover:text-red-600 hover:bg-red-500/10 active:scale-95 transition-all shrink-0 ml-1 cursor-pointer';
      delBtn.title = '이 세션 삭제';
      delBtn.type = 'button';
      delBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">delete</span>';
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (confirm(`'${sess.title || '선택한 세션'}'을(를) 삭제하시겠습니까?`)) {
          await DaonAPI.deleteSession(sess.session_id);
          if (state.currentSessionId === sess.session_id) {
            state.currentSessionId = null;
          }
          await loadSessions();
        }
      });
      a.appendChild(delBtn);

      a.addEventListener('click', async () => {
        await switchSession(sess.session_id);
      });

      container.appendChild(a);
    });

    // Select the first session if none selected
    if (!state.currentSessionId && sessions.length > 0) {
      await switchSession(sessions[0].session_id);
    }
  } catch (err) {
    console.error('Failed to load sessions:', err);
  }
}"""

if old_load_sessions in code:
    code = code.replace(old_load_sessions, new_load_sessions)
    print("loadSessions updated.")
else:
    print("WARNING: old_load_sessions not matched.")

# 3. Update switchSession to sync persona
old_switch_session = """async function switchSession(sessionId) {
  state.currentSessionId = sessionId;
  window.activateTab('chat-session');

  // Update session ID in thread meta
  const sessionLabel = document.getElementById('session-meta-id');
  if (sessionLabel) {
    sessionLabel.textContent = `SESSION #${sessionId.toUpperCase().slice(0, 8)}`;
  }

  // Refresh active sidebar highlighting
  const container = document.getElementById('recent-threads-list');
  if (container) {
    Array.from(container.children).forEach(child => {
      child.className = 'h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface transition-colors truncate cursor-pointer';
    });
  }

  // Load messages
  try {
    const sess = await DaonAPI.getSession(sessionId);
    if (!sess) return;
    state.currentSessionModel = sess.model;
    const currentModelLabel = document.getElementById('current-model-label');
    if (currentModelLabel && sess.model) {
      currentModelLabel.textContent = typeof sess.model === 'string' ? sess.model : (sess.model.id || 'deepseek-v4.1-flash');
    }
    renderSessionMessages(sess.messages || []);
  } catch (err) {
    console.error('Failed to load session messages:', err);
  }
}"""

new_switch_session = """async function switchSession(sessionId) {
  state.currentSessionId = sessionId;
  window.activateTab('chat-session');

  // Update session ID in thread meta
  const sessionLabel = document.getElementById('session-meta-id');
  if (sessionLabel) {
    sessionLabel.textContent = `SESSION #${sessionId.toUpperCase().slice(0, 8)}`;
  }

  // Refresh active sidebar highlighting
  const container = document.getElementById('recent-threads-list');
  if (container) {
    Array.from(container.children).forEach(child => {
      child.className = 'h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface transition-colors truncate cursor-pointer';
    });
  }

  // Load messages & sync profile
  try {
    const sess = await DaonAPI.getSession(sessionId);
    if (!sess) return;
    state.currentSessionModel = sess.model;
    const currentModelLabel = document.getElementById('current-model-label');
    if (currentModelLabel && sess.model) {
      currentModelLabel.textContent = typeof sess.model === 'string' ? sess.model : (sess.model.id || 'deepseek-v4.1-flash');
    }

    // Sync agent persona from session
    if (sess.profile) {
      let prof = sess.profile;
      if (prof.includes('다온') || prof.toLowerCase().includes('daon')) {
        prof = 'raon';
      }
      state.currentAgentPersona = prof;
      localStorage.setItem('daon_active_persona', prof);
      const personaSelect = document.getElementById('agent-persona-select');
      if (personaSelect && personaSelect.querySelector(`option[value="${prof}"]`)) {
        personaSelect.value = prof;
      }
      DaonAPI.switchProfile(prof).catch(() => {});
    }

    renderSessionMessages(sess.messages || []);
  } catch (err) {
    console.error('Failed to load session messages:', err);
  }
}"""

if old_switch_session in code:
    code = code.replace(old_switch_session, new_switch_session)
    print("switchSession updated.")
else:
    print("WARNING: old_switch_session not matched.")

# 4. Update newSessionBtns to pass persona
old_new_session_btns = """  newSessionBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const res = await DaonAPI.createSession('새 세션');
        const sid = res?.session_id || res?.session?.session_id;
        if (sid) {
          await loadSessions();
          await switchSession(sid);
          input?.focus();
        }
      } catch (err) {
        alert('세션 생성 실패: ' + err.message);
      }
    });
  });

  // Global shortcut: Cmd/Ctrl + N
  window.addEventListener('keydown', async (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'n' && !e.shiftKey) {
      e.preventDefault();
      const res = await DaonAPI.createSession('새 세션');
      const sid = res?.session_id || res?.session?.session_id;
      if (sid) {
        await loadSessions();
        await switchSession(sid);
      }
    }
  });"""

new_new_session_btns = """  newSessionBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
        const sid = res?.session_id || res?.session?.session_id;
        if (sid) {
          await loadSessions();
          await switchSession(sid);
          input?.focus();
        }
      } catch (err) {
        alert('세션 생성 실패: ' + err.message);
      }
    });
  });

  // Global shortcut: Cmd/Ctrl + N
  window.addEventListener('keydown', async (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'n' && !e.shiftKey) {
      e.preventDefault();
      const res = await DaonAPI.createSession('새 세션', state.currentAgentPersona || 'raon');
      const sid = res?.session_id || res?.session?.session_id;
      if (sid) {
        await loadSessions();
        await switchSession(sid);
      }
    }
  });"""

if old_new_session_btns in code:
    code = code.replace(old_new_session_btns, new_new_session_btns)
    print("newSessionBtns updated.")
else:
    print("WARNING: old_new_session_btns not matched.")

# 5. Replace initAgentPersonas with full-featured version
old_init_personas_pattern = re.compile(r"function initAgentPersonas\(\)\s*\{[\s\S]*?loadCustomPersonas\(\);\s*\}", re.MULTILINE)

new_init_personas = """async function initAgentPersonas() {
  const select = document.getElementById('agent-persona-select');
  const modal = document.getElementById('new-agent-modal');
  const closeBtn = document.getElementById('new-agent-modal-close');
  const cancelBtn = document.getElementById('new-agent-modal-cancel');
  const saveBtn = document.getElementById('new-agent-modal-save');
  const nameInput = document.getElementById('new-agent-name');
  const iconInput = document.getElementById('new-agent-icon');
  const promptInput = document.getElementById('new-agent-prompt');
  if (!select) return;

  async function refreshPersonaOptions() {
    // 1) Fetch profiles from backend
    let serverProfiles = [];
    try {
      const res = await DaonAPI.getProfiles();
      serverProfiles = (res && res.profiles) ? res.profiles : [];
    } catch (_) {}

    // 필터링: 다온응대는 대표님 지침에 따라 철저히 배제
    serverProfiles = serverProfiles.filter(p => {
      const n = (p.name || '').toLowerCase();
      return !n.includes('다온') && !n.includes('daon');
    });

    // 2) Get custom personas from localStorage
    let customPersonas = [];
    try {
      customPersonas = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
    } catch (_) {}

    // 3) Rebuild select options
    select.innerHTML = '';

    // A. 정적 에이전트 5인방 (라온, 토니, 빌, 셜록, 프라다)
    STATIC_PERSONAS.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.icon} ${p.name} (${p.role})`;
      select.appendChild(opt);
    });

    // B. 서버에 등록된 추가 프로필 (정적 5인방에 없는 다른 프로필이 있을 때)
    const staticIds = new Set(STATIC_PERSONAS.map(p => p.id));
    serverProfiles.forEach(p => {
      const pName = p.name;
      if (!staticIds.has(pName) && !pName.includes('다온') && !pName.toLowerCase().includes('daon')) {
        const opt = document.createElement('option');
        opt.value = pName;
        opt.textContent = `${getPersonaIcon(pName)} ${pName}`;
        select.appendChild(opt);
      }
    });

    // C. 커스텀 로컬 페르소나
    customPersonas.forEach(p => {
      if (!select.querySelector(`option[value="${p.id}"]`)) {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.icon || '⚡'} ${p.name}`;
        select.appendChild(opt);
      }
    });

    // D. 새 에이전트 생성 옵션
    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '➕ 새 에이전트 생성...';
    select.appendChild(newOpt);

    // E. 현재 활성 페르소나 복원
    let active = state.currentAgentPersona || localStorage.getItem('daon_active_persona') || 'raon';
    if (active.includes('다온') || active.toLowerCase().includes('daon')) active = 'raon';
    state.currentAgentPersona = active;
    localStorage.setItem('daon_active_persona', active);

    if (select.querySelector(`option[value="${active}"]`)) {
      select.value = active;
    } else {
      select.value = 'raon';
      state.currentAgentPersona = 'raon';
    }

    // 서버 활성 프로필과 일치시키기
    try {
      await DaonAPI.switchProfile(state.currentAgentPersona);
    } catch (_) {}
  }

  // Handle select change
  select.addEventListener('change', async () => {
    const val = select.value;
    if (val === '__new__') {
      select.value = state.currentAgentPersona || 'raon';
      openNewAgentModal();
      return;
    }

    state.currentAgentPersona = val;
    localStorage.setItem('daon_active_persona', val);

    // 1) 백엔드 프로필 전환
    try {
      await DaonAPI.switchProfile(val);
    } catch (err) {
      console.warn('Profile switch failed:', err);
    }

    // 2) 현재 세션의 프로필도 갱신
    if (state.currentSessionId) {
      try {
        await DaonAPI.updateSession(state.currentSessionId, { profile: val });
        await loadSessions();
      } catch (_) {}
    }

    // 3) 토스트 피드백
    showPersonaToast(val);
  });

  function openNewAgentModal() {
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (nameInput) nameInput.value = '';
    if (iconInput) iconInput.value = '⚡';
    if (promptInput) promptInput.value = '';
  }

  function closeNewAgentModal() {
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  if (closeBtn) closeBtn.addEventListener('click', closeNewAgentModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeNewAgentModal);

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const name = (nameInput?.value || '').trim();
      const icon = (iconInput?.value || '⚡').trim();
      const prompt = (promptInput?.value || '').trim();
      if (!name) { alert('에이전트 이름을 입력해주세요.'); return; }
      if (name.includes('다온') || name.toLowerCase().includes('daon')) {
        alert('다온응대 명칭은 사용할 수 없습니다.');
        return;
      }

      saveBtn.disabled = true;
      saveBtn.textContent = '생성 중...';

      try {
        try {
          await DaonAPI.createProfile(name);
        } catch (_) {}

        const id = name;
        const newPersona = { id, name, icon, prompt };
        const saved = JSON.parse(localStorage.getItem('daon_custom_personas') || '[]');
        saved.push(newPersona);
        localStorage.setItem('daon_custom_personas', JSON.stringify(saved));

        await refreshPersonaOptions();
        select.value = id;
        state.currentAgentPersona = id;
        localStorage.setItem('daon_active_persona', id);
        try { await DaonAPI.switchProfile(id); } catch (_) {}

        if (state.currentSessionId) {
          try {
            await DaonAPI.updateSession(state.currentSessionId, { profile: id });
            await loadSessions();
          } catch (_) {}
        }

        closeNewAgentModal();
        showPersonaToast(id);
      } catch (err) {
        alert('에이전트 생성 실패: ' + err.message);
      } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = '<span class="material-symbols-outlined text-[15px]">add</span><span>에이전트 등록</span>';
      }
    });
  }

  function showPersonaToast(personaId) {
    let pName = personaId;
    let pIcon = getPersonaIcon(personaId);
    const match = STATIC_PERSONAS.find(p => p.id === personaId);
    if (match) {
      pName = `${match.name} (${match.role})`;
      pIcon = match.icon;
    }

    const toast = document.createElement('div');
    toast.className = 'fixed bottom-5 right-5 bg-surface-container-highest border border-black/15 shadow-xl rounded-[10px] px-4 py-2.5 flex items-center gap-2.5 text-[13px] text-on-surface z-50 animate-in fade-in slide-in-from-bottom-3 duration-200';
    toast.innerHTML = `<span class="text-[18px]">${pIcon}</span><div class="flex flex-col"><span class="font-medium">에이전트 전환 완료</span><span class="text-[11px] text-on-surface-variant">${escapeHtml(pName)} 역할로 대화합니다.</span></div>`;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('opacity-0', 'transition-opacity');
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  await refreshPersonaOptions();
}"""

if old_init_personas_pattern.search(code):
    code = old_init_personas_pattern.sub(new_init_personas, code)
    print("initAgentPersonas replaced.")
else:
    print("WARNING: initAgentPersonas pattern not matched.")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(code)

print("app.js updated successfully!")

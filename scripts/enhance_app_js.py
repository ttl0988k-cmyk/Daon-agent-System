#!/usr/bin/env python3
"""
Enhance static/v2/js/app.js with:
1. Mobile responsive sidebar drawer (open/close/backdrop/auto-close on tab click)
2. Session delete (individual trash button on each thread + delete all button)
3. Provider & Model slide drawer above user profile
4. Attachment handling (file/image/video upload, chips preview, and chat display)
5. Reasoning effort verification and display
"""

import sys
from pathlib import Path

app_path = Path(r"c:\daon\Daon agent System\static\v2\js\app.js")
code = app_path.read_text(encoding="utf-8")

# 1. State additions
if 'pendingFiles: []' not in code:
    code = code.replace(
        "const state = {\n  activeTab: 'chat-session',",
        "const state = {\n  activeTab: 'chat-session',\n  pendingFiles: [],"
    )

# 2. Add Mobile Sidebar, Provider Drawer, Attachments initializers in DOMContentLoaded
if 'initMobileSidebar();' not in code:
    code = code.replace(
        "document.addEventListener('DOMContentLoaded', async () => {\n  initTabs();",
        "document.addEventListener('DOMContentLoaded', async () => {\n  initTabs();\n  initMobileSidebar();\n  initAttachments();\n  initProviderDrawer();"
    )

# 3. Enhance resolveModelString to prioritize user-selected provider model
old_resolve = """function resolveModelString(cardModel, sessionModel) {
  if (cardModel === 'flash') return 'glm-5.3-flash';
  if (cardModel === 'reasoning') return 'deepseek/deepseek-r1';

  if (typeof sessionModel === 'string' && sessionModel.trim()) {
    return sessionModel.trim();
  }
  if (typeof sessionModel === 'object' && sessionModel) {
    if (sessionModel.default) return sessionModel.default;
    if (sessionModel.id) return sessionModel.id;
    if (sessionModel.model) return sessionModel.model;
  }
  return 'deepseek/deepseek-v4.1-flash';
}"""

new_resolve = """function resolveModelString(cardModel, sessionModel) {
  if (state.currentSessionModel && typeof state.currentSessionModel === 'string' && state.currentSessionModel.trim()) {
    return state.currentSessionModel.trim();
  }
  if (typeof sessionModel === 'string' && sessionModel.trim()) {
    return sessionModel.trim();
  }
  if (cardModel === 'flash') return 'glm-5.3-flash';
  if (cardModel === 'reasoning') return 'deepseek/deepseek-r1';

  if (typeof sessionModel === 'object' && sessionModel) {
    if (sessionModel.default) return sessionModel.default;
    if (sessionModel.id) return sessionModel.id;
    if (sessionModel.model) return sessionModel.model;
  }
  return 'deepseek/deepseek-v4.1-flash';
}"""

if old_resolve in code:
    code = code.replace(old_resolve, new_resolve)

# 4. Enhance loadSessions() to include individual delete button and delete all listener
old_load_sessions = """async function loadSessions() {
  const container = document.getElementById('recent-threads-list');
  if (!container) return;

  try {
    const sessions = await DaonAPI.getSessions();
    if (!sessions || sessions.length === 0) {
      container.innerHTML = '<div class="px-space-sm text-[12px] text-on-surface-variant/60">진행된 세션이 없습니다.</div>';
      return;
    }

    container.innerHTML = '';
    sessions.slice(0, 15).forEach((sess, idx) => {
      const a = document.createElement('a');
      a.className = `h-[32px] flex items-center justify-between px-space-sm rounded-[8px] font-body-sm text-body-sm transition-colors truncate cursor-pointer ${
        sess.session_id === state.currentSessionId ? 'bg-black/[0.06] text-on-surface font-medium' : 'text-on-surface-variant hover:bg-black/[0.04] hover:text-on-surface'
      }`;
      a.title = sess.title || '세션';
      
      const span = document.createElement('span');
      span.className = 'truncate';
      span.textContent = sess.title || `세션 #${sess.session_id.slice(0, 6)}`;
      a.appendChild(span);

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

      // Trash button for individual session delete
      const delBtn = document.createElement('button');
      delBtn.className = 'opacity-0 group-hover:opacity-100 p-0.5 hover:text-red-600 transition-opacity flex items-center justify-center shrink-0 rounded cursor-pointer';
      delBtn.title = '이 세션 삭제';
      delBtn.type = 'button';
      delBtn.innerHTML = '<span class="material-symbols-outlined text-[14px]">delete</span>';
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

# 5. Enhance handleSendMessage() to upload pending files and attach to chat
old_send_msg = """async function handleSendMessage() {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const message = input.value.trim();
  if (!message || state.isStreaming) return;

  // Clear input
  input.value = '';
  input.style.height = 'auto';

  // Ensure session exists
  if (!state.currentSessionId) {
    try {
      const res = await DaonAPI.createSession(message.slice(0, 24));
      state.currentSessionId = res?.session_id || res?.session?.session_id;
      await loadSessions();
    } catch (err) {
      alert('세션 초기화 실패: ' + err.message);
      return;
    }
  }

  // 1. Append user message to UI
  appendUserMessage(message);

  // 2. Prepare streaming assistant bubble
  const { bubble, contentEl, timeEl } = appendAssistantMessage('', null, true);
  state.isStreaming = true;
  let fullResponse = '';

  scrollChatToBottom();

  try {
    const modelToUse = resolveModelString(state.selectedModelCard, state.currentSessionModel);
    const startRes = await DaonAPI.startChat({
      sessionId: state.currentSessionId,
      message,
      model: modelToUse,
      reasoningEffort: state.reasoningEffort
    });"""

new_send_msg = """async function handleSendMessage() {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const message = input.value.trim();
  const hasFiles = state.pendingFiles && state.pendingFiles.length > 0;
  if ((!message && !hasFiles) || state.isStreaming) return;

  // Clear input
  input.value = '';
  input.style.height = 'auto';

  // Ensure session exists
  if (!state.currentSessionId) {
    try {
      const titlePrompt = message ? message.slice(0, 24) : (hasFiles ? state.pendingFiles[0].name : '새 세션');
      const res = await DaonAPI.createSession(titlePrompt);
      state.currentSessionId = res?.session_id || res?.session?.session_id;
      await loadSessions();
    } catch (err) {
      alert('세션 초기화 실패: ' + err.message);
      return;
    }
  }

  // Upload pending attachments (images, videos, files)
  let uploadedNames = [];
  if (hasFiles) {
    const filesToUpload = [...state.pendingFiles];
    state.pendingFiles = [];
    const tray = document.getElementById('chat-attach-tray');
    if (tray) {
      tray.innerHTML = '<span class="text-[11px] text-on-surface-variant font-mono">파일 업로드 중...</span>';
    }
    for (const f of filesToUpload) {
      try {
        const upRes = await DaonAPI.uploadFile(state.currentSessionId, f);
        if (upRes && upRes.filename) {
          uploadedNames.push(upRes.filename);
        }
      } catch (upErr) {
        console.error('File upload failed:', f.name, upErr);
      }
    }
    if (tray) {
      tray.innerHTML = '';
      tray.classList.add('hidden');
    }
  }

  // Compose display & prompt message
  let finalMessage = message;
  if (uploadedNames.length > 0) {
    if (!finalMessage) {
      finalMessage = `[첨부 파일: ${uploadedNames.join(', ')}]`;
    } else {
      finalMessage = `${message}\\n\\n[첨부 파일: ${uploadedNames.join(', ')}]`;
    }
  }

  // 1. Append user message to UI
  appendUserMessage(finalMessage);

  // 2. Prepare streaming assistant bubble
  const { bubble, contentEl, timeEl } = appendAssistantMessage('', null, true);
  state.isStreaming = true;
  let fullResponse = '';

  scrollChatToBottom();

  try {
    const modelToUse = resolveModelString(state.selectedModelCard, state.currentSessionModel);
    const startRes = await DaonAPI.startChat({
      sessionId: state.currentSessionId,
      message: finalMessage,
      model: modelToUse,
      attachments: uploadedNames,
      reasoningEffort: state.reasoningEffort
    });"""

if old_send_msg in code:
    code = code.replace(old_send_msg, new_send_msg)

# 6. Add renderUserMessageContent and update appendUserMessage
old_append_user = """function appendUserMessage(text, timestamp) {
  const container = document.getElementById('chat-messages-container');
  if (!container) return;

  const article = document.createElement('article');
  article.className = 'flex flex-col gap-space-sm w-full animate-fadeIn';
  article.innerHTML = `
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-space-sm">
        <div class="w-6 h-6 rounded-full bg-primary flex items-center justify-center text-on-primary font-label-sm text-[11px] font-semibold">
          사
        </div>
        <span class="font-label-md text-label-md text-on-surface font-medium">사용자</span>
      </div>
      <span class="font-code text-code text-[11px] text-on-surface-variant">${formatTime(timestamp)}</span>
    </div>
    <div class="pl-8 text-on-surface font-body-lg text-body-lg leading-relaxed whitespace-pre-wrap">${escapeHtml(text)}</div>
  `;
  container.appendChild(article);
  scrollChatToBottom();
}"""

new_append_user = """function renderUserMessageContent(content, sessionId) {
  if (!content) return '';
  let escaped = escapeHtml(content);

  // Parse attached file marker: [첨부 파일: a.png, b.mp4] or [Attached files: ...]
  const regex = /\\[(?:첨부 파일|Attached files):\\s*([^\\]]+)\\]/;
  const match = escaped.match(regex);
  let mediaHtml = '';

  if (match) {
    const rawFiles = match[1].split(',').map(f => f.trim());
    let mediaItems = [];

    rawFiles.forEach(filename => {
      const ext = filename.split('.').pop().toLowerCase();
      const sid = sessionId || state.currentSessionId || '';
      const rawUrl = `/api/file/raw?session_id=${encodeURIComponent(sid)}&path=${encodeURIComponent(filename)}`;

      if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) {
        mediaItems.push(`
          <div class="relative group inline-block my-1">
            <img src="${rawUrl}" class="max-w-[260px] max-h-[220px] rounded-[8px] border border-black/10 object-cover cursor-zoom-in hover:opacity-95 shadow-sm" onclick="window.open('${rawUrl}', '_blank')" alt="${escapeHtml(filename)}" />
            <div class="text-[10px] font-mono text-on-surface-variant truncate max-w-[260px] mt-0.5">${escapeHtml(filename)}</div>
          </div>
        `);
      } else if (['mp4', 'webm', 'mov', 'ogg'].includes(ext)) {
        mediaItems.push(`
          <div class="my-1">
            <video src="${rawUrl}" controls class="max-w-[320px] max-h-[240px] rounded-[8px] border border-black/10 shadow-sm"></video>
            <div class="text-[10px] font-mono text-on-surface-variant truncate max-w-[320px] mt-0.5">${escapeHtml(filename)}</div>
          </div>
        `);
      } else {
        mediaItems.push(`
          <a href="${rawUrl}&download=1" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-surface border border-black/10 rounded-[8px] text-[12px] font-mono text-on-surface hover:bg-black/5 my-1 transition-colors" download="${escapeHtml(filename)}">
            <span class="material-symbols-outlined text-[16px] text-primary">description</span>
            <span class="truncate max-w-[200px]">${escapeHtml(filename)}</span>
            <span class="material-symbols-outlined text-[13px] text-on-surface-variant">download</span>
          </a>
        `);
      }
    });

    if (mediaItems.length > 0) {
      mediaHtml = `<div class="flex flex-wrap gap-2 mt-2 pt-2 border-t border-black/[0.06]">${mediaItems.join('')}</div>`;
    }
  }

  return `<div class="whitespace-pre-wrap">${escaped}</div>${mediaHtml}`;
}

function appendUserMessage(text, timestamp) {
  const container = document.getElementById('chat-messages-container');
  if (!container) return;

  const article = document.createElement('article');
  article.className = 'flex flex-col gap-space-sm w-full animate-fadeIn';
  article.innerHTML = `
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-space-sm">
        <div class="w-6 h-6 rounded-full bg-primary flex items-center justify-center text-on-primary font-label-sm text-[11px] font-semibold">
          사
        </div>
        <span class="font-label-md text-label-md text-on-surface font-medium">사용자</span>
      </div>
      <span class="font-code text-code text-[11px] text-on-surface-variant">${formatTime(timestamp)}</span>
    </div>
    <div class="pl-8 text-on-surface font-body-lg text-body-lg leading-relaxed">${renderUserMessageContent(text, state.currentSessionId)}</div>
  `;
  container.appendChild(article);
  scrollChatToBottom();
}"""

if old_append_user in code:
    code = code.replace(old_append_user, new_append_user)

# 7. Add initMobileSidebar, initAttachments, and initProviderDrawer functions
helper_functions = """
// ── Mobile Responsive Sidebar Drawer ─────────────────────────────────────────

function initMobileSidebar() {
  const toggleBtn = document.getElementById('sidebar-toggle-btn');
  const closeBtn = document.getElementById('sidebar-close-btn');
  const backdrop = document.getElementById('sidebar-backdrop');
  const sidebar = document.getElementById('app-sidebar');

  function openSidebar() {
    if (sidebar) {
      sidebar.classList.remove('-translate-x-full');
      sidebar.classList.add('translate-x-0');
    }
    if (backdrop) {
      backdrop.classList.remove('hidden');
    }
  }

  function closeSidebar() {
    if (sidebar) {
      sidebar.classList.add('-translate-x-full');
      sidebar.classList.remove('translate-x-0');
    }
    if (backdrop) {
      backdrop.classList.add('hidden');
    }
  }

  toggleBtn?.addEventListener('click', openSidebar);
  closeBtn?.addEventListener('click', closeSidebar);
  backdrop?.addEventListener('click', closeSidebar);

  // Auto-close drawer on mobile when clicking any sidebar item
  document.querySelectorAll('[data-tab-target], .new-session-btn, .sidebar-item').forEach(el => {
    el.addEventListener('click', () => {
      if (window.innerWidth < 768) {
        closeSidebar();
      }
    });
  });
}

// ── Attachment Handling (Files, Images, Videos) ───────────────────────────────

function initAttachments() {
  const attachBtn = document.getElementById('chat-attach-btn');
  const fileInput = document.getElementById('chat-file-input');
  const tray = document.getElementById('chat-attach-tray');

  if (!attachBtn || !fileInput || !tray) return;

  attachBtn.addEventListener('click', () => {
    fileInput.click();
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length > 0) {
      Array.from(fileInput.files).forEach(f => state.pendingFiles.push(f));
      fileInput.value = '';
      renderAttachTray();
    }
  });

  function renderAttachTray() {
    tray.innerHTML = '';
    if (state.pendingFiles.length === 0) {
      tray.classList.add('hidden');
      return;
    }
    tray.classList.remove('hidden');

    state.pendingFiles.forEach((f, idx) => {
      const chip = document.createElement('div');
      chip.className = 'flex items-center gap-1.5 px-2 py-1 bg-surface-container border border-black/10 rounded-[6px] text-[11px] font-mono text-on-surface max-w-[200px] truncate';

      let iconHtml = '<span class="material-symbols-outlined text-[14px] text-on-surface-variant shrink-0">description</span>';
      if (f.type.startsWith('image/')) {
        const previewUrl = URL.createObjectURL(f);
        iconHtml = `<img src="${previewUrl}" class="w-4 h-4 rounded object-cover shrink-0" />`;
      } else if (f.type.startsWith('video/')) {
        iconHtml = '<span class="material-symbols-outlined text-[14px] text-primary shrink-0">videocam</span>';
      }

      chip.innerHTML = `
        ${iconHtml}
        <span class="truncate flex-1">${escapeHtml(f.name)}</span>
        <button type="button" class="text-on-surface-variant hover:text-red-600 p-0.5 rounded cursor-pointer remove-file-btn" data-idx="${idx}">
          <span class="material-symbols-outlined text-[13px]">close</span>
        </button>
      `;

      chip.querySelector('.remove-file-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        state.pendingFiles.splice(idx, 1);
        renderAttachTray();
      });

      tray.appendChild(chip);
    });
  }
}

// ── Provider & Model Slide Drawer (Above User Profile) ────────────────────────

let cachedProvidersData = null;

async function initProviderDrawer() {
  const toggleBtn = document.getElementById('provider-drawer-toggle');
  const content = document.getElementById('provider-drawer-content');
  const arrow = document.getElementById('provider-drawer-arrow');
  const pillsList = document.getElementById('provider-pills-list');
  const modelsList = document.getElementById('provider-models-list');
  const currentProviderLabel = document.getElementById('current-provider-label');
  const currentModelLabel = document.getElementById('current-model-label');

  if (!toggleBtn || !content) return;

  toggleBtn.addEventListener('click', () => {
    const isHidden = content.classList.contains('hidden');
    if (isHidden) {
      content.classList.remove('hidden');
      content.classList.add('flex');
      arrow?.classList.add('rotate-180');
    } else {
      content.classList.add('hidden');
      content.classList.remove('flex');
      arrow?.classList.remove('rotate-180');
    }
  });

  try {
    const data = await DaonAPI.getProviders();
    if (!data || !data.providers) return;
    cachedProvidersData = data.providers;

    const providerKeys = Object.keys(data.providers);
    if (providerKeys.length === 0) return;

    let selectedProviderKey = providerKeys.includes('opencode-go') ? 'opencode-go' : providerKeys[0];

    renderProviderPills();
    renderProviderModels(selectedProviderKey);

    function renderProviderPills() {
      if (!pillsList) return;
      pillsList.innerHTML = '';
      providerKeys.forEach(key => {
        const p = cachedProvidersData[key];
        const isSelected = key === selectedProviderKey;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `px-2 py-0.5 rounded-[6px] text-[11px] font-code whitespace-nowrap transition-colors cursor-pointer shrink-0 ${
          isSelected ? 'bg-primary text-on-primary font-medium' : 'bg-surface-container border border-black/10 text-on-surface hover:bg-black/5'
        }`;
        btn.textContent = p.label || key;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          selectedProviderKey = key;
          renderProviderPills();
          renderProviderModels(key);
        });
        pillsList.appendChild(btn);
      });
    }

    function renderProviderModels(pKey) {
      if (!modelsList) return;
      modelsList.innerHTML = '';
      const p = cachedProvidersData[pKey];
      const models = p?.models || [];

      if (models.length === 0) {
        modelsList.innerHTML = '<div class="text-[11px] text-on-surface-variant/60 px-1 py-1">등록된 모델이 없습니다.</div>';
        return;
      }

      models.forEach(m => {
        const isCurrent = (state.currentSessionModel === m.id) || (!state.currentSessionModel && m.id === 'deepseek-v4.1-flash');
        const item = document.createElement('div');
        item.className = `px-2 py-1.5 rounded-[6px] border flex items-center justify-between text-[11px] cursor-pointer transition-colors ${
          isCurrent ? 'bg-surface-container-lowest border-primary font-semibold text-on-surface' : 'bg-surface border-black/10 text-on-surface hover:bg-black/[0.03]'
        }`;
        item.innerHTML = `
          <div class="flex items-center gap-1.5 truncate">
            <span class="material-symbols-outlined text-[13px] ${isCurrent ? 'text-primary' : 'text-on-surface-variant/50'}">
              ${isCurrent ? 'radio_button_checked' : 'radio_button_unchecked'}
            </span>
            <span class="truncate">${escapeHtml(m.label || m.id)}</span>
          </div>
          <span class="px-1 py-[1px] bg-black/[0.05] text-on-surface-variant text-[10px] font-code rounded shrink-0">${m.type || 'chat'}</span>
        `;

        item.addEventListener('click', () => {
          state.currentSessionModel = m.id;
          if (currentModelLabel) currentModelLabel.textContent = m.id;
          if (currentProviderLabel) currentProviderLabel.textContent = `PROVIDER: ${p.label || pKey}`;
          renderProviderModels(pKey);
        });

        modelsList.appendChild(item);
      });
    }

  } catch (err) {
    console.warn('Failed to initialize provider drawer:', err);
  }
}
"""

if 'function initMobileSidebar()' not in code:
    code = code + "\n" + helper_functions

app_path.write_text(code, encoding="utf-8")
print("Successfully enhanced static/v2/js/app.js")

# -*- coding: utf-8 -*-
"""
Perfected Folder Explorer & Drive Navigation Sync:
1. Fix all backslash escaping issues in JS regex.
2. Synchronize workspace-path-input whenever Up button is clicked.
3. Synchronize workspace-path-input whenever drive chip is clicked.
4. Synchronize workspace-path-input whenever folder row or choose button is clicked.
5. Synchronize workspace-path-input whenever loadFolderTree runs.
6. Provide instant drive chips ([ C: ] [ D: ]) and refresh button.
7. Support Enter key in workspace-path-input to navigate.
8. Sync everything to static/v2 and resources/ (Electron build).
"""
import os
import re
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

# 1. Clean up HTML files
explorer_html = """        <!-- Interactive In-Modal Folder Explorer -->
        <div id="workspace-fs-explorer-section" class="flex flex-col gap-2 border border-black/10 rounded-[10px] p-2.5 bg-surface-container-low/60">
          <div class="flex flex-wrap items-center justify-between gap-1.5 px-1">
            <div class="flex items-center gap-1.5 min-w-0">
              <span class="material-symbols-outlined text-[16px] text-primary shrink-0">account_tree</span>
              <span id="fs-current-path-label" class="text-[12px] font-code font-semibold text-on-surface truncate max-w-[200px] sm:max-w-[260px]" title="C:/daon">C:/daon</span>
            </div>
            <div class="flex items-center gap-1.5 shrink-0" id="fs-controls">
              <!-- Drive chips rendered dynamically: e.g. [ C: ] [ D: ] -->
              <div id="fs-drive-chips" class="flex items-center gap-1"></div>
              <button id="fs-up-btn" type="button" class="px-2.5 py-0.5 bg-surface hover:bg-black/[0.05] border border-black/15 rounded-[6px] text-[11px] font-label-md font-medium text-on-surface flex items-center gap-1 cursor-pointer transition-colors" title="상위 폴더로 이동">
                <span class="material-symbols-outlined text-[13px]">arrow_upward</span>
                <span>상위</span>
              </button>
              <button id="fs-refresh-btn" type="button" class="p-1 bg-surface hover:bg-black/[0.05] border border-black/15 rounded-[6px] text-on-surface flex items-center justify-center cursor-pointer transition-colors" title="폴더 목록 새로고침">
                <span class="material-symbols-outlined text-[13px]">refresh</span>
              </button>
            </div>
          </div>
          <div id="fs-folder-list" class="flex flex-col gap-0.5 max-h-[170px] overflow-y-auto border border-black/10 rounded-[8px] p-1 bg-surface-container-lowest">
            <!-- Dynamically populated folder items -->
          </div>
        </div>"""

html_pattern = re.compile(
    r'(?:<div>\s*)?<!-- Interactive In-Modal Folder Explorer -->[\s\S]*?</div>\s*</div>\s*(?=<div>\s*<label[^>]*>최근 사용한 워크스페이스)',
    re.MULTILINE
)

for html_file in [os.path.join(ROOT_DIR, "index.html"), os.path.join(ROOT_DIR, "static", "v2", "index.html")]:
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()

        if html_pattern.search(content):
            content = html_pattern.sub(explorer_html + "\n\n        ", content)
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[HTML] Replaced explorer HTML in {html_file}")
        else:
            print(f"[HTML] Warning: Pattern not found in {html_file}")

# 2. Update app.js
app_js_path = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")
with open(app_js_path, "r", encoding="utf-8") as f:
    app_js = f.read()

js_replacement = """  let currentBrowsePath = state.currentWorkspace || 'C:/daon';
  let currentParentPath = '';
  let availableDrives = ['C:/', 'D:/'];

  function normalizePath(p) {
    if (!p) return '';
    return p.replace(/\\\\/g, '/');
  }

  function getParentPath(p) {
    if (!p) return '';
    const norm = normalizePath(p).replace(/\\/+$/, '');
    const lastSlash = norm.lastIndexOf('/');
    if (lastSlash <= 0) return '';
    const parent = norm.substring(0, lastSlash);
    if (/^[A-Za-z]:$/.test(parent)) return parent + '/';
    return parent;
  }

  async function loadDrives() {
    try {
      const res = await fetch('/api/fs/list?path=');
      if (res.ok) {
        const data = await res.json();
        if (data.drives && data.drives.length > 0) {
          availableDrives = data.drives;
        }
      }
    } catch (_) {}
    renderDriveChips();
  }

  function renderDriveChips() {
    const chipsContainer = document.getElementById('fs-drive-chips');
    if (!chipsContainer) return;
    const curNorm = normalizePath(currentBrowsePath).toUpperCase();

    chipsContainer.innerHTML = availableDrives.map(d => {
      const dNorm = normalizePath(d).toUpperCase();
      const isActive = curNorm.startsWith(dNorm) || curNorm.startsWith(dNorm.replace('/', ''));
      const letter = d.replace(/[:/\\\\]/g, '');
      return `
        <button type="button" class="px-2 py-0.5 rounded-[5px] text-[11px] font-mono font-bold transition-all cursor-pointer ${
          isActive 
            ? 'bg-primary text-on-primary shadow-xs ring-1 ring-primary' 
            : 'bg-surface hover:bg-black/[0.08] text-on-surface border border-black/15'
        } fs-drive-btn" data-drive="${escapeHtml(d)}" title="${escapeHtml(d)} 드라이브로 전환">
          ${letter}:
        </button>
      `;
    }).join('');

    chipsContainer.querySelectorAll('.fs-drive-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const d = btn.getAttribute('data-drive');
        if (d) {
          if (pathInput) pathInput.value = d;
          loadFolderTree(d);
        }
      });
    });
  }

  async function loadFolderTree(folderPath) {
    const folderList = document.getElementById('fs-folder-list');
    const pathLabel = document.getElementById('fs-current-path-label');
    if (!folderList) return;

    folderList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2 text-center flex items-center justify-center gap-1.5"><span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>폴더 목록 불러오는 중...</span></div>';
    
    try {
      const targetQuery = (folderPath !== undefined && folderPath !== null) ? folderPath : currentBrowsePath;
      const res = await fetch(`/api/fs/list?path=${encodeURIComponent(targetQuery || '')}`);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();

      currentBrowsePath = data.current || targetQuery || '';
      currentParentPath = (data.parent !== undefined && data.parent !== '') ? data.parent : getParentPath(currentBrowsePath);

      if (pathLabel) {
        pathLabel.textContent = currentBrowsePath || '내 컴퓨터 (드라이브 선택)';
        pathLabel.title = currentBrowsePath;
      }

      // CRITICAL: Always keep pathInput synchronized with currentBrowsePath!
      if (pathInput && currentBrowsePath) {
        pathInput.value = currentBrowsePath;
      }

      renderDriveChips();

      const entries = (data.entries || []).filter(e => {
        if (e.type !== 'dir' && e.type !== 'drive') return false;
        const n = e.name || '';
        return !n.startsWith('$') && !n.startsWith('.') && n !== 'System Volume Information' && n !== '__pycache__';
      });

      if (entries.length === 0) {
        folderList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2.5 text-center">선택 가능한 하위 폴더가 없습니다.</div>';
        return;
      }

      folderList.innerHTML = entries.map(item => `
        <div class="flex items-center justify-between px-2 py-1.5 rounded-[6px] hover:bg-black/[0.05] cursor-pointer transition-colors fs-folder-item" data-path="${escapeHtml(item.path)}" title="${escapeHtml(item.path)}">
          <div class="flex items-center gap-2 truncate min-w-0">
            <span class="material-symbols-outlined text-[16px] text-amber-600 shrink-0">${item.type === 'drive' ? 'hard_drive' : 'folder'}</span>
            <span class="text-[12px] font-medium text-on-surface truncate">${escapeHtml(item.name)}</span>
          </div>
          <button type="button" class="text-[11px] font-label-sm px-2 py-0.5 rounded bg-surface-container-high hover:bg-primary hover:text-on-primary border border-black/10 transition-colors fs-choose-btn shrink-0" data-path="${escapeHtml(item.path)}">
            선택
          </button>
        </div>
      `).join('');

      folderList.querySelectorAll('.fs-folder-item').forEach(el => {
        el.addEventListener('click', (e) => {
          if (e.target.closest('.fs-choose-btn')) return;
          const p = el.getAttribute('data-path');
          if (p) {
            if (pathInput) pathInput.value = p;
            loadFolderTree(p);
          }
        });
      });

      folderList.querySelectorAll('.fs-choose-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const p = btn.getAttribute('data-path');
          if (p && pathInput) {
            pathInput.value = p;
            if (typeof showVoiceToast === 'function') {
              showVoiceToast('📂 작업 폴더 지정: ' + p, '📂', 2000);
            }
          }
        });
      });

    } catch (err) {
      folderList.innerHTML = `<div class="text-[11px] text-rose-500 p-2 text-center">목록 조회 실패: ${escapeHtml(err.message)}</div>`;
    }
  }

  const upBtn = document.getElementById('fs-up-btn');
  if (upBtn) {
    upBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const parent = currentParentPath || getParentPath(currentBrowsePath);
      // Synchronize path input immediately!
      if (pathInput && parent) {
        pathInput.value = parent;
      }
      loadFolderTree(parent);
    });
  }

  const refreshBtn = document.getElementById('fs-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', (e) => {
      e.preventDefault();
      loadFolderTree(currentBrowsePath);
    });
  }

  if (pathInput) {
    pathInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = (pathInput.value || '').trim();
        if (val) loadFolderTree(val);
      }
    });
  }

  function openModal() {
    const initialPath = (pathInput?.value || state.currentWorkspace || 'C:/daon').trim();
    if (pathInput) pathInput.value = initialPath;
    renderDriveChips();
    loadDrives();
    loadFolderTree(initialPath);
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    loadRecentWorkspaces();
  }"""

# Replace in app.js
target_start = "  let currentBrowsePath = state.currentWorkspace || 'C:/daon';"
target_end = "  function closeModal() {"

if target_start in app_js and target_end in app_js:
    idx1 = app_js.index(target_start)
    idx2 = app_js.index(target_end)
    app_js = app_js[:idx1] + js_replacement + "\n\n" + app_js[idx2:]
    print("[app.js] Replaced entire explorer logic successfully!")
else:
    print("[app.js] Error: target delimiters not found!")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(app_js)

# Bump script version param in HTML
v_param = f"app.js?v=20261009_{hex(int(os.path.getmtime(app_js_path)))[2:]}"
for html_file in [os.path.join(ROOT_DIR, "index.html"), os.path.join(ROOT_DIR, "static", "v2", "index.html")]:
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            h_text = f.read()
        h_text = re.sub(r'app\.js\?v=[a-zA-Z0-9_]+', v_param, h_text)
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(h_text)

# Sync to DEPLOY_DIR (resources)
if os.path.exists(DEPLOY_DIR):
    deploy_index = os.path.join(DEPLOY_DIR, "index.html")
    deploy_v2_index = os.path.join(DEPLOY_DIR, "static", "v2", "index.html")
    deploy_app_js = os.path.join(DEPLOY_DIR, "static", "v2", "js", "app.js")

    src_index = os.path.join(ROOT_DIR, "index.html")
    shutil.copy2(src_index, deploy_index)
    shutil.copy2(src_index, deploy_v2_index)
    shutil.copy2(app_js_path, deploy_app_js)
    print(f"[DEPLOY] Successfully synced to {DEPLOY_DIR}!")

print("[DONE] Everything patched and synced cleanly!")

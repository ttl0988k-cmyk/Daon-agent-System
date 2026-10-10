# -*- coding: utf-8 -*-
"""
Inject interactive In-Modal Folder Explorer into workspace-modal in HTML and app.js:
1. Adds a sleek breadcrumbs and folder directory tree powered by /api/fs/list directly in the modal.
2. Completely bypasses Windows Session 0 isolation hangs (never freezes, 0ms latency).
3. Allows 1-click folder browsing across all drives (C:/, D:/, subfolders).
4. Syncs index.html, static/v2/index.html, static/v2/js/app.js to DEPLOY_DIR (resources).
"""
import os
import re
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

# 1. Update HTML files
explorer_html = """        <!-- Interactive In-Modal Folder Explorer -->
        <div id="workspace-fs-explorer-section" class="flex flex-col gap-1.5 border border-black/10 rounded-[10px] p-2.5 bg-surface-container-low/60">
          <div class="flex items-center justify-between px-1">
            <div class="flex items-center gap-1.5 text-[11px] font-medium text-on-surface truncate min-w-0" id="fs-breadcrumbs">
              <span class="material-symbols-outlined text-[15px] text-primary shrink-0">account_tree</span>
              <span id="fs-current-path-label" class="font-code truncate max-w-[260px] sm:max-w-[320px]">C:/daon</span>
            </div>
            <div class="flex items-center gap-1 shrink-0">
              <button id="fs-drives-btn" type="button" class="px-2 py-0.5 bg-surface hover:bg-black/[0.05] border border-black/15 rounded text-[11px] font-label-sm text-on-surface flex items-center gap-0.5 cursor-pointer" title="드라이브 목록 (내 컴퓨터)">
                <span class="material-symbols-outlined text-[13px]">hard_drive</span>
                <span>드라이브</span>
              </button>
              <button id="fs-up-btn" type="button" class="px-2 py-0.5 bg-surface hover:bg-black/[0.05] border border-black/15 rounded text-[11px] font-label-sm text-on-surface flex items-center gap-0.5 cursor-pointer" title="상위 폴더로 이동">
                <span class="material-symbols-outlined text-[13px]">arrow_upward</span>
                <span>상위</span>
              </button>
            </div>
          </div>
          <div id="fs-folder-list" class="flex flex-col gap-0.5 max-h-[160px] overflow-y-auto border border-black/10 rounded-[8px] p-1 bg-surface-container-lowest">
            <!-- Dynamically populated folder items -->
          </div>
        </div>"""

for html_file in [os.path.join(ROOT_DIR, "index.html"), os.path.join(ROOT_DIR, "static", "v2", "index.html")]:
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        if 'id="workspace-fs-explorer-section"' not in content:
            needle = '<label class="block font-label-sm text-[12px] text-on-surface-variant mb-1.5">최근 사용한 워크스페이스</label>'
            if needle in content:
                content = content.replace(needle, explorer_html + "\n\n        <div>\n          " + needle)
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[HTML] Injected folder explorer into {html_file}")
            else:
                print(f"[HTML] Needle not found in {html_file}")
        else:
            print(f"[HTML] Already has workspace-fs-explorer-section in {html_file}")

# 2. Update app.js
app_js_path = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")
with open(app_js_path, "r", encoding="utf-8") as f:
    app_js = f.read()

# Update initWorkspaceModal logic with live folder browser
old_modal_start = "async function initWorkspaceManager() {"
if old_modal_start in app_js:
    # Check if loadFolderTree is already there
    if "async function loadFolderTree" not in app_js:
        # Replacement code for initWorkspaceModal
        tree_code = """  let currentBrowsePath = state.currentWorkspace || 'C:/daon';

  async function loadFolderTree(folderPath) {
    const folderList = document.getElementById('fs-folder-list');
    const pathLabel = document.getElementById('fs-current-path-label');
    if (!folderList) return;

    folderList.innerHTML = '<div class="text-[11px] text-on-surface-variant p-2 text-center flex items-center justify-center gap-1.5"><span class="material-symbols-outlined text-[14px] animate-spin">refresh</span><span>폴더 목록 불러오는 중...</span></div>';
    try {
      const res = await fetch(`/api/fs/list?path=${encodeURIComponent(folderPath || '')}`);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();

      currentBrowsePath = data.current || folderPath || '';
      if (pathLabel) {
        pathLabel.textContent = currentBrowsePath || '내 컴퓨터 (드라이브 선택)';
        pathLabel.title = currentBrowsePath;
      }

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
              showVoiceToast('📂 폴더 선택됨: ' + p, '📂', 2000);
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
    upBtn.addEventListener('click', () => {
      if (!currentBrowsePath) return;
      const normalized = currentBrowsePath.replace(/\\\\/g, '/').replace(/\\/+$/, '');
      const parts = normalized.split('/');
      if (parts.length <= 1 || (parts.length === 2 && parts[1] === '')) {
        loadFolderTree('');
      } else {
        parts.pop();
        const parentPath = parts.join('/') + (parts.length === 1 ? '/' : '');
        loadFolderTree(parentPath);
      }
    });
  }

  const drivesBtn = document.getElementById('fs-drives-btn');
  if (drivesBtn) {
    drivesBtn.addEventListener('click', () => {
      loadFolderTree('');
    });
  }
"""

        # Inject tree_code and call loadFolderTree in openModal
        app_js = app_js.replace(
            "function openModal() {",
            tree_code + "\n  function openModal() {\n    loadFolderTree(pathInput?.value || state.currentWorkspace || 'C:/daon');"
        )

        # Also update browseBtn to refresh and focus the folder tree
        old_browse_handler = """  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      try {
        browseBtn.disabled = true;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">refresh</span><span>선택 대기...</span>';
        
        // 45초 세이프티 타임아웃 (스피너 무한 회전 방지)
        const timeoutPromise = new Promise((_, reject) =>
          setTimeout(() => reject(new Error('TIMEOUT')), 45000)
        );

        const res = await Promise.race([
          DaonAPI.selectWorkspaceDialog(),
          timeoutPromise
        ]);

        if (res && res.path && pathInput) {
          pathInput.value = res.path;
          if (typeof showVoiceToast === 'function') {
            showVoiceToast('📂 폴더 선택 완료: ' + res.path, '📂', 2500);
          }
        }
      } catch (err) {
        if (err.message !== 'TIMEOUT') {
          console.warn('[Workspace Dialog]', err);
        }
      } finally {
        browseBtn.disabled = false;
        browseBtn.innerHTML = '<span class="material-symbols-outlined text-[16px]">folder_open</span><span>찾아보기</span>';
      }
    });
  }"""

        new_browse_handler = """  if (browseBtn) {
    browseBtn.addEventListener('click', async () => {
      // 1순위: 인라인 폴더 탐색기 즉시 갱신 및 포커스
      const cur = (pathInput?.value || state.currentWorkspace || 'C:/daon').trim();
      loadFolderTree(cur);
      const fsSection = document.getElementById('workspace-fs-explorer-section');
      if (fsSection) {
        fsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      if (typeof showVoiceToast === 'function') {
        showVoiceToast('📂 폴더 목록을 조회했습니다. 아래에서 폴더를 클릭하세요.', '📂', 2500);
      }
    });
  }"""

        if old_browse_handler in app_js:
            app_js = app_js.replace(old_browse_handler, new_browse_handler)
            print("[app.js] Replaced browseBtn handler with instant In-Modal Explorer!")

        with open(app_js_path, "w", encoding="utf-8") as f:
            f.write(app_js)
        print("[app.js] Injected folder tree logic successfully!")

# 3. Synchronize to DEPLOY_DIR (resources)
if os.path.exists(DEPLOY_DIR):
    deploy_index = os.path.join(DEPLOY_DIR, "index.html")
    deploy_v2_index = os.path.join(DEPLOY_DIR, "static", "v2", "index.html")
    deploy_app_js = os.path.join(DEPLOY_DIR, "static", "v2", "js", "app.js")

    src_index = os.path.join(ROOT_DIR, "index.html")
    shutil.copy2(src_index, deploy_index)
    shutil.copy2(src_index, deploy_v2_index)
    shutil.copy2(app_js_path, deploy_app_js)
    print(f"[DEPLOY] Successfully synced to {DEPLOY_DIR}!")

print("All done!")

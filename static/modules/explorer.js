// ── File Explorer ──
async function refreshFileTree() {
  const welcome = $('noWorkspaceWelcome');
  const tree = $('fileTreeContainer');
  if (!welcome || !tree) return;

  if (!State.activeWorkspacePath || !State.activeSessionId) {
    welcome.style.display = 'flex';
    $('newFileBtn').style.display = 'none';
    $('newDirBtn').style.display = 'none';
    $('openFileBtn').style.display = 'none';
    $('refreshExplorerBtn').style.display = 'none';
    $('openFolderBtn').style.display = 'none';
    tree.innerHTML = '';
    return;
  }

  welcome.style.display = 'none';
  $('newFileBtn').style.display = 'block';
  $('newDirBtn').style.display = 'block';
  $('openFileBtn').style.display = 'block';
  $('refreshExplorerBtn').style.display = 'block';
  $('openFolderBtn').style.display = 'block';

  try {
    const res = await api(`/api/list?session_id=${encodeURIComponent(State.activeSessionId)}&path=.`);
    tree.innerHTML = '';

    const rootName = getFolderName(State.activeWorkspacePath);
    const rootNode = document.createElement('div');
    const isRootExpanded = State.expandedDirs.has('.');
    rootNode.className = `tree-node dir root-node${isRootExpanded ? ' expanded' : ''}`;
    rootNode.style.paddingLeft = '8px';

    const nameSpan = document.createElement('span');
    nameSpan.innerHTML = '📁 ' + esc(rootName);
    rootNode.appendChild(nameSpan);

    const childContainer = document.createElement('div');
    childContainer.className = 'root-child-container';
    childContainer.style.display = isRootExpanded ? 'block' : 'none';

    rootNode.onclick = (e) => {
      e.stopPropagation();
      if (State.expandedDirs.has('.')) {
        State.expandedDirs.delete('.');
        rootNode.classList.remove('expanded');
        childContainer.style.display = 'none';
      } else {
        State.expandedDirs.add('.');
        rootNode.classList.add('expanded');
        childContainer.style.display = 'block';
      }
    };

    renderFileNodes(childContainer, res.entries, 1);

    tree.appendChild(rootNode);
    tree.appendChild(childContainer);
  } catch (e) {
    console.error("Explorer refresh failed:", e);
    tree.innerHTML = `<div class="text-danger" style="padding:10px; font-size:12px;">Failed to load files: ${e.message}</div>`;
  }
}

function renderFileNodes(container, entries, depth) {
  entries.forEach(entry => {
    const node = document.createElement('div');
    node.className = `tree-node ${entry.type}`;
    node.style.paddingLeft = `${depth * 14 + 8}px`;

    const nameSpan = document.createElement('span');
    const icon = entry.type === 'dir' ? '📁 ' : '📄 ';
    nameSpan.textContent = icon + entry.name;
    node.appendChild(nameSpan);

    if (entry.type === 'dir') {
      const isExpanded = State.expandedDirs.has(entry.path);
      if (isExpanded) {
        node.classList.add('expanded');
      }

      const childContainer = document.createElement('div');
      childContainer.style.display = isExpanded ? 'block' : 'none';

      node.onclick = async (e) => {
        e.stopPropagation();
        if (State.expandedDirs.has(entry.path)) {
          State.expandedDirs.delete(entry.path);
          node.classList.remove('expanded');
          childContainer.style.display = 'none';
        } else {
          State.expandedDirs.add(entry.path);
          node.classList.add('expanded');
          childContainer.style.display = 'block';
          if (childContainer.children.length === 0) {
            try {
              const res = await api(`/api/list?session_id=${encodeURIComponent(State.activeSessionId)}&path=${encodeURIComponent(entry.path)}`);
              renderFileNodes(childContainer, res.entries, depth + 1);
            } catch (err) {
              console.error(err);
            }
          }
        }
      };

      container.appendChild(node);
      container.appendChild(childContainer);
    } else {
      node.onclick = (e) => {
        e.stopPropagation();
        openFileInTab(entry.path);
      };
      container.appendChild(node);
    }
  });
}
async function openFilePrompt() {
  if (!State.activeSessionId) return;
  openWebExplorer({
    type: 'file',
    title: '파일 열기',
    initialPath: State.activeWorkspacePath,
    onSelect: async (selectedPath) => {
      let relativePath = selectedPath;
      const wsClean = State.activeWorkspacePath.replace(/\\/g, '/').replace(/\/$/, '');
      const selectedClean = selectedPath.replace(/\\/g, '/');

      if (selectedClean.toLowerCase().startsWith(wsClean.toLowerCase())) {
        relativePath = selectedClean.substring(wsClean.length);
        if (relativePath.startsWith('/')) {
          relativePath = relativePath.substring(1);
        }
      } else {
        showToast("선택한 파일이 현재 작업공간 내에 있지 않습니다. 작업공간 내부의 파일을 선택해주세요.");
        return;
      }

      await openFileInTab(relativePath);
    }
  });
}

// ── Custom Input Modal (replaces native prompt() for Electron WebContentsView compatibility) ──
var _inputModalResolve = null;

function showInputModal(title, placeholder, defaultValue) {
  return new Promise(function (resolve, reject) {
    try {
      var overlay = document.getElementById('inputModal');
      var field = document.getElementById('inputModalField');
      var titleEl = document.getElementById('inputModalTitle');
      if (!overlay || !field) {
        // Fallback to native prompt if modal elements not found
        var fallback = prompt(title + '\n' + placeholder);
        resolve(fallback);
        return;
      }
      titleEl.textContent = title;
      field.placeholder = placeholder || '';
      field.value = defaultValue || '';
      overlay.style.display = 'flex';
      field.focus();
      _inputModalResolve = resolve;
    } catch (e) {
      console.error('[inputModal] showInputModal error:', e);
      resolve(null);
    }
  });
}

function confirmInputModal() {
  var field = document.getElementById('inputModalField');
  var overlay = document.getElementById('inputModal');
  var value = field ? field.value : '';
  if (overlay) overlay.style.display = 'none';
  if (_inputModalResolve) {
    _inputModalResolve(value);
    _inputModalResolve = null;
  }
}

function closeInputModal() {
  var overlay = document.getElementById('inputModal');
  if (overlay) overlay.style.display = 'none';
  if (_inputModalResolve) {
    _inputModalResolve(null);
    _inputModalResolve = null;
  }
}

// Allow Enter key to confirm
document.addEventListener('DOMContentLoaded', function () {
  var field = document.getElementById('inputModalField');
  if (field) {
    field.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        confirmInputModal();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        closeInputModal();
      }
    });
  }
});

// ── Custom Confirm Modal (replaces native confirm() for Electron WebContentsView compatibility) ──
var _confirmModalResolve = null;

function showConfirmModal(message, title) {
  return new Promise(function (resolve) {
    try {
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.style.cssText = 'display:flex; align-items:center; justify-content:center; z-index:10001;';

      var panel = document.createElement('div');
      panel.className = 'modal-panel';
      panel.style.cssText = 'max-width:440px; width:90%;';

      panel.innerHTML =
        '<div class="modal-header">' +
        '<span>' + (title || '확인') + '</span>' +
        '<button class="action-btn _cfm-close" style="background:none;border:none;font-size:18px;cursor:pointer;color:var(--text);">&times;</button>' +
        '</div>' +
        '<div class="modal-body" style="padding:16px; display:flex; flex-direction:column; gap:12px;">' +
        '<p style="margin:0; color:var(--text); font-size:14px; line-height:1.5;">' + (message || '') + '</p>' +
        '<div style="display:flex; gap:6px; justify-content:flex-end;">' +
        '<button class="cron-btn _cfm-cancel">취소</button>' +
        '<button class="cron-btn run _cfm-yes">확인</button>' +
        '</div></div>';

      overlay.appendChild(panel);
      document.body.appendChild(overlay);

      var yesBtn = panel.querySelector('._cfm-yes');

      function cleanup(result) {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        if (_confirmModalResolve === resolve) _confirmModalResolve = null;
        resolve(result);
      }

      _confirmModalResolve = resolve;

      panel.querySelector('._cfm-close').onclick = function () { cleanup(false); };
      panel.querySelector('._cfm-cancel').onclick = function () { cleanup(false); };
      yesBtn.onclick = function () { cleanup(true); };
      overlay.addEventListener('click', function (e) { if (e.target === overlay) cleanup(false); });

      if (yesBtn) yesBtn.focus();

      function onKey(e) {
        if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); cleanup(false); }
        if (e.key === 'Enter') { document.removeEventListener('keydown', onKey); cleanup(true); }
      }
      document.addEventListener('keydown', onKey);
    } catch (e) {
      console.error('[confirmModal] showConfirmModal error:', e);
      resolve(false);
    }
  });
}

async function createNewFilePrompt() {
  if (!State.activeSessionId) {
    showToast("활성 세션이 없습니다. 먼저 세션을 생성하거나 선택해주세요.");
    return;
  }
  if (!State.activeWorkspacePath) {
    showToast("작업공간 폴더가 열려있지 않습니다. 먼저 폴더를 열어주세요.");
    return;
  }
  console.log('[explorer] createNewFilePrompt: sessionId=', State.activeSessionId, 'workspace=', State.activeWorkspacePath);
  var name = await showInputModal('새 파일 만들기', 'Enter new file path relative to workspace root (e.g. src/index.js):');
  if (!name || name.trim() === '') return;

  try {
    console.log('[explorer] Calling POST /api/file/create with path=', name.trim());
    var result = await api('/api/file/create', {
      method: 'POST',
      body: { session_id: State.activeSessionId, path: name.trim() }
    });
    console.log('[explorer] /api/file/create response:', result);
    await refreshFileTree();
    await openFileInTab(name.trim());
  } catch (e) {
    console.error('[explorer] createNewFilePrompt failed:', e);
    showToast("생성 실패: " + e.message);
  }
}

async function createNewDirPrompt() {
  if (!State.activeSessionId) {
    showToast("활성 세션이 없습니다. 먼저 세션을 생성하거나 선택해주세요.");
    return;
  }
  if (!State.activeWorkspacePath) {
    showToast("작업공간 폴더가 열려있지 않습니다. 먼저 폴더를 열어주세요.");
    return;
  }
  console.log('[explorer] createNewDirPrompt: sessionId=', State.activeSessionId, 'workspace=', State.activeWorkspacePath);
  var name = await showInputModal('새 폴더 만들기', 'Enter new directory path relative to workspace root (e.g. components):');
  if (!name || name.trim() === '') return;

  try {
    console.log('[explorer] Calling POST /api/file/create-dir with path=', name.trim());
    var result = await api('/api/file/create-dir', {
      method: 'POST',
      body: { session_id: State.activeSessionId, path: name.trim() }
    });
    console.log('[explorer] /api/file/create-dir response:', result);
    await refreshFileTree();
  } catch (e) {
    console.error('[explorer] createNewDirPrompt failed:', e);
    showToast("디렉토리 생성 실패: " + e.message);
  }
}

let trayImageUrls = [];

function formatUserMessageContent(content, sessionId) {
  let escaped = esc(content);
  // 한/영 첨부 파일 마커 모두 매칭: "[첨부 파일: a.png, b.jpg]" 또는 "[Attached files: ...]"
  const regex = /\[(?:첨부 파일|Attached files):\s*([^\]]+)\]/;
  const match = escaped.match(regex);
  if (match) {
    const fileList = match[1].split(',').map(f => f.trim());
    let imagesHtml = '<div class="chat-attached-images" style="display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; align-items:flex-start;">';
    let hasImages = false;
    fileList.forEach(filename => {
      const ext = filename.split('.').pop().toLowerCase();
      if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) {
        hasImages = true;
        const imageUrl = `/api/file/raw?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(filename)}`;
        imagesHtml += `<img src="${imageUrl}" class="chat-message-image" style="width:150px !important; height:150px !important; object-fit:cover !important; border-radius:6px; border:1px solid var(--border2); cursor:zoom-in;" onclick="openImageLightbox(this.src)">`;
      }
    });
    imagesHtml += '</div>';
    if (hasImages) {
      escaped = escaped + imagesHtml;
    }
  }
  return escaped.replace(/\n/g, '<br>');
}
async function selectWorkspacePathNative() {
  openWebExplorer({
    type: 'dir',
    title: '작업공간 폴더 선택',
    onSelect: async (selectedPath) => {
      if (selectedPath && State.activeSessionId) {
        await api('/api/session/update', {
          method: 'POST',
          body: { session_id: State.activeSessionId, workspace: selectedPath }
        });
        State.activeWorkspacePath = selectedPath;
        await refreshFileTree();
      }
    }
  });
}
// ── Web File/Folder Explorer Modal Logic ──
let webExplorerCurrentPath = '';
let webExplorerSelectedPath = '';
let webExplorerType = 'dir'; // 'dir' or 'file'
let webExplorerCallback = null;

async function openWebExplorer(options) {
  webExplorerType = options.type || 'dir';
  webExplorerCallback = options.onSelect;
  webExplorerSelectedPath = '';

  $('webExplorerTitle').innerText = options.title || (webExplorerType === 'dir' ? '폴더 선택' : '파일 선택');
  $('webExplorerModal').style.display = 'flex';

  // Set initial path
  const initial = options.initialPath || State.activeWorkspacePath || '';
  await loadWebExplorerPath(initial);
}

function closeWebExplorer() {
  $('webExplorerModal').style.display = 'none';
  webExplorerCallback = null;
}

async function loadWebExplorerPath(path) {
  try {
    const data = await api(`/api/fs/list?path=${encodeURIComponent(path)}`);
    webExplorerCurrentPath = data.current;
    $('webExplorerPathInput').value = data.current || '내 컴퓨터 (드라이브 목록)';

    // Toggle parent button
    $('webExplorerParentBtn').disabled = !data.current;

    const listContainer = $('webExplorerList');
    listContainer.innerHTML = '';

    const entries = data.entries || [];
    if (entries.length === 0) {
      listContainer.innerHTML = '<div style="padding: 20px; color: var(--muted); text-align: center;">폴더가 비어 있습니다.</div>';
      return;
    }

    entries.forEach(entry => {
      // If we are looking for directories only, show only dirs/drives
      if (webExplorerType === 'dir' && entry.type === 'file') {
        return;
      }

      const itemEl = document.createElement('div');
      itemEl.className = 'web-explorer-item';

      let icon = '📁';
      if (entry.type === 'drive') icon = '💻';
      else if (entry.type === 'file') icon = '📄';

      itemEl.innerHTML = `
        <span class="web-explorer-item-icon">${icon}</span>
        <span class="web-explorer-item-name">${entry.name}</span>
      `;

      // Single click selects
      itemEl.onclick = (e) => {
        document.querySelectorAll('.web-explorer-item').forEach(el => el.classList.remove('selected'));
        itemEl.classList.add('selected');
        webExplorerSelectedPath = entry.path;
      };

      // Double click enters directories/drives
      itemEl.ondblclick = async () => {
        if (entry.type === 'dir' || entry.type === 'drive') {
          await loadWebExplorerPath(entry.path);
        } else if (webExplorerType === 'file') {
          // Double click on a file selects it and closes
          if (webExplorerCallback) {
            webExplorerCallback(entry.path);
          }
          closeWebExplorer();
        }
      };

      listContainer.appendChild(itemEl);
    });
  } catch (e) {
    showToast("경로 로드 실패: " + e.message);
  }
}

function initWebExplorerEvents() {
  $('closeWebExplorerBtn').onclick = closeWebExplorer;
  $('webExplorerCancelBtn').onclick = closeWebExplorer;

  $('webExplorerParentBtn').onclick = async () => {
    if (webExplorerCurrentPath) {
      let parts = webExplorerCurrentPath.replace(/\\/g, '/').split('/');
      parts.pop();
      if (parts.length > 0 && parts[parts.length - 1] === '') {
        parts.pop();
      }
      let parentPath = parts.join('/');
      if (parentPath && !parentPath.endsWith('/')) {
        if (parentPath.endsWith(':')) parentPath += '/';
      }
      await loadWebExplorerPath(parentPath);
    } else {
      await loadWebExplorerPath('');
    }
  };

  $('webExplorerSelectBtn').onclick = () => {
    if (webExplorerType === 'dir') {
      const selected = webExplorerSelectedPath || webExplorerCurrentPath;
      if (selected) {
        if (webExplorerCallback) webExplorerCallback(selected);
        closeWebExplorer();
      } else {
        showToast("선택된 폴더가 없습니다.");
      }
    } else {
      if (webExplorerSelectedPath) {
        if (webExplorerCallback) webExplorerCallback(webExplorerSelectedPath);
        closeWebExplorer();
      } else {
        showToast("선택된 파일이 없습니다.");
      }
    }
  };
}

function getFolderName(path) {
  if (!path) return '';
  const cleanPath = path.replace(/\\/g, '/');
  const parts = cleanPath.split('/').filter(p => p);
  if (parts.length === 0) return path;
  const lastPart = parts[parts.length - 1];
  if (lastPart.endsWith(':')) return lastPart + '/';
  return lastPart;
}
function renderTray() {
  const tray = $('attachTray');
  if (!tray) return;

  // Revoke old URLs to prevent memory leaks
  if (window.trayImageUrls) {
    window.trayImageUrls.forEach(url => URL.revokeObjectURL(url));
  }
  window.trayImageUrls = [];

  tray.innerHTML = '';

  if (!State.pendingFiles || !State.pendingFiles.length) {
    tray.style.display = 'none';
    return;
  }

  tray.style.display = 'flex';
  State.pendingFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    const isImage = f.type && f.type.startsWith('image/');
    if (isImage) {
      chip.className = 'attach-chip image-chip';
      const imgUrl = URL.createObjectURL(f);
      window.trayImageUrls.push(imgUrl);
      chip.innerHTML = `<img src="${imgUrl}" title="${esc(f.name)}"><button title="Remove">&times;</button>`;
    } else {
      chip.className = 'attach-chip';
      chip.innerHTML = `📎 ${esc(f.name)} <button title="Remove">&times;</button>`;
    }
    chip.querySelector('button').onclick = (e) => {
      e.stopPropagation();
      State.pendingFiles.splice(i, 1);
      renderTray();
    };
    tray.appendChild(chip);
  });
}

// 멀티모달(비전) 지원 모델 prefix 패턴
// [2026-08-26] 'minimax-m3' 소문자 하드코딩 제거 — MiniMax-M3는 비전 모델이 아니며,
// 이 배열은 toLowerCase() 비교용이라 대소문자 혼동의 발단이 되던 항목을 정리함.
function _isVisionModel(modelId) {
  if (!modelId) return false;
  const visionPrefixes = [
    'gpt-4o', 'gpt-4-turbo', 'gpt-4-vision',
    'claude-3', 'claude-3.5', 'claude-3.7',
    'deepseek-janus-pro', 'deepseek-vl2',
  ];
  const lower = modelId.toLowerCase();
  // 로컬 모델은 vision 지원 가능성 있음 (ollama llama3.2-vision 등)
  if (lower.startsWith('ollama/')) {
    if (lower.includes('vision') || lower.includes('llava') || lower.includes('bakllava')) return true;
    return false;
  }
  return visionPrefixes.some(p => lower.startsWith(p));
}

function addFiles(files) {
  if (!State.pendingFiles) State.pendingFiles = [];
  let hasNewImages = false;
  for (const f of files) {
    if (!State.pendingFiles.find(p => p.name === f.name)) {
      // 중복 파일명 방지: 이미 있으면 _N suffix 붙이기
      let uniqueName = f.name;
      let counter = 1;
      while (State.pendingFiles.find(p => p.name === uniqueName || p.uniqueName === uniqueName)) {
        const dotIdx = f.name.lastIndexOf('.');
        if (dotIdx > 0) {
          uniqueName = f.name.slice(0, dotIdx) + '_' + counter + f.name.slice(dotIdx);
        } else {
          uniqueName = f.name + '_' + counter;
        }
        counter++;
      }
      State.pendingFiles.push(f);
      if (f.type && f.type.startsWith('image/')) hasNewImages = true;
    }
  }
  renderTray();

  // 이미지 첨부 시 멀티모달 미지원 모델 경고
  if (hasNewImages) {
    const modelSelect = document.getElementById('modelSelect');
    const currentModel = modelSelect ? modelSelect.value : (State.model || '');
    if (currentModel && !_isVisionModel(currentModel)) {
      showToast('⚠️ 현재 선택된 모델은 이미지(비전)를 지원하지 않을 수 있습니다. GPT-4o, Claude 3+, Gemini 1.5+ 등의 멀티모달 모델로 전환하세요.', 5000);
    }
  }
}

async function uploadPendingFiles() {
  if (!State.pendingFiles || !State.pendingFiles.length || !State.activeSessionId) return [];
  const names = [];
  let failures = 0;

  const bar = $('uploadBar');
  const barWrap = $('uploadBarWrap');
  if (barWrap && bar) {
    barWrap.classList.add('active');
    bar.style.width = '0%';
  }

  const total = State.pendingFiles.length;
  for (let i = 0; i < total; i++) {
    let f = State.pendingFiles[i];

    // 클라이언트 사이드 이미지 리사이징 (max 2048px, JPEG quality 0.75)
    if (f.type && f.type.startsWith('image/') && f.type !== 'image/svg+xml' && f.type !== 'image/gif') {
      try {
        f = await _resizeImageFile(f, 2048, 0.75);
      } catch (rsErr) {
        console.warn('Image resize skipped:', rsErr.message);
      }
    }

    const fd = new FormData();
    fd.append('session_id', State.activeSessionId);
    fd.append('file', f, f.name);

    try {
      const res = await fetch(new URL('/api/upload', location.origin).href, {
        method: 'POST',
        credentials: 'include',
        body: fd
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      names.push(data.filename);
    } catch (e) {
      failures++;
      console.error("Upload failed: ", e);
      if (typeof logToConsole === 'function') {
        logToConsole(`Upload failed: ${f.name} — ${e.message}`, 'error');
      }
    }

    if (bar) {
      bar.style.width = `${Math.round((i + 1) / total * 100)}%`;
    }
  }

  if (barWrap && bar) {
    barWrap.classList.remove('active');
    bar.style.width = '0%';
  }

  State.pendingFiles = [];
  renderTray();

  if (failures === total && total > 0) {
    throw new Error(`All ${total} upload(s) failed`);
  }
  return names;
}

// ── Client-side image resize (Canvas API) ──
function _resizeImageFile(file, maxDim, quality) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      let w = img.width, h = img.height;
      if (Math.max(w, h) <= maxDim) {
        resolve(file); // already small enough
        return;
      }
      const ratio = maxDim / Math.max(w, h);
      w = Math.round(w * ratio);
      h = Math.round(h * ratio);
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, w, h);
      const mime = file.type || 'image/jpeg';
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error('Canvas toBlob failed'));
          return;
        }
        const resized = new File([blob], file.name, { type: mime, lastModified: Date.now() });
        resolve(resized);
      }, mime, quality);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('Image load failed'));
    };
    img.src = url;
  });
}

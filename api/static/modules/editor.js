function initMonaco() {
  require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min/vs' } });
  require(['vs/editor/editor.main'], () => {
    const savedTheme = localStorage.getItem('daon_theme') || 'midnight';
    const monacoTheme = savedTheme === 'light' ? 'vs' : 'vs-dark';
    State.editor = monaco.editor.create($('monacoContainer'), {
      value: '',
      language: 'plaintext',
      theme: monacoTheme,
      automaticLayout: true,
      fontSize: 13,
      fontFamily: 'Fira Code, monospace',
      minimap: { enabled: false },
      scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 }
    });

    logToConsole('Monaco editor ready.', 'success');

    // Track modifications to show unsaved (dirty) status
    State.editor.onDidChangeModelContent(() => {
      const tab = getActiveTab();
      if (tab) {
        const currentVal = State.editor.getValue();
        if (currentVal !== tab.content) {
          tab.content = currentVal;
          if (!tab.dirty) {
            tab.dirty = true;
            renderTabs();
            updateEditorActions();
          }
          if ($('htmlPreviewContainer').classList.contains('active')) {
            refreshHtmlPreviewFrame(tab);
          }
          if ($('mdPreviewContainer').style.display === 'block') {
            refreshMarkdownPreview(tab);
          }
        }
      }
    });
  });
}
// ── Multi-Tab Editor ──

/**
 * 디스크 읽기 없이 주어진 content로 에디터 탭을 생성/전환한다.
 * AI가 파일을 쓰는 도중(tool.started 시점)에는 디스크에 아직 파일이 없으므로
 * 이 헬퍼로 즉시 에디터에 반영한다.
 */
function createTabWithContent(path, content) {
  const existingIdx = State.openTabs.findIndex(t => t.path === path);
  if (existingIdx !== -1) {
    // 기존 탭이 있으면 모델 내용 갱신 후 전환
    const tab = State.openTabs[existingIdx];
    tab.content = content;
    if (tab.model && window.monaco) {
      tab.model.setValue(content);
    }
    switchTab(existingIdx);
    return;
  }

  const name = path.split('/').pop();
  const ext = name.split('.').pop().toLowerCase();
  let lang = 'plaintext';
  if (ext === 'js') lang = 'javascript';
  else if (ext === 'py') lang = 'python';
  else if (ext === 'html') lang = 'html';
  else if (ext === 'css') lang = 'css';
  else if (ext === 'json') lang = 'json';
  else if (ext === 'md') lang = 'markdown';

  let model = null;
  if (window.monaco) {
    model = monaco.editor.createModel(content || '', lang);
  }

  const newTab = {
    path,
    name,
    mode: 'code',
    model,
    content: content || '',
    dirty: false
  };

  State.openTabs.push(newTab);
  switchTab(State.openTabs.length - 1);
}

async function openFileInTab(path) {
  const existingIdx = State.openTabs.findIndex(t => t.path === path);
  if (existingIdx !== -1) {
    switchTab(existingIdx);
    return;
  }

  try {
    const file = await api(`/api/file?session_id=${encodeURIComponent(State.activeSessionId)}&path=${encodeURIComponent(path)}`);
    const name = path.split('/').pop();

    let mode = 'code';
    const ext = name.split('.').pop().toLowerCase();
    const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg'];
    if (file.binary || imageExts.includes(ext)) {
      if (imageExts.includes(ext)) {
        mode = 'image';
      } else {
        mode = 'binary';
      }
    }

    let model = null;
    if (mode === 'code' && window.monaco) {
      // Determine language
      let lang = 'plaintext';
      if (ext === 'js') lang = 'javascript';
      else if (ext === 'py') lang = 'python';
      else if (ext === 'html') lang = 'html';
      else if (ext === 'css') lang = 'css';
      else if (ext === 'json') lang = 'json';
      else if (ext === 'md') lang = 'markdown';

      model = monaco.editor.createModel(file.content, lang);
    }

    const newTab = {
      path,
      name,
      mode,
      model,
      content: file.content,
      dirty: false
    };

    State.openTabs.push(newTab);
    switchTab(State.openTabs.length - 1);
  } catch (e) {
    showToast("파일 로드 실패: " + e.message);
  }
}

function switchTab(index) {
  if (index < 0 || index >= State.openTabs.length) return;
  State.activeTabIndex = index;
  renderTabs();

  // Close any toggle-preview overlays when switching tabs
  _closeAllPreviews();

  const tab = State.openTabs[index];
  $('activeFilePath').textContent = tab.path;

  if (tab.mode === 'code') {
    showCanvas('monaco');
    if (State.editor && tab.model) {
      State.editor.setModel(tab.model);
    }
  } else if (tab.mode === 'image') {
    showCanvas('image');
    $('imgPreview').src = `/api/file/raw?session_id=${encodeURIComponent(State.activeSessionId)}&path=${encodeURIComponent(tab.path)}`;
  } else if (tab.mode === 'markdown') {
    showCanvas('markdown');
    $('mdPreviewContainer').innerHTML = renderMd(tab.content);
  } else {
    const ext = tab.name.split('.').pop().toLowerCase();
    if (ext === 'html') {
      showCanvas('monaco');
      if (State.editor && tab.model) {
        State.editor.setModel(tab.model);
      }
      if ($('htmlPreviewContainer').classList.contains('active')) {
        refreshHtmlPreviewFrame(tab);
      }
    } else {
      showCanvas('markdown');
      $('mdPreviewContainer').innerHTML = `<p class="text-muted" style="text-align:center;padding-top:40px;">${tab.content}</p>`;
    }
  }

  updateEditorActions();
}

function closeTab(index, e) {
  if (e) e.stopPropagation();
  const tab = State.openTabs[index];

  if (tab.dirty) {
    if (!confirm(`Discard unsaved changes to ${tab.name}?`)) return;
  }

  // Dispose monaco model to prevent memory leaks
  if (tab.model) tab.model.dispose();

  State.openTabs.splice(index, 1);

  if (State.openTabs.length === 0) {
    State.activeTabIndex = -1;
    renderTabs();
    showCanvas('welcome');
    $('activeFilePath').textContent = 'Select a file from the explorer';
    updateEditorActions();
  } else {
    let nextIdx = State.activeTabIndex;
    if (nextIdx >= State.openTabs.length) {
      nextIdx = State.openTabs.length - 1;
    }
    switchTab(nextIdx);
  }
}

function getActiveTab() {
  return State.activeTabIndex >= 0 && State.activeTabIndex < State.openTabs.length ? State.openTabs[State.activeTabIndex] : null;
}

function renderTabs() {
  const container = $('editorTabs');
  container.innerHTML = '';
  if (State.openTabs.length === 0) {
    container.style.display = 'none';
  } else {
    container.style.display = 'flex';
  }
  State.openTabs.forEach((t, i) => {
    const activeClass = i === State.activeTabIndex ? 'active' : '';
    const dirtyMark = t.dirty ? ' *' : '';
    const tabEl = document.createElement('div');
    tabEl.className = `tab-item ${activeClass}`;
    tabEl.onclick = () => switchTab(i);
    tabEl.innerHTML = `
      <span>${t.name}${dirtyMark}</span>
      <span class="tab-close" onclick="closeTab(${i}, event)">&times;</span>
    `;
    container.appendChild(tabEl);
  });
}

function showCanvas(canvasName) {
  $('monacoContainer').style.display = canvasName === 'monaco' ? 'block' : 'none';
  $('imgPreviewContainer').style.display = canvasName === 'image' ? 'flex' : 'none';
  $('mdPreviewContainer').style.display = canvasName === 'markdown' ? 'block' : 'none';
  $('welcomeCanvas').style.display = canvasName === 'welcome' ? 'flex' : 'none';

  // Close HTML toggle-preview overlay when switching away from monaco.
  // Image and MD canvases are set directly above, so only clean up the
  // htmlPreview overlay + preview-active state (don't touch img/md display).
  if (canvasName !== 'monaco') {
    $('htmlPreviewContainer').classList.remove('active');
    $('monacoContainer').classList.remove('preview-active');
    $('previewHtmlBtn').classList.remove('active');
  }
}

function updateEditorActions() {
  const tab = getActiveTab();
  if (tab) {
    $('deleteFileBtn').style.display = 'block';
    const ext = tab.name.split('.').pop().toLowerCase();
    const previewable = ext === 'html' || ext === 'png' || ext === 'jpg' || ext === 'jpeg' || ext === 'gif' || ext === 'svg' || ext === 'md';
    if (previewable) {
      $('previewHtmlBtn').style.display = 'block';
      $('openSystemBrowserBtn').style.display = ext === 'html' ? 'block' : 'none';
    } else {
      $('previewHtmlBtn').style.display = 'none';
      $('openSystemBrowserBtn').style.display = 'none';
      // Close any active preview when switching to non-previewable file
      if ($('htmlPreviewContainer').classList.contains('active') ||
        $('imgPreviewContainer').style.display === 'flex' ||
        $('mdPreviewContainer').style.display === 'block') {
        _closeAllPreviews();
      }
    }
    if (tab.mode === 'code') {
      $('saveFileBtn').style.display = 'block';
      $('saveFileBtn').disabled = !tab.dirty;
    } else {
      $('saveFileBtn').style.display = 'none';
    }
  } else {
    $('deleteFileBtn').style.display = 'none';
    $('saveFileBtn').style.display = 'none';
    $('previewHtmlBtn').style.display = 'none';
    $('openSystemBrowserBtn').style.display = 'none';
    _closeAllPreviews();
  }

  if (State.editor) {
    State.editor.layout();
  }
}

async function saveCurrentFile() {
  const tab = getActiveTab();
  if (!tab || !tab.dirty || tab.mode !== 'code') return;

  const content = State.editor.getValue();
  try {
    await api('/api/file/save', {
      method: 'POST',
      body: { session_id: State.activeSessionId, path: tab.path, content }
    });
    tab.content = content;
    tab.dirty = false;
    renderTabs();
    updateEditorActions();
    if ($('htmlPreviewContainer').classList.contains('active')) {
      refreshHtmlPreviewFrame(tab);
    }
    if ($('mdPreviewContainer').style.display === 'block') {
      refreshMarkdownPreview(tab);
    }
  } catch (e) {
    showToast("저장 실패: " + e.message);
  }
}

async function deleteCurrentFile() {
  const tab = getActiveTab();
  if (!tab) return;
  if (!confirm(`Are you sure you want to delete ${tab.path}?`)) return;

  try {
    await api('/api/file/delete', {
      method: 'POST',
      body: { session_id: State.activeSessionId, path: tab.path }
    });
    // Close tab without confirmation dialog since file is already deleted
    tab.dirty = false;
    closeTab(State.activeTabIndex);
    await refreshFileTree();
  } catch (e) {
    showToast("삭제 실패: " + e.message);
  }
}
function _closeAllPreviews() {
  $('htmlPreviewContainer').classList.remove('active');
  $('imgPreviewContainer').style.display = 'none';
  $('mdPreviewContainer').style.display = 'none';
  $('monacoContainer').classList.remove('preview-active');
  $('previewHtmlBtn').classList.remove('active');
}

function togglePreview() {
  const tab = getActiveTab();
  if (!tab) return;
  const ext = tab.name.split('.').pop().toLowerCase();

  const btn = $('previewHtmlBtn');
  const monaco = $('monacoContainer');

  // Determine which preview is currently active
  const htmlActive = $('htmlPreviewContainer').classList.contains('active');
  const imgActive = $('imgPreviewContainer').style.display === 'flex';
  const mdActive = $('mdPreviewContainer').style.display === 'block';
  const anyActive = htmlActive || imgActive || mdActive;

  if (anyActive) {
    // Turn off all previews
    _closeAllPreviews();
  } else {
    // Turn on preview based on file type
    if (ext === 'html') {
      $('htmlPreviewContainer').classList.add('active');
      monaco.classList.add('preview-active');
      btn.classList.add('active');
      refreshHtmlPreviewFrame(tab);
    } else if (['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(ext)) {
      $('imgPreviewContainer').style.display = 'flex';
      monaco.classList.add('preview-active');
      btn.classList.add('active');
      refreshImagePreview(tab);
    } else if (ext === 'md') {
      $('mdPreviewContainer').style.display = 'block';
      monaco.classList.add('preview-active');
      btn.classList.add('active');
      refreshMarkdownPreview(tab);
    }
  }

  if (State.editor) {
    State.editor.layout();
  }
}

// Keep backward compatibility - called from onDidChangeModelContent
function toggleHtmlPreview() {
  togglePreview();
}

function refreshHtmlPreviewFrame(tab) {
  if (!tab) return;
  const frame = $('htmlPreview');
  if (frame) {
    // srcdoc: HTML5 표준 속성으로 iframe 콘텐츠를 직접 주입.
    // contentDocument/doc.write() 방식은 Electron WebContentsView에서
    // about:blank iframe 접근 제한으로 실패할 수 있음 (배포 모드 이슈).
    // srcdoc는 DOM 접근 없이 작동하므로 브라우저/Electron 모두에서 신뢰성 있음.
    //
    // <base href> 주입: srcdoc iframe은 about:srcdoc 오리진을 가지므로
    // /static/... 같은 절대 경로 외부 리소스가 해석되지 않음.
    // 서버 오리진을 base로 설정하여 index.html 등 외부 리소스 의존 파일도
    // 내장 미리보기에서 정상 렌더링됨.
    var html = tab.content;
    var baseTag = '<base href="' + window.location.origin + '/">';
    if (/<head[^>]*>/i.test(html)) {
      html = html.replace(/<head([^>]*)>/i, '<head$1>' + baseTag);
    } else if (/<html[^>]*>/i.test(html)) {
      html = html.replace(/<html([^>]*)>/i, '<html$1><head>' + baseTag + '</head>');
    } else {
      html = baseTag + html;
    }
    frame.srcdoc = html;
  }
}

function refreshImagePreview(tab) {
  if (!tab) return;
  const img = $('imgPreview');
  if (img) {
    // Load image via raw file endpoint (binary-safe)
    img.src = '/api/file/raw?session_id=' + encodeURIComponent(State.activeSessionId) +
      '&path=' + encodeURIComponent(tab.path) + '&t=' + Date.now();
  }
}

function refreshMarkdownPreview(tab) {
  if (!tab) return;
  const container = $('mdPreviewContainer');
  if (container) {
    container.innerHTML = renderMd(tab.content);
  }
}

/**
 * Open the current HTML file in the user's default system browser.
 * Saves the current content to a temp file first, then opens it.
 * Falls back to srcdoc blob URL if Electron API is not available.
 */
async function openHtmlInSystemBrowser() {
  const tab = getActiveTab();
  if (!tab) return;

  // Get latest content from editor if it's dirty
  var content = tab.content;
  if (tab.dirty && State.editor) {
    content = State.editor.getValue();
  }

  // Try Electron IPC first (opens file path in system browser)
  if (window.electronAPI && window.electronAPI.openSystemBrowser) {
    // Save content to temp file via backend, then open in system browser
    try {
      var result = await api('/api/file/save', {
        method: 'POST',
        body: { session_id: State.activeSessionId, path: tab.path, content: content }
      });
      // Update tab state
      tab.content = content;
      tab.dirty = false;
      renderTabs();
      updateEditorActions();

      // Now open the actual file path in system browser
      // The file path on disk is: State.activeWorkspacePath + '/' + tab.path
      var fullPath = (State.activeWorkspacePath || '').replace(/\\/g, '/').replace(/\/$/, '') + '/' + tab.path.replace(/^\//, '');
      console.log('[editor] Opening in system browser:', fullPath);
      window.electronAPI.openSystemBrowser(fullPath);
      showToast('시스템 브라우저에서 열렸습니다: ' + tab.name);
    } catch (e) {
      console.error('[editor] Failed to save or open in system browser:', e);
      // Fallback: open via blob URL
      _openHtmlViaBlobUrl(content, tab.name);
    }
  } else {
    // No Electron API: create blob URL and open in new window
    _openHtmlViaBlobUrl(content, tab.name);
  }
}

function _openHtmlViaBlobUrl(content, fileName) {
  try {
    var blob = new Blob([content], { type: 'text/html' });
    var url = URL.createObjectURL(blob);
    var w = window.open(url, '_blank');
    if (!w) {
      showToast('팝업이 차단되었습니다. 팝업 차단을 해제해주세요.');
    } else {
      showToast('새 창에서 열렸습니다: ' + (fileName || 'HTML'));
      // Revoke the blob URL after a delay to ensure the new window loaded it
      setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
    }
  } catch (e) {
    console.error('[editor] _openHtmlViaBlobUrl failed:', e);
    showToast('미리보기를 열 수 없습니다: ' + e.message);
  }
}

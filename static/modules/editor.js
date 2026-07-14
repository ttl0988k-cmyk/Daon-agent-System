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
        }
      }
    });
  });
}
// ── Multi-Tab Editor ──
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
    if (file.binary) {
      if (['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(ext)) {
        mode = 'image';
      } else {
        mode = 'binary';
      }
    } else if (ext === 'md') {
      mode = 'markdown';
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
    showToast("Failed to load file: " + e.message);
  }
}

function switchTab(index) {
  if (index < 0 || index >= State.openTabs.length) return;
  State.activeTabIndex = index;
  renderTabs();

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

  if (canvasName !== 'monaco' && canvasName !== 'html') {
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
    if (ext === 'html') {
      $('previewHtmlBtn').style.display = 'block';
    } else {
      $('previewHtmlBtn').style.display = 'none';
      $('htmlPreviewContainer').classList.remove('active');
      $('monacoContainer').classList.remove('preview-active');
      $('previewHtmlBtn').classList.remove('active');
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
    $('htmlPreviewContainer').classList.remove('active');
    $('monacoContainer').classList.remove('preview-active');
    $('previewHtmlBtn').classList.remove('active');
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
  } catch (e) {
    showToast("Save failed: " + e.message);
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
    showToast("Delete failed: " + e.message);
  }
}
function toggleHtmlPreview() {
  const tab = getActiveTab();
  if (!tab) return;
  const ext = tab.name.split('.').pop().toLowerCase();
  if (ext !== 'html') return;

  const btn = $('previewHtmlBtn');
  const container = $('htmlPreviewContainer');
  const monaco = $('monacoContainer');
  const isPreviewActive = container.classList.contains('active');

  if (isPreviewActive) {
    container.classList.remove('active');
    monaco.classList.remove('preview-active');
    btn.classList.remove('active');
  } else {
    container.classList.add('active');
    monaco.classList.add('preview-active');
    btn.classList.add('active');
    refreshHtmlPreviewFrame(tab);
  }

  if (State.editor) {
    State.editor.layout();
  }
}

function refreshHtmlPreviewFrame(tab) {
  if (!tab) return;
  const frame = $('htmlPreview');
  if (frame) {
    try {
      const doc = frame.contentDocument || frame.contentWindow.document;
      doc.open();
      doc.write(tab.content);
      doc.close();
    } catch (err) {
      frame.src = `/api/file/raw?session_id=${encodeURIComponent(State.activeSessionId)}&path=${encodeURIComponent(tab.path)}&t=${Date.now()}`;
    }
  }
}

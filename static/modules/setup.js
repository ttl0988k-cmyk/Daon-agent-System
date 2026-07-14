// ── AI Setup Generator UI ──

let _setupCurrentWs = null;
let _setupTechStack = null;

/**
 * Load the setup panel — detect tech stack and show generation UI.
 * Called when switching to 'setup' panel.
 */
async function loadSetupPanel() {
    const panel = $('panelSetup');
    if (!panel) return;

    // Get current active workspace (use the one from the workspace selector or settings)
    const wsSelect = $('workspaceSelect');
    const currentWs = wsSelect ? wsSelect.value : null;

    if (!currentWs) {
        // Try to get from session
        try {
            const s = await api('/api/session/info');
            _setupCurrentWs = s.workspace || null;
        } catch (e) {
            _setupCurrentWs = null;
        }
    } else {
        _setupCurrentWs = currentWs;
    }

    if (!_setupCurrentWs) {
        renderSetupNoWorkspace();
        return;
    }

    // Detect tech stack
    renderSetupLoading();
    try {
        const data = await api('/api/setup/detect?workspace=' + encodeURIComponent(_setupCurrentWs));
        if (!data || data.error) {
            renderSetupError(data?.error || '감지 실패');
            return;
        }
        _setupTechStack = data.tech_stack;
        renderSetupGenerator(data);
        renderSyncWatcherSection(document.getElementById('setupGeneratorSection'));
        loadSyncWatcherStatus();
    } catch (err) {
        console.error('[Setup] Detect failed:', err);
        renderSetupError(err.message);
    }
}

/**
 * Render the setup panel with no workspace selected.
 */
function renderSetupNoWorkspace() {
    const container = document.getElementById('setupGeneratorSection');
    if (!container) return;
    container.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:11px;text-align:center;">'
        + '워크스페이스가 선택되지 않았습니다.<br>좌측 상단에서 워크스페이스를 선택해주세요.</div>';
}

/**
 * Render loading state.
 */
function renderSetupLoading() {
    const container = document.getElementById('setupGeneratorSection');
    if (!container) return;
    container.innerHTML = '<div style="padding:12px;color:var(--muted);text-align:center;">'
        + '🔍 프로젝트 분석 중...</div>';
}

/**
 * Render error state.
 */
function renderSetupError(msg) {
    const container = document.getElementById('setupGeneratorSection');
    if (!container) return;
    container.innerHTML = '<div style="padding:12px;color:var(--danger);font-size:11px;">'
        + '⚠️ ' + esc(msg) + '</div>';
}

/**
 * Render the main setup generator UI with tech stack info and file type checkboxes.
 */
function renderSetupGenerator(data) {
    const container = document.getElementById('setupGeneratorSection');
    if (!container) return;

    const ts = data.tech_stack || {};
    const fileCount = data.file_count || 0;

    let html = '';

    // ── Tech stack summary ──
    html += '<div class="setup-tech-summary">';
    html += '<div class="setup-tech-title">📊 감지된 기술 스택</div>';
    html += '<div class="setup-tech-grid">';

    const items = [
        { label: '주 언어', value: ts.primary_language || 'Unknown', icon: '💻' },
        { label: '프레임워크', value: ts.framework || 'N/A', icon: '🧩' },
        { label: '패키지 매니저', value: ts.package_manager || 'N/A', icon: '📦' },
        { label: '전체 파일', value: fileCount + '개', icon: '📄' },
        { label: 'Docker', value: ts.has_docker ? '✔ 사용' : '✘ 미사용', icon: '🐳' },
        { label: 'Git', value: ts.has_git ? '✔ 사용' : '✘ 미사용', icon: '🔀' },
    ];

    for (const item of items) {
        html += '<div class="setup-tech-item">';
        html += '<span class="setup-tech-icon">' + item.icon + '</span>';
        html += '<div class="setup-tech-info">';
        html += '<span class="setup-tech-label">' + item.label + '</span>';
        html += '<span class="setup-tech-value">' + esc(String(item.value)) + '</span>';
        html += '</div></div>';
    }
    html += '</div></div>';

    // ── File type checkboxes ──
    html += '<div class="setup-file-types">';
    html += '<div class="setup-section-title">📝 생성할 파일 선택</div>';

    const fileTypes = [
        { id: 'agents.md', label: 'AGENTS.md', desc: 'Hermes, Cursor, Copilot용 범용 가이드', checked: true },
        { id: 'claude.md', label: 'CLAUDE.md', desc: 'Claude Code 특화 지침 포함', checked: true },
        { id: 'cursor_rules', label: '.cursor/rules', desc: 'Cursor 전용 규칙 파일', checked: false },
        { id: 'copilot_instructions', label: 'Copilot Instructions', desc: 'GitHub Copilot 전용 지침', checked: false },
    ];

    for (const ft of fileTypes) {
        html += '<label class="setup-file-check">';
        html += '<input type="checkbox" value="' + ft.id + '"' + (ft.checked ? ' checked' : '') + ' '
            + 'onchange="onSetupFileTypeChange()">';
        html += '<div class="setup-file-info">';
        html += '<span class="setup-file-name">' + ft.label + '</span>';
        html += '<span class="setup-file-desc">' + ft.desc + '</span>';
        html += '</div></label>';
    }
    html += '</div>';

    // ── Overwrite toggle ──
    html += '<div class="setup-options">';
    html += '<label class="setup-overwrite-check">';
    html += '<input type="checkbox" id="setupOverwrite" onchange="onSetupOptionChange()">';
    html += '<span>기존 파일 덮어쓰기</span>';
    html += '</label>';
    html += '</div>';

    // ── Action buttons ──
    html += '<div class="setup-actions">';
    html += '<button class="setup-btn preview" onclick="previewSetupFile()">👁️ 미리보기</button>';
    html += '<button class="setup-btn generate" onclick="generateSetupFiles()">🚀 생성하기</button>';
    html += '</div>';

    // ── Preview area ──
    html += '<div id="setupPreviewArea" class="setup-preview-area" style="display:none;">';
    html += '<div class="setup-preview-header">';
    html += '<span id="setupPreviewTitle">미리보기</span>';
    html += '<button class="setup-preview-close" onclick="closeSetupPreview()">✕</button>';
    html += '</div>';
    html += '<pre id="setupPreviewContent" class="setup-preview-content"></pre>';
    html += '</div>';

    // ── Result area ──
    html += '<div id="setupResultArea" class="setup-result-area" style="display:none;"></div>';

    container.innerHTML = html;
}

/**
 * Get selected file types from checkboxes.
 */
function getSelectedSetupTypes() {
    const checks = document.querySelectorAll('#setupGeneratorSection input[type="checkbox"][value]');
    const selected = [];
    for (const cb of checks) {
        if (cb.checked && cb.value && cb.value !== 'on') selected.push(cb.value);
    }
    return selected;
}

/**
 * Get overwrite option.
 */
function getSetupOverwrite() {
    const cb = $('setupOverwrite');
    return cb ? cb.checked : false;
}

/**
 * Handle file type checkbox change.
 */
function onSetupFileTypeChange() {
    const selected = getSelectedSetupTypes();
    const previewBtn = document.querySelector('.setup-btn.preview');
    const generateBtn = document.querySelector('.setup-btn.generate');
    if (previewBtn) previewBtn.disabled = selected.length === 0;
    if (generateBtn) generateBtn.disabled = selected.length === 0;
}

/**
 * Handle overwrite option change.
 */
function onSetupOptionChange() {
    // Nothing else needed for now
}

/**
 * Preview a single setup file.
 */
async function previewSetupFile() {
    const types = getSelectedSetupTypes();
    if (types.length === 0) return;

    const previewArea = $('setupPreviewArea');
    const previewContent = $('setupPreviewContent');
    const previewTitle = $('setupPreviewTitle');

    // Preview the first selected type
    const fileType = types[0];

    previewTitle.textContent = '미리보기: ' + fileType + ' — 로딩 중...';
    previewContent.textContent = '로딩 중...';
    previewArea.style.display = 'block';

    try {
        const data = await api('/api/setup/preview?workspace='
            + encodeURIComponent(_setupCurrentWs)
            + '&file_type=' + encodeURIComponent(fileType));

        if (data.error) {
            previewContent.textContent = '오류: ' + data.error;
            return;
        }

        const status = data.will_overwrite ? '⚠️ 기존 파일을 덮어씁니다' : '✨ 새 파일 생성';
        previewTitle.textContent = '미리보기: ' + data.filename + ' — ' + status;
        previewContent.textContent = data.content;
    } catch (err) {
        previewContent.textContent = '오류: ' + err.message;
    }
}

/**
 * Close preview area.
 */
function closeSetupPreview() {
    const previewArea = $('setupPreviewArea');
    if (previewArea) previewArea.style.display = 'none';
}

/**
 * Generate selected setup files.
 */
async function generateSetupFiles() {
    const types = getSelectedSetupTypes();
    const overwrite = getSetupOverwrite();

    if (types.length === 0) {
        showToast('생성할 파일 타입을 하나 이상 선택해주세요.');
        return;
    }

    if (!_setupCurrentWs) {
        showToast('워크스페이스가 선택되지 않았습니다.');
        return;
    }

    const resultArea = $('setupResultArea');
    resultArea.style.display = 'block';
    resultArea.innerHTML = '<div style="padding:8px;color:var(--muted);">⚙️ 생성 중...</div>';

    try {
        const data = await api('/api/setup/generate', {
            method: 'POST',
            body: {
                workspace: _setupCurrentWs,
                file_types: types,
                overwrite: overwrite,
            }
        });

        renderSetupResult(data);
    } catch (err) {
        resultArea.innerHTML = '<div style="padding:8px;color:var(--danger);">'
            + '❌ 오류: ' + esc(err.message) + '</div>';
    }
}

/**
 * Render generation result.
 */
function renderSetupResult(data) {
    const resultArea = $('setupResultArea');
    resultArea.style.display = 'block';

    let html = '<div class="setup-result-box">';

    // Generated
    if (data.generated_labels && data.generated_labels.length > 0) {
        html += '<div class="setup-result-section success">';
        html += '<div class="setup-result-section-title">✅ 생성됨 (' + data.generated_labels.length + ')</div>';
        for (const label of data.generated_labels) {
            html += '<div class="setup-result-item">✓ ' + esc(label) + '</div>';
        }
        html += '</div>';
    }

    // Skipped
    if (data.skipped_labels && data.skipped_labels.length > 0) {
        html += '<div class="setup-result-section warning">';
        html += '<div class="setup-result-section-title">⏭️ 건너뜀 (' + data.skipped_labels.length + ')</div>';
        for (const label of data.skipped_labels) {
            html += '<div class="setup-result-item">' + esc(label)
                + ' <span style="font-size:9px;color:var(--muted);">(기존 파일 존재 — 덮어쓰기 OFF)</span></div>';
        }
        html += '</div>';
    }

    // Errors
    if (data.error_messages && data.error_messages.length > 0) {
        html += '<div class="setup-result-section danger">';
        html += '<div class="setup-result-section-title">❌ 오류 (' + data.error_messages.length + ')</div>';
        for (const msg of data.error_messages) {
            html += '<div class="setup-result-item">' + esc(msg) + '</div>';
        }
        html += '</div>';
    }

    html += '<div style="margin-top:8px;font-size:10px;color:var(--muted);">'
        + '워크스페이스: ' + esc(data.workspace || _setupCurrentWs) + '</div>';
    html += '</div>';

    resultArea.innerHTML = html;
}

// ── Phase 4: Auto Sync Watcher ──

let _syncWatcherStatus = { running: false };

/**
 * Render the sync watcher toggle section in the setup panel.
 */
function renderSyncWatcherSection(container) {
    if (!_setupCurrentWs) return;

    let html = '';
    html += '<div id="syncWatcherSection" style="margin-top:12px;padding:10px;border:1px solid var(--border2);border-radius:8px;background:var(--bg2);">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">';
    html += '<span style="font-weight:600;font-size:12px;">🔄 자동 동기화</span>';
    html += '<label style="display:flex;align-items:center;gap:6px;font-size:11px;">';
    html += '<input type="checkbox" id="syncWatcherToggle" onchange="toggleSyncWatcher()" '
        + (_syncWatcherStatus.running ? 'checked' : '') + '>';
    html += '<span id="syncWatcherLabel">' + (_syncWatcherStatus.running ? '실행 중' : '중지됨') + '</span>';
    html += '</label>';
    html += '</div>';
    html += '<div id="syncWatcherInfo" style="font-size:10px;color:var(--muted);">';
    if (_syncWatcherStatus.running) {
        html += '마지막 동기화: ' + (_syncWatcherStatus.last_sync || '없음') + '<br>';
        html += '감시 파일: ' + (_syncWatcherStatus.files_watched || 0) + '개';
    } else {
        html += '코드 변경 시 AGENTS.md/CLAUDE.md 자동 갱신';
    }
    html += '</div>';

    // Git hooks section
    html += '<div style="margin-top:8px;display:flex;gap:4px;">';
    html += '<button class="setup-btn" style="font-size:10px;padding:3px 8px;" onclick="installSyncGitHooks()">🔗 Git Hook 설치</button>';
    html += '<button class="setup-btn" style="font-size:10px;padding:3px 8px;" onclick="uninstallSyncGitHooks()">🔓 Hook 제거</button>';
    html += '</div>';

    html += '</div>';

    container.innerHTML = (container.innerHTML || '') + html;
}

/**
 * Toggle sync watcher on/off.
 */
async function toggleSyncWatcher() {
    const toggle = document.getElementById('syncWatcherToggle');
    const label = document.getElementById('syncWatcherLabel');
    const info = document.getElementById('syncWatcherInfo');

    if (!toggle || !_setupCurrentWs) return;

    if (toggle.checked) {
        // Start watcher
        try {
            const data = await api('/api/sync/start', {
                method: 'POST',
                body: { workspace: _setupCurrentWs, debounce_seconds: 5.0 }
            });
            if (data.ok) {
                _syncWatcherStatus = data.status;
                label.textContent = '실행 중';
                info.innerHTML = '마지막 동기화: 없음<br>감시 파일: ' + (data.status.files_watched || 0) + '개';
                _showToast('자동 동기화가 시작되었습니다', 'success');
            } else {
                toggle.checked = false;
                label.textContent = '중지됨';
                _showToast('시작 실패: ' + (data.error || '알 수 없는 오류'), 'error');
            }
        } catch (err) {
            toggle.checked = false;
            label.textContent = '중지됨';
            _showToast('오류: ' + err.message, 'error');
        }
    } else {
        // Stop watcher
        try {
            const data = await api('/api/sync/stop', { method: 'POST', body: {} });
            if (data.ok) {
                _syncWatcherStatus = { running: false };
                label.textContent = '중지됨';
                info.innerHTML = '코드 변경 시 AGENTS.md/CLAUDE.md 자동 갱신';
                _showToast('자동 동기화가 중지되었습니다', 'success');
            } else {
                toggle.checked = true;
                _showToast('중지 실패: ' + (data.error || '알 수 없는 오류'), 'error');
            }
        } catch (err) {
            toggle.checked = true;
            _showToast('오류: ' + err.message, 'error');
        }
    }
}

/**
 * Install git hooks for auto-sync.
 */
async function installSyncGitHooks() {
    if (!_setupCurrentWs) return;
    try {
        const data = await api('/api/sync/hook/install', {
            method: 'POST',
            body: { workspace: _setupCurrentWs }
        });
        if (data.ok) {
            _showToast('Git Hook 설치 완료: ' + (data.installed || []).join(', '), 'success');
        } else {
            _showToast('설치 실패: ' + (data.error || ''), 'error');
        }
    } catch (err) {
        _showToast('오류: ' + err.message, 'error');
    }
}

/**
 * Uninstall git hooks.
 */
async function uninstallSyncGitHooks() {
    if (!_setupCurrentWs) return;
    try {
        const data = await api('/api/sync/hook/uninstall', {
            method: 'POST',
            body: { workspace: _setupCurrentWs }
        });
        if (data.ok) {
            _showToast('Git Hook 제거 완료: ' + (data.removed || []).join(', '), 'success');
        } else {
            _showToast('제거 실패: ' + (data.error || ''), 'error');
        }
    } catch (err) {
        _showToast('오류: ' + err.message, 'error');
    }
}

/**
 * Load sync watcher status.
 */
async function loadSyncWatcherStatus() {
    try {
        const data = await api('/api/sync/status');
        _syncWatcherStatus = data;
        const toggle = document.getElementById('syncWatcherToggle');
        const label = document.getElementById('syncWatcherLabel');
        const info = document.getElementById('syncWatcherInfo');
        if (toggle) toggle.checked = data.running;
        if (label) label.textContent = data.running ? '실행 중' : '중지됨';
        if (info) {
            if (data.running) {
                info.innerHTML = '마지막 동기화: ' + (data.last_sync || '없음') + '<br>감시 파일: ' + (data.files_watched || 0) + '개';
            } else {
                info.innerHTML = '코드 변경 시 AGENTS.md/CLAUDE.md 자동 갱신';
            }
        }
    } catch (err) { /* silently ignore */ }
}

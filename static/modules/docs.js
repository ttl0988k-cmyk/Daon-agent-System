/**
 * ── Auto Documentation Module ──
 * 
 * Dependency: core.js ($, api, showToast, State)
 * 
 * Provides: loadDocsPanel, generateDocs, updateDocsGenerateBtn,
 *           pollDocsJob — automated README/Wiki generation from codebase analysis.
 */
"use strict";

// ── Module state ──
var _docsJobId = null;
var _docsPollTimer = null;

/**
 * Load docs panel — called from switchPanel('docs')
 */
async function loadDocsPanel() {
    resetDocsUI();
    await loadDocsList();
}

/**
 * Full reset: clear progress, result, job state, and reload doc list
 */
async function resetDocsPanel() {
    stopPollingDocsJob();
    _docsJobId = null;
    resetDocsUI();
    // Clear result message text
    var result = document.getElementById('docsResult');
    if (result) {
        result.textContent = '';
        result.style.display = 'none';
    }
    // Reload doc list (await to ensure list refreshes)
    await loadDocsList();
    showToast('문서화 패널이 초기화되었습니다.', 'success');
}

/**
 * Update the generate button label based on selected checkboxes
 */
function updateDocsGenerateBtn() {
    var btn = document.getElementById('docsGenerateBtn');
    if (!btn) return;
    var checked = getSelectedDocTypes();
    btn.textContent = '\uD83D\uDE80 \uBB38\uC11C \uC0DD\uC131\uD558\uAE30 (' + checked.length + '\uAC1C \uC720\uD615)';
}

/**
 * Get selected document types from checkboxes
 */
function getSelectedDocTypes() {
    var container = document.getElementById('docsTypeCheckboxes');
    if (!container) return [];
    var checks = container.querySelectorAll('input[type="checkbox"]:checked');
    var types = [];
    for (var i = 0; i < checks.length; i++) {
        types.push(checks[i].value);
    }
    return types;
}

/**
 * Start document generation
 */
async function generateDocs() {
    if (!State.activeSessionId) {
        showToast('\uC138\uC158\uC744 \uBA3C\uC800 \uC120\uD0DD\uD558\uC138\uC694.', 'error');
        return;
    }

    var docTypes = getSelectedDocTypes();
    if (docTypes.length === 0) {
        showToast('\uCD5C\uC18C 1\uAC1C \uC774\uC0C1\uC758 \uBB38\uC11C \uC720\uD615\uC744 \uC120\uD0DD\uD558\uC138\uC694.', 'error');
        return;
    }

    var outputDir = document.getElementById('docsOutputDir');
    var dir = outputDir ? outputDir.value.trim() || 'docs' : 'docs';

    // UI feedback
    var btn = document.getElementById('docsGenerateBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '\u23F3 \uBB38\uC11C \uC0DD\uC131 \uC911...';
        btn.style.opacity = '0.6';
        btn.style.cursor = 'not-allowed';
    }

    // Show progress
    var progress = document.getElementById('docsProgress');
    if (progress) progress.style.display = 'block';
    updateDocsProgress(0, '\uC2DC\uC791 \uC900\uBE44 \uC911...');

    // Hide previous result
    var result = document.getElementById('docsResult');
    if (result) result.style.display = 'none';

    try {
        var data = await api('/api/docs/generate', {
            method: 'POST',
            body: {
                session_id: State.activeSessionId,
                doc_types: docTypes,
                output_dir: dir,
            },
        });

        if (data.success) {
            _docsJobId = data.job_id;
            showToast('\uBB38\uC11C \uC0DD\uC131\uC774 \uC2DC\uC791\uB418\uC5C8\uC2B5\uB2C8\uB2E4. (' + docTypes.length + '\uAC1C \uC720\uD615)', 'success');
            startPollingDocsJob();
        } else {
            showDocsResult(data.error || '\uC0DD\uC131 \uC2E4\uD328', 'error');
            resetDocsUI();
        }
    } catch (e) {
        showDocsResult('\uC694\uCCAD \uC2E4\uD328: ' + (e.message || e), 'error');
        resetDocsUI();
    }
}

/**
 * Poll documentation job progress
 */
function startPollingDocsJob() {
    stopPollingDocsJob();

    _docsPollTimer = setInterval(async function () {
        if (!_docsJobId) {
            stopPollingDocsJob();
            return;
        }

        try {
            var data = await api('/api/docs/status?job_id=' + encodeURIComponent(_docsJobId));

            if (data.status === 'completed') {
                stopPollingDocsJob();
                updateDocsProgress(100, '\uC644\uB8CC!');
                showDocsResult(
                    '\u2705 ' + (data.message || '\uBB38\uC11C \uC0DD\uC131 \uC644\uB8CC') + '\n' +
                    '\uBD84\uC11D\uB41C \uD30C\uC77C: ' + (data.result && data.result.files_analyzed ? data.result.files_analyzed : 'N/A') + '\uAC1C',
                    'success'
                );
                resetDocsUI();
                await loadDocsList();
            } else if (data.status === 'error') {
                stopPollingDocsJob();
                updateDocsProgress(0, '\uC624\uB958');
                showDocsResult('\u274C ' + (data.message || '\uBB38\uC11C \uC0DD\uC131 \uC2E4\uD328'), 'error');
                resetDocsUI();
            } else {
                updateDocsProgress(data.progress || 0, data.message || '\uCC98\uB9AC \uC911...');
            }
        } catch (e) {
            stopPollingDocsJob();
            showDocsResult('\uC0C1\uD0DC \uD655\uC778 \uC2E4\uD328: ' + (e.message || e), 'error');
            resetDocsUI();
        }
    }, 800);
}

/**
 * Stop polling for doc job progress
 */
function stopPollingDocsJob() {
    if (_docsPollTimer) {
        clearInterval(_docsPollTimer);
        _docsPollTimer = null;
    }
}

/**
 * Update progress bar UI
 */
function updateDocsProgress(percent, label) {
    var bar = document.getElementById('docsProgressBar');
    var percentEl = document.getElementById('docsProgressPercent');
    var labelEl = document.getElementById('docsProgressLabel');
    var progress = document.getElementById('docsProgress');

    if (progress) progress.style.display = 'block';
    if (bar) bar.style.width = Math.min(100, Math.max(0, percent)) + '%';
    if (percentEl) percentEl.textContent = Math.round(percent) + '%';
    if (labelEl) labelEl.textContent = label || '';
}

/**
 * Show result message
 */
function showDocsResult(message, type) {
    var el = document.getElementById('docsResult');
    if (!el) return;
    el.style.display = 'block';
    el.textContent = message;
    el.style.background = type === 'error' ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)';
    el.style.color = type === 'error' ? 'var(--danger)' : 'var(--success)';
    el.style.border = '1px solid ' + (type === 'error' ? 'var(--danger)' : 'var(--success)');
}

/**
 * Reset generate button UI
 */
function resetDocsUI() {
    var btn = document.getElementById('docsGenerateBtn');
    if (btn) {
        btn.disabled = false;
        btn.style.opacity = '1';
        btn.style.cursor = 'pointer';
        updateDocsGenerateBtn();
    }
    // Hide progress bar
    var progress = document.getElementById('docsProgress');
    if (progress) progress.style.display = 'none';
    // Hide result message
    var result = document.getElementById('docsResult');
    if (result) result.style.display = 'none';
    // Reset progress bar width
    var bar = document.getElementById('docsProgressBar');
    if (bar) bar.style.width = '0%';
}

/**
 * Load list of generated documents in workspace
 */
async function loadDocsList() {
    if (!State.activeSessionId) return;

    var listEl = document.getElementById('docsList');
    if (!listEl) return;

    listEl.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">\uBB38\uC11C\uB97C \uBD88\uB7EC\uC624\uB294 \uC911...</div>';

    try {
        var data = await api('/api/docs/list?session_id=' + encodeURIComponent(State.activeSessionId));

        if (!data.docs || data.docs.length === 0) {
            listEl.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px;text-align:center">\uC0DD\uC131\uB41C \uBB38\uC11C\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.<br>\uBB38\uC11C \uC720\uD615\uC744 \uC120\uD0DD\uD558\uACE0 \uC0DD\uC131 \uBC84\uD2BC\uC744 \uB20C\uB7EC\uC8FC\uC138\uC694.</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < data.docs.length; i++) {
            var doc = data.docs[i];
            var sizeKB = (doc.size_bytes / 1024).toFixed(1);
            var modDate = doc.modified ? doc.modified.split('T')[0] : '';
            var icon = doc.name === 'README.md' ? '\uD83D\uDCCB' : (doc.name.includes('ARCHITECTURE') ? '\uD83C\uDFD7\uFE0F' : '\uD83D\uDCC4');
            html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 8px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;gap:8px">' +
                '<div style="display:flex;align-items:center;gap:6px;overflow:hidden;flex:1">' +
                '<span style="font-size:14px">' + icon + '</span>' +
                '<div style="overflow:hidden">' +
                '<div style="font-size:11px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:monospace">' + escapeHtml(doc.path) + '</div>' +
                '<div style="font-size:9px;color:var(--muted)">' + sizeKB + ' KB' + (modDate ? ' \u00B7 ' + modDate : '') + '</div>' +
                '</div></div>' +
                '<button onclick="openDocFile(\'' + escapeJs(doc.path) + '\')" style="background:var(--bg3);color:var(--accent);border:1px solid var(--border2);padding:2px 8px;border-radius:4px;font-size:10px;cursor:pointer;white-space:nowrap">\uD83D\uDC41\uFE0F \uBCF4\uAE30</button>' +
                '</div>';
        }
        listEl.innerHTML = html;

    } catch (e) {
        listEl.innerHTML = '<div style="padding:8px;color:var(--danger);font-size:11px">\uBB38\uC11C \uBAA9\uB85D \uB85C\uB529 \uC2E4\uD328</div>';
    }
}

/**
 * Open a generated doc file in the editor
 */
function openDocFile(docPath) {
    if (!State.activeSessionId || !State.activeWorkspacePath) {
        showToast('\uC138\uC158\uACFC \uC791\uC5C5\uACF5\uAC04\uC774 \uD544\uC694\uD569\uB2C8\uB2E4.', 'error');
        return;
    }

    // Open file in Monaco editor tab
    if (typeof openFileInTab === 'function') {
        openFileInTab(docPath);
        showToast('\uBB38\uC11C \uC5F4\uAE30: ' + docPath, 'success');
    } else if (typeof openFile === 'function') {
        openFile(docPath);
        showToast('\uBB38\uC11C \uC5F4\uAE30: ' + docPath, 'success');
    } else {
        showToast('\uBB38\uC11C\uAC00 \uC0DD\uC131\uB418\uC5C8\uC2B5\uB2C8\uB2E4: ' + docPath + ' (\uD30C\uC77C \uD0D0\uC9C9\uAE30\uC5D0\uC11C \uD655\uC778\uD574\uC8FC\uC138\uC694)', 'success');
    }
}

/**
 * Escape HTML special characters
 */
function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"');
}

/**
 * Escape string for use in JS string literal
 */
function escapeJs(str) {
    if (!str) return '';
    return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}


/**
 * Open web explorer to browse for docs output directory
 */
function browseDocsOutputDir() {
    if (typeof openWebExplorer === 'function') {
        openWebExplorer({
            type: 'dir',
            title: '출력 디렉토리 선택',
            initialPath: State.activeWorkspacePath || '',
            onSelect: function (selectedPath) {
                var input = document.getElementById('docsOutputDir');
                if (input) {
                    // Convert absolute path to relative if inside workspace
                    if (State.activeWorkspacePath && selectedPath.startsWith(State.activeWorkspacePath)) {
                        var rel = selectedPath.substring(State.activeWorkspacePath.length);
                        if (rel.startsWith('/') || rel.startsWith('\\')) {
                            rel = rel.substring(1);
                        }
                        input.value = rel || 'docs';
                    } else {
                        input.value = selectedPath;
                    }
                }
                showToast('출력 디렉토리: ' + selectedPath, 'success');
            }
        });
    } else {
        showToast('파일 탐색기 기능을 사용할 수 없습니다.', 'error');
    }
}

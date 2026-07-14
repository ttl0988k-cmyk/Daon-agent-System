/**
 * diff_preview.js — Diff Preview UI Module
 * Real-time Diff Panel, Change History, File-by-file change list, AI Workflow Log.
 *
 * Flow: AI → Preview → Monaco Diff → User Approval → Apply → Checkpoint
 */
"use strict";

// ── State ────────────────────────────────────────────────────────────────────
var _diffPreviewState = {
    activePreviews: {},      // preview_id → preview data
    historyEntries: [],      // change history list
    currentPreviewId: null,  // currently viewing preview_id
    workflowSteps: [],       // AI workflow log
};

// ── Panel HTML ───────────────────────────────────────────────────────────────

var _diffPanelHTML = [
    '<!-- Diff Preview Panel -->',
    '<div id="diffPreviewPanel" style="display:none;">',
    '',
    '  <!-- Active Preview Bar -->',
    '  <div id="diffActiveBar" style="display:none; background:var(--bg2); border:1px solid var(--accent); border-radius:6px; padding:6px 10px; margin-bottom:6px;">',
    '    <div style="display:flex; align-items:center; gap:8px; justify-content:space-between;">',
    '      <div style="display:flex; align-items:center; gap:6px; font-size:12px; color:var(--accent);">',
    '        <span>📄</span>',
    '        <span id="diffPreviewLabel">Changes pending…</span>',
    '      </div>',
    '      <div style="display:flex; gap:4px;">',
    '        <button class="diff-btn diff-btn-apply" id="diffApplyBtn" title="Apply all changes">✓ 적용</button>',
    '        <button class="diff-btn diff-btn-reject" id="diffRejectBtn" title="Reject all changes">✕ 거절</button>',
    '        <button class="diff-btn diff-btn-view" id="diffViewBtn" title="View in Monaco Diff">👁 상세 보기</button>',
    '      </div>',
    '    </div>',
    '  </div>',
    '',
    '  <!-- File-by-File Change List -->',
    '  <div id="diffFileList" style="display:none; max-height:200px; overflow-y:auto; background:var(--bg2); border-radius:6px; padding:4px; margin-bottom:6px;">',
    '    <div style="font-size:11px; color:var(--text2); padding:4px 6px; font-weight:600;">📁 변경 파일 목록</div>',
    '    <div id="diffFileListItems"></div>',
    '  </div>',
    '',
    '  <!-- AI Workflow Log -->',
    '  <div id="diffWorkflowLog" style="display:none; max-height:180px; overflow-y:auto; background:var(--bg2); border-radius:6px; padding:4px; margin-bottom:6px;">',
    '    <div style="font-size:11px; color:var(--text2); padding:4px 6px; font-weight:600;">🤖 AI 작업 로그</div>',
    '    <div id="diffWorkflowSteps"></div>',
    '  </div>',
    '',
    '  <!-- Change History -->',
    '  <div id="diffHistoryPanel" style="display:none; max-height:250px; overflow-y:auto; background:var(--bg2); border-radius:6px; padding:4px;">',
    '    <div style="display:flex; justify-content:space-between; align-items:center; padding:4px 6px;">',
    '      <span style="font-size:11px; color:var(--text2); font-weight:600;">📜 변경 히스토리</span>',
    '      <button onclick="_clearDiffHistory()" style="background:none; border:none; color:var(--text2); cursor:pointer; font-size:11px;" title="Clear history">🗑</button>',
    '    </div>',
    '    <div id="diffHistoryList"></div>',
    '  </div>',
    '',
    '</div>'
].join('\n');

// ── Initialization ───────────────────────────────────────────────────────────

function initDiffPreview() {
    // Insert diff panel before chat content
    var chatContent = document.getElementById('chatModeContent');
    if (!chatContent || !chatContent.parentNode) return;

    var panel = document.createElement('div');
    panel.innerHTML = _diffPanelHTML;
    var diffPanel = panel.firstElementChild;

    // Insert before chatModeContent in its parent
    chatContent.parentNode.insertBefore(diffPanel, chatContent);

    // Setup event listeners
    var applyBtn = document.getElementById('diffApplyBtn');
    var rejectBtn = document.getElementById('diffRejectBtn');
    var viewBtn = document.getElementById('diffViewBtn');

    if (applyBtn) applyBtn.addEventListener('click', _applyCurrentPreview);
    if (rejectBtn) rejectBtn.addEventListener('click', _rejectCurrentPreview);
    if (viewBtn) viewBtn.addEventListener('click', _viewCurrentPreviewDiff);

    // Load initial history
    _refreshChangeHistory();
}

// ── Load Preview (called when AI sends a diff) ──────────────────────────────

async function loadDiffPreview(sessionId, filePath, diffText, sourceAgent) {
    if (!sessionId || !filePath || !diffText) {
        console.warn('[DiffPreview] Missing required params');
        return null;
    }

    try {
        var res = await api('/api/file/preview-diff', {
            method: 'POST',
            body: {
                session_id: sessionId,
                path: filePath,
                diff: diffText,
                source_agent: sourceAgent || 'unknown'
            }
        });

        if (res.ok && res.preview_id) {
            _diffPreviewState.activePreviews[res.preview_id] = res;
            _diffPreviewState.currentPreviewId = res.preview_id;

            // Add to workflow log
            _addWorkflowStep(sourceAgent || 'unknown', filePath, res.line_changes);

            // Update UI
            _renderActivePreviewBar(res);
            _updateFileChangeList();
            _refreshChangeHistory();
            _showDiffPanel();

            return res.preview_id;
        } else {
            console.error('[DiffPreview] Preview failed:', res.error || res.details);
            return null;
        }
    } catch (e) {
        console.error('[DiffPreview] Error loading preview:', e);
        return null;
    }
}

// ── Multi-file preview support ──────────────────────────────────────────────

async function loadMultiDiffPreview(sessionId, fileDiffs) {
    // fileDiffs: [{path, diff, agent}]
    var previewIds = [];
    for (var i = 0; i < fileDiffs.length; i++) {
        var fd = fileDiffs[i];
        var pid = await loadDiffPreview(sessionId, fd.path, fd.diff, fd.agent);
        if (pid) previewIds.push(pid);
    }
    return previewIds;
}

// ── UI Rendering ─────────────────────────────────────────────────────────────

function _showDiffPanel() {
    var panel = document.getElementById('diffPreviewPanel');
    if (panel) panel.style.display = 'block';
}

function _hideDiffPanel() {
    var panel = document.getElementById('diffPreviewPanel');
    if (panel) panel.style.display = 'none';
}

function _renderActivePreviewBar(preview) {
    var bar = document.getElementById('diffActiveBar');
    var label = document.getElementById('diffPreviewLabel');
    if (!bar || !label) return;

    bar.style.display = 'block';

    var lc = preview.line_changes || {};
    var file = preview.path || 'unknown';
    var added = lc.added || 0;
    var removed = lc.removed || 0;

    var agentIcon = _agentIcon(preview.source_agent || 'unknown');
    label.textContent = file + '  ' + agentIcon + ' +' + added + ' -' + removed;
}

function _updateFileChangeList() {
    var list = document.getElementById('diffFileList');
    var items = document.getElementById('diffFileListItems');
    if (!list || !items) return;

    var previews = Object.values(_diffPreviewState.activePreviews);
    if (previews.length === 0) {
        list.style.display = 'none';
        return;
    }

    list.style.display = 'block';
    items.innerHTML = '';

    for (var i = 0; i < previews.length; i++) {
        var p = previews[i];
        var lc = p.line_changes || {};
        var activeClass = p.preview_id === _diffPreviewState.currentPreviewId ? ' diff-file-active' : '';

        var row = document.createElement('div');
        row.className = 'diff-file-row' + activeClass;
        row.onclick = (function (pid) {
            return function () { _setCurrentPreview(pid); };
        })(p.preview_id);

        var agentIcon = _agentIcon(p.source_agent || 'unknown');
        row.innerHTML =
            '<span style="font-size:11px; color:var(--accent);">📄</span>' +
            '<span style="flex:1; font-size:12px; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' +
            _escapeHtml(p.path) +
            '</span>' +
            '<span style="font-size:10px; color:var(--text2);">' + agentIcon + '</span>' +
            '<span style="font-size:10px; color:var(--success);">+' + (lc.added || 0) + '</span>' +
            '<span style="font-size:10px; color:var(--danger);">-' + (lc.removed || 0) + '</span>' +
            '<button class="diff-file-apply-btn" onclick="event.stopPropagation();_applySinglePreview(\'' + p.preview_id + '\')" title="Apply">✓</button>' +
            '<button class="diff-file-reject-btn" onclick="event.stopPropagation();_rejectSinglePreview(\'' + p.preview_id + '\')" title="Reject">✕</button>';

        items.appendChild(row);
    }

    // Always show current preview
    if (_diffPreviewState.currentPreviewId) {
        _setCurrentPreview(_diffPreviewState.currentPreviewId);
    }
}

function _setCurrentPreview(previewId) {
    _diffPreviewState.currentPreviewId = previewId;
    var preview = _diffPreviewState.activePreviews[previewId];
    if (preview) {
        _renderActivePreviewBar(preview);
    }

    // Highlight active row
    var rows = document.querySelectorAll('.diff-file-row');
    for (var i = 0; i < rows.length; i++) {
        rows[i].classList.remove('diff-file-active');
    }
    // Find and highlight by preview_id
    var btns = document.querySelectorAll('.diff-file-apply-btn');
    for (var j = 0; j < btns.length; j++) {
        var onclick = btns[j].getAttribute('onclick') || '';
        if (onclick.indexOf(previewId) !== -1) {
            var row = btns[j].closest('.diff-file-row');
            if (row) row.classList.add('diff-file-active');
        }
    }
}

// ── Monaco Side-by-Side Diff ────────────────────────────────────────────────

function _viewCurrentPreviewDiff() {
    var pid = _diffPreviewState.currentPreviewId;
    if (!pid) return;
    _viewPreviewDiff(pid);
}

function _viewPreviewDiff(previewId) {
    var preview = _diffPreviewState.activePreviews[previewId];
    if (!preview || !window.monaco) {
        showToast('Monaco editor not ready yet. Please wait.');
        return;
    }

    var original = preview.original_full || preview.original || '';
    var modified = preview.new_full || preview.new_content || '';
    var path = preview.path || 'diff';

    // Determine language
    var ext = path.split('.').pop().toLowerCase();
    var lang = 'plaintext';
    if (ext === 'js') lang = 'javascript';
    else if (ext === 'py') lang = 'python';
    else if (ext === 'html') lang = 'html';
    else if (ext === 'css') lang = 'css';
    else if (ext === 'json') lang = 'json';
    else if (ext === 'md') lang = 'markdown';
    else if (ext === 'ts') lang = 'typescript';
    else if (ext === 'yaml' || ext === 'yml') lang = 'yaml';
    else if (ext === 'xml') lang = 'xml';
    else if (ext === 'java') lang = 'java';
    else if (ext === 'cpp' || ext === 'c' || ext === 'h') lang = 'cpp';
    else if (ext === 'rs') lang = 'rust';
    else if (ext === 'go') lang = 'go';

    // Create diff models
    var originalModel = monaco.editor.createModel(original, lang);
    var modifiedModel = monaco.editor.createModel(modified, lang);

    // Create diff editor in the middle panel
    var diffContainer = document.createElement('div');
    diffContainer.id = 'monacoDiffContainer';
    diffContainer.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;z-index:100;';

    var editorArea = document.querySelector('.editor-area');
    if (!editorArea) return;

    // Hide canvas area, show diff
    var canvasArea = document.querySelector('.canvas-area');
    if (canvasArea) canvasArea.style.display = 'none';

    editorArea.appendChild(diffContainer);

    var diffEditor = monaco.editor.createDiffEditor(diffContainer, {
        theme: 'vs-dark',
        automaticLayout: true,
        readOnly: true,
        renderSideBySide: true,
        fontSize: 12,
        fontFamily: 'Fira Code, monospace',
        minimap: { enabled: false },
        scrollbar: { verticalScrollbarSize: 8 },
    });

    diffEditor.setModel({
        original: originalModel,
        modified: modifiedModel,
    });

    // Add close button overlay
    var closeBtn = document.createElement('button');
    closeBtn.textContent = '✕ Close Diff';
    closeBtn.style.cssText =
        'position:absolute; top:8px; right:8px; z-index:101; ' +
        'background:var(--bg2); color:var(--text); border:1px solid var(--border); ' +
        'border-radius:4px; padding:4px 10px; cursor:pointer; font-size:12px;';
    closeBtn.onclick = function () {
        diffEditor.dispose();
        originalModel.dispose();
        modifiedModel.dispose();
        diffContainer.remove();
        if (canvasArea) canvasArea.style.display = 'flex';
    };
    diffContainer.appendChild(closeBtn);

    // Store reference for cleanup
    _diffPreviewState._diffEditor = diffEditor;
    _diffPreviewState._diffContainer = diffContainer;
    _diffPreviewState._originalModel = originalModel;
    _diffPreviewState._modifiedModel = modifiedModel;
}

// ── Apply / Reject Actions ──────────────────────────────────────────────────

async function _applyCurrentPreview() {
    var pid = _diffPreviewState.currentPreviewId;
    if (!pid) return;
    await _applySinglePreview(pid);
}

async function _applySinglePreview(previewId) {
    var sid = State.activeSessionId;
    if (!sid) return;

    try {
        var res = await api('/api/file/apply-preview', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                preview_id: previewId
            })
        });

        if (res.ok) {
            // Remove from active previews
            delete _diffPreviewState.activePreviews[previewId];
            if (_diffPreviewState.currentPreviewId === previewId) {
                _diffPreviewState.currentPreviewId = null;
            }

            // Refresh UI
            _updateFileChangeList();
            _refreshChangeHistory();

            // Refresh file tree if the file was modified
            if (typeof refreshFileTree === 'function') {
                refreshFileTree();
            }

            // Close Monaco diff if open
            _closeDiffEditor();

            // Show toast
            _showToast('✓ Changes applied: ' + (res.path || ''));

            // Hide panel if no more previews
            if (Object.keys(_diffPreviewState.activePreviews).length === 0) {
                _hideDiffPanel();
            }
        } else {
            _showToast('⚠ Failed to apply: ' + (res.error || 'unknown error'));
        }
    } catch (e) {
        console.error('[DiffPreview] Apply error:', e);
        _showToast('⚠ Error applying diff: ' + e.message);
    }
}

async function _rejectCurrentPreview() {
    var pid = _diffPreviewState.currentPreviewId;
    if (!pid) return;
    await _rejectSinglePreview(pid);
}

async function _rejectSinglePreview(previewId) {
    var sid = State.activeSessionId;
    if (!sid) return;

    try {
        var res = await api('/api/file/reject-preview', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                preview_id: previewId
            })
        });

        if (res.ok) {
            delete _diffPreviewState.activePreviews[previewId];
            if (_diffPreviewState.currentPreviewId === previewId) {
                _diffPreviewState.currentPreviewId = null;
            }

            _updateFileChangeList();
            _refreshChangeHistory();
            _closeDiffEditor();
            _showToast('✕ Changes rejected');

            if (Object.keys(_diffPreviewState.activePreviews).length === 0) {
                _hideDiffPanel();
            }
        }
    } catch (e) {
        console.error('[DiffPreview] Reject error:', e);
        _showToast('⚠ Error rejecting diff: ' + e.message);
    }
}

function _closeDiffEditor() {
    try {
        if (_diffPreviewState._diffEditor) {
            _diffPreviewState._diffEditor.dispose();
            _diffPreviewState._diffEditor = null;
        }
        if (_diffPreviewState._originalModel) {
            _diffPreviewState._originalModel.dispose();
            _diffPreviewState._originalModel = null;
        }
        if (_diffPreviewState._modifiedModel) {
            _diffPreviewState._modifiedModel.dispose();
            _diffPreviewState._modifiedModel = null;
        }
        if (_diffPreviewState._diffContainer) {
            _diffPreviewState._diffContainer.remove();
            _diffPreviewState._diffContainer = null;
        }
        var canvasArea = document.querySelector('.canvas-area');
        if (canvasArea) canvasArea.style.display = 'flex';
    } catch (e) {
        // ignore cleanup errors
    }
}

// ── Change History ──────────────────────────────────────────────────────────

async function _refreshChangeHistory() {
    var sid = State.activeSessionId;
    if (!sid) return;

    try {
        var res = await api('/api/diff/history?session_id=' + encodeURIComponent(sid));
        _diffPreviewState.historyEntries = res.history || [];
        _renderChangeHistory();
    } catch (e) {
        console.error('[DiffPreview] History load error:', e);
    }
}

function _renderChangeHistory() {
    var panel = document.getElementById('diffHistoryPanel');
    var list = document.getElementById('diffHistoryList');
    if (!panel || !list) return;

    var entries = _diffPreviewState.historyEntries;
    if (entries.length === 0) {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    list.innerHTML = '';

    for (var i = 0; i < entries.length; i++) {
        var entry = entries[i];
        var lc = entry.line_changes || {};
        var added = lc.added || 0;
        var removed = lc.removed || 0;
        var agentIcon = _agentIcon(entry.agent || 'unknown');
        var actionIcon = entry.action === 'applied' ? '✅' :
            entry.action === 'rejected' ? '❌' :
                entry.action === 'rollback' ? '↩️' : '📝';

        var row = document.createElement('div');
        row.className = 'diff-history-row';
        row.innerHTML =
            '<span style="font-size:10px; color:var(--text2); min-width:42px;">' + (entry.time || '') + '</span>' +
            '<span style="font-size:11px; margin-right:2px;">' + actionIcon + '</span>' +
            '<span style="font-size:11px; margin-right:2px;">' + agentIcon + '</span>' +
            '<span style="flex:1; font-size:11px; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + _escapeHtml(entry.file || '') + '">' +
            _escapeHtml(entry.file || 'unknown') +
            '</span>' +
            '<span style="font-size:10px; color:var(--success);">+' + added + '</span>' +
            '<span style="font-size:10px; color:var(--danger); margin-left:2px;">-' + removed + '</span>';

        // Rollback button for applied entries
        if (entry.action === 'applied' && entry.checkpoint_id) {
            var rbBtn = document.createElement('button');
            rbBtn.textContent = '↩';
            rbBtn.title = 'Rollback to this checkpoint';
            rbBtn.style.cssText =
                'background:none; border:none; color:var(--text2); cursor:pointer; font-size:11px; padding:0 2px; margin-left:4px;';
            rbBtn.onclick = (function (cpid) {
                return function (e) {
                    e.stopPropagation();
                    _rollbackToCheckpoint(cpid);
                };
            })(entry.checkpoint_id);
            row.appendChild(rbBtn);
        }

        list.appendChild(row);
    }
}

async function _clearDiffHistory() {
    if (!confirm('변경 히스토리를 모두 삭제하시겠습니까?')) return;
    // History is in-memory, clear by reloading (will be empty on server)
    _diffPreviewState.historyEntries = [];
    _renderChangeHistory();
    var panel = document.getElementById('diffHistoryPanel');
    if (panel) panel.style.display = 'none';
}

async function _rollbackToCheckpoint(checkpointId) {
    var sid = State.activeSessionId;
    if (!sid || !confirm('이 체크포인트로 롤백하시겠습니까?')) return;

    try {
        var res = await api('/api/checkpoints/rollback', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                checkpoint_id: checkpointId
            })
        });

        if (res.ok) {
            _showToast('↩ Rollback complete: ' + (res.file_path || ''));
            _refreshChangeHistory();
            if (typeof refreshFileTree === 'function') refreshFileTree();
        }
    } catch (e) {
        console.error('[DiffPreview] Rollback error:', e);
        _showToast('⚠ Rollback failed');
    }
}

// ── AI Workflow Log ─────────────────────────────────────────────────────────

function _addWorkflowStep(agent, file, lineChanges) {
    var step = {
        time: new Date().toTimeString().slice(0, 5),
        agent: agent,
        file: file,
        added: (lineChanges && lineChanges.added) || 0,
        removed: (lineChanges && lineChanges.removed) || 0,
    };
    _diffPreviewState.workflowSteps.push(step);
    _renderWorkflowLog();
}

function _renderWorkflowLog() {
    var log = document.getElementById('diffWorkflowLog');
    var steps = document.getElementById('diffWorkflowSteps');
    if (!log || !steps) return;

    var wfSteps = _diffPreviewState.workflowSteps;
    if (wfSteps.length === 0) {
        log.style.display = 'none';
        return;
    }

    log.style.display = 'block';

    // Build pipeline visualization: CEO ↓ Architect ↓ Coder ↓ Reviewer ↓ Patch
    var agentOrder = ['ceo', 'architect', 'coder', 'reviewer', 'patch'];
    var seen = {};
    var pipelineHTML = '<div style="display:flex; align-items:center; gap:2px; padding:4px 6px; flex-wrap:wrap;">';

    for (var a = 0; a < agentOrder.length; a++) {
        var agent = agentOrder[a];
        var found = false;
        for (var i = 0; i < wfSteps.length; i++) {
            if (wfSteps[i].agent === agent && !seen[agent]) {
                seen[agent] = true;
                found = true;
                break;
            }
        }
        var agentIcon = _agentIcon(agent);
        var agentLabel = _agentLabel(agent);
        pipelineHTML +=
            '<span style="font-size:10px; color:' + (found ? 'var(--accent)' : 'var(--text3)') + '; ' +
            'background:var(--bg3); border-radius:4px; padding:2px 5px; white-space:nowrap;">' +
            agentIcon + ' ' + agentLabel +
            '</span>';
        if (a < agentOrder.length - 1) {
            pipelineHTML += '<span style="color:var(--text3); font-size:9px;">↓</span>';
        }
    }
    pipelineHTML += '</div>';

    // Detailed steps
    var detailHTML = '';
    for (var j = 0; j < wfSteps.length && j < 10; j++) {
        var s = wfSteps[j];
        detailHTML +=
            '<div style="display:flex; align-items:center; gap:4px; padding:2px 6px; font-size:10px;">' +
            '<span style="color:var(--text2); width:35px;">' + s.time + '</span>' +
            '<span>' + _agentIcon(s.agent) + '</span>' +
            '<span style="color:var(--text2);">' + _agentLabel(s.agent) + '</span>' +
            '<span style="color:var(--text); font-size:10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; flex:1;">' +
            _escapeHtml(s.file) +
            '</span>' +
            '<span style="color:var(--success);">+' + s.added + '</span>' +
            '<span style="color:var(--danger);">-' + s.removed + '</span>' +
            '</div>';
    }

    steps.innerHTML = pipelineHTML + detailHTML;
}

// ── Exposure to AI streaming ────────────────────────────────────────────────

/**
 * Called from streaming handler when AI returns a diff/edit.
 * @param {string} sessionId
 * @param {string} filePath
 * @param {string} diffText  - The SEARCH/REPLACE diff block text
 * @param {string} sourceAgent - 'coder', 'architect', etc.
 */
async function previewAIDiff(sessionId, filePath, diffText, sourceAgent) {
    return await loadDiffPreview(sessionId, filePath, diffText, sourceAgent);
}

/**
 * Direct apply without preview (backward compatible with existing flow).
 */
// ── Approval Banner (Architect mode) ─────────────────────────────────────────

/**
 * Show approval-required banner in the diff preview panel.
 * Called from the SSE 'approval' event listener.
 */
function _showApprovalBanner(data) {
    if (!data || data.status !== 'pending') return;

    var panel = document.getElementById('diffPreviewPanel');
    var bar = document.getElementById('diffActiveBar');
    if (!panel || !bar) return;

    panel.style.display = 'block';
    bar.style.display = 'block';
    bar.style.borderColor = 'var(--warning-orange, #e67e22)';
    bar.style.boxShadow = '0 0 8px rgba(230, 126, 34, .3)';

    var label = document.getElementById('diffPreviewLabel');
    if (!label) return;

    var lc = data.line_changes || {};
    var file = data.path || 'unknown';
    var added = lc.added || 0;
    var removed = lc.removed || 0;

    // Replace action buttons with approval buttons
    var applyBtn = document.getElementById('diffApplyBtn');
    var rejectBtn = document.getElementById('diffRejectBtn');
    var viewBtn = document.getElementById('diffViewBtn');

    if (applyBtn) {
        applyBtn.textContent = '✅ 승인';
        applyBtn.style.background = 'var(--warning-orange, #e67e22)';
        applyBtn.style.color = '#fff';
        applyBtn.style.borderColor = 'var(--warning-orange, #e67e22)';
        applyBtn.onclick = function () { _approveAndApply(data.preview_id, data.path, data.is_plan); };
    }
    if (rejectBtn) {
        rejectBtn.textContent = '❌ 거절';
        rejectBtn.style.display = 'inline-block';
        rejectBtn.onclick = function () { _rejectAndDiscard(data.preview_id, data.path); };
    }
    if (viewBtn) {
        viewBtn.style.display = 'inline-block';
    }

    if (data.is_plan) {
        label.innerHTML = '🪃 <span style="color:var(--warning-orange, #e67e22); font-weight:700;">Orchestrator Plan Approval</span> — ' + _escapeHtml(data.message || 'Review execution plan');
    } else {
        label.innerHTML = '🏗️ <span style="color:var(--warning-orange, #e67e22); font-weight:700;">Architect Approval Required</span> — '
            + _escapeHtml(file) + ' +' + added + ' -' + removed;
    }

    if (data.is_plan) {
        _showToast('⚠ Orchestrator plan approval required.');
    } else {
        _showToast('⚠ Architect approval required for: ' + file);
    }
}

/**
 * Approve the previewed changes and auto-apply them.
 */
async function _approveAndApply(previewId, filePath, isPlan) {
    var sid = State.sessionId || State.activeSessionId;
    if (!sid || !previewId) {
        _showToast('⚠ Cannot approve: missing session or preview');
        return;
    }

    try {
        // Call approval API
        var apprRes = await api('/api/approval/approve', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                preview_id: previewId,
                reviewer: 'user'
            })
        });
        console.log('[Approval] Approve result:', apprRes);

        var applyRes = { ok: true };
        if (!isPlan) {
            // Then apply the diff
            applyRes = await api('/api/file/apply-preview', {
                method: 'POST',
                body: JSON.stringify({
                    session_id: sid,
                    preview_id: previewId
                })
            });
        }

        if (applyRes.ok) {
            // Clean up state
            delete _diffPreviewState.activePreviews[previewId];
            if (_diffPreviewState.currentPreviewId === previewId) {
                _diffPreviewState.currentPreviewId = null;
            }

            _updateFileChangeList();
            _refreshChangeHistory();
            _closeDiffEditor();

            if (typeof refreshFileTree === 'function') {
                refreshFileTree();
            }

            // Reset buttons
            _resetApprovalButtons();

            _showToast('✅ Architect changes approved & applied: ' + (filePath || ''));

            // Hide panel if no more previews
            if (Object.keys(_diffPreviewState.activePreviews).length === 0) {
                _hideDiffPanel();
            }
        } else {
            _showToast('⚠ Failed to apply: ' + (applyRes.error || 'unknown error'));
        }
    } catch (e) {
        console.error('[Approval] Approve error:', e);
        _showToast('⚠ Error approving diff: ' + e.message);
    }
}

/**
 * Reject the previewed changes and discard them.
 */
async function _rejectAndDiscard(previewId, filePath) {
    var sid = State.sessionId || State.activeSessionId;
    if (!sid || !previewId) {
        _showToast('⚠ Cannot reject: missing session or preview');
        return;
    }

    try {
        // Call approval reject API
        var apprRes = await api('/api/approval/reject', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                preview_id: previewId,
                reason: 'User rejected architect changes',
                reviewer: 'user'
            })
        });
        console.log('[Approval] Reject result:', apprRes);

        // Then reject the diff preview
        var rejectRes = await api('/api/file/reject-preview', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sid,
                preview_id: previewId
            })
        });

        if (rejectRes.ok) {
            delete _diffPreviewState.activePreviews[previewId];
            if (_diffPreviewState.currentPreviewId === previewId) {
                _diffPreviewState.currentPreviewId = null;
            }

            _updateFileChangeList();
            _refreshChangeHistory();
            _closeDiffEditor();

            // Reset buttons
            _resetApprovalButtons();

            _showToast('❌ Architect changes rejected: ' + (filePath || ''));

            if (Object.keys(_diffPreviewState.activePreviews).length === 0) {
                _hideDiffPanel();
            }
        }
    } catch (e) {
        console.error('[Approval] Reject error:', e);
        _showToast('⚠ Error rejecting diff: ' + e.message);
    }
}

/**
 * Reset the approve/reject buttons to normal apply/reject buttons.
 */
function _resetApprovalButtons() {
    var bar = document.getElementById('diffActiveBar');
    if (bar) {
        bar.style.borderColor = '';
        bar.style.boxShadow = '';
    }

    var applyBtn = document.getElementById('diffApplyBtn');
    var rejectBtn = document.getElementById('diffRejectBtn');

    if (applyBtn) {
        applyBtn.textContent = '✓ Apply';
        applyBtn.style.background = '';
        applyBtn.style.color = '';
        applyBtn.style.borderColor = '';
        applyBtn.onclick = _applyCurrentPreview;
    }
    if (rejectBtn) {
        rejectBtn.textContent = '✕ Reject';
        rejectBtn.onclick = _rejectCurrentPreview;
    }
}

// ── Direct apply (backward compat) ───────────────────────────────────────────

async function applyAIDiffDirect(sessionId, filePath, diffText, sourceAgent) {
    try {
        var res = await api('/api/file/apply-diff', {
            method: 'POST',
            body: JSON.stringify({
                session_id: sessionId,
                path: filePath,
                diff: diffText,
                source_agent: sourceAgent || 'direct'
            })
        });

        if (res.ok) {
            _refreshChangeHistory();
            if (typeof refreshFileTree === 'function') refreshFileTree();
        }
        return res;
    } catch (e) {
        console.error('[DiffPreview] Direct apply error:', e);
        return { ok: false, error: e.message };
    }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function _agentIcon(agent) {
    var icons = {
        'ceo': '👑', 'architect': '🏗️', 'coder': '💻', 'reviewer': '👁️',
        'patch': '📌', 'debug': '🪲', 'test': '🧪', 'orchestrator': '🪃',
        'ask': '❓', 'rollback': '↩️', 'direct': '⚡', 'unknown': '🤖'
    };
    return icons[agent] || '🤖';
}

function _agentLabel(agent) {
    var labels = {
        'ceo': 'CEO', 'architect': 'Architect', 'coder': 'Coder',
        'reviewer': 'Reviewer', 'patch': 'Patch', 'debug': 'Debug',
        'test': 'Test', 'orchestrator': 'Orch', 'ask': 'Ask',
        'rollback': 'Rollback', 'direct': 'Direct', 'unknown': 'AI'
    };
    return labels[agent] || 'AI';
}

function _escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"');
}

function _showToast(msg) {
    var toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(function () { toast.classList.remove('show'); }, 2500);
}

// ── Auto-init ────────────────────────────────────────────────────────────────

// Initialize after DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDiffPreview);
} else {
    // DOM already loaded, run on next tick to ensure other modules are ready
    setTimeout(initDiffPreview, 100);
}

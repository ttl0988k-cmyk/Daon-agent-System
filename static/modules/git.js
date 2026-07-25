// ── Git Automation Module ──
// Provides commit, push, pull, diff, log UI for the active session workspace.

let _gitAutoRefreshTimer = null;

async function loadGitPanel() {
    const sid = State.activeSessionId;
    const elBranch = $('gitBranch');
    const elCommit = $('gitCommitShort');
    const elAheadBehind = $('gitAheadBehind');
    const elChanged = $('gitChangedFiles');
    const elDiff = $('gitDiffOutput');
    const elResult = $('gitResult');
    const elCommitBtn = $('gitCommitBtn');

    if (!sid) {
        if (elBranch) elBranch.textContent = '(세션 선택 필요)';
        return;
    }
    try {
        const data = await api('/api/git/status?session_id=' + encodeURIComponent(sid));
        renderGitPanel(data, {
            gitBranch: elBranch,
            gitCommitShort: elCommit,
            gitAheadBehind: elAheadBehind,
            gitChangedFiles: elChanged,
            gitDiffOutput: elDiff,
            gitResult: elResult,
            gitCommitBtn: elCommitBtn
        });
    } catch (e) {
        if (elBranch) elBranch.textContent = '오류';
        if (elCommit) elCommit.textContent = 'Git 정보를 가져올 수 없습니다.';
    }
}

function renderGitPanel(data, els) {
    const git = data.git;
    if (!git || !git.is_git) {
        if (els.gitBranch) els.gitBranch.textContent = '(Git 아님)';
        if (els.gitCommitShort) els.gitCommitShort.textContent = '이 작업공간은 Git 저장소가 아닙니다.';
        if (els.gitAheadBehind) els.gitAheadBehind.textContent = '';
        if (els.gitChangedFiles) els.gitChangedFiles.innerHTML = '';
        if (els.gitDiffOutput) els.gitDiffOutput.textContent = '';
        if (els.gitCommitBtn) els.gitCommitBtn.disabled = true;
        return;
    }

    // Branch
    if (els.gitBranch) els.gitBranch.textContent = git.branch;

    // Commit short & ahead/behind
    if (els.gitCommitShort) els.gitCommitShort.textContent = git.commit_short || '...';
    const abParts = [];
    if (git.ahead) abParts.push('↑' + git.ahead);
    if (git.behind) abParts.push('↓' + git.behind);
    if (els.gitAheadBehind) els.gitAheadBehind.textContent = abParts.join(' ');

    // Commit button state
    if (els.gitCommitBtn) {
        els.gitCommitBtn.disabled = !git.dirty;
        els.gitCommitBtn.style.opacity = git.dirty ? '1' : '0.5';
    }

    // Changed files
    renderChangedFiles(git, els);

    // Start auto-refresh if dirty
    if (git.dirty && !_gitAutoRefreshTimer) {
        _gitAutoRefreshTimer = setInterval(loadGitPanel, 5000);
    } else if (!git.dirty && _gitAutoRefreshTimer) {
        clearInterval(_gitAutoRefreshTimer);
        _gitAutoRefreshTimer = null;
    }
}

function renderChangedFiles(git, els) {
    if (!els.gitChangedFiles) return;
    if (!git.dirty) {
        els.gitChangedFiles.innerHTML = '<div style="font-size:10px;color:var(--ok);padding:4px">✅ 변경사항 없음 (깨끗함)</div>';
        return;
    }
    const sid = State.activeSessionId;
    if (!sid) return;

    api('/api/git/diff?session_id=' + encodeURIComponent(sid) + '&staged=0').then(function (diffData) {
        const diffText = diffData.diff || '';
        const files = parseChangedFilesFromDiff(diffText);
        if (files.length === 0) {
            els.gitChangedFiles.innerHTML = '<div style="font-size:10px;color:var(--muted);padding:4px">변경 파일 없음</div>';
            return;
        }
        let html = '';
        for (var i = 0; i < Math.min(files.length, 20); i++) {
            var f = files[i];
            html += '<div class="git-file-item" style="display:flex;justify-content:space-between;align-items:center;padding:3px 6px;font-size:10px;border-bottom:1px solid var(--border);cursor:pointer" onclick="loadGitDiffForFile(\'' + escapeHtml(f) + '\')">';
            html += '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">' + escapeHtml(f) + '</span>';
            html += '<button class="git-discard-btn" style="padding:1px 5px;font-size:9px;background:var(--danger);color:#fff;border:none;border-radius:3px;cursor:pointer" onclick="event.stopPropagation();gitDiscardFile(\'' + escapeHtml(f) + '\')" title="변경 취소">↩</button>';
            html += '</div>';
        }
        if (files.length > 20) {
            html += '<div style="font-size:9px;color:var(--muted);padding:4px">... 외 ' + (files.length - 20) + '개 파일</div>';
        }
        els.gitChangedFiles.innerHTML = html;
    }).catch(function () {
        els.gitChangedFiles.innerHTML = '';
    });
}

function parseChangedFilesFromDiff(diffText) {
    const files = [];
    const lines = diffText.split('\n');
    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var match = line.match(/^diff --git a\/(.+) b\/(.+)$/);
        if (match) {
            files.push(match[2]);
        }
    }
    var seen = {};
    var unique = [];
    for (var j = 0; j < files.length; j++) {
        if (!seen[files[j]]) {
            seen[files[j]] = true;
            unique.push(files[j]);
        }
    }
    return unique;
}

async function loadGitDiff() {
    const elDiff = $('gitDiffOutput');
    const elResult = $('gitResult');
    const sid = State.activeSessionId;
    if (!sid) return;
    try {
        const data = await api('/api/git/diff?session_id=' + encodeURIComponent(sid));
        if (elDiff) {
            elDiff.style.display = 'block';
            elDiff.textContent = data.diff || '(변경 사항 없음)';
        }
    } catch (e) {
        if (elResult) {
            elResult.style.display = 'block';
            elResult.style.background = 'var(--danger-bg)';
            elResult.style.color = 'var(--danger)';
            elResult.textContent = 'Diff를 가져올 수 없습니다.';
        }
    }
}

async function loadGitDiffForFile(filepath) {
    const elDiff = $('gitDiffOutput');
    const sid = State.activeSessionId;
    if (!sid) return;
    try {
        const data = await api('/api/git/diff?session_id=' + encodeURIComponent(sid));
        if (!elDiff) return;
        var lines = (data.diff || '').split('\n');
        var inFile = false;
        var fileLines = [];
        for (var i = 0; i < lines.length; i++) {
            if (lines[i].indexOf('diff --git a/' + filepath + ' ') === 0 || lines[i].indexOf('diff --git a/' + filepath) === 0) {
                inFile = true;
                fileLines.push(lines[i]);
            } else if (inFile && lines[i].indexOf('diff --git ') === 0) {
                break;
            } else if (inFile) {
                fileLines.push(lines[i]);
            }
        }
        elDiff.style.display = 'block';
        elDiff.textContent = fileLines.length > 0 ? fileLines.join('\n') : (data.diff || '(변경 사항 없음)');
    } catch (e) {
        if (elDiff) {
            elDiff.style.display = 'block';
            elDiff.textContent = 'Diff를 가져올 수 없습니다.';
        }
    }
}

async function gitCommit() {
    const msgInput = $('gitCommitMsg');
    const commitBtn = $('gitCommitBtn');
    const msgWrap = $('gitCommitMsgWrap');
    const elResult = $('gitResult');

    // Show commit message input if hidden
    if (msgWrap && msgWrap.style.display === 'none') {
        msgWrap.style.display = 'block';
        if (msgInput) msgInput.focus();
        return;
    }

    const message = (msgInput ? msgInput.value : '').trim();
    if (!message) {
        showToast('커밋 메시지를 입력하세요.');
        return;
    }
    const sid = State.activeSessionId;
    if (!sid) return;
    if (commitBtn) commitBtn.disabled = true;
    try {
        const data = await api('/api/git/commit', { method: 'POST', body: { session_id: sid, message: message } });
        if (data.ok) {
            if (data.empty) {
                showResult(elResult, '커밋할 변경사항이 없습니다.', 'info');
            } else {
                showResult(elResult, '✓ 커밋 완료: ' + escapeHtml(message), 'ok');
            }
            if (msgInput) msgInput.value = '';
            if (msgWrap) msgWrap.style.display = 'none';
            loadGitPanel();
        } else {
            showResult(elResult, '커밋 실패: ' + (data.error || data.message || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        showResult(elResult, '커밋 중 오류 발생', 'error');
    } finally {
        if (commitBtn) commitBtn.disabled = false;
    }
}

async function gitPush() {
    const elResult = $('gitResult');
    const sid = State.activeSessionId;
    if (!sid) return;
    try {
        const data = await api('/api/git/push', { method: 'POST', body: { session_id: sid } });
        if (data.ok) {
            showResult(elResult, '✓ 푸시 완료', 'ok');
            loadGitPanel();
        } else {
            showResult(elResult, '푸시 실패: ' + (data.error || data.message || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        showResult(elResult, '푸시 중 오류 발생', 'error');
    }
}

async function gitPull() {
    const elResult = $('gitResult');
    const sid = State.activeSessionId;
    if (!sid) return;
    try {
        const data = await api('/api/git/pull', { method: 'POST', body: { session_id: sid } });
        if (data.ok) {
            var msg = '✓ 풀 완료';
            if (data.conflicts && data.conflicts.length > 0) {
                msg += ' (충돌: ' + data.conflicts.join(', ') + ')';
            }
            showResult(elResult, msg, data.conflicts && data.conflicts.length ? 'warn' : 'ok');
            loadGitPanel();
        } else {
            showResult(elResult, '풀 실패: ' + (data.error || data.message || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        showResult(elResult, '풀 중 오류 발생', 'error');
    }
}

async function gitDiscardFile(filepath) {
    if (!confirm('정말로 ' + filepath + '의 변경사항을 취소하시겠습니까?\n이 작업은 되돌릴 수 없습니다.')) return;
    const elResult = $('gitResult');
    const sid = State.activeSessionId;
    if (!sid) return;
    try {
        const data = await api('/api/git/discard', { method: 'POST', body: { session_id: sid, path: filepath } });
        if (data.ok) {
            showResult(elResult, '✓ 변경 취소: ' + filepath, 'ok');
            loadGitPanel();
        } else {
            showResult(elResult, '취소 실패: ' + (data.error || ''), 'error');
        }
    } catch (e) {
        showResult(elResult, '취소 중 오류 발생', 'error');
    }
}

function showResult(el, msg, type) {
    if (!el) return;
    el.style.display = 'block';
    if (type === 'ok') {
        el.style.background = 'var(--ok-bg, #d4edda)';
        el.style.color = 'var(--ok, #155724)';
    } else if (type === 'error') {
        el.style.background = 'var(--danger-bg, #f8d7da)';
        el.style.color = 'var(--danger, #721c24)';
    } else if (type === 'warn') {
        el.style.background = 'var(--warn-bg, #fff3cd)';
        el.style.color = 'var(--warn, #856404)';
    } else {
        el.style.background = 'var(--surface, #e2e3e5)';
        el.style.color = 'var(--text, #383d41)';
    }
    el.textContent = msg;
    setTimeout(function () {
        el.style.display = 'none';
    }, 5000);
}

function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

// Cleanup on panel switch
function cleanupGitPanel() {
    if (_gitAutoRefreshTimer) {
        clearInterval(_gitAutoRefreshTimer);
        _gitAutoRefreshTimer = null;
    }
}

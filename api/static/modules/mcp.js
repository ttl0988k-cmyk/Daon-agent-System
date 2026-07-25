/**
 * MCP (Model Context Protocol) Client UI Module
 * Manages MCP server connections, tool discovery, and tool execution.
 */
var _mcpState = {
    servers: [],
    activeServerId: null,
    presets: {},
};

async function loadMcpPanel() {
    await refreshMcpServers();
    await loadMcpPresets();
    // Show recommendation section and auto-analyze
    var section = document.getElementById('mcpRecommendSection');
    if (section) section.style.display = 'flex';
    runMcpRecommend();
    // Show capability diagnosis section
    var diagSection = document.getElementById('capabilityDiagnosisSection');
    if (diagSection) diagSection.style.display = 'flex';
}

async function refreshMcpServers() {
    try {
        var data = await api('/api/mcp/servers', { method: 'GET' });
        _mcpState.servers = data.servers || [];
        renderMcpServerList();
    } catch (e) {
        console.error('MCP servers load failed:', e);
        var listEl = document.getElementById('mcpServerList');
        if (listEl) listEl.innerHTML = '<div style="padding:12px;color:var(--danger);text-align:center;font-size:13px;">❌ MCP 서버 로드 실패: ' + _escapeHtml(e.message) + '<br><button class="cron-btn run" style="margin-top:8px;padding:3px 8px;font-size:10px" onclick="refreshMcpServers()">🔄 재시도</button></div>';
    }
}

function renderMcpServerList() {
    var listEl = document.getElementById('mcpServerList');
    if (!listEl) return;

    if (_mcpState.servers.length === 0) {
        listEl.innerHTML = '<div style="padding:12px;color:var(--text-muted);text-align:center;font-size:13px;">연결된 MCP 서버가 없습니다.<br>아래 프리셋에서 추가하거나 직접 설정하세요.</div>';
        return;
    }

    var html = '';
    for (var i = 0; i < _mcpState.servers.length; i++) {
        var srv = _mcpState.servers[i];
        var statusColor = srv.connected ? 'var(--success)' : 'var(--danger)';
        var statusText = srv.connected ? '● 연결됨' : (srv.error ? '✕ 오류' : '○ 해제됨');
        var toolCount = srv.tools_count || 0;
        var isExpired = srv.expired === true;

        var desc = '';
        if (_mcpState.presets && _mcpState.presets[srv.server_id] && _mcpState.presets[srv.server_id].description) {
            desc = _mcpState.presets[srv.server_id].description;
        }

        html += '<div class="mcp-server-card' + (srv.connected ? ' mcp-connected' : '') + (isExpired ? ' mcp-expired' : '') + '" data-server-id="' + _escapeHtml(srv.server_id) + '" title="' + _escapeHtml(desc) + '">';
        html += '  <div class="mcp-server-header" onclick="toggleMcpServerDetail(\'' + _escapeJs(srv.server_id) + '\')">';
        html += '    <span style="color:' + statusColor + ';margin-right:6px;">' + statusText + '</span>';
        html += '    <span style="font-weight:600;">' + _escapeHtml(srv.label) + '</span>';
        html += '    <span style="margin-left:auto;font-size:11px;color:var(--text-muted);">' + toolCount + ' tools</span>';
        html += '    <span class="mcp-expand-icon" id="mcpExpand_' + _escapeJs(srv.server_id) + '">▶</span>';
        html += '  </div>';
        html += '  <div class="mcp-server-detail" id="mcpDetail_' + _escapeJs(srv.server_id) + '" style="display:none;">';
        html += '    <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">';
        html += '      <code>' + _escapeHtml(srv.command) + '</code>';
        var isHttpTransport = srv.transport === 'http';
        if (isExpired) {
            html += '      <div style="margin-top:8px;padding:8px;background:rgba(255,0,0,0.1);border-radius:4px;">';
            html += '        <div style="color:var(--danger);font-weight:600;margin-bottom:4px;">토큰이 만료되었습니다.</div>';
            html += '        <input type="password" id="mcpOttInput_' + _escapeJs(srv.server_id) + '" class="mcp-token-input" placeholder="새로운 oneTimeToken 입력" style="width:100%;margin-bottom:4px;padding:6px;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-lighter);color:var(--text-color);" />';
            html += '        <button class="mcp-action-btn" onclick="updateMcpOtt(\'' + _escapeJs(srv.server_id) + '\')" style="width:100%;margin-top:4px;">토큰 교환 및 연결</button>';
            html += '      </div>';
        } else if (isHttpTransport) {
            html += '      <div style="margin-top:8px;padding:10px;background:rgba(255,193,7,0.12);border:1.5px solid rgba(255,193,7,0.45);border-radius:6px;">';
            html += '        <div style="color:var(--warning);font-weight:700;margin-bottom:6px;font-size:12px;">🔑 토큰 재설정 (만료 전 갱신)</div>';
            html += '        <input type="password" id="mcpOttInput_' + _escapeJs(srv.server_id) + '" class="mcp-token-input" placeholder="새로운 oneTimeToken 입력" style="width:100%;margin-bottom:4px;padding:6px;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-lighter);color:var(--text-color);" />';
            html += '        <button class="mcp-action-btn" onclick="updateMcpOtt(\'' + _escapeJs(srv.server_id) + '\')" style="width:100%;margin-top:4px;">토큰 교환 및 연결</button>';
            html += '      </div>';
        }
        if (srv.error) {
            html += '      <div style="color:var(--danger);margin-top:4px;">오류: ' + _escapeHtml(srv.error) + '</div>';
        }
        html += '    </div>';

        // Tools list (category-grouped with details/summary)
        if (srv.tools && srv.tools.length > 0) {
            // Group tools by prefix (first underscore/colon segment)
            var groups = {};
            var uncategorized = [];
            for (var t = 0; t < srv.tools.length; t++) {
                var toolName = srv.tools[t].name || '';
                var sepIdx = toolName.indexOf('_');
                if (sepIdx === -1) sepIdx = toolName.indexOf(':');
                var category = sepIdx > 0 ? toolName.substring(0, sepIdx) : '기타';
                if (!groups[category]) groups[category] = [];
                groups[category].push(srv.tools[t]);
            }
            var catNames = Object.keys(groups).sort();

            html += '    <div style="font-size:12px;font-weight:600;margin-bottom:6px;">🛠️ Tools (' + srv.tools.length + ')</div>';
            html += '    <div class="mcp-tools-groups" style="max-height:420px;overflow-y:auto;border:1px solid var(--border-color);border-radius:6px;padding:4px;">';
            for (var c = 0; c < catNames.length; c++) {
                var cat = catNames[c];
                var catTools = groups[cat];
                var catId = 'mcpCat_' + _escapeJs(srv.server_id) + '_' + c;
                html += '    <details class="mcp-tool-group"' + (catTools.length <= 3 ? ' open' : '') + ' style="margin-bottom:2px;">';
                html += '      <summary style="cursor:pointer;padding:5px 8px;background:var(--bg-lighter);border-radius:4px;font-size:11px;color:var(--text-muted);user-select:none;border:1px solid var(--border-color);">';
                html += '        📁 ' + _escapeHtml(cat) + ' <span style="color:var(--accent);font-weight:600;">(' + catTools.length + ')</span>';
                html += '      </summary>';
                html += '      <div style="padding-left:6px;margin-top:2px;">';
                for (var tt = 0; tt < catTools.length; tt++) {
                    var tool = catTools[tt];
                    html += '        <div class="mcp-tool-item" onclick="testMcpTool(\'' + _escapeJs(srv.server_id) + '\', \'' + _escapeJs(tool.name) + '\')" title="클릭하여 테스트 실행">';
                    html += '          <span class="mcp-tool-name">' + _escapeHtml(tool.name) + '</span>';
                    if (tool.description) {
                        html += '          <span class="mcp-tool-desc">' + _escapeHtml(tool.description.substring(0, 80)) + '</span>';
                    }
                    html += '        </div>';
                }
                html += '      </div>';
                html += '    </details>';
            }
            html += '    </div>';
        }

        // Actions
        html += '    <div style="display:flex;gap:4px;margin-top:8px;">';
        if (srv.connected) {
            html += '      <button class="mcp-action-btn mcp-action-disconnect" onclick="event.stopPropagation();disconnectMcpServer(\'' + _escapeJs(srv.server_id) + '\')">연결해제</button>';
        } else {
            html += '      <button class="mcp-action-btn mcp-action-connect" onclick="event.stopPropagation();connectMcpServer(\'' + _escapeJs(srv.server_id) + '\')">연결</button>';
        }
        html += '      <button class="mcp-action-btn mcp-action-remove" onclick="event.stopPropagation();removeMcpServer(\'' + _escapeJs(srv.server_id) + '\')">제거</button>';
        html += '    </div>';
        html += '  </div>';
        html += '</div>';
    }
    listEl.innerHTML = html;
}

async function loadMcpPresets() {
    try {
        var data = await api('/api/mcp/presets', { method: 'GET' });
        _mcpState.presets = data.presets || {};
        renderMcpPresets();
    } catch (e) {
        console.error('MCP presets load failed:', e);
        var listEl = document.getElementById('mcpPresetList');
        if (listEl) listEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">❌ 프리셋 로드 실패: ' + _escapeHtml(e.message) + '<br><button class="cron-btn run" style="margin-top:8px;padding:3px 8px;font-size:10px" onclick="loadMcpPresets()">🔄 재시도</button></div>';
    }
}

function renderMcpPresets() {
    var listEl = document.getElementById('mcpPresetList');
    if (!listEl) return;

    // Build a set of already-added server IDs for quick lookup
    var addedIds = {};
    for (var s = 0; s < _mcpState.servers.length; s++) {
        addedIds[_mcpState.servers[s].server_id] = true;
    }

    var html = '';
    var presetKeys = Object.keys(_mcpState.presets);
    for (var i = 0; i < presetKeys.length; i++) {
        var pid = presetKeys[i];
        var preset = _mcpState.presets[pid];
        var alreadyAdded = addedIds[pid];
        html += '<div class="mcp-preset-card' + (alreadyAdded ? ' mcp-preset-added' : '') + '"';
        if (!alreadyAdded) {
            html += ' onclick="addMcpPreset(\'' + _escapeJs(pid) + '\')"';
        } else {
            html += ' title="이미 추가됨"';
        }
        html += '>';
        html += '  <div class="mcp-preset-label">' + _escapeHtml(preset.label) + (alreadyAdded ? ' ✓' : '') + '</div>';
        html += '  <div class="mcp-preset-cmd"><code>' + _escapeHtml(preset.command) + ' ' + _escapeHtml((preset.args || []).join(' ')) + '</code></div>';
        html += '  <div class="mcp-preset-desc">' + _escapeHtml(preset.description || '') + '</div>';
        html += '</div>';
    }
    listEl.innerHTML = html;
    filterMcpPresets(); // Apply filter immediately if there is any text
}

function filterMcpPresets() {
    var searchInput = document.getElementById('mcpPresetSearch');
    if (!searchInput) return;
    var query = searchInput.value.toLowerCase();

    var listEl = document.getElementById('mcpPresetList');
    if (!listEl) return;

    var cards = listEl.getElementsByClassName('mcp-preset-card');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var text = card.textContent.toLowerCase();
        if (text.indexOf(query) !== -1) {
            card.style.display = '';
        } else {
            card.style.display = 'none';
        }
    }
}

async function addMcpPreset(presetId) {
    try {
        var data = await api('/api/mcp/servers/add-preset', {
            method: 'POST',
            body: { preset_id: presetId },
        });
        if (data.ok) {
            _showToast('MCP 서버 추가됨: ' + presetId, 'success');
            await refreshMcpServers();
        } else {
            _showToast('추가 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('추가 실패: ' + e.message, 'error');
    }
}

async function addMcpCustomServer() {
    var serverId = document.getElementById('mcpCustomId')?.value?.trim();
    var command = document.getElementById('mcpCustomCmd')?.value?.trim();
    var argsStr = document.getElementById('mcpCustomArgs')?.value?.trim();
    var label = document.getElementById('mcpCustomLabel')?.value?.trim();

    if (!serverId || !command) {
        _showToast('Server ID와 Command는 필수입니다.', 'error');
        return;
    }

    var args = argsStr ? argsStr.split(/\s+/) : [];

    try {
        var data = await api('/api/mcp/servers/add', {
            method: 'POST',
            body: {
                server_id: serverId,
                command: command,
                args: args,
                label: label || serverId,
            },
        });
        if (data.ok) {
            _showToast('MCP 서버 추가됨: ' + serverId, 'success');
            // Clear form
            if (document.getElementById('mcpCustomId')) document.getElementById('mcpCustomId').value = '';
            if (document.getElementById('mcpCustomCmd')) document.getElementById('mcpCustomCmd').value = '';
            if (document.getElementById('mcpCustomArgs')) document.getElementById('mcpCustomArgs').value = '';
            if (document.getElementById('mcpCustomLabel')) document.getElementById('mcpCustomLabel').value = '';
            await refreshMcpServers();
        } else {
            _showToast('추가 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('추가 실패: ' + e.message, 'error');
    }
}

async function connectMcpServer(serverId) {
    try {
        var data = await api('/api/mcp/servers/connect', {
            method: 'POST',
            body: { server_id: serverId },
        });
        if (data.ok) {
            _showToast('연결됨: ' + serverId, 'success');
            await refreshMcpServers();
        } else {
            _showToast('연결 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('연결 실패: ' + e.message, 'error');
    }
}

async function disconnectMcpServer(serverId) {
    try {
        var data = await api('/api/mcp/servers/disconnect', {
            method: 'POST',
            body: { server_id: serverId },
        });
        if (data.ok) {
            _showToast('연결 해제됨: ' + serverId, 'success');
            await refreshMcpServers();
        } else {
            _showToast('해제 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('해제 실패: ' + e.message, 'error');
    }
}

async function removeMcpServer(serverId) {
    if (!confirm('정말로 MCP 서버 \'' + serverId + '\'를 제거하시겠습니까?')) return;

    try {
        var data = await api('/api/mcp/servers/remove', {
            method: 'POST',
            body: { server_id: serverId },
        });
        if (data.ok) {
            _showToast('제거됨: ' + serverId, 'success');
            await refreshMcpServers();
        } else {
            _showToast('제거 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('제거 실패: ' + e.message, 'error');
    }
}

function toggleMcpServerDetail(serverId) {
    var detail = document.getElementById('mcpDetail_' + serverId);
    var icon = document.getElementById('mcpExpand_' + serverId);
    if (!detail || !icon) return;
    if (detail.style.display === 'none') {
        detail.style.display = 'block';
        icon.textContent = '▼';
    } else {
        detail.style.display = 'none';
        icon.textContent = '▶';
    }
}

async function testMcpTool(serverId, toolName) {
    var argsStr = prompt('"' + toolName + '" 도구의 인자 (JSON):', '{}');
    if (argsStr === null) return; // cancelled

    var args = {};
    try {
        args = JSON.parse(argsStr || '{}');
    } catch (e) {
        _showToast('올바른 JSON이 아닙니다.', 'error');
        return;
    }

    try {
        var data = await api('/api/mcp/tools/call', {
            method: 'POST',
            body: {
                server_id: serverId,
                tool_name: toolName,
                arguments: args,
                timeout: 30,
            },
        });

        var resultEl = document.getElementById('mcpToolResult');
        if (resultEl) {
            resultEl.style.display = 'block';
            resultEl.innerHTML = '<div style="font-weight:600;margin-bottom:4px;">📋 결과: ' + _escapeHtml(toolName) + '</div>'
                + '<pre style="background:var(--bg3);padding:8px;border-radius:4px;max-height:300px;overflow:auto;font-size:12px;">'
                + _escapeHtml(JSON.stringify(data, null, 2))
                + '</pre>';
        }
    } catch (e) {
        _showToast('도구 실행 실패: ' + e.message, 'error');
    }
}

function toggleMcpCustomForm() {
    var form = document.getElementById('mcpCustomForm');
    if (!form) return;
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
}

function _escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"');
}

function _escapeJs(str) {
    if (!str) return '';
    return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

// ── MCP Auto Recommendation ─────────────────────────────────────────────────

async function runMcpRecommend() {
    var section = document.getElementById('mcpRecommendSection');
    var listEl = document.getElementById('mcpRecommendList');
    if (!listEl) return;

    listEl.innerHTML = '<div style="padding:8px;color:var(--muted);text-align:center;font-size:12px;">🔍 워크스페이스 분석 중...</div>';
    if (section) section.style.display = 'flex';

    try {
        // Get current workspace from session state
        var wsPath = (typeof S !== 'undefined' && S.session && S.session.workspace) ? S.session.workspace : '';
        if (!wsPath) {
            listEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">⚠️ 워크스페이스를 먼저 선택하세요</div>';
            return;
        }

        var data = await api('/api/mcp/recommend?workspace=' + encodeURIComponent(wsPath), { method: 'GET' });
        renderMcpRecommendations(data);
    } catch (e) {
        console.error('MCP recommend failed:', e);
        listEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">❌ 분석 실패: ' + _escapeHtml(e.message) + '</div>';
    }
}

function renderMcpRecommendations(data) {
    var listEl = document.getElementById('mcpRecommendList');
    if (!listEl) return;

    var recs = data.recommendations || [];
    if (recs.length === 0) {
        listEl.innerHTML = '<div style="padding:8px;color:var(--muted);text-align:center;font-size:12px;">✅ 추가 추천 MCP 서버가 없습니다</div>';
        return;
    }

    // Build set of already-added server IDs
    var addedIds = {};
    for (var s = 0; s < _mcpState.servers.length; s++) {
        addedIds[_mcpState.servers[s].server_id] = true;
    }

    var confColors = { high: 'var(--success)', medium: 'var(--warning, #f0ad4e)', low: 'var(--muted)' };
    var confLabels = { high: '🟢 높음', medium: '🟡 중간', low: '⚪ 낮음' };

    var html = '';
    for (var i = 0; i < recs.length; i++) {
        var rec = recs[i];
        var isAlready = rec.already_installed || addedIds[rec.mcp_id];
        var confColor = confColors[rec.confidence] || 'var(--muted)';
        var confLabel = confLabels[rec.confidence] || rec.confidence;

        html += '<div class="mcp-recommend-card' + (isAlready ? ' mcp-rec-installed' : '') + '" style="display:flex;align-items:center;gap:6px;padding:6px 8px;background:var(--bg3);border-radius:6px;border-left:3px solid ' + confColor + ';">';
        html += '  <div style="flex:1;min-width:0;">';
        html += '    <div style="font-size:12px;font-weight:600;color:var(--text);">' + _escapeHtml(rec.label) + '</div>';
        html += '    <div style="font-size:10px;color:var(--muted);margin-top:2px;">' + _escapeHtml(rec.reason) + '</div>';
        html += '  </div>';
        html += '  <span style="font-size:10px;color:' + confColor + ';white-space:nowrap;">' + confLabel + '</span>';
        if (isAlready) {
            html += '  <span style="font-size:10px;color:var(--success);white-space:nowrap;">✓ 설치됨</span>';
        } else {
            if (rec.preset) {
                html += '  <button class="cron-btn run" style="padding:2px 6px;font-size:10px;white-space:nowrap;" onclick="event.stopPropagation();installMcpRecommend(\'' + _escapeJs(rec.mcp_id) + '\')">설치</button>';
            } else if (rec.install_hint) {
                html += '  <button class="cron-btn run" style="padding:2px 6px;font-size:10px;white-space:nowrap;" onclick="event.stopPropagation();installMcpRecommendCustom(\'' + _escapeJs(rec.mcp_id) + '\')">커스텀 설치</button>';
            } else {
                html += '  <span style="font-size:10px;color:var(--muted);white-space:nowrap;">프리셋 없음</span>';
            }
        }
        html += '</div>';
    }
    listEl.innerHTML = html;
}

async function installMcpRecommend(mcpId) {
    try {
        var data = await api('/api/mcp/servers/add-preset', {
            method: 'POST',
            body: { preset_id: mcpId },
        });
        if (data.ok) {
            _showToast('MCP 서버 설치됨: ' + mcpId, 'success');
            await refreshMcpServers();
            // Re-run recommend to refresh status
            setTimeout(runMcpRecommend, 500);
        } else {
            _showToast('설치 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('설치 실패: ' + e.message, 'error');
    }
}

async function installMcpRecommendCustom(mcpId) {
    // For custom recommendations, show the custom form with pre-filled values
    var form = document.getElementById('mcpCustomForm');
    if (form) form.style.display = 'flex';

    var idEl = document.getElementById('mcpCustomId');
    var cmdEl = document.getElementById('mcpCustomCmd');
    var argsEl = document.getElementById('mcpCustomArgs');
    var labelEl = document.getElementById('mcpCustomLabel');

    var hints = {
        'docker': { cmd: 'npx', args: '-y @anthropic/mcp-server-docker', label: '🐳 Docker MCP' },
        'sqlite': { cmd: 'npx', args: '-y @anthropic/mcp-server-sqlite data/', label: '🗄️ SQLite MCP' },
        'postgresql': { cmd: 'npx', args: '-y @anthropic/mcp-server-postgres postgresql://localhost:5432/mydb', label: '🐘 PostgreSQL MCP' },
    };

    var hint = hints[mcpId] || { cmd: 'npx', args: '', label: mcpId };

    if (idEl) idEl.value = mcpId;
    if (cmdEl) cmdEl.value = hint.cmd;
    if (argsEl) argsEl.value = hint.args;
    if (labelEl) labelEl.value = hint.label;

    _showToast('커스텀 MCP 설치 폼이 채워졌습니다. args를 확인한 후 "추가"를 눌러주세요.', 'success');
}

function _showToast(msg, type) {
    var toast = document.getElementById('toast');
    if (toast) {
        toast.textContent = msg;
        toast.style.display = 'block';
        toast.style.background = type === 'error' ? 'var(--danger)' : 'var(--success)';
        setTimeout(function () { toast.style.display = 'none'; }, 3000);
    } else {
        // Fallback: toast element not found in DOM
        alert('[TOAST] ' + msg);
    }
}

async function updateMcpOtt(serverId) {
    console.log('[updateMcpOtt] called with serverId:', serverId);
    var inputEl = document.getElementById('mcpOttInput_' + serverId);
    if (!inputEl) {
        console.error('[updateMcpOtt] input element not found: mcpOttInput_' + serverId);
        alert('[ERROR] 입력 필드를 찾을 수 없습니다.\nID: mcpOttInput_' + serverId);
        _showToast('입력 필드를 찾을 수 없습니다. 서버 목록을 새로고침 해주세요.', 'error');
        return;
    }
    var ott = inputEl.value.trim();
    if (!ott) {
        alert('[오류] One Time Token을 입력하세요.');
        _showToast('One Time Token을 입력하세요.', 'error');
        return;
    }
    console.log('[updateMcpOtt] sending exchange request...');
    try {
        var res = await api('/api/mcp/exchange-ott', {
            method: 'POST',
            body: { server_id: serverId, oneTimeToken: ott }
        });
        console.log('[updateMcpOtt] response:', res);
        if (res.ok) {
            _showToast('토큰이 교환되어 연결을 다시 시도합니다.', 'success');
            setTimeout(refreshMcpServers, 1500);
        } else {
            alert('[ERROR] 토큰 교환 실패: ' + (res.error || '알 수 없는 오류'));
            _showToast('토큰 교환 실패: ' + (res.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        console.error('[updateMcpOtt] error:', e);
        alert('[ERROR] 토큰 교환 중 오류 발생: ' + (e.message || '네트워크 오류'));
        _showToast('토큰 교환 중 오류 발생: ' + (e.message || '네트워크 오류'), 'error');
    }
}

// ── Skill Unit Tests (TRACE Environment Generation) ──────────────────────

async function loadCapabilityTests() {
    var testsResultEl = document.getElementById('capabilityTestsResult');
    if (!testsResultEl) return;

    testsResultEl.style.display = 'flex';
    testsResultEl.innerHTML = '<div style="padding:8px;color:var(--muted);text-align:center;font-size:12px;">🧪 테스트 시나리오 생성 중...</div>';

    try {
        var sessionId = (typeof S !== 'undefined' && S.session && S.session.id) ? S.session.id : '';
        if (!sessionId) {
            testsResultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">⚠️ 활성 세션이 없습니다.</div>';
            return;
        }

        var data = await api('/api/capability/tests?session_id=' + encodeURIComponent(sessionId), { method: 'GET' });
        renderCapabilityTests(data);
    } catch (e) {
        console.error('Skill test generation failed:', e);
        testsResultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">❌ 테스트 생성 실패: ' + _escapeHtml(e.message) + '</div>';
    }
}

function renderCapabilityTests(data) {
    var testsResultEl = document.getElementById('capabilityTestsResult');
    if (!testsResultEl) return;

    var tests = data.tests || [];
    var summary = data.summary || '';

    var html = '';
    if (summary) {
        html += '<div style="font-size:10px;color:var(--muted);padding:2px 0;font-style:italic;">' + _escapeHtml(summary) + '</div>';
    }

    if (tests.length === 0) {
        html += '<div style="padding:6px;color:var(--muted);text-align:center;font-size:11px;">생성된 테스트가 없습니다.</div>';
    } else {
        html += '<div style="font-size:11px;font-weight:600;color:var(--text);margin-top:2px;">생성된 테스트 (' + tests.length + '개)</div>';
        for (var i = 0; i < tests.length; i++) {
            var t = tests[i];
            var diffColor = t.difficulty === 'hard' ? 'var(--danger)' : (t.difficulty === 'medium' ? 'var(--warning, #f0ad4e)' : 'var(--success)');
            html += '<div style="background:var(--bg3);border-radius:4px;padding:6px;margin-top:4px;">';
            html += '  <div style="display:flex;justify-content:space-between;align-items:center;">';
            html += '    <span style="font-size:11px;font-weight:600;color:var(--text);">' + _escapeHtml(t.title) + '</span>';
            html += '    <span style="font-size:9px;color:' + diffColor + ';">' + _escapeHtml(t.difficulty || 'medium') + '</span>';
            html += '  </div>';
            html += '  <div style="font-size:10px;color:var(--muted);margin-top:2px;">' + _escapeHtml(t.description) + '</div>';
            html += '  <div style="font-size:10px;color:var(--text);margin-top:2px;padding:4px;background:var(--bg2);border-radius:3px;word-break:break-all;">';
            html += '    <span style="color:var(--muted);">📝 </span>' + _escapeHtml(t.test_prompt);
            html += '  </div>';
            html += '  <div style="display:flex;gap:8px;margin-top:3px;font-size:9px;">';
            html += '    <span style="color:var(--success);">✅ 기대: ' + _escapeHtml(t.expected_behavior) + '</span>';
            html += '  </div>';
            html += '  <div style="display:flex;gap:8px;margin-top:1px;font-size:9px;">';
            html += '    <span style="color:var(--danger);">❌ 실패지표: ' + _escapeHtml(t.failure_indicator) + '</span>';
            html += '  </div>';
            if (t.target_skill) {
                html += '  <div style="margin-top:2px;font-size:9px;color:var(--info, #5bc0de);">🎯 대상 스킬: ' + _escapeHtml(t.target_skill) + '</div>';
            }
            html += '</div>';
        }
    }

    testsResultEl.innerHTML = html;
}

// ── Skill Router (TRACE MoE Gate) ───────────────────────────────────────

function toggleSkillRouter() {
    var panel = document.getElementById('skillRouterPanel');
    if (panel) {
        panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    }
}

async function runSkillRouting() {
    var inputEl = document.getElementById('skillRouterTaskInput');
    var resultEl = document.getElementById('skillRouterResult');
    if (!inputEl || !resultEl) return;

    var task = inputEl.value.trim();
    if (!task) {
        resultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">⚠️ 작업 내용을 입력하세요.</div>';
        return;
    }

    resultEl.innerHTML = '<div style="padding:8px;color:var(--muted);text-align:center;font-size:12px;">🧭 라우팅 분석 중...</div>';

    try {
        // Collect diagnosis history from current session if available
        var diagnosisHistory = null;
        var diagResultEl = document.getElementById('capabilityDiagnosisResult');
        if (diagResultEl && diagResultEl.dataset.lastDiagnosis) {
            try {
                diagnosisHistory = [JSON.parse(diagResultEl.dataset.lastDiagnosis)];
            } catch (e) { /* ignore */ }
        }

        var data = await api('/api/capability/route', {
            method: 'POST',
            body: { task: task, diagnosis_history: diagnosisHistory }
        });
        renderSkillRouting(data);
    } catch (e) {
        console.error('Skill routing failed:', e);
        resultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">❌ 라우팅 실패: ' + _escapeHtml(e.message) + '</div>';
    }
}

function renderSkillRouting(data) {
    var resultEl = document.getElementById('skillRouterResult');
    if (!resultEl) return;

    var html = '';

    // Summary
    if (data.summary) {
        html += '<div style="font-size:10px;color:var(--muted);padding:2px 0;font-style:italic;">' + _escapeHtml(data.summary) + '</div>';
    }

    // Required capabilities
    if (data.required_capabilities && data.required_capabilities.length > 0) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:4px;">';
        html += '  <span style="font-size:10px;color:var(--muted);">필요 역량:</span>';
        for (var i = 0; i < data.required_capabilities.length; i++) {
            html += '  <span style="font-size:9px;padding:1px 5px;background:var(--bg3);color:var(--text);border-radius:3px;">' + _escapeHtml(data.required_capabilities[i]) + '</span>';
        }
        html += '</div>';
    }

    // Lacking capabilities (from history)
    if (data.lacking_capabilities && data.lacking_capabilities.length > 0) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:2px;">';
        html += '  <span style="font-size:10px;color:var(--warning, #f0ad4e);">부족 역량:</span>';
        for (var j = 0; j < data.lacking_capabilities.length; j++) {
            html += '  <span style="font-size:9px;padding:1px 5px;background:var(--warning-bg, #fff3cd);color:var(--warning, #f0ad4e);border-radius:3px;">' + _escapeHtml(data.lacking_capabilities[j]) + '</span>';
        }
        html += '</div>';
    }

    // Activated skills
    if (data.activated_skills && data.activated_skills.length > 0) {
        html += '<div style="margin-top:4px;">';
        html += '  <div style="font-size:10px;font-weight:600;color:var(--success);">🎯 활성화 스킬</div>';
        for (var k = 0; k < data.activated_skills.length; k++) {
            html += '  <div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:10px;">';
            html += '    <span style="color:var(--success);">✓</span>';
            html += '    <span style="color:var(--text);">' + _escapeHtml(data.activated_skills[k]) + '</span>';
            html += '  </div>';
        }
        html += '</div>';
    }

    // Activated MCPs
    if (data.activated_mcps && data.activated_mcps.length > 0) {
        html += '<div style="margin-top:4px;">';
        html += '  <div style="font-size:10px;font-weight:600;color:var(--info, #5bc0de);">🔌 활성화 MCP</div>';
        for (var m = 0; m < data.activated_mcps.length; m++) {
            html += '  <div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:10px;">';
            html += '    <span style="color:var(--info, #5bc0de);">⚡</span>';
            html += '    <span style="color:var(--text);">' + _escapeHtml(data.activated_mcps[m]) + '</span>';
            html += '  </div>';
        }
        html += '</div>';
    }

    // Prompt additions
    if (data.prompt_additions && data.prompt_additions.length > 0) {
        html += '<div style="margin-top:4px;">';
        html += '  <div style="font-size:10px;font-weight:600;color:var(--text);">💡 프롬프트 힌트</div>';
        for (var p = 0; p < data.prompt_additions.length; p++) {
            html += '  <div style="font-size:9px;color:var(--muted);padding:2px 0;">' + _escapeHtml(data.prompt_additions[p]) + '</div>';
        }
        html += '</div>';
    }

    // Routing explanation
    if (data.routing_explanation && data.routing_explanation.length > 0) {
        html += '<div style="margin-top:4px;">';
        html += '  <div style="font-size:9px;font-weight:600;color:var(--muted);">라우팅 근거</div>';
        for (var r = 0; r < data.routing_explanation.length; r++) {
            html += '  <div style="font-size:9px;color:var(--muted);padding:1px 0;">• ' + _escapeHtml(data.routing_explanation[r]) + '</div>';
        }
        html += '</div>';
    }

    if (!data.activated_skills || data.activated_skills.length === 0) {
        if (!data.activated_mcps || data.activated_mcps.length === 0) {
            html += '<div style="padding:6px;color:var(--muted);text-align:center;font-size:11px;">특별한 라우팅이 필요하지 않습니다.</div>';
        }
    }

    resultEl.innerHTML = html;
}

// ── Capability Diagnosis (TRACE-inspired) ─────────────────────────────────

async function runCapabilityDiagnosis() {
    var resultEl = document.getElementById('capabilityDiagnosisResult');
    if (!resultEl) return;

    resultEl.innerHTML = '<div style="padding:8px;color:var(--muted);text-align:center;font-size:12px;">🔍 세션 분석 중...</div>';

    try {
        var sessionId = (typeof S !== 'undefined' && S.session && S.session.id) ? S.session.id : '';
        if (!sessionId) {
            resultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">⚠️ 활성 세션이 없습니다. 먼저 채팅을 시작하세요.</div>';
            return;
        }

        var data = await api('/api/capability/diagnose?session_id=' + encodeURIComponent(sessionId), { method: 'GET' });
        renderCapabilityDiagnosis(data);
    } catch (e) {
        console.error('Capability diagnosis failed:', e);
        resultEl.innerHTML = '<div style="padding:8px;color:var(--danger);text-align:center;font-size:12px;">❌ 진단 실패: ' + _escapeHtml(e.message) + '</div>';
    }
}

function renderCapabilityDiagnosis(data) {
    var resultEl = document.getElementById('capabilityDiagnosisResult');
    if (!resultEl) return;

    // Store last diagnosis for skill router use
    try {
        resultEl.dataset.lastDiagnosis = JSON.stringify(data);
    } catch (e) { /* ignore */ }

    var capabilities = data.capabilities || [];
    var recommendations = data.recommendations || {};
    var summary = data.summary || '';

    var lacking = [];
    for (var i = 0; i < capabilities.length; i++) {
        if (capabilities[i].label === 'LACKING') {
            lacking.push(capabilities[i]);
        }
    }

    lacking.sort(function (a, b) { return b.confidence - a.confidence; });

    var html = '';

    if (summary) {
        html += '<div style="font-size:11px;color:var(--text);padding:4px 0;font-style:italic;">' + _escapeHtml(summary) + '</div>';
    }

    if (lacking.length > 0) {
        html += '<div style="font-size:11px;font-weight:600;color:var(--warning, #f0ad4e);margin-top:4px;">부족한 역량</div>';
        for (var j = 0; j < lacking.length; j++) {
            var cap = lacking[j];
            var stars = _confidenceStars(cap.confidence);
            html += '<div style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border);">';
            html += '  <span style="font-size:12px;color:var(--warning, #f0ad4e);">' + stars + '</span>';
            html += '  <span style="font-size:12px;font-weight:500;color:var(--text);">' + _escapeHtml(cap.name) + '</span>';
            html += '</div>';
            if (cap.reason) {
                html += '<div style="font-size:10px;color:var(--muted);padding-left:20px;margin-bottom:2px;">' + _escapeHtml(cap.reason) + '</div>';
            }
        }
    } else {
        html += '<div style="padding:8px;color:var(--success);text-align:center;font-size:12px;">✅ 뚜렷한 역량 부족이 감지되지 않았습니다.</div>';
    }

    var hasRecs = (recommendations.mcps && recommendations.mcps.length > 0) ||
        (recommendations.skills && recommendations.skills.length > 0) ||
        (recommendations.references && recommendations.references.length > 0) ||
        (recommendations.prompt_improvements && recommendations.prompt_improvements.length > 0);

    if (hasRecs) {
        html += '<div style="font-size:11px;font-weight:600;color:var(--text);margin-top:8px;">추천</div>';

        if (recommendations.mcps && recommendations.mcps.length > 0) {
            for (var m = 0; m < recommendations.mcps.length; m++) {
                html += '<div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:11px;">';
                html += '  <span style="color:var(--success);">✓</span>';
                html += '  <span style="color:var(--text);">' + _escapeHtml(recommendations.mcps[m]) + ' MCP</span>';
                html += '</div>';
            }
        }

        if (recommendations.skills && recommendations.skills.length > 0) {
            for (var s = 0; s < recommendations.skills.length; s++) {
                html += '<div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:11px;">';
                html += '  <span style="color:var(--success);">✓</span>';
                html += '  <span style="color:var(--text);">' + _escapeHtml(recommendations.skills[s]) + ' Skill</span>';
                html += '</div>';
            }
        }

        if (recommendations.references && recommendations.references.length > 0) {
            for (var r = 0; r < recommendations.references.length; r++) {
                html += '<div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:11px;">';
                html += '  <span style="color:var(--info, #5bc0de);">📚</span>';
                html += '  <span style="color:var(--text);">' + _escapeHtml(recommendations.references[r]) + '</span>';
                html += '</div>';
            }
        }

        if (recommendations.prompt_improvements && recommendations.prompt_improvements.length > 0) {
            for (var p = 0; p < recommendations.prompt_improvements.length; p++) {
                html += '<div style="display:flex;align-items:flex-start;gap:4px;padding:2px 0;font-size:11px;">';
                html += '  <span style="color:var(--info, #5bc0de);margin-top:1px;">💡</span>';
                html += '  <span style="color:var(--muted);">' + _escapeHtml(recommendations.prompt_improvements[p]) + '</span>';
                html += '</div>';
            }
        }
    }

    resultEl.innerHTML = html;

    // Show action buttons after diagnosis
    var actionsEl = document.getElementById('capabilityActions');
    if (actionsEl) {
        actionsEl.style.display = 'flex';
    }
}

function _confidenceStars(confidence) {
    if (confidence >= 0.9) return '★★★★★';
    if (confidence >= 0.75) return '★★★★☆';
    if (confidence >= 0.6) return '★★★☆☆';
    if (confidence >= 0.4) return '★★☆☆☆';
    return '★☆☆☆☆';
}

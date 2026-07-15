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
}

async function refreshMcpServers() {
    try {
        var data = await api('/api/mcp/servers', { method: 'GET' });
        _mcpState.servers = data.servers || [];
        renderMcpServerList();
    } catch (e) {
        console.error('MCP servers load failed:', e);
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
        if (isExpired) {
            html += '      <div style="margin-top:8px;padding:8px;background:rgba(255,0,0,0.1);border-radius:4px;">';
            html += '        <div style="color:var(--danger);font-weight:600;margin-bottom:4px;">토큰이 만료되었습니다.</div>';
            html += '        <input type="password" id="mcpOttInput_' + _escapeJs(srv.server_id) + '" class="mcp-token-input" placeholder="새로운 oneTimeToken 입력" style="width:100%;margin-bottom:4px;padding:6px;border:1px solid var(--border-color);border-radius:4px;background:var(--bg-lighter);color:var(--text-color);" />';
            html += '        <button class="mcp-action-btn" onclick="updateMcpOtt(\'' + _escapeJs(srv.server_id) + '\')" style="width:100%;margin-top:4px;">토큰 교환 및 연결</button>';
            html += '      </div>';
        } else if (srv.error) {
            html += '      <div style="color:var(--danger);margin-top:4px;">오류: ' + _escapeHtml(srv.error) + '</div>';
        }
        html += '    </div>';

        // Tools list
        if (srv.tools && srv.tools.length > 0) {
            html += '    <div style="font-size:12px;font-weight:600;margin-bottom:4px;">🛠️ Tools</div>';
            for (var t = 0; t < srv.tools.length; t++) {
                var tool = srv.tools[t];
                html += '    <div class="mcp-tool-item" onclick="testMcpTool(\'' + _escapeJs(srv.server_id) + '\', \'' + _escapeJs(tool.name) + '\')" title="클릭하여 테스트 실행">';
                html += '      <span class="mcp-tool-name">' + _escapeHtml(tool.name) + '</span>';
                if (tool.description) {
                    html += '      <span class="mcp-tool-desc">' + _escapeHtml(tool.description.substring(0, 80)) + '</span>';
                }
                html += '    </div>';
            }
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
    }
}

async function updateMcpOtt(serverId) {
    var inputEl = document.getElementById('mcpOttInput_' + serverId);
    if (!inputEl) return;
    var ott = inputEl.value.trim();
    if (!ott) {
        showToast('One Time Token�� �Է��ϼ���.', 'error');
        return;
    }
    try {
        var res = await api('/api/mcp/exchange-ott', {
            method: 'POST',
            body: { server_id: serverId, oneTimeToken: ott }
        });
        if (res.ok) {
            showToast('��ū�� ���ŵǾ� ������ �ٽ� �õ��մϴ�.', 'success');
            // Give it a short delay to allow background reconnect
            setTimeout(refreshMcpServers, 1500);
        } else {
            showToast('��ū ���� ����: ' + (res.error || '�� �� ���� ����'), 'error');
        }
    } catch (e) {
        showToast('��ū ���� �� ���� �߻�', 'error');
    }
}

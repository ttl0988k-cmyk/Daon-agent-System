/**
 * Plugin Manager UI Module — DAON 플러그인 관리 패널.
 *
 * 외부 플러그인 import(Git URL / 로컬 폴더), 설치 목록 표시,
 * 전역 ON/OFF + 세션(탭) 단위 스코프 ON/OFF 토글을 제공한다.
 *
 * 세션 스코프 모델:
 *   - 전역 ON  : 모든 세션(탭)에서 플러그인 로드 (스킬/MCP/툴/훅 노출)
 *   - 세션 ON  : 특정 채팅 탭/세션에서만 플러그인 활성화 (Dynamic Harness forced_skills 병합)
 *   - 전역 OFF : 어떤 세션에서도 로드되지 않는다.
 *
 * API:
 *   GET  /api/plugins                  목록 + 상태
 *   POST /api/plugins/import           import {identifier, source_type?}
 *   POST /api/plugins/{name}/enable    전역 ON
 *   POST /api/plugins/{name}/disable   전역 OFF
 *   POST /api/plugins/{name}/session   세션 토글 {session_id, enabled}
 *   POST /api/plugins/{name}/remove    사용자 플러그인 삭제
 */
var _pluginState = {
    plugins: [],
    state: { global_enabled: {}, sessions: {} },
    sessionId: null,
};

function _pluginSessionId() {
    if (_pluginState.sessionId) return _pluginState.sessionId;
    var sid = (typeof S !== 'undefined' && S.session && S.session.id) ? S.session.id : '';
    _pluginState.sessionId = sid;
    return sid;
}

function _pluginSessionPlugins() {
    var sid = _pluginSessionId();
    if (!sid) return {};
    var sessions = (_pluginState.state && _pluginState.state.sessions) || {};
    var active = sessions[sid] || [];
    var map = {};
    for (var i = 0; i < active.length; i++) map[active[i]] = true;
    return map;
}

async function loadPluginsPanel() {
    await refreshPlugins();
}

async function refreshPlugins() {
    try {
        var data = await api('/api/plugins', { method: 'GET' });
        _pluginState.plugins = data.plugins || [];
        _pluginState.state = data.state || { global_enabled: {}, sessions: {} };
        renderPluginStateSummary();
        renderPluginList();
    } catch (e) {
        console.error('Plugins load failed:', e);
        var listEl = document.getElementById('pluginList');
        if (listEl) listEl.innerHTML = '<div style="padding:12px;color:var(--danger);text-align:center;font-size:13px;">❌ 플러그인 로드 실패: ' + esc(e.message) + '<br><button class="cron-btn run" style="margin-top:8px;padding:3px 8px;font-size:10px" onclick="refreshPlugins()">🔄 재시도</button></div>';
        var sumEl = document.getElementById('pluginStateSummary');
        if (sumEl) sumEl.textContent = '';
    }
}

function renderPluginStateSummary() {
    var el = document.getElementById('pluginStateSummary');
    if (!el) return;
    var globalEnabled = (_pluginState.state && _pluginState.state.global_enabled) || {};
    var sessions = (_pluginState.state && _pluginState.state.sessions) || {};
    var sid = _pluginSessionId();
    var globalCount = 0;
    for (var k in globalEnabled) if (globalEnabled[k]) globalCount++;
    var sessionNames = sid ? (sessions[sid] || []) : [];
    var text = '전역 ON: ' + globalCount + '개';
    if (sid) {
        text += '  |  현재 탭 세션(' + sid.slice(0, 8) + '…) 활성: ' + sessionNames.length + '개';
    } else {
        text += '  |  활성 세션이 없어 세션 스코프는 적용되지 않습니다.';
    }
    el.textContent = text;
}

function renderPluginList() {
    var listEl = document.getElementById('pluginList');
    if (!listEl) return;

    if (_pluginState.plugins.length === 0) {
        listEl.innerHTML = '<div style="padding:12px;color:var(--muted);text-align:center;font-size:13px;">설치된 플러그인이 없습니다.<br>위에서 Git URL 또는 폴더 경로로 가져오세요.</div>';
        return;
    }

    var sessionActive = _pluginSessionPlugins();
    var html = '';
    for (var i = 0; i < _pluginState.plugins.length; i++) {
        var p = _pluginState.plugins[i];
        var name = p.name || '';
        var globalOn = p.enabled === true;
        var sessionOn = sessionActive[name] === true;
        var skillsCount = (p.skills && p.skills.length) || 0;
        var mcpCount = (p.mcp && p.mcp.length) || 0;
        var toolsCount = (p.tools && p.tools.length) || 0;
        var hooksCount = (p.hooks && p.hooks.length) || 0;

        html += '<div class="mcp-server-card" data-plugin-name="' + esc(name) + '" style="border:1px solid ' + (globalOn ? 'rgba(76,175,80,0.5)' : 'var(--border)') + ';border-radius:8px;background:var(--bg2);padding:8px;">';

        // 헤더
        html += '  <div style="display:flex;align-items:center;gap:6px;">';
        html += '    <span style="font-weight:700;color:var(--text);font-size:12px;">' + esc(name) + '</span>';
        if (p.version) html += '    <span style="font-size:10px;color:var(--muted);">v' + esc(p.version) + '</span>';
        html += '    <span style="margin-left:auto;font-size:10px;color:' + (globalOn ? 'var(--success)' : 'var(--muted)') + ';font-weight:600;">' + (globalOn ? '● 전역 ON' : '○ 전역 OFF') + '</span>';
        html += '  </div>';

        // 설명
        if (p.description) {
            html += '  <div style="font-size:10px;color:var(--muted);margin-top:2px;">' + esc(p.description) + '</div>';
        }

        // 구성 요약
        html += '  <div style="display:flex;gap:8px;flex-wrap:wrap;font-size:10px;color:var(--muted);margin-top:4px;">';
        html += '    <span>🧩 스킬 ' + skillsCount + '</span>';
        html += '    <span>🔌 MCP ' + mcpCount + '</span>';
        html += '    <span>🛠️ 툴 ' + toolsCount + '</span>';
        html += '    <span>🪝 훅 ' + hooksCount + '</span>';
        html += '  </div>';

        // 스킬 상세
        if (p.skills && p.skills.length) {
            html += '  <div style="display:flex;flex-direction:column;gap:2px;margin-top:4px;">';
            for (var s = 0; s < p.skills.length; s++) {
                html += '    <div style="font-size:10px;color:var(--accent);padding-left:4px;border-left:2px solid var(--border2);">' + esc(p.skills[s].name) + '</div>';
            }
            html += '  </div>';
        }

        // 툴/훅 목록
        if ((p.tools && p.tools.length) || (p.hooks && p.hooks.length)) {
            html += '  <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">';
            for (var t = 0; t < (p.tools || []).length; t++) {
                html += '    <code style="font-size:9px;background:var(--bg3);border:1px solid var(--border);border-radius:3px;padding:1px 4px;color:var(--text);">' + esc(p.tools[t]) + '</code>';
            }
            for (var h = 0; h < (p.hooks || []).length; h++) {
                html += '    <code style="font-size:9px;background:rgba(255,193,7,0.12);border:1px solid rgba(255,193,7,0.4);border-radius:3px;padding:1px 4px;color:var(--warning);">🪝 ' + esc(p.hooks[h]) + '</code>';
            }
            html += '  </div>';
        }

        // 스코프 토글
        html += '  <div style="display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap;">';
        // 전역 토글
        html += '    <label style="display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);cursor:pointer;font-weight:normal;">';
        html += '      <input type="checkbox" data-plugin-global="' + esc(name) + '" onchange="togglePluginGlobal(this,\'' + esc(name) + '\')"' + (globalOn ? ' checked' : '') + ' style="width:auto;margin:0;"> 전역';
        html += '    </label>';
        // 세션 토글 (활성 세션 있을 때만)
        if (_pluginSessionId()) {
            html += '    <label style="display:flex;align-items:center;gap:4px;font-size:10px;color:var(--muted);cursor:pointer;font-weight:normal;">';
            html += '      <input type="checkbox" data-plugin-session="' + esc(name) + '" onchange="togglePluginSession(this,\'' + esc(name) + '\')"' + (sessionOn ? ' checked' : '') + ' style="width:auto;margin:0;"> 이 탭에서 사용';
            html += '    </label>';
        } else {
            html += '    <span style="font-size:9px;color:var(--muted);">세션 미선택 — 전역만 적용</span>';
        }
        // 제거
        html += '    <button class="cron-btn" style="margin-left:auto;padding:2px 8px;font-size:9px;border-color:rgba(255,77,79,0.4);color:var(--danger);" onclick="removePlugin(\'' + esc(name) + '\')">삭제</button>';
        html += '  </div>';

        html += '</div>';
    }
    listEl.innerHTML = html;
}

// ── 가져오기 ────────────────────────────────────────────────────────────────

async function importPlugin() {
    var input = document.getElementById('pluginImportInput');
    var identifier = input ? input.value.trim() : '';
    if (!identifier) {
        _showToast('Git URL 또는 폴더 경로를 입력하세요.', 'error');
        return;
    }
    try {
        var data = await api('/api/plugins/import', {
            method: 'POST',
            body: { identifier: identifier, source_type: 'auto' },
            timeout: 120000, // git clone은 오래 걸릴 수 있다
        });
        if (data.ok) {
            _showToast('플러그인 가져오기 성공: ' + (data.plugin && data.plugin.name ? data.plugin.name : identifier), 'success');
            if (input) input.value = '';
            await refreshPlugins();
        } else {
            _showToast('가져오기 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('가져오기 실패: ' + e.message, 'error');
    }
}

// ── 전역 ON/OFF ─────────────────────────────────────────────────────────────

async function togglePluginGlobal(checkbox, name) {
    var enabled = checkbox.checked;
    // 낙관적 UI (즉시 반영, 실패 시 롤백)
    var target = '/api/plugins/' + encodeURIComponent(name) + '/' + (enabled ? 'enable' : 'disable');
    try {
        var data = await api(target, { method: 'POST', body: {} });
        if (data.ok) {
            _showToast((enabled ? '✅ 전역 ON: ' : '⛔ 전역 OFF: ') + name, 'success');
            await refreshPlugins();
        } else {
            checkbox.checked = !enabled;
            _showToast('전환 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        checkbox.checked = !enabled;
        _showToast('전환 실패: ' + e.message, 'error');
    }
}

// ── 세션(탭) 스코프 ON/OFF ──────────────────────────────────────────────────

async function togglePluginSession(checkbox, name) {
    var sid = _pluginSessionId();
    if (!sid) {
        checkbox.checked = !checkbox.checked;
        _showToast('활성 세션이 없습니다. 채팅 탭을 선택해 주세요.', 'error');
        return;
    }
    var enabled = checkbox.checked;
    try {
        var data = await api('/api/plugins/' + encodeURIComponent(name) + '/session', {
            method: 'POST',
            body: { session_id: sid, enabled: enabled },
        });
        if (data.ok) {
            _showToast((enabled ? '✅ 이 탭에서 사용: ' : '⛔ 이 탭에서 제거: ') + name, 'success');
            await refreshPlugins();
        } else {
            checkbox.checked = !enabled;
            _showToast('세션 전환 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        checkbox.checked = !enabled;
        _showToast('세션 전환 실패: ' + e.message, 'error');
    }
}

// ── 제거 ────────────────────────────────────────────────────────────────────

async function removePlugin(name) {
    if (!confirm('정말로 플러그인 \'' + name + '\'을(를) 삭제하시겠습니까?\n(사용자 플러그인만 삭제되며 번들 플러그인은 보호됩니다)')) return;
    try {
        var data = await api('/api/plugins/' + encodeURIComponent(name) + '/remove', {
            method: 'POST',
            body: {},
        });
        if (data.ok) {
            _showToast('삭제됨: ' + name, 'success');
            await refreshPlugins();
        } else {
            _showToast('삭제 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (e) {
        _showToast('삭제 실패: ' + e.message, 'error');
    }
}

// ── 외부에서 세션 변경 시 상태 갱신용 (선택적) ──────────────────────────────

function pluginsSessionChanged() {
    _pluginState.sessionId = null;
    if (_currentPanel === 'plugins') refreshPlugins();
}

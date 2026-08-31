/**
 * DAON Connector Store — 외부 서비스 연동 커넥터 (GPT Plugins 스타일)
 *
 * 원칙 (대표님 확정 2026-08-31):
 *   1. 외부 사이트 연동만 다룬다 (Notion/GitHub/Slack...). 스킬 패키지는 플러그인 패널.
 *   2. 시크릿 절대 하드코딩 금지 — 카탈로그(static/store/connectors.json)에는
 *      필드 '이름'과 안내 문구만 있다. 값은 사용자가 연결할 때 입력하며,
 *      서버로 전송 후 화면에서 즉시 폐기한다. 재표시도 하지 않는다.
 *   3. 연결: MCP 계열은 POST /api/mcp/servers/add(env에 입력값) 후 /api/mcp/servers/connect,
 *      Slack 계열(transport=integration)은 POST /api/integration/config + /api/integration/slack/test.
 *      저장 위치는 앱 로컬 파일이며 외부로 전송되지 않는다.
 *   4. 상태 배지는 GET /api/mcp/servers 의 connector_<id> + GET /api/integration/config 로 판정.
 *      서버 응답의 env/토큰 값은 UI에서 절대 렌더링하지 않는다.
 */
var _connState = {
    catalog: null,
    servers: [],
    integration: null,   // /api/integration/config 응답(마스킹됨)
    loading: false,
    modal: null,
};

async function loadConnectorsPanel() {
    await _connEnsureCatalog();
    await Promise.all([_connRefreshServers(), _connRefreshIntegration()]);
    _connRender();
}

async function _connEnsureCatalog() {
    if (_connState.catalog) return;
    try {
        var res = await fetch('/static/store/connectors.json?ts=' + Date.now());
        if (!res.ok) throw new Error('HTTP ' + res.status);
        _connState.catalog = await res.json();
    } catch (e) {
        _connState.catalog = { connectors: [], updated: '', _error: e.message };
    }
}

async function _connRefreshServers() {
    try {
        var data = await api('/api/mcp/servers', { method: 'GET' });
        _connState.servers = (data && data.servers) || [];
    } catch (e) { _connState.servers = []; }
}

async function _connRefreshIntegration() {
    try {
        _connState.integration = await api('/api/integration/config', { method: 'GET' });
    } catch (e) { _connState.integration = null; }
}

function _connFind(id) {
    var cats = (_connState.catalog && _connState.catalog.connectors) || [];
    for (var i = 0; i < cats.length; i++) { if (cats[i].id === id) return cats[i]; }
    return null;
}

function _connServerFor(id) {
    var sid = 'connector_' + id;
    for (var i = 0; i < _connState.servers.length; i++) {
        if (_connState.servers[i].server_id === sid) return _connState.servers[i];
    }
    return null;
}

function _connStatusOf(c) {
    if (c.transport === 'coming-soon') return 'soon';
    if (c.transport === 'integration') {
        var cfg = _connState.integration;
        if (!cfg) return 'off';
        return cfg.slack && cfg.slack.configured ? 'on' : 'off';
    }
    var srv = _connServerFor(c.id);
    if (!srv) return 'off';
    return srv.connected ? 'on' : 'error';
}

// ── 렌더 ────────────────────────────────────────────────────────────────────
function _connRender() {
    var box = document.getElementById('connectorList');
    if (!box) return;
    if (_connState.catalog && _connState.catalog._error) {
        box.innerHTML = '<div class="pstore-empty">❌ 카탈로그 로드 실패: ' + esc(_connState.catalog._error) + '</div>';
        return;
    }
    var cats = (_connState.catalog && _connState.catalog.connectors) || [];
    var onCount = 0, html = '';
    for (var i = 0; i < cats.length; i++) {
        var st = _connStatusOf(cats[i]);
        if (st === 'on') onCount++;
        html += _connCardHtml(cats[i], st);
    }
    box.innerHTML = html || '<div class="pstore-empty">카탈로그가 비어 있습니다.</div>';
    var sum = document.getElementById('connectorSummary');
    if (sum) sum.textContent = '연결됨 ' + onCount + ' / ' + cats.length + ' · 카탈로그: ' + ((_connState.catalog && _connState.catalog.updated) || '?');
}

function _connCardHtml(c, st) {
    var badge =
        st === 'on' ? '<span class="conn-badge on">● 연결됨</span>' :
        st === 'error' ? '<span class="conn-badge err">● 오류</span>' :
        st === 'soon' ? '<span class="conn-badge soon">준비 중</span>' :
        '<span class="conn-badge off">○ 미연결</span>';
    var act =
        st === 'soon' ? '<button class="pstore-btn" disabled style="opacity:.45;cursor:default">🔒 OAuth 연동 예정</button>' :
        st === 'on' ? '<button class="pstore-btn on" onclick="connDisconnect(\'' + esc(c.id) + '\')">🔌 연결 해제</button>' :
        st === 'error' ? '<button class="pstore-btn install" onclick="connReconnect(\'' + esc(c.id) + '\')">🔄 재연결</button>' :
        '<button class="pstore-btn install" onclick="connOpenModal(\'' + esc(c.id) + '\')">🔗 연결</button>';
    var h = '<div class="conn-card">';
    h += '  <div class="conn-head"><span class="conn-icon">' + c.icon + '</span><span class="conn-name">' + esc(c.name) + '</span>' + badge + '</div>';
    h += '  <div class="conn-tag">' + esc(c.tagline) + '</div>';
    h += '  <div class="conn-act">' + act + (c.guide_url ? ' <a class="conn-guide" href="' + esc(c.guide_url) + '" target="_blank" rel="noopener">키 발급 ↗</a>' : '') + '</div>';
    h += '</div>';
    return h;
}

// ── 연결 모달 (secure input: 입력→전송→즉시 폐기, 재표시 없음) ──────────────
function connOpenModal(id) {
    var c = _connFind(id);
    if (!c) return;
    _connState.modal = { id: id };
    var m = document.getElementById('connModal');
    if (!m) return;
    document.getElementById('connModalTitle').textContent = '🔗 ' + c.name + ' 연결';
    document.getElementById('connModalBody').innerHTML = _connModalFormHtml(c);
    m.style.display = 'flex';
    var first = m.querySelector('input');
    if (first) first.focus();
}

function _connModalFormHtml(c) {
    var h = '';
    h += '<div class="conn-guide-box">📋 아래 순서로 키를 복사해 붙여넣으세요. 값은 <b>이 PC의 앱 로컬에만</b> 저장되고 화면에 다시 표시되지 않습니다.</div>';
    if (c.guide_url) h += '<div style="font-size:10px;margin-bottom:6px;"><a href="' + esc(c.guide_url) + '" target="_blank" rel="noopener">① 발급 페이지 열기 ↗</a></div>';
    h += '<ol class="conn-fields">';
    for (var i = 0; i < c.env_fields.length; i++) {
        var f = c.env_fields[i];
        h += '<li>';
        h += '  <label>② ' + esc(f.label) + (f.required === false ? ' (선택)' : '') + '</label>';
        h += '  <input type="password" id="connField_' + esc(f.key) + '" autocomplete="off" spellcheck="false" placeholder="(붙여넣기)" />';
        h += '  <div class="conn-hint">' + esc(f.hint || '') + '</div>';
        h += '</li>';
    }
    h += '</ol>';
    h += '<div class="conn-modal-actions"><button class="action-btn" style="background:var(--accent);color:#fff" onclick="connSaveFromModal()">연결 저장</button> ';
    h += '<button class="cron-btn" onclick="closeConnModal()">취소</button></div>';
    h += '<div id="connModalResult"></div>';
    return h;
}

function closeConnModal() {
    _connState.modal = null;
    var m = document.getElementById('connModal');
    if (m) m.style.display = 'none';
    document.querySelectorAll('[id^="connField_"]').forEach(function (el) { el.value = ''; });
}

async function connSaveFromModal() {
    if (!_connState.modal) return;
    var c = _connFind(_connState.modal.id);
    if (!c) return;
    var vals = {};
    for (var i = 0; i < c.env_fields.length; i++) {
        var f = c.env_fields[i];
        var el = document.getElementById('connField_' + f.key);
        var v = el ? el.value.trim() : '';
        if (!v && f.required !== false) {
            _showToast('입력하세요: ' + f.label, 'error');
            if (el) el.focus();
            return;
        }
        if (v) vals[f.key] = v;
    }
    var ok = (c.transport === 'integration')
        ? await _connConnectIntegration(c, vals)
        : await _connConnectMcp(c, vals);
    // 값 폐기
    vals = null;
    document.querySelectorAll('[id^="connField_"]').forEach(function (el) { el.value = ''; });
    if (ok) setTimeout(closeConnModal, 1600);
}

// MCP 계열 연결 — env/args에 placeholder 치환 후 등록+연결
function _connBuildSpec(c, vals) {
    var env = {};
    var tpl = c.auth_env_template || {};
    Object.keys(tpl).forEach(function (k) {
        var v = String(tpl[k] || '');
        Object.keys(vals).forEach(function (vk) { v = v.split('${' + vk + '}').join(vals[vk]); });
        env[k] = v;
    });
    var args = (c.args || []).map(function (a) {
        var r = String(a);
        Object.keys(vals).forEach(function (vk) { r = r.split('${' + vk + '}').join(vals[vk]); });
        return r;
    });
    return {
        server_id: 'connector_' + c.id,
        label: '🔗 ' + c.name,
        command: c.command,
        args: args,
        env: env,
        transport: 'stdio',
        auto_connect: true,
    };
}

async function _connConnectMcp(c, vals) {
    var resBox = document.getElementById('connModalResult');
    if (resBox) resBox.innerHTML = '<div class="conn-result">연결 중… (첫 실행은 패키지 다운로드 수 분 소요)</div>';
    var spec = _connBuildSpec(c, vals);
    try {
        // 기존 등록분(오류 상태) 있으면 제거 후 재등록
        if (_connServerFor(c.id)) {
            try { await api('/api/mcp/servers/remove', { method: 'POST', body: { server_id: spec.server_id } }); } catch (e) { }
        }
        var add = await api('/api/mcp/servers/add', { method: 'POST', body: spec });
        if (!add || !add.ok) throw new Error((add && add.error) || '등록 실패');
        try { await api('/api/mcp/servers/connect', { method: 'POST', body: { server_id: spec.server_id } }); } catch (e) { }
        await _connRefreshServers();
        _connRender();
        var srv = _connServerFor(c.id);
        if (srv && srv.connected) {
            if (resBox) resBox.innerHTML = '<div class="conn-result ok">✅ ' + esc(c.name) + ' 연결됨 — 라온이 바로 쓸 수 있습니다.</div>';
            return true;
        }
        var e2 = (srv && srv.error) || '서버 응답 대기 중';
        if (resBox) resBox.innerHTML = '<div class="conn-result err">⚠ 등록됨·연결 확인 필요: ' + esc(String(e2).slice(0, 160)) + ' <button class="cron-btn" onclick="connReconnect(\'' + esc(c.id) + '\')">🔄 재시도</button></div>';
        return false;
    } catch (e3) {
        if (resBox) resBox.innerHTML = '<div class="conn-result err">❌ ' + esc(e3.message) + '</div>';
        return false;
    }
}

// Slack(웹훅) 연결 — integration config 저장 + 테스트 발송
async function _connConnectIntegration(c, vals) {
    var resBox = document.getElementById('connModalResult');
    if (resBox) resBox.innerHTML = '<div class="conn-result">저장·테스트 중…</div>';
    try {
        await api('/api/integration/config', { method: 'POST', body: { config: { slack: { enabled: true, webhook_url: vals.SLACK_WEBHOOK_URL || '' } } } });
        var t = await api('/api/integration/slack/test', { method: 'POST', body: {} });
        await _connRefreshIntegration();
        _connRender();
        if (t && t.ok) {
            if (resBox) resBox.innerHTML = '<div class="conn-result ok">✅ 연결 테스트 발송 성공</div>';
            return true;
        }
        if (resBox) resBox.innerHTML = '<div class="conn-result err">⚠ 저장됨, 테스트 실패: ' + esc(((t && t.error) || 'webhook 확인').slice(0, 160)) + '</div>';
        return false;
    } catch (e) {
        if (resBox) resBox.innerHTML = '<div class="conn-result err">❌ ' + esc(e.message) + '</div>';
        return false;
    }
}

async function connReconnect(id) {
    var c = _connFind(id);
    if (!c) return;
    try {
        await api('/api/mcp/servers/connect', { method: 'POST', body: { server_id: 'connector_' + id } });
    } catch (e) { }
    await Promise.all([_connRefreshServers(), _connRefreshIntegration()]);
    _connRender();
}

async function connDisconnect(id) {
    var c = _connFind(id);
    if (!c) return;
    if (!confirm(c.name + ' 연결을 해제할까요?\n(저장된 키/설정만 지우고 다른 연동은 영향 없습니다)')) return;
    try {
        if (c.transport === 'integration') {
            await api('/api/integration/config', { method: 'POST', body: { config: { slack: { enabled: false, webhook_url: '', bot_token: '' } } } });
            await _connRefreshIntegration();
        } else {
            await api('/api/mcp/servers/remove', { method: 'POST', body: { server_id: 'connector_' + id } });
            await _connRefreshServers();
        }
        _showToast('연결 해제: ' + c.name, 'success');
    } catch (e) {
        _showToast('해제 실패: ' + e.message, 'error');
    }
    _connRender();
}

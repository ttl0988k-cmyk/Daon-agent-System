// ═══════════════════════════════════════════════════════════════════════
// ── DAON Plugin Store (ChatGPT-Plugins style) ──────────────────────────
// 카탈로그: /static/store/plugins.json (scripts/extract_plugin_store.py 생성)
// 설치: POST /api/plugins/import {identifier:<plugin_store 경로>, source_type:'folder'}
// ═══════════════════════════════════════════════════════════════════════
var _storeState = {
    catalog: null,     // plugins.json 원본
    loading: false,
    view: 'store',     // 'store' | 'installed'
    q: '',             // 검색어
    cat: '전체',       // 활성 카테고리
};

function storeSetView(v) {
    _storeState.view = v;
    var sv = document.getElementById('pstoreView');
    var iv = document.getElementById('installedView');
    var bs = document.getElementById('pstoreSegStore');
    var bi = document.getElementById('pstoreSegInstalled');
    if (sv) sv.style.display = (v === 'store') ? '' : 'none';
    if (iv) iv.style.display = (v === 'installed') ? '' : 'none';
    if (bs) bs.classList.toggle('active', v === 'store');
    if (bi) bi.classList.toggle('active', v === 'installed');
}

function storeSetQuery(q) {
    _storeState.q = (q || '').toLowerCase();
    storeRenderGrid();
}

function storeSetCat(c) {
    _storeState.cat = c || '전체';
    storeRenderCats();
    storeRenderGrid();
}

function storeInstalledMap() {
    var m = {};
    for (var i = 0; i < _pluginState.plugins.length; i++) {
        var p = _pluginState.plugins[i];
        m[p.name] = p;
    }
    return m;
}

async function storeInit(force) {
    if (_storeState.catalog && !force) { storeRenderAll(); return; }
    if (_storeState.loading) return;
    _storeState.loading = true;
    var grid = document.getElementById('pstoreGrid');
    if (grid) grid.innerHTML = '<div class="pstore-empty">스토어 불러오는 중…</div>';
    try {
        var res = await fetch('/static/store/plugins.json?ts=' + Date.now());
        if (!res.ok) throw new Error('HTTP ' + res.status);
        _storeState.catalog = await res.json();
    } catch (e) {
        if (grid) grid.innerHTML = '<div class="pstore-empty">❌ 카탈로그 없음 — 라온에게 "플러그인 스토어 재생성" 요청 (' + esc(e.message) + ')</div>';
    }
    _storeState.loading = false;
    storeRenderAll();
}

function storeRenderAll() {
    var el = document.getElementById('pstoreInstalledCount');
    if (el) el.textContent = String(_pluginState.plugins.length);
    if (!_storeState.catalog) return;
    storeRenderCats();
    storeRenderGrid();
}

function storeRenderCats() {
    var box = document.getElementById('pstoreCats');
    if (!box || !_storeState.catalog) return;
    var cats = ['전체'].concat(_storeState.catalog.categories || []);
    var html = '';
    for (var i = 0; i < cats.length; i++) {
        var c = cats[i];
        var active = (_storeState.cat === c);
        html += '<button class="pstore-cat' + (active ? ' active' : '') + '" onclick="storeSetCat(\'' + esc(c) + '\')">' + esc(c) + '</button>';
    }
    box.innerHTML = html;
}

function _storeInitials(name) {
    var parts = name.replace(/^anthropic-/, '').split(/[-_]/).filter(Boolean);
    if (parts.length >= 2) return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
    return (parts[0] || 'P').slice(0, 2).toUpperCase();
}

function storeRenderGrid() {
    var grid = document.getElementById('pstoreGrid');
    var foot = document.getElementById('pstoreFooter');
    if (!grid) return;
    if (!_storeState.catalog) { return; }
    var installed = storeInstalledMap();
    var items = _storeState.catalog.plugins || [];
    var html = '', shown = 0;
    for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (_storeState.cat !== '전체' && it.category !== _storeState.cat) continue;
        if (_storeState.q) {
            var hay = (it.name + ' ' + (it.desc || '') + ' ' + it.category).toLowerCase();
            if (hay.indexOf(_storeState.q) === -1) continue;
        }
        shown++;
        var inst = installed[it.name];
        var isOn = inst && inst.enabled === true;
        var srcBadge = it.src === 'anthropics'
            ? '<span class="pstore-src a">공식</span>'
            : '<span class="pstore-src w">W</span>';
        html += '<div class="pstore-card' + (inst ? ' installed' : '') + '">';
        html += '  <div class="pstore-card-top">';
        html += '    <div class="pstore-avatar" style="background:' + esc(it.color || '#6c8cff') + '">' + esc(_storeInitials(it.name)) + '</div>';
        html += '    <div class="pstore-card-head">';
        html += '      <div class="pstore-name">' + esc(it.name.replace(/^anthropic-/, '')) + ' ' + srcBadge + '</div>';
        html += '      <div class="pstore-meta">🧩 ' + (it.skills || 0) + ' · ' + esc(it.category) + '</div>';
        html += '    </div>';
        html += '  </div>';
        html += '  <div class="pstore-desc">' + esc((it.desc || '').slice(0, 140)) + ((it.desc || '').length > 140 ? '…' : '') + '</div>';
        if (inst) {
            html += '  <div class="pstore-actions"><button class="pstore-btn on" onclick="storeSetView(\'installed\')">✓ 설치됨 · 관리</button></div>';
        } else {
            html += '  <div class="pstore-actions"><button class="pstore-btn install" onclick="storeInstall(this,\'' + esc(it.name) + '\')">⬇ 설치</button></div>';
        }
        html += '</div>';
    }
    grid.innerHTML = html || '<div class="pstore-empty">결과 없음 — 검색어/카테고리를 바꿔보세요</div>';
    if (foot) foot.textContent = '총 ' + shown + '개 표시 · 카탈로그 ' + items.length + '개 (갱신: ' + (_storeState.catalog.updated || '?') + ')';
}

async function storeInstall(btn, name) {
    if (!name) return;
    var item = null;
    var list = (_storeState.catalog && _storeState.catalog.plugins) || [];
    for (var i = 0; i < list.length; i++) { if (list[i].name === name) { item = list[i]; break; } }
    if (!item) { _showToast('카탈로그에서 찾을 수 없음: ' + name, 'error'); return; }
    if (btn) { btn.disabled = true; btn.textContent = '설치 중…'; }
    try {
        var data = await api('/api/plugins/import', {
            method: 'POST',
            body: { identifier: item.path, source_type: 'folder' },
            timeout: 120000,
        });
        if (data && data.ok) {
            _showToast('✅ 설치됨: ' + name + ' (전역 ON)', 'success');
            await refreshPlugins();
            storeRenderAll();
        } else {
            _showToast('설치 실패: ' + ((data && data.error) || '알 수 없는 오류'), 'error');
            if (btn) { btn.disabled = false; btn.textContent = '⬇ 설치'; }
        }
    } catch (e) {
        _showToast('설치 실패: ' + e.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '⬇ 설치'; }
    }
}

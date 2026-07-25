// ── Phase 5: Community Skills Hub ──

let _skillsHubResults = [];
let _skillsHubSources = [];
let _skillsHubSearching = false;

/**
 * Load the skills hub panel — show search UI and available sources.
 * Called when switching to the skills panel.
 */
async function loadSkillsHubPanel() {
    const section = document.getElementById('skillsHubSection');
    if (!section) return;

    // Load available sources
    try {
        const data = await api('/api/skills/hub/sources');
        _skillsHubSources = data.sources || [];
    } catch (e) {
        _skillsHubSources = [];
    }

    renderSkillsHubUI(section);
}

/**
 * Render the skills hub search UI.
 */
function renderSkillsHubUI(container) {
    if (!container) return;

    let html = '';

    // Search bar
    html += '<div class="skills-hub-search-bar">';
    html += '<input id="skillsHubSearchInput" type="text" placeholder="커뮤니티 스킬 검색 (예: pdf, docker, testing)..." '
        + 'onkeydown="if(event.key===\'Enter\')searchSkillsHub()" '
        + 'style="flex:1;background:var(--bg3);border:1px solid var(--border2);border-radius:5px;color:var(--text);padding:6px 8px;font-size:11px;outline:none;">';
    html += '<button id="skillsHubSearchBtn" onclick="searchSkillsHub()" '
        + 'style="background:var(--accent);color:#fff;border:none;border-radius:5px;padding:6px 12px;font-size:11px;cursor:pointer;">🔍 검색</button>';
    html += '</div>';

    // Source filter chips
    if (_skillsHubSources.length > 0) {
        html += '<div id="skillsHubSourceFilters" style="display:flex;gap:4px;flex-wrap:wrap;margin-top:6px;">';
        for (const src of _skillsHubSources) {
            const checked = src.id === 'github' || src.id === 'clawhub' ? 'checked' : '';
            html += '<label class="skills-hub-source-chip">';
            html += '<input type="checkbox" value="' + esc(src.id) + '" ' + checked + ' onchange="onSkillsHubSourceChange()">';
            html += '<span>' + esc(src.name || src.id) + '</span>';
            html += '</label>';
        }
        html += '</div>';
    }

    // Results area
    html += '<div id="skillsHubResults" style="margin-top:8px;"></div>';

    // Status
    html += '<div id="skillsHubStatus" style="font-size:10px;color:var(--muted);text-align:center;margin-top:6px;display:none;"></div>';

    container.innerHTML = html;
}

/**
 * Get selected source filters.
 */
function getSkillsHubSelectedSources() {
    const chips = document.querySelectorAll('#skillsHubSourceFilters input[type="checkbox"]:checked');
    const selected = [];
    for (const cb of chips) {
        selected.push(cb.value);
    }
    return selected.length > 0 ? selected : ['github', 'clawhub'];
}

/**
 * Handle source filter change.
 */
function onSkillsHubSourceChange() {
    // Re-search if there's a query already
    const input = document.getElementById('skillsHubSearchInput');
    if (input && input.value.trim()) {
        searchSkillsHub();
    }
}

/**
 * Search skills from community hubs.
 */
async function searchSkillsHub() {
    const input = document.getElementById('skillsHubSearchInput');
    const statusEl = document.getElementById('skillsHubStatus');
    const btn = document.getElementById('skillsHubSearchBtn');

    if (!input) return;
    const query = input.value.trim();
    if (!query) {
        _showToast('검색어를 입력해주세요', 'error');
        return;
    }

    if (_skillsHubSearching) return;
    _skillsHubSearching = true;

    if (btn) btn.disabled = true;
    if (statusEl) {
        statusEl.style.display = 'block';
        statusEl.textContent = '🔍 검색 중...';
    }

    const sources = getSkillsHubSelectedSources();

    try {
        const data = await api('/api/skills/search?q=' + encodeURIComponent(query)
            + '&source=' + encodeURIComponent(sources.join(','))
            + '&limit=20');

        _skillsHubResults = data.results || [];
        renderSkillsHubResults(_skillsHubResults);

        if (statusEl) {
            statusEl.textContent = '✅ ' + _skillsHubResults.length + '개 결과 (출처: ' + (data.sources_searched || sources).join(', ') + ')';
        }
    } catch (err) {
        if (statusEl) {
            statusEl.style.display = 'block';
            statusEl.textContent = '❌ 검색 실패: ' + err.message;
        }
        _skillsHubResults = [];
        renderSkillsHubResults([]);
    } finally {
        _skillsHubSearching = false;
        if (btn) btn.disabled = false;
    }
}

/**
 * Render search results.
 */
function renderSkillsHubResults(results) {
    const container = document.getElementById('skillsHubResults');
    if (!container) return;

    if (!results || results.length === 0) {
        container.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:11px;text-align:center;">'
            + '검색 결과가 없습니다.</div>';
        return;
    }

    let html = '';
    for (const r of results) {
        const trustBadge = getTrustBadge(r.trust_level);
        const scoreColor = r.score >= 70 ? 'var(--success)' : r.score >= 40 ? 'var(--warning)' : 'var(--muted)';

        html += '<div class="skills-hub-card">';
        html += '<div class="skills-hub-card-header">';
        html += '<span class="skills-hub-card-name">' + esc(r.name) + '</span>';
        html += '<span class="skills-hub-card-score" style="color:' + scoreColor + '">' + (r.score || 0) + '</span>';
        html += '</div>';

        if (r.description) {
            html += '<div class="skills-hub-card-desc">' + esc(r.description) + '</div>';
        }

        html += '<div class="skills-hub-card-meta">';
        if (r.source) html += '<span class="skills-hub-tag source">' + esc(r.source) + '</span>';
        html += trustBadge;
        if (r.author) html += '<span class="skills-hub-tag author">@' + esc(r.author) + '</span>';
        html += '</div>';

        if (r.tags && r.tags.length > 0) {
            html += '<div class="skills-hub-card-tags">';
            for (const tag of r.tags.slice(0, 5)) {
                html += '<span class="skills-hub-tag">' + esc(tag) + '</span>';
            }
            html += '</div>';
        }

        html += '<div class="skills-hub-card-actions">';
        html += '<button class="skills-hub-install-btn" onclick="installSkillsHubSkill(\''
            + esc(r.identifier) + '\', \'' + esc(r.source || '') + '\')">📥 설치</button>';
        html += '</div>';

        html += '</div>';
    }

    container.innerHTML = html;
}

/**
 * Get trust level badge HTML.
 */
function getTrustBadge(level) {
    switch (level) {
        case 'trusted':
        case 'builtin':
            return '<span class="skills-hub-tag trust trusted">✅ 신뢰</span>';
        case 'community':
            return '<span class="skills-hub-tag trust community">👥 커뮤니티</span>';
        default:
            return '<span class="skills-hub-tag trust unknown">❓ ' + esc(level || 'unknown') + '</span>';
    }
}

/**
 * Install a skill from the hub.
 */
async function installSkillsHubSkill(identifier, source) {
    if (!identifier) return;

    _showToast('📥 설치 중: ' + identifier + '...', 'info');

    try {
        const data = await api('/api/skills/install', {
            method: 'POST',
            body: { identifier: identifier, source: source || undefined }
        });

        if (data.ok) {
            _showToast('✅ 설치 완료: ' + identifier + ' → ' + (data.installed_to || 'skills/'), 'success');
            // Reload local skills
            if (typeof loadSkills === 'function') {
                setTimeout(() => loadSkills(), 500);
            }
        } else {
            _showToast('❌ 설치 실패: ' + (data.error || '알 수 없는 오류'), 'error');
        }
    } catch (err) {
        _showToast('❌ 오류: ' + err.message, 'error');
    }
}

// ── Phase 5b: Auto-Recommend Skills (Workspace-based) ──

let _skillsRecommendRunning = false;

/**
 * Run workspace-based skill recommendation.
 * Same pattern as runMcpRecommend() in mcp.js.
 */
async function runSkillsRecommend() {
    const listEl = document.getElementById('skillsRecommendList');
    const statusEl = document.getElementById('skillsRecommendStatus');
    const btn = document.getElementById('skillsRecommendBtn');

    if (!listEl) return;
    if (_skillsRecommendRunning) return;
    _skillsRecommendRunning = true;

    if (btn) btn.disabled = true;
    listEl.innerHTML = '<div style="padding:6px;color:var(--muted);text-align:center;font-size:11px;">🔍 워크스페이스 분석 중...</div>';
    if (statusEl) { statusEl.style.display = 'none'; }

    // Get current workspace (same pattern as mcp.js — fixed to use S.session.workspace)
    var wsPath = (typeof S !== 'undefined' && S.session && S.session.workspace) ? S.session.workspace : '';
    if (!wsPath) {
        listEl.innerHTML = '<div style="padding:6px;color:var(--danger);text-align:center;font-size:11px;">⚠️ 워크스페이스를 먼저 선택하세요</div>';
        _skillsRecommendRunning = false;
        if (btn) btn.disabled = false;
        return;
    }

    try {
        const data = await api('/api/skills/recommend?workspace=' + encodeURIComponent(wsPath) + '&limit=5', { method: 'GET' });
        renderSkillsRecommend(data);
    } catch (e) {
        console.error('Skills recommend failed:', e);
        listEl.innerHTML = '<div style="padding:6px;color:var(--danger);text-align:center;font-size:11px;">❌ 분석 실패: ' + esc(e.message) + '</div>';
    } finally {
        _skillsRecommendRunning = false;
        if (btn) btn.disabled = false;
    }
}

/**
 * Render recommended skills.
 */
function renderSkillsRecommend(data) {
    const listEl = document.getElementById('skillsRecommendList');
    const statusEl = document.getElementById('skillsRecommendStatus');

    if (!listEl) return;

    const recs = data.recommendations || [];

    if (recs.length === 0) {
        listEl.innerHTML = '<div style="padding:6px;color:var(--muted);text-align:center;font-size:11px;">추천할 스킬이 없습니다.</div>';
        if (statusEl) { statusEl.style.display = 'none'; }
        return;
    }

    let html = '';
    for (var i = 0; i < recs.length; i++) {
        const r = recs[i];
        const trustBadge = getTrustBadge(r.trust_level);
        const confBadge = r.confidence === 'high'
            ? '<span style="color:var(--success);font-size:10px;">🟢</span>'
            : r.confidence === 'medium'
                ? '<span style="color:var(--warning);font-size:10px;">🟡</span>'
                : '<span style="color:var(--muted);font-size:10px;">⚪</span>';

        html += '<div class="skills-hub-card" style="border-left:3px solid '
            + (r.confidence === 'high' ? 'var(--success)' : r.confidence === 'medium' ? 'var(--warning)' : 'var(--muted)') + '">';
        html += '<div class="skills-hub-card-header">';
        html += '<span class="skills-hub-card-name">' + confBadge + ' ' + esc(r.name) + '</span>';
        html += '<span class="skills-hub-card-score" style="color:'
            + (r.score >= 70 ? 'var(--success)' : r.score >= 40 ? 'var(--warning)' : 'var(--muted)') + '">' + (r.score || 0) + '</span>';
        html += '</div>';

        if (r.reason) {
            html += '<div class="skills-hub-card-desc" style="font-size:10px;color:var(--muted);">💡 ' + esc(r.reason) + '</div>';
        }

        if (r.description) {
            html += '<div class="skills-hub-card-desc">' + esc(r.description) + '</div>';
        }

        html += '<div class="skills-hub-card-meta">';
        if (r.source) html += '<span class="skills-hub-tag source">' + esc(r.source) + '</span>';
        html += trustBadge;
        if (r.author) html += '<span class="skills-hub-tag author">@' + esc(r.author) + '</span>';
        html += '</div>';

        if (r.tags && r.tags.length > 0) {
            html += '<div class="skills-hub-card-tags">';
            for (var j = 0; j < Math.min(r.tags.length, 4); j++) {
                html += '<span class="skills-hub-tag">' + esc(r.tags[j]) + '</span>';
            }
            html += '</div>';
        }

        html += '<div class="skills-hub-card-actions">';
        html += '<button class="skills-hub-install-btn" onclick="installSkillsHubSkill(\''
            + esc(r.identifier) + '\', \'' + esc(r.source || '') + '\')">📥 설치</button>';
        html += '</div>';

        html += '</div>';
    }

    listEl.innerHTML = html;

    if (statusEl) {
        statusEl.style.display = 'block';
        const queries = data.queries_made || [];
        statusEl.textContent = '✅ ' + recs.length + '개 추천 (검색어: ' + queries.slice(0, 3).join(', ') + ')';
    }
}

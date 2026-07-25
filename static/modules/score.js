// ── Config Score Dashboard ──

let _scoreData = null;

/**
 * Fetch config score from backend and render the full dashboard.
 * Called on panel switch to 'dashboard' and via the refresh button.
 */
async function loadConfigScore() {
    const container = $('scoreContainer');
    if (!container) return;

    container.innerHTML = '<div style="padding:12px;color:var(--muted);text-align:center;">⚙️ 설정 평가 중...</div>';

    try {
        const data = await api('/api/score/evaluate');
        if (!data || data.total_score === undefined) {
            container.innerHTML = '<div style="padding:12px;color:var(--danger);">평가 데이터를 불러오지 못했습니다.</div>';
            return;
        }
        _scoreData = data;
        renderScoreDashboard(data, container);
        renderScoreBadge(data);
    } catch (err) {
        console.error('[Score] Failed:', err);
        container.innerHTML = '<div style="padding:12px;color:var(--danger);">오류: ' + esc(err.message) + '</div>';
    }
}

/**
 * Render the main score dashboard (big grade circle + category bars).
 */
function renderScoreDashboard(data, container) {
    const { total_score, grade, grade_emoji, categories, recommendations } = data;

    // Determine color for the score circle
    let scoreColor = 'var(--danger)';
    if (total_score >= 75) scoreColor = 'var(--success)';
    else if (total_score >= 60) scoreColor = 'var(--warning, #f0a500)';
    else if (total_score >= 40) scoreColor = '#f0a500';

    let html = '';

    // ── Grade circle ──
    html += '<div class="score-hero">';
    html += '<div class="score-circle" style="border-color:' + scoreColor + ';">';
    html += '<span class="score-number">' + total_score + '</span>';
    html += '<span class="score-max">/100</span>';
    html += '</div>';
    html += '<div class="score-grade">';
    html += '<span class="score-grade-emoji">' + grade_emoji + '</span>';
    html += '<span class="score-grade-text" style="color:' + scoreColor + ';">등급 ' + grade + '</span>';
    html += '</div>';
    html += '</div>';

    // ── Category bars ──
    html += '<div class="score-categories">';
    for (const cat of categories) {
        const fillColor = cat.status === 'good' ? 'var(--success)' :
            cat.status === 'warning' ? 'var(--warning, #f0a500)' : 'var(--danger)';
        html += '<div class="score-cat-row">';
        html += '<div class="score-cat-header">';
        html += '<span class="score-cat-name">' + esc(cat.name) + '</span>';
        html += '<span class="score-cat-value">' + cat.score + '/' + cat.max + '</span>';
        html += '</div>';
        html += '<div class="score-cat-bar-bg">';
        html += '<div class="score-cat-bar-fill" style="width:' + cat.percentage + '%;background:' + fillColor + ';"></div>';
        html += '</div>';
        html += '<div class="score-cat-detail">' + esc(cat.detail) + '</div>';
        html += '</div>';
    }
    html += '</div>';

    // ── Recommendations ──
    if (recommendations && recommendations.length > 0) {
        html += '<div class="score-recommendations">';
        html += '<div class="score-rec-title">💡 개선 권장사항</div>';
        for (const rec of recommendations) {
            html += '<div class="score-rec-item">• ' + esc(rec) + '</div>';
        }
        html += '</div>';
    }

    container.innerHTML = html;
}

/**
 * Render the compact score badge (for Settings modal header).
 */
function renderScoreBadge(data) {
    if (!data) data = _scoreData;
    if (!data) return;

    const badge = $('scoreBadge');
    if (!badge) return;

    const { total_score, grade, grade_emoji } = data;
    let badgeClass = 'score-badge-d';
    if (total_score >= 75) badgeClass = 'score-badge-a';
    else if (total_score >= 60) badgeClass = 'score-badge-b';
    else if (total_score >= 40) badgeClass = 'score-badge-c';

    badge.className = 'score-badge ' + badgeClass;
    badge.innerHTML = grade_emoji + ' ' + total_score + '점 · ' + grade + '등급';
    badge.style.display = 'inline-block';
    badge.title = '설정 완성도: ' + total_score + '/100 — 클릭하면 대시보드에서 상세 확인';
    badge.onclick = function () { switchPanel('dashboard'); };
}

/**
 * Refresh score data (called from dashboard refresh button).
 */
async function refreshConfigScore() {
    const container = $('scoreContainer');
    if (!container) return;
    await loadConfigScore();
}

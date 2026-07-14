/**
 * modes.js — Roo Code-style Mode System UI
 * Agent mode switching (separate from Chat/Harness view switching).
 */
"use strict";

var _currentMode = 'code';

async function loadModes() {
    try {
        var data = await api('/api/modes');
        _renderModeTabs(data.modes, data.default);
    } catch (e) {
        console.error('Failed to load modes:', e);
    }
}

function _renderModeTabs(modes, defaultMode) {
    var container = document.getElementById('modeTabs');
    if (!container) return;

    container.innerHTML = '';
    var keys = Object.keys(modes);

    for (var i = 0; i < keys.length; i++) {
        var slug = keys[i];
        var info = modes[slug];
        var btn = document.createElement('button');
        btn.className = 'agent-mode-tab' + (slug === _currentMode ? ' active' : '');
        btn.title = info.label + ': ' + info.description;
        btn.setAttribute('data-mode', slug);
        btn.innerHTML = info.icon;
        btn.onclick = (function (mode) {
            return function () { switchAgentMode(mode); };
        })(slug);
        container.appendChild(btn);
    }
}

async function switchAgentMode(mode) {
    var sid = State.activeSessionId;
    if (!sid) return;

    try {
        var data = await api('/api/mode', {
            method: 'POST',
            body: { session_id: sid, mode: mode }
        });
        if (data.ok) {
            _currentMode = mode;
            // Update tab highlights
            var tabs = document.querySelectorAll('.agent-mode-tab');
            for (var i = 0; i < tabs.length; i++) {
                tabs[i].classList.toggle('active', tabs[i].getAttribute('data-mode') === mode);
            }
            // Update status indicator
            var info = data.mode_info;
            if (info) {
                var indicator = document.getElementById('currentModeName');
                if (indicator) indicator.textContent = info.icon + ' ' + info.label;
            }
        }
    } catch (e) {
        console.error('Failed to switch agent mode:', e);
    }
}

async function loadSessionMode() {
    var sid = State.activeSessionId;
    if (!sid) return;

    try {
        var data = await api('/api/mode?session_id=' + encodeURIComponent(sid));
        if (data.mode) {
            _currentMode = data.mode;
            var tabs = document.querySelectorAll('.agent-mode-tab');
            for (var i = 0; i < tabs.length; i++) {
                tabs[i].classList.toggle('active', tabs[i].getAttribute('data-mode') === data.mode);
            }
            if (data.mode_info) {
                var indicator = document.getElementById('currentModeName');
                if (indicator) indicator.textContent = data.mode_info.icon + ' ' + data.mode_info.label;
            }
        }
    } catch (e) {
        console.error('Failed to load session mode:', e);
    }
}


// ── Mode Intent Detection & Suggestion UI ──

var _modeSuggestions = null;        // cached suggestions from backend
var _modeIntentTimer = null;        // debounce timer
var _modeIntentLastText = '';       // last analyzed text
var _pendingSuggestedMode = null;   // mode selected by user from suggestions

/**
 * Called on every keystroke in the prompt input (debounced).
 * Analyzes the message and shows mode suggestions.
 */
function analyzeModeIntent(text) {
    text = (text || '').trim();
    if (text.length < 3) {
        hideModeSuggestions();
        return;
    }
    if (text === _modeIntentLastText) return;

    clearTimeout(_modeIntentTimer);
    _modeIntentTimer = setTimeout(function () {
        _fetchModeIntent(text);
    }, 500);
}

async function _fetchModeIntent(text) {
    try {
        var data = await api('/api/mode/intent', {
            method: 'POST',
            body: { message: text }
        });
        _modeSuggestions = data.suggestions;
        _modeIntentLastText = text;
        _renderModeSuggestions(data.suggestions);
    } catch (e) {
        console.error('Mode intent analysis failed:', e);
    }
}

function _renderModeSuggestions(suggestions) {
    var bar = document.getElementById('modeSuggestionBar');
    var body = document.getElementById('modeSuggestionBody');
    if (!bar || !body) return;

    if (!suggestions || suggestions.length === 0) {
        hideModeSuggestions();
        return;
    }

    body.innerHTML = '';
    for (var i = 0; i < suggestions.length; i++) {
        var s = suggestions[i];
        var btn = document.createElement('button');
        btn.className = 'mode-suggestion-btn';
        btn.setAttribute('data-mode', s.mode);
        btn.onclick = (function (mode) {
            return function () { selectSuggestedMode(mode); };
        })(s.mode);

        btn.innerHTML =
            '<span class="mode-suggestion-btn-icon">' + esc(s.icon) + '</span>' +
            '<span class="mode-suggestion-btn-info">' +
            '<span class="mode-suggestion-btn-label">' + esc(s.label) + '</span>' +
            '<span class="mode-suggestion-btn-desc">' + esc(s.description) + '</span>' +
            '</span>' +
            '<span class="mode-suggestion-btn-confidence">' + s.confidence + '%</span>' +
            '<span class="mode-suggestion-btn-badge">' + esc(s.icon) + ' ' + esc(s.label) + '</span>';

        body.appendChild(btn);
    }

    bar.style.display = 'block';
}

function hideModeSuggestions() {
    var bar = document.getElementById('modeSuggestionBar');
    if (bar) bar.style.display = 'none';
    _modeSuggestions = null;
    _pendingSuggestedMode = null;
}

/**
 * User clicked a suggested mode.
 * Apply the mode to the session and update UI, then show a toast.
 */
async function selectSuggestedMode(modeSlug) {
    try {
        await switchAgentMode(modeSlug);
        _pendingSuggestedMode = modeSlug;

        // Update suggestion bar visual: highlight the selected one + show approval badge
        var btns = document.querySelectorAll('.mode-suggestion-btn');
        for (var i = 0; i < btns.length; i++) {
            var btn = btns[i];
            if (btn.getAttribute('data-mode') === modeSlug) {
                btn.classList.add('selected');
                // Move the badge from behind to inline approval indicator
                var badge = btn.querySelector('.mode-suggestion-btn-badge');
                if (badge) {
                    badge.classList.add('approved');
                }
            } else {
                btn.classList.remove('selected');
                btn.style.opacity = '0.55';
            }
        }

        // Update header to show approval state
        var headerLabel = document.querySelector('.mode-suggestion-label');
        if (headerLabel) {
            var info = _modeSuggestions ? _modeSuggestions.find(function (s) { return s.mode === modeSlug; }) : null;
            if (info) {
                headerLabel.textContent = info.icon + ' ' + info.label + ' 모드 승인됨 — Enter로 실행';
            }
        }

        // Show toast confirmation
        var info = _modeSuggestions ? _modeSuggestions.find(function (s) { return s.mode === modeSlug; }) : null;
        if (info) {
            showToast(info.icon + ' ' + info.label + ' 모드 승인됨 (Enter=실행)');
        } else {
            showToast('모드가 전환되었습니다.');
        }
    } catch (e) {
        console.error('Failed to apply suggested mode:', e);
    }
}

/**
 * Render mode suggestion cards directly in the chat area.
 * Called by sendPrompt() when intent detection returns suggestions.
 * Each card is a clickable box that selects a mode, then proceeds with the send.
 */
function renderModeSuggestionCards(suggestions, displayText, uploaded) {
    hideModeSuggestions();

    var box = document.getElementById('chatMessages');
    if (!box) return;

    // Create card container
    var container = document.createElement('div');
    container.className = 'mode-suggestion-cards';
    container.id = 'modeSuggestionCards';

    // Header
    var header = document.createElement('div');
    header.className = 'mode-suggestion-cards-header';
    header.id = 'modeSuggestionCardsHeader';
    header.innerHTML = '<span>🤔 어떤 작업을 도와드릴까요?</span>';
    container.appendChild(header);

    // Cards wrapper
    var cardsWrap = document.createElement('div');
    cardsWrap.className = 'mode-suggestion-cards-body';

    for (var i = 0; i < suggestions.length; i++) {
        var s = suggestions[i];
        var card = document.createElement('div');
        card.className = 'mode-suggestion-card';
        card.setAttribute('data-mode', s.mode);
        card.setAttribute('data-index', i + 1);
        card.innerHTML =
            '<span class="mode-suggestion-card-num">' + (i + 1) + '</span>' +
            '<span class="mode-suggestion-card-icon">' + esc(s.icon) + '</span>' +
            '<div class="mode-suggestion-card-body">' +
            '<span class="mode-suggestion-card-label">' + esc(s.label) + '</span>' +
            '<span class="mode-suggestion-card-desc">' + esc(s.description) + '</span>' +
            '</div>' +
            '<span class="mode-suggestion-card-slug">' + esc(s.mode) + '</span>' +
            '<span class="mode-suggestion-card-check">✓</span>';

        card.onclick = (function (modeSlug, info) {
            return async function () {
                // Apply the selected mode
                await switchAgentMode(modeSlug);

                // Visual feedback: highlight selected, dim others
                var allCards = document.querySelectorAll('.mode-suggestion-card');
                for (var j = 0; j < allCards.length; j++) {
                    if (allCards[j].getAttribute('data-mode') === modeSlug) {
                        allCards[j].classList.add('selected');
                    } else {
                        allCards[j].classList.add('dimmed');
                    }
                }

                // Update header to show approval
                var hdr = document.getElementById('modeSuggestionCardsHeader');
                if (hdr) {
                    hdr.innerHTML = '<span>✅ ' + esc(info.icon) + ' ' + esc(info.label) + ' 모드 승인됨 — 실행 중…</span>';
                }

                // Disable skip button
                var skipBtn = document.getElementById('modeSuggestionSkipBtn');
                if (skipBtn) skipBtn.disabled = true;

                showToast(esc(info.icon) + ' ' + esc(info.label) + ' 모드로 실행합니다');

                // Proceed with the actual send
                if (typeof _executeAgentStream === 'function') {
                    await _executeAgentStream(displayText, uploaded);
                }
            };
        })(s.mode, s);

        cardsWrap.appendChild(card);
    }

    container.appendChild(cardsWrap);

    // Footer: skip button
    var footer = document.createElement('div');
    footer.className = 'mode-suggestion-cards-footer';

    var skipBtn = document.createElement('button');
    skipBtn.className = 'mode-suggestion-skip-btn';
    skipBtn.id = 'modeSuggestionSkipBtn';
    skipBtn.textContent = '현재 모드(' + _currentMode + ')로 바로 실행';
    skipBtn.onclick = async function () {
        // Remove the card container
        container.remove();
        // Proceed with normal execution
        if (typeof _executeAgentStream === 'function') {
            await _executeAgentStream(displayText, uploaded);
        }
    };

    footer.appendChild(skipBtn);
    container.appendChild(footer);

    box.appendChild(container);
    scrollToChatBottom();
}

/**
 * Send the message using the currently active mode (ignore suggestions).
 */
function sendWithCurrentMode() {
    _pendingSuggestedMode = null;
    hideModeSuggestions();

    // Trigger the actual send
    var input = document.getElementById('promptInput');
    if (input && input.value.trim()) {
        // The sendPrompt function will use the current active mode
        if (typeof sendPrompt === 'function') {
            sendPrompt();
        }
    }
}

/**
 * Called by sendPrompt() on every send.
 * If the user manually clicked a suggestion button, use that.
 * Otherwise, auto-select the top-ranked suggestion (if available).
 * Applies the mode via switchAgentMode(), then hides the bar.
 */
async function consumeSuggestedMode() {
    var mode = _pendingSuggestedMode;

    // No manual click → keep current mode (do NOT auto-pick top suggestion)
    // User must explicitly click a suggestion to switch modes

    _pendingSuggestedMode = null;
    hideModeSuggestions();
    return mode || _currentMode;
}

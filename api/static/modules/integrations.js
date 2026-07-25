/**
 * ── Integration Module (Slack & Notion) ──
 * 
 * Dependency: core.js ($, api, showToast, State)
 * 
 * Provides: loadIntegrationsPanel, sendToSlack, sendToNotion,
 *           testSlack, testNotion, saveIntegrationConfig
 */
"use strict";

// ── Module state ──
var _integrationConfig = null;
var _integrationSending = false;

/**
 * Load integrations panel — called from switchPanel('integrations')
 */
async function loadIntegrationsPanel() {
    await loadIntegrationConfig();
    await _renderIntegrationPanel();
}

/**
 * Fetch current integration config from API
 */
async function loadIntegrationConfig() {
    try {
        var data = await api('/api/integration/config');
        _integrationConfig = data;
    } catch (e) {
        _integrationConfig = { slack: { enabled: false, configured: false }, notion: { enabled: false, configured: false } };
        console.error('Failed to load integration config:', e);
    }
}

/**
 * Render the full integration panel based on current config
 */
function _renderIntegrationPanel() {
    if (!_integrationConfig) return;

    _renderSlackSection();
    _renderNotionSection();
}

/**
 * Render Slack configuration section
 */
function _renderSlackSection() {
    var container = document.getElementById('integrationSlackConfig');
    if (!container) return;

    var sc = _integrationConfig.slack;
    var statusIcon = sc.configured ? '\u2705' : '\u26A0\uFE0F';
    var statusText = sc.configured ? '\uC5F0\uACB0\uB428' : '\uBBF8\uC124\uC815';
    var statusColor = sc.configured ? 'var(--green,#4caf50)' : 'var(--muted)';

    container.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">' +
        '<span style="font-size:11px;font-weight:600">\uD83D\uDCAC Slack</span>' +
        '<span style="font-size:9px;padding:1px 6px;border-radius:4px;background:' + statusColor + '22;color:' + statusColor + '">' +
        statusIcon + ' ' + statusText + '</span>' +
        '</div>' +
        '<div style="display:flex;flex-direction:column;gap:4px">' +
        '<label style="font-size:10px;color:var(--muted)">Webhook URL</label>' +
        '<input id="intSlackWebhook" type="password" placeholder="https://hooks.slack.com/services/..." ' +
        'style="width:100%;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--text);font-size:10px;font-family:monospace" ' +
        'onchange="_markIntegrationDirty()">' +
        '<label style="font-size:10px;color:var(--muted)">Bot Token (xoxb-...)</label>' +
        '<input id="intSlackToken" type="password" placeholder="xoxb-..." ' +
        'style="width:100%;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--text);font-size:10px;font-family:monospace" ' +
        'onchange="_markIntegrationDirty()">' +
        '<label style="font-size:10px;color:var(--muted)">\uAE30\uBCF8 \uCC44\uB110</label>' +
        '<input id="intSlackChannel" type="text" placeholder="#general" ' +
        'style="width:100%;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--text);font-size:10px;font-family:monospace" ' +
        'onchange="_markIntegrationDirty()">' +
        '</div>' +
        '<div style="display:flex;gap:4px;margin-top:6px">' +
        '<button class="cron-btn" style="padding:3px 8px;font-size:10px" onclick="testSlack()">\uD83E\uDDEA \uC5F0\uACB0 \uD14C\uC2A4\uD2B8</button>' +
        '<button id="intSlackSaveBtn" class="cron-btn run" style="padding:3px 8px;font-size:10px;display:none" onclick="saveIntegrationConfig()">\uD83D\uDCBE \uC800\uC7A5</button>' +
        '</div>';
}

/**
 * Render Notion configuration section
 */
function _renderNotionSection() {
    var container = document.getElementById('integrationNotionConfig');
    if (!container) return;

    var nc = _integrationConfig.notion;
    var statusIcon = nc.configured ? '\u2705' : '\u26A0\uFE0F';
    var statusText = nc.configured ? '\uC5F0\uACB0\uB428' : '\uBBF8\uC124\uC815';
    var statusColor = nc.configured ? 'var(--green,#4caf50)' : 'var(--muted)';

    container.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">' +
        '<span style="font-size:11px;font-weight:600">\uD83D\uDCC4 Notion</span>' +
        '<span style="font-size:9px;padding:1px 6px;border-radius:4px;background:' + statusColor + '22;color:' + statusColor + '">' +
        statusIcon + ' ' + statusText + '</span>' +
        '</div>' +
        '<div style="display:flex;flex-direction:column;gap:4px">' +
        '<label style="font-size:10px;color:var(--muted)">Integration Token</label>' +
        '<input id="intNotionToken" type="password" placeholder="secret_..." ' +
        'style="width:100%;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--text);font-size:10px;font-family:monospace" ' +
        'onchange="_markIntegrationDirty()">' +
        '<label style="font-size:10px;color:var(--muted)">Database ID</label>' +
        '<input id="intNotionDbId" type="text" placeholder="abc123..." ' +
        'style="width:100%;padding:5px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--text);font-size:10px;font-family:monospace" ' +
        'onchange="_markIntegrationDirty()">' +
        '</div>' +
        '<div style="display:flex;gap:4px;margin-top:6px">' +
        '<button class="cron-btn" style="padding:3px 8px;font-size:10px" onclick="testNotion()">\uD83E\uDDEA \uC5F0\uACB0 \uD14C\uC2A4\uD2B8</button>' +
        '<button id="intNotionSaveBtn" class="cron-btn run" style="padding:3px 8px;font-size:10px;display:none" onclick="saveIntegrationConfig()">\uD83D\uDCBE \uC800\uC7A5</button>' +
        '</div>';
}

/**
 * Mark integration config as dirty (show save buttons)
 */
function _markIntegrationDirty() {
    var slackSave = document.getElementById('intSlackSaveBtn');
    var notionSave = document.getElementById('intNotionSaveBtn');
    if (slackSave) slackSave.style.display = '';
    if (notionSave) notionSave.style.display = '';
}

/**
 * Save integration configuration to server
 */
async function saveIntegrationConfig() {
    var config = {
        slack: {
            enabled: true,
            webhook_url: (document.getElementById('intSlackWebhook')?.value || '').trim(),
            bot_token: (document.getElementById('intSlackToken')?.value || '').trim(),
            default_channel: (document.getElementById('intSlackChannel')?.value || '').trim() || '#general',
        },
        notion: {
            enabled: true,
            token: (document.getElementById('intNotionToken')?.value || '').trim(),
            database_id: (document.getElementById('intNotionDbId')?.value || '').trim(),
        }
    };

    try {
        var result = await api('/api/integration/config', {
            method: 'POST',
            body: JSON.stringify({ config: config })
        });
        if (result.ok) {
            var slackSave = document.getElementById('intSlackSaveBtn');
            var notionSave = document.getElementById('intNotionSaveBtn');
            if (slackSave) slackSave.style.display = 'none';
            if (notionSave) notionSave.style.display = 'none';
            showToast('\uC5F0\uB3D9 \uC124\uC815\uC774 \uC800\uC7A5\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
            await loadIntegrationConfig();
            _renderIntegrationPanel();
        } else {
            showToast('\uC124\uC815 \uC800\uC7A5 \uC2E4\uD328: ' + (result.message || 'unknown'), 'error');
        }
    } catch (e) {
        showToast('\uC124\uC815 \uC800\uC7A5 \uC911 \uC624\uB958: ' + e, 'error');
    }
}

/**
 * Send a message to Slack
 */
async function sendToSlack() {
    if (_integrationSending) return;

    var textEl = document.getElementById('intSlackMessage');
    var channelEl = document.getElementById('intSlackChannelSelect');
    if (!textEl) return;

    var text = textEl.value.trim();
    if (!text) {
        showToast('\uC804\uC1A1\uD560 \uBA54\uC2DC\uC9C0\uB97C \uC785\uB825\uD558\uC138\uC694.', 'error');
        return;
    }

    _integrationSending = true;
    var resultEl = document.getElementById('intSlackResult');
    if (resultEl) {
        resultEl.style.display = '';
        resultEl.style.background = 'var(--bg2)';
        resultEl.style.color = 'var(--muted)';
        resultEl.textContent = '\uC804\uC1A1 \uC911...';
    }

    try {
        var body = { text: text };
        if (channelEl && channelEl.value.trim()) {
            body.channel = channelEl.value.trim();
        }
        var data = await api('/api/integration/slack/send', {
            method: 'POST',
            body: JSON.stringify(body)
        });
        if (data.success) {
            if (resultEl) {
                resultEl.style.background = '#4caf5022';
                resultEl.style.color = 'var(--green,#4caf50)';
                resultEl.textContent = '\u2705 Slack\uC73C\uB85C \uC804\uC1A1\uB418\uC5C8\uC2B5\uB2C8\uB2E4. (\uCC44\uB110: ' + (data.channel || 'default') + ')';
            }
            textEl.value = '';
            showToast('Slack\uC73C\uB85C \uBA54\uC2DC\uC9C0\uAC00 \uC804\uC1A1\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
        } else {
            if (resultEl) {
                resultEl.style.background = '#f4433622';
                resultEl.style.color = 'var(--red,#f44336)';
                resultEl.textContent = '\u274C \uC804\uC1A1 \uC2E4\uD328: ' + (data.error || data.message || 'unknown');
            }
            showToast('Slack \uC804\uC1A1 \uC2E4\uD328', 'error');
        }
    } catch (e) {
        if (resultEl) {
            resultEl.style.background = '#f4433622';
            resultEl.style.color = 'var(--red,#f44336)';
            resultEl.textContent = '\u274C \uC624\uB958: ' + e;
        }
        showToast('Slack \uC804\uC1A1 \uC911 \uC624\uB958: ' + e, 'error');
    } finally {
        _integrationSending = false;
    }
}

/**
 * Test Slack connection
 */
async function testSlack() {
    try {
        var data = await api('/api/integration/slack/test', {
            method: 'POST',
            body: JSON.stringify({})
        });
        if (data.ok) {
            showToast('\u2705 Slack \uC5F0\uACB0 \uD14C\uC2A4\uD2B8 \uC131\uACF5! \uCC44\uB110\uC5D0 \uD14C\uC2A4\uD2B8 \uBA54\uC2DC\uC9C0\uAC00 \uC804\uC1A1\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
        } else {
            showToast('\u274C Slack \uC5F0\uACB0 \uD14C\uC2A4\uD2B8 \uC2E4\uD328: ' + (data.message || data.error || 'unknown'), 'error');
        }
    } catch (e) {
        showToast('Slack \uD14C\uC2A4\uD2B8 \uC911 \uC624\uB958: ' + e, 'error');
    }
}

/**
 * Send content to Notion
 */
async function sendToNotion() {
    if (_integrationSending) return;

    var titleEl = document.getElementById('intNotionTitle');
    var contentEl = document.getElementById('intNotionContent');
    var tagsEl = document.getElementById('intNotionTags');
    if (!titleEl || !contentEl) return;

    var title = titleEl.value.trim();
    var content = contentEl.value.trim();
    if (!title) {
        showToast('\uD398\uC774\uC9C0 \uC81C\uBAA9\uC744 \uC785\uB825\uD558\uC138\uC694.', 'error');
        return;
    }
    if (!content) {
        showToast('\uD398\uC774\uC9C0 \uB0B4\uC6A9\uC744 \uC785\uB825\uD558\uC138\uC694.', 'error');
        return;
    }

    _integrationSending = true;
    var resultEl = document.getElementById('intNotionResult');
    if (resultEl) {
        resultEl.style.display = '';
        resultEl.style.background = 'var(--bg2)';
        resultEl.style.color = 'var(--muted)';
        resultEl.textContent = '\uD398\uC774\uC9C0 \uC0DD\uC131 \uC911...';
    }

    try {
        var body = { title: title, content: content };
        if (tagsEl && tagsEl.value.trim()) {
            body.tags = tagsEl.value.trim();
        }
        var data = await api('/api/integration/notion/create', {
            method: 'POST',
            body: JSON.stringify(body)
        });
        if (data.ok && data.page && data.page.success) {
            if (resultEl) {
                resultEl.style.background = '#4caf5022';
                resultEl.style.color = 'var(--green,#4caf50)';
                resultEl.innerHTML = '\u2705 Notion \uD398\uC774\uC9C0\uAC00 \uC0DD\uC131\uB418\uC5C8\uC2B5\uB2C8\uB2E4. <a href="' +
                    escapeHtml(data.page.url) + '" target="_blank" style="color:var(--accent)">\uD398\uC774\uC9C0 \uC5F4\uAE30</a>';
            }
            titleEl.value = '';
            contentEl.value = '';
            if (tagsEl) tagsEl.value = '';
            showToast('Notion \uD398\uC774\uC9C0\uAC00 \uC0DD\uC131\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
        } else {
            if (resultEl) {
                resultEl.style.background = '#f4433622';
                resultEl.style.color = 'var(--red,#f44336)';
                resultEl.textContent = '\u274C \uD398\uC774\uC9C0 \uC0DD\uC131 \uC2E4\uD328: ' + (data.message || 'unknown');
            }
            showToast('Notion \uD398\uC774\uC9C0 \uC0DD\uC131 \uC2E4\uD328', 'error');
        }
    } catch (e) {
        if (resultEl) {
            resultEl.style.background = '#f4433622';
            resultEl.style.color = 'var(--red,#f44336)';
            resultEl.textContent = '\u274C \uC624\uB958: ' + e;
        }
        showToast('Notion \uD398\uC774\uC9C0 \uC0DD\uC131 \uC911 \uC624\uB958: ' + e, 'error');
    } finally {
        _integrationSending = false;
    }
}

/**
 * Test Notion connection
 */
async function testNotion() {
    try {
        var data = await api('/api/integration/notion/test', {
            method: 'POST',
            body: JSON.stringify({})
        });
        if (data.ok && data.page && data.page.success) {
            showToast('\u2705 Notion \uC5F0\uACB0 \uD14C\uC2A4\uD2B8 \uC131\uACF5! \uD14C\uC2A4\uD2B8 \uD398\uC774\uC9C0\uAC00 \uC0DD\uC131\uB418\uC5C8\uC2B5\uB2C8\uB2E4.');
        } else {
            showToast('\u274C Notion \uC5F0\uACB0 \uD14C\uC2A4\uD2B8 \uC2E4\uD328: ' + (data.message || data.error || 'unknown'), 'error');
        }
    } catch (e) {
        showToast('Notion \uD14C\uC2A4\uD2B8 \uC911 \uC624\uB958: ' + e, 'error');
    }
}

/**
 * Escape HTML special characters
 */
function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

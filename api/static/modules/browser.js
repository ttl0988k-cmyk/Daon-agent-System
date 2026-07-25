/**
 * ── Browser Automation Module (Playwright) ──
 * 
 * Dependency: core.js ($, api, showToast, State)
 * 
 * Provides: navigate, snapshot, click, type, screenshot,
 *           execute JS, close browser via backend Playwright.
 */

// ── Module state ──
var _browserConnected = false;
var _browserLastUrl = '';
var _browserElements = [];
var _browserScreenshotData = null;

/**
 * Load browser panel — called from switchPanel('browser')
 */
async function loadBrowserPanel() {
    // Check browser status
    try {
        const data = await api('/api/browser/status');
        _browserConnected = data.status === 'connected';
        _browserLastUrl = data.url || '';
        // Show pending URL hint if browser tab isn't open yet
        if (!_browserConnected && data.pending_url) {
            showToast('브라우저 탭이 아직 열리지 않았습니다. 브라우저 뷰를 열어주세요.', 4000);
        }
    } catch (e) {
        _browserConnected = false;
    }
    renderBrowserPanel();
}

/**
 * Render the browser panel UI.
 */
function renderBrowserPanel() {
    const statusEl = $('browserStatus');
    const urlInput = $('browserUrlInput');
    const elementsEl = $('browserElementsList');
    const screenshotEl = $('browserScreenshot');

    if (statusEl) {
        if (_browserConnected) {
            statusEl.textContent = '🟢 연결됨';
            statusEl.style.color = 'var(--success)';
        } else {
            statusEl.textContent = '⚫ 연결 안 됨';
            statusEl.style.color = 'var(--muted)';
        }
    }

    if (urlInput && _browserLastUrl) {
        urlInput.value = _browserLastUrl;
    }

    if (elementsEl && _browserElements.length === 0) {
        elementsEl.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">페이지를 먼저 열어주세요.</div>';
    }

    // Show cached screenshot
    if (screenshotEl && _browserScreenshotData) {
        screenshotEl.innerHTML = `<img src="${esc(_browserScreenshotData)}" style="max-width:100%;border-radius:4px" />`;
    }
}

/**
 * Navigate to a URL.
 */
async function browserNavigate() {
    const input = $('browserUrlInput');
    const url = (input ? input.value : '').trim();
    if (!url) {
        showToast('URL을 입력해주세요.');
        return;
    }

    const resultEl = $('browserResult');
    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<span style="color:var(--muted)">⏳ 탐색 중...</span>';
    }

    try {
        const data = await api('/api/browser/navigate', {
            method: 'POST',
            body: { url: url }
        });

        if (data.ok) {
            _browserConnected = true;
            _browserLastUrl = data.url;
            _browserElements = [];
            _browserScreenshotData = null;

            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--success)">✅ 열림: ${esc(data.title || data.url)}</span>`;
            }

            if (input) input.value = data.url;
            showToast('페이지 열기 완료');

            // Auto-snapshot
            setTimeout(() => browserSnapshot(), 300);
        } else {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--danger)">❌ ${esc(data.error || '탐색 실패')}</span>`;
            }
            showToast('탐색 실패');
        }
    } catch (e) {
        if (resultEl) {
            resultEl.innerHTML = `<span style="color:var(--danger)">❌ 오류: ${esc(e.message)}</span>`;
        }
        showToast('브라우저 오류');
    }
}

/**
 * Take an accessibility snapshot of the current page.
 */
async function browserSnapshot() {
    const elementsEl = $('browserElementsList');
    const resultEl = $('browserResult');

    if (elementsEl) {
        elementsEl.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">⏳ 스냅샷 수집 중...</div>';
    }

    try {
        const data = await api('/api/browser/snapshot', {
            method: 'POST',
            body: {}
        });

        if (data.ok) {
            _browserElements = data.elements || [];
            _browserLastUrl = data.url;

            renderBrowserElements(data.elements || []);

            if (resultEl) {
                const textLen = data.text ? data.text.length : 0;
                const truncNote = data.truncated ? ' (일부 생략됨)' : '';
                resultEl.innerHTML = `<span style="color:var(--success)">✅ ${data.elements.length}개 요소, 텍스트 ${textLen}자${truncNote}</span>`;
            }

            // Store page text for viewing
            if (data.text) {
                const textDisp = $('browserPageText');
                if (textDisp) {
                    textDisp.textContent = data.text;
                }
            }
        } else {
            if (elementsEl) {
                elementsEl.innerHTML = '<div style="padding:8px;color:var(--danger);font-size:11px">스냅샷 실패</div>';
            }
        }
    } catch (e) {
        if (elementsEl) {
            elementsEl.innerHTML = `<div style="padding:8px;color:var(--danger);font-size:11px">오류: ${esc(e.message)}</div>`;
        }
    }
}

/**
 * Render interactive elements found by snapshot.
 */
function renderBrowserElements(elements) {
    const el = $('browserElementsList');
    if (!el) return;

    if (!elements || elements.length === 0) {
        el.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">상호작용 요소가 없습니다.</div>';
        return;
    }

    let html = '';
    elements.forEach((elem) => {
        const tagIcon = elem.tag === 'a' ? '🔗' : elem.tag === 'button' ? '🔘' :
            elem.tag === 'input' ? '📝' : elem.tag === 'select' ? '📋' :
                elem.tag === 'textarea' ? '📄' : '🔹';
        const typeInfo = elem.type ? ` [${elem.type}]` : '';
        const text = elem.text || elem.id || elem.name || '(빈 요소)';
        html += `<div class="browser-elem" data-ref="${esc(elem.ref)}" onclick="browserClickElement('${esc(elem.ref)}')" title="클릭: ${esc(elem.ref)}">
            <span style="font-size:9px;color:var(--accent);font-family:monospace;margin-right:4px">${esc(elem.ref)}</span>
            <span>${tagIcon}</span>
            <span style="margin-left:4px;font-size:11px">${esc(text.substring(0, 80))}${typeInfo}</span>
        </div>`;
    });
    el.innerHTML = html;
}

/**
 * Click an element by ref ID (e.g., @e5).
 */
async function browserClickElement(ref) {
    const resultEl = $('browserResult');
    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = `<span style="color:var(--muted)">⏳ 클릭: ${esc(ref)}...</span>`;
    }

    try {
        const data = await api('/api/browser/click', {
            method: 'POST',
            body: { ref: ref }
        });

        if (data.ok) {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--success)">✅ 클릭됨: ${esc(ref)} — ${esc(data.title || data.url || '')}</span>`;
            }
            _browserLastUrl = data.url;
            // Auto-refresh snapshot
            setTimeout(() => browserSnapshot(), 300);
        } else {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--danger)">❌ ${esc(data.error || '클릭 실패')}</span>`;
            }
            showToast('클릭 실패');
        }
    } catch (e) {
        if (resultEl) {
            resultEl.innerHTML = `<span style="color:var(--danger)">❌ 오류: ${esc(e.message)}</span>`;
        }
    }

    renderBrowserPanel();
}

/**
 * Type text into an element by ref ID.
 */
async function browserTypeText() {
    const refInput = $('browserTypeRef');
    const textInput = $('browserTypeValue');
    const ref = refInput ? refInput.value.trim() : '';
    const text = textInput ? textInput.value : '';

    if (!ref) {
        showToast('요소 ref를 입력해주세요 (예: @e0)');
        return;
    }

    const resultEl = $('browserResult');
    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = `<span style="color:var(--muted)">⏳ 입력 중: ${esc(ref)}...</span>`;
    }

    try {
        const data = await api('/api/browser/type', {
            method: 'POST',
            body: { ref: ref, text: text }
        });

        if (data.ok) {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--success)">✅ 입력 완료: ${esc(ref)} ← "${esc(text.substring(0, 40))}"</span>`;
            }
            showToast('입력 완료');
            setTimeout(() => browserSnapshot(), 300);
        } else {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--danger)">❌ ${esc(data.error || '입력 실패')}</span>`;
            }
            showToast('입력 실패');
        }
    } catch (e) {
        if (resultEl) {
            resultEl.innerHTML = `<span style="color:var(--danger)">❌ 오류: ${esc(e.message)}</span>`;
        }
    }
}

/**
 * Take a screenshot of the current page.
 */
async function browserScreenshot() {
    const resultEl = $('browserResult');
    const screenshotEl = $('browserScreenshot');

    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<span style="color:var(--muted)">⏳ 스크린샷 촬영 중...</span>';
    }

    try {
        const data = await api('/api/browser/screenshot', {
            method: 'POST',
            body: {}
        });

        if (data.ok && (data.png_base64 || data.screenshot)) {
            const b64 = data.png_base64 || data.screenshot;
            _browserScreenshotData = b64;
            if (screenshotEl) {
                screenshotEl.innerHTML = `<img src="data:image/png;base64,${esc(b64)}" style="max-width:100%;border-radius:4px" />`;
                screenshotEl.style.display = 'block';
            }
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--success)">✅ 스크린샷 촬영 완료 — ${esc(data.title || data.url)}</span>`;
            }
            showToast('스크린샷 촬영 완료');
        } else {
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--danger)">❌ ${esc(data.error || '스크린샷 실패')}</span>`;
            }
        }
    } catch (e) {
        if (resultEl) {
            resultEl.innerHTML = `<span style="color:var(--danger)">❌ 오류: ${esc(e.message)}</span>`;
        }
    }
}

/**
 * Execute JavaScript in the page context.
 */
async function browserExecuteJS() {
    const exprInput = $('browserJSInput');
    const expression = exprInput ? exprInput.value.trim() : '';
    if (!expression) {
        showToast('JavaScript 코드를 입력해주세요.');
        return;
    }

    const resultEl = $('browserResult');
    const jsOutput = $('browserJSOutput');

    if (resultEl) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = '<span style="color:var(--muted)">⏳ 실행 중...</span>';
    }

    try {
        const data = await api('/api/browser/execute', {
            method: 'POST',
            body: { expression: expression }
        });

        if (data.ok) {
            const output = typeof data.result === 'string' ? data.result : JSON.stringify(data.result, null, 2);
            if (jsOutput) {
                jsOutput.textContent = output;
                jsOutput.style.display = 'block';
            }
            if (resultEl) {
                resultEl.innerHTML = '<span style="color:var(--success)">✅ JavaScript 실행 완료</span>';
            }
        } else {
            if (jsOutput) {
                jsOutput.textContent = data.error || '실행 실패';
                jsOutput.style.display = 'block';
            }
            if (resultEl) {
                resultEl.innerHTML = `<span style="color:var(--danger)">❌ ${esc(data.error || '실행 실패')}</span>`;
            }
        }
    } catch (e) {
        if (jsOutput) {
            jsOutput.textContent = e.message;
            jsOutput.style.display = 'block';
        }
        if (resultEl) {
            resultEl.innerHTML = `<span style="color:var(--danger)">❌ 오류: ${esc(e.message)}</span>`;
        }
    }
}

/**
 * Close the browser.
 */
async function browserClose() {
    try {
        await api('/api/browser/close', { method: 'POST' });
        _browserConnected = false;
        _browserLastUrl = '';
        _browserElements = [];
        _browserScreenshotData = null;
    } catch (e) {
        // ignore
    }

    // Clear UI
    const screenshotEl = $('browserScreenshot');
    const elementsEl = $('browserElementsList');
    const resultEl = $('browserResult');
    const jsOutput = $('browserJSOutput');
    const pageText = $('browserPageText');
    const urlInput = $('browserUrlInput');

    if (screenshotEl) { screenshotEl.innerHTML = ''; screenshotEl.style.display = 'none'; }
    if (elementsEl) elementsEl.innerHTML = '<div style="padding:8px;color:var(--muted);font-size:11px">브라우저가 닫혔습니다.</div>';
    if (resultEl) { resultEl.innerHTML = ''; resultEl.style.display = 'none'; }
    if (jsOutput) { jsOutput.textContent = ''; jsOutput.style.display = 'none'; }
    if (pageText) pageText.textContent = '';
    if (urlInput) urlInput.value = '';

    renderBrowserPanel();
    showToast('브라우저 닫힘');
}

/**
 * Cleanup browser panel resources.
 */
function cleanupBrowserPanel() {
    // nothing critical to clean on switch-away
}

// ── Beginner Overlay (초보자 시작 화면) ──

const BEGINNER_STORAGE_KEY = 'daon_skip_beginner';

/**
 * Initialize: show overlay on first visit unless user opted out.
 */
(function initBeginnerOverlay() {
    const skip = localStorage.getItem(BEGINNER_STORAGE_KEY);
    if (skip === 'true') return;

    // Wait for DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _showOverlay);
    } else {
        _showOverlay();
    }
})();

function _showOverlay() {
    const overlay = document.getElementById('beginnerOverlay');
    if (!overlay) return;
    overlay.style.display = 'block';

    // 사이드바 + 좌측 패널을 오버레이 위에 올려 클릭 가능하게
    _elevateSidebar(true);

    // Add hover effects to cards
    const cards = overlay.querySelectorAll('.beginner-card');
    cards.forEach(card => {
        card.addEventListener('mouseenter', () => {
            card.style.borderColor = 'var(--accent)';
            card.style.transform = 'translateY(-3px)';
            card.style.boxShadow = '0 8px 24px rgba(0,0,0,0.3)';
        });
        card.addEventListener('mouseleave', () => {
            card.style.borderColor = 'var(--border2)';
            card.style.transform = 'translateY(0)';
            card.style.boxShadow = 'none';
        });
    });

    // Focus the input
    const input = document.getElementById('beginnerInput');
    if (input) setTimeout(() => input.focus(), 300);
}

/**
 * Show the beginner overlay (from header button).
 */
function showBeginnerOverlay() {
    const overlay = document.getElementById('beginnerOverlay');
    if (overlay) overlay.style.display = 'block';
    _elevateSidebar(true);
}

/**
 * Dismiss the overlay without sending anything.
 */
function beginnerDismiss() {
    const overlay = document.getElementById('beginnerOverlay');
    if (overlay) overlay.style.display = 'none';
    _elevateSidebar(false);
}

/**
 * Card clicked → dismiss overlay, fill prompt, and send.
 */
function beginnerStart(title, prompt) {
    beginnerDismiss();

    // Fill the main chat input and trigger send
    const promptInput = document.getElementById('promptInput');
    if (promptInput) {
        promptInput.value = prompt;
        // Small delay to ensure UI is visible
        setTimeout(() => {
            if (typeof sendPrompt === 'function') {
                sendPrompt();
            }
        }, 200);
    }
}

/**
 * Direct input from the beginner overlay.
 */
function beginnerSendInput() {
    const input = document.getElementById('beginnerInput');
    if (!input) return;
    const text = input.value.trim();
    if (!text) {
        input.focus();
        return;
    }

    beginnerDismiss();

    const promptInput = document.getElementById('promptInput');
    if (promptInput) {
        promptInput.value = text;
        setTimeout(() => {
            if (typeof sendPrompt === 'function') {
                sendPrompt();
            }
        }, 200);
    }
}

/**
 * Toggle "don't show again" preference.
 */
function beginnerToggleDontShow(checked) {
    if (checked) {
        localStorage.setItem(BEGINNER_STORAGE_KEY, 'true');
    } else {
        localStorage.removeItem(BEGINNER_STORAGE_KEY);
    }
}

/**
 * 오버레이 표시 중 사이드바 + 좌측 패널을 오버레이 위에 올림.
 * 초보자도 세션/MCP 등 좌측 메뉴를 클릭해서 확인할 수 있게.
 */
function _elevateSidebar(elevate) {
    const sidebar = document.querySelector('.sidebar-nav');
    const leftPanel = document.querySelector('.left-panel');
    const z = elevate ? '10001' : '';
    if (sidebar) {
        sidebar.style.position = elevate ? 'relative' : '';
        sidebar.style.zIndex = z;
    }
    if (leftPanel) {
        leftPanel.style.position = elevate ? 'relative' : '';
        leftPanel.style.zIndex = z;
    }
}

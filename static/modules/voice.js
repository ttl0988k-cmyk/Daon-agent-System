/**
 * ── Voice Input Module (Web Speech API) ──
 * Browser SpeechRecognition → text auto-fill (no API key needed)
 *
 * Dependency: core.js ($, api, showToast, State)
 */

// ── Module state ──
var _voiceRecognition = null;
var _voiceIsRecording = false;
var _voicePrefix = '';  // existing textarea content before recording started
var _voiceFinalTranscript = '';  // accumulated final results

/**
 * Initialize voice input ─ called from setupEventListeners in chat.js
 * Detects SpeechRecognition support and shows/hides mic button.
 */
function initVoiceInput() {
    const btn = $('voiceMicBtn');
    if (!btn) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const hasSpeechRecognition = typeof SpeechRecognition !== 'undefined';

    if (hasSpeechRecognition) {
        btn.style.display = '';
        btn.onclick = toggleVoiceRecording;
        btn.title = '음성 입력 (Web Speech API)';
    }
    // else: button stays hidden (display:none in HTML)
}

/**
 * Toggle voice recording on/off.
 */
async function toggleVoiceRecording() {
    if (_voiceIsRecording) {
        stopVoiceRecording();
        return;
    }
    startVoiceRecording();
}

/**
 * Start speech recognition.
 */
function startVoiceRecording() {
    const btn = $('voiceMicBtn');
    const input = $('promptInput');
    if (!btn || !input) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        showToast('이 브라우저는 음성 인식을 지원하지 않습니다.');
        return;
    }

    try {
        _voiceRecognition = new SpeechRecognition();
        _voiceRecognition.lang = 'ko-KR';         // Korean
        _voiceRecognition.interimResults = true;   // show partial results
        _voiceRecognition.continuous = true;       // keep listening until stopped
        _voiceRecognition.maxAlternatives = 1;

        // Snapshot existing textarea content
        _voicePrefix = input.value;
        _voiceFinalTranscript = '';

        _voiceRecognition.onresult = (event) => {
            if (!_voiceIsRecording) return; // ignore late events after stop

            let interim = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                if (result.isFinal) {
                    _voiceFinalTranscript += result[0].transcript;
                } else {
                    interim += result[0].transcript;
                }
            }

            // Show interim + final in textarea
            const displayText = _voiceFinalTranscript + interim;
            input.value = _voicePrefix
                ? _voicePrefix + (_voicePrefix.endsWith(' ') || _voicePrefix.endsWith('\n') ? '' : ' ') + displayText
                : displayText;

            // Trigger textarea auto-resize
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 400) + 'px';
        };

        _voiceRecognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            if (event.error === 'not-allowed') {
                showToast('마이크 권한이 차단되었습니다. 브라우저 설정에서 마이크를 허용해주세요.');
            } else if (event.error === 'no-speech') {
                showToast('음성이 감지되지 않았습니다. 다시 시도해주세요.');
            } else if (event.error === 'aborted') {
                // normal stop, no message needed
            } else {
                showToast('음성 인식 오류: ' + event.error);
            }
            _setRecordingUI(false);
        };

        _voiceRecognition.onend = () => {
            // If not recording anymore (user clicked stop), just clean up
            if (!_voiceIsRecording) {
                _voiceRecognition = null;
                return;
            }
            // Auto-restart for continuous mode (browser may stop after silence)
            try { _voiceRecognition.start(); return; } catch (e) { /* ignore */ }
            _setRecordingUI(false);
        };

        _voiceRecognition.onstart = () => {
            _setRecordingUI(true);
            showToast('🎤 음성 인식 시작 (말씀하신 후 다시 마이크를 누르면 종료됩니다)');
        };

        _voiceRecognition.start();
    } catch (e) {
        console.error('Voice recognition init error:', e);
        showToast('음성 인식 초기화 실패: ' + e.message);
    }
}

/**
 * Stop the current speech recognition.
 */
function stopVoiceRecording() {
    // Mark stopped FIRST so onend handler doesn't auto-restart
    _voiceIsRecording = false;
    _setRecordingUI(false);

    if (_voiceRecognition) {
        try {
            _voiceRecognition.stop();
        } catch (e) {
            // may already be stopped
        }
    }
    _voiceRecognition = null;
    showToast('🔄 음성 변환 완료...');
}

/**
 * Update the recording UI state.
 */
function _setRecordingUI(active) {
    _voiceIsRecording = active;
    const btn = $('voiceMicBtn');
    if (btn) {
        btn.classList.toggle('recording', active);
        btn.title = active ? '녹음 중지 (클릭)' : '음성 입력 (Web Speech API)';
    }
}

/**
 * Clean up ─ called on page unload (optional).
 */
function cleanupVoiceInput() {
    if (_voiceIsRecording) {
        stopVoiceRecording();
    }
    _voiceRecognition = null;
}

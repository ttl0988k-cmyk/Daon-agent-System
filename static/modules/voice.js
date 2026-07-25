/**
 * ── Voice Input Module (Web Speech API + Cumulative Streaming ASR fallback) ──
 *
 * Primary: Browser SpeechRecognition → real-time text as you speak.
 * Fallback: If SpeechRecognition fails (Electron: Google servers unreachable),
 *           use MediaRecorder with 2s timeslice → send cumulative audio chunks
 *           to POST /api/whisper/transcribe → text updates in real-time as you speak.
 *           On stop: final full-audio transcription replaces intermediate results.
 *
 * Dependency: core.js ($, showToast, State)
 */

console.log("🔥 [VOICE ENGINE VERSION 2026-07-23 23:10] 🔥");

// ── Module state ──
var _voiceRecognition = null;
var _voiceMediaRecorder = null;
var _voiceAudioChunks = [];
var _voiceIsRecording = false;
var _voicePrefix = '';
var _voiceFinalTranscript = '';
var _voiceStream = null;
var _voiceUsingFallback = false;
var _voiceFallbackChecked = false;
var _voiceFallbackNeeded = false;

// ── Cumulative streaming state ──
var _voiceCumulativeText = '';      // latest cumulative transcript displayed
var _voiceCumulativeSeq = 0;        // monotonically increasing request counter
var _voicePendingFinal = false;     // true while final transcription is in-flight
var _voiceThrottleTimer = null;     // throttle timer for scheduling next send
var _voiceSendInFlight = false;     // true while _sendCumulativeTranscription is awaiting
var _voiceLastSendTime = 0;         // timestamp of last send (ms)
var _voicePendingSend = false;      // true when new chunks arrived while throttled
var THROTTLE_INTERVAL = 800;        // minimum ms between cumulative sends (matches MediaRecorder 800ms chunk)

/**
 * Initialize voice input ─ called from setupEventListeners in chat.js.
 */
function initVoiceInput() {
    var btn = $('voiceMicBtn');
    if (!btn) return;

    // Always show mic button — we have backend ASR fallback
    btn.style.display = '';
    btn.onclick = toggleVoiceRecording;
    btn.title = '음성 입력';

    // Check if SpeechRecognition exists
    var hasSR = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    console.log('[voice] SpeechRecognition available:', hasSR);
    _voiceFallbackChecked = true;
    if (!hasSR) {
        _voiceFallbackNeeded = true;
        console.log('[voice] No SpeechRecognition → backend ASR fallback');
    }
}

/**
 * Toggle voice recording on/off.
 */
async function toggleVoiceRecording() {
    if (_voiceIsRecording) {
        stopVoiceRecording();
        return;
    }
    await startVoiceRecording();
}

/**
 * Start voice recording.
 * Tries SpeechRecognition first; falls back to cumulative streaming MediaRecorder if it fails.
 */
async function startVoiceRecording() {
    var btn = $('voiceMicBtn');
    var input = $('promptInput');
    if (!btn || !input) return;

    _voicePrefix = input.value;
    _voiceFinalTranscript = '';
    _voiceCumulativeText = '';
    _voiceCumulativeSeq = 0;
    _voicePendingFinal = false;
    _voiceSendInFlight = false;
    _voiceLastSendTime = 0;
    _voicePendingSend = false;
    _voiceUsingFallback = true;  // Always use cumulative streaming (MediaRecorder + faster-whisper)
    clearTimeout(_voiceThrottleTimer);

    // Skip Web Speech API entirely — it's unreliable in Electron (hangs silently
    // when Google STT servers are unreachable) and inconsistent across browsers.
    // Cumulative streaming (MediaRecorder timeslice → faster-whisper) gives true
    // real-time feedback everywhere.
    await _startCumulativeRecording(input);
}

// ── Cumulative Streaming ASR (sends cumulative audio → gets full text so far) ──

async function _startCumulativeRecording(input) {
    _cleanupMedia();
    _voiceUsingFallback = true;
    _voiceCumulativeText = '';
    _voiceCumulativeSeq = 0;
    _voiceLastSentIndex = 0;
    _voicePendingFinal = false;
    _voiceSendInFlight = false;
    _voiceLastSendTime = 0;
    _voicePendingSend = false;
    clearTimeout(_voiceThrottleTimer);

    // Set up mirror div for confirmed/pending visual distinction
    _setupMirror(input);

    try {
        _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        var mimeType = _getSupportedMimeType();
        _voiceMediaRecorder = new MediaRecorder(_voiceStream, {
            mimeType: mimeType,
            audioBitsPerSecond: 128000
        });

        _voiceAudioChunks = [];

        _voiceMediaRecorder.ondataavailable = function (event) {
            if (event.data && event.data.size > 0) {
                _voiceAudioChunks.push(event.data);
                var chunkIdx = _voiceAudioChunks.length;
                var totalBytes = _voiceAudioChunks.reduce(function (sum, c) { return sum + c.size; }, 0);
                console.log('[voice] Chunk #' + chunkIdx + ' arrived: chunkBytes=' + event.data.size + 'B, cumulativeTotal=' + totalBytes + 'B');

                _scheduleThrottledSend(input);
            }
        };

        _voiceMediaRecorder.onstop = function () {
            clearTimeout(_voiceThrottleTimer);
            _voiceSendInFlight = false;
            _processFinalRecording(input);
        };

        // 800ms timeslice = chunk every ~0.8s
        _voiceMediaRecorder.start(800);
        _setRecordingUI(true);
        showToast('🎤 실시간 음성 인식 중... 말하는 대로 텍스트가 나타납니다');

        // Send first transcription after ~800ms
        _voiceThrottleTimer = setTimeout(function () {
            if (!_voiceIsRecording) return;
            _scheduleThrottledSend(input);
        }, 800);

    } catch (e) {
        console.error('[voice] Cumulative recording error:', e);
        if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
            showToast('마이크 권한이 차단되었습니다. 설정에서 마이크를 허용해주세요.');
        } else if (e.name === 'NotFoundError') {
            showToast('마이크를 찾을 수 없습니다.');
        } else {
            showToast('음성 녹음 초기화 실패: ' + (e.message || '알 수 없는 오류'));
        }
        _cleanupMedia();
        _setRecordingUI(false);
    }
}

/**
 * Send cumulative audio (all chunks so far) to server.
 * Uses Whisper's segment-level output: previous segments are "confirmed",
 * only the last segment is "pending" (may change on next update).
 */
var _voiceLastSentIndex = 0;

async function _sendCumulativeTranscription(input) {
    console.log('[voice] send enter', { inFlight: _voiceSendInFlight, chunks: _voiceAudioChunks.length });

    if (!_voiceIsRecording || _voiceAudioChunks.length === 0) return;
    if (!_voiceMediaRecorder || _voiceMediaRecorder.mimeType === '') return;
    if (_voiceAudioChunks.length <= _voiceLastSentIndex) return;

    _voiceSendInFlight = true;
    _voicePendingSend = false;
    _voiceLastSendTime = Date.now();

    var currentSeq = ++_voiceCumulativeSeq;
    console.log('[voice] lock acquired: seq=' + currentSeq + ', inFlight=' + _voiceSendInFlight);

    try {
        var mimeType = _voiceMediaRecorder.mimeType;
        var ext = _mimeToExt(mimeType);

        // Filter out empty or zero-payload initialization fragments (< 500B)
        var chunkBlob = new Blob(_voiceAudioChunks.slice(), { type: mimeType });
        console.log('[voice] blob', chunkBlob.size, chunkBlob.type);

        if (chunkBlob.size < 500) {
            console.log('[voice] Skipping sub-header fragment: size=' + chunkBlob.size + 'B');
            return;
        }

        var formData = new FormData();
        formData.append('audio', chunkBlob, 'delta_' + currentSeq + '.' + ext);
        var promptContext = _voiceCumulativeText.trim().slice(-200);
        if (promptContext) {
            formData.append('prompt', promptContext);
        }

        var signal = (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function')
            ? AbortSignal.timeout(30000)
            : null;

        var fetchOptions = { method: 'POST', body: formData };
        if (signal) fetchOptions.signal = signal;

        var fetchStartTime = Date.now();
        console.log('[voice] Fetch start: seq=' + currentSeq + ', bytes=' + chunkBlob.size);

        var res = await fetch('/api/whisper/transcribe', fetchOptions);
        var fetchDuration = Date.now() - fetchStartTime;
        console.log('[voice] Fetch end: seq=' + currentSeq + ', status=' + res.status + ', duration=' + fetchDuration + 'ms');

        if (currentSeq !== _voiceCumulativeSeq) return;

        if (res.status === 503) {
            console.warn('[voice] Whisper engine is initializing, retrying on next tick...');
            return;
        }

        if (!res.ok) throw new Error('HTTP ' + res.status);
        var data = await res.json();
        var text = (data.text || '').trim();

        if (_voiceIsRecording && currentSeq === _voiceCumulativeSeq && text) {
            // Append deduplicated chunk text delta in real-time
            _appendDeduplicatedText(input, text);
        }
    } catch (e) {
        if (currentSeq === _voiceCumulativeSeq && e.name !== 'AbortError' && e.name !== 'DOMException') {
            console.warn('[voice] Chunk delta transcription skipped:', e.message || e);
        }
    } finally {
        _voiceSendInFlight = false;
        console.log('[voice] inFlight released: seq=' + currentSeq);
        if (_voiceIsRecording && _voicePendingSend) {
            _scheduleThrottledSend(input);
        }
    }
}

/**
 * Append text with smart word overlap deduplication.
 * Prevents "안녕 안녕하세요" -> "안녕 하세요" repetition.
 */
function _appendDeduplicatedText(input, newText) {
    if (!input || !newText) return;
    newText = newText.trim();
    if (!newText) return;

    var currentText = _voiceCumulativeText.trim();
    if (!currentText) {
        _voiceCumulativeText = newText;
        _updateMirrorUI(input, _voiceCumulativeText, '');
        return;
    }

    var wordsCurrent = currentText.split(/\s+/);
    var wordsNew = newText.split(/\s+/);

    var overlapCount = 0;
    for (var len = Math.min(wordsCurrent.length, wordsNew.length, 4); len > 0; len--) {
        var tailCurrent = wordsCurrent.slice(-len).join(' ');
        var headNew = wordsNew.slice(0, len).join(' ');
        if (tailCurrent === headNew) {
            overlapCount = len;
            break;
        }
    }

    var addedText = overlapCount > 0
        ? wordsNew.slice(overlapCount).join(' ')
        : newText;

    if (addedText) {
        _voiceCumulativeText += (currentText ? ' ' : '') + addedText;
        _updateMirrorUI(input, _voiceCumulativeText, '');
    }
}

/**
 * Throttled send scheduler: if enough time has passed since last send,
 * fire immediately; otherwise set a timer for the remaining interval.
 */
function _scheduleThrottledSend(input) {
    if (!_voiceIsRecording) return;

    _voicePendingSend = true;
    console.log('[voice] _scheduleThrottledSend: inFlight=' + _voiceSendInFlight + ', pending=' + _voicePendingSend);

    // Don't start a new timer if one is already scheduled
    if (_voiceSendInFlight) {
        console.warn('[voice] Skip send: already in flight');
        return;
    }

    clearTimeout(_voiceThrottleTimer);

    var elapsed = Date.now() - _voiceLastSendTime;
    if (elapsed >= THROTTLE_INTERVAL) {
        _sendCumulativeTranscription(input);
    } else {
        _voiceThrottleTimer = setTimeout(function () {
            if (!_voiceIsRecording) return;
            _sendCumulativeTranscription(input);
        }, THROTTLE_INTERVAL - elapsed);
    }
}

/**
 * Find a natural split point for confirmed vs pending text.
 * Returns index where "pending" portion starts (last ~10% of text or after last sentence break).
 */
function _findPendingSplit(text) {
    if (!text) return 0;
    // Prefer splitting at last sentence/clause boundary
    var breakChars = ['. ', '? ', '! ', '\n', '다. ', '요. ', '니다. '];
    for (var i = 0; i < breakChars.length; i++) {
        var idx = text.lastIndexOf(breakChars[i]);
        if (idx > text.length * 0.3 && idx < text.length - 3) {
            return idx + breakChars[i].length;
        }
    }
    // Fallback: last ~20% of chars are pending (min 3, max 20)
    var pendingLen = Math.max(3, Math.min(20, Math.floor(text.length * 0.2)));
    return Math.max(0, text.length - pendingLen);
}

/**
 * Update input field with confirmed + pending text directly in real-time.
 */
function _updateMirrorUI(input, confirmed, pending) {
    if (!input || !_voiceIsRecording) return;

    var fullText = confirmed + pending;

    // Directly update textarea value so live text is 100% visible as user speaks
    input.value = _voicePrefix
        ? _voicePrefix + (_voicePrefix.endsWith(' ') || _voicePrefix.endsWith('\n') ? '' : ' ') + fullText
        : fullText;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 400) + 'px';
    input.scrollTop = input.scrollHeight;
}

/**
 * Setup input field state for recording.
 */
function _setupMirror(input) {
    if (!input) return;
    _teardownMirror();
}

/**
 * Restore input field state after recording.
 */
function _teardownMirror() {
    var input = document.getElementById('promptInput');
    if (input) {
        input.style.color = '';
        input.style.caretColor = '';
    }
}

function _escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}

/**
 * Update the input field with text (no mirror — used for final result).
 */
function _updateInputText(input, text) {
    if (!input) return;
    input.value = _voicePrefix
        ? _voicePrefix + (_voicePrefix.endsWith(' ') || _voicePrefix.endsWith('\n') ? '' : ' ') + text
        : text;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 400) + 'px';
}

var _processingSafetyTimer = null;

/**
 * Final transcription: send full recording for the most accurate result.
 */
async function _processFinalRecording(input) {
    clearTimeout(_processingSafetyTimer);

    if (_voiceAudioChunks.length === 0) {
        showToast('녹음된 오디오가 없습니다.');
        _setRecordingUI(false);
        _cleanupMedia();
        return;
    }

    _setRecordingUI('processing');
    showToast('🔄 최종 변환 중...');

    try {
        var mimeType = _voiceMediaRecorder ? _voiceMediaRecorder.mimeType : 'audio/webm';
        var audioBlob = new Blob(_voiceAudioChunks, { type: mimeType });
        var ext = _mimeToExt(mimeType);

        var formData = new FormData();
        formData.append('audio', audioBlob, 'full_recording.' + ext);

        var signal = (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function')
            ? AbortSignal.timeout(120000)
            : null;

        var fetchOptions = { method: 'POST', body: formData };
        if (signal) fetchOptions.signal = signal;

        var res = await fetch('/api/whisper/transcribe', fetchOptions);

        if (!res.ok) {
            var err = await res.json().catch(function () { return {}; });
            throw new Error(err.error || 'HTTP ' + res.status);
        }

        var data = await res.json();
        var transcribedText = data.text || '';

        if (!transcribedText) {
            // Fall back to cumulative partial if final is empty
            if (_voiceCumulativeText) {
                transcribedText = _voiceCumulativeText;
                showToast('⚠️ 부분 변환 결과를 사용합니다.');
            } else {
                showToast('음성 변환 결과가 비어 있습니다. 다시 시도해주세요.');
            }
        }

        if (transcribedText) {
            _teardownMirror();
            _updateInputText(input, transcribedText);
            showToast('✅ 음성 변환 완료');
        }

    } catch (e) {
        console.error('[voice] Final transcription error:', e);
        if (e.name === 'AbortError') {
            showToast('음성 변환 시간이 초과되었습니다.');
        } else {
            showToast('음성 변환 오류: ' + (e.message || '알 수 없는 오류'));
        }
        // Fall back to cumulative partial on error
        _teardownMirror();
        if (_voiceCumulativeText && (!input || !input.value)) {
            _updateInputText(input, _voiceCumulativeText);
        }
    } finally {
        clearTimeout(_processingSafetyTimer);
        _voiceSendInFlight = false;
        _setRecordingUI(false);
        _cleanupMedia();
        _teardownMirror();
    }
}

// ── Stop ──

function stopVoiceRecording() {
    _voiceIsRecording = false;
    clearTimeout(_voiceThrottleTimer);
    clearTimeout(_processingSafetyTimer);

    // Watchdog safety net: restore UI state if stop process hangs longer than 8 seconds
    _processingSafetyTimer = setTimeout(function () {
        console.warn('[voice] Watchdog safety net: restoring idle UI state');
        _setRecordingUI(false);
        _cleanupMedia();
        _teardownMirror();
    }, 8000);

    // Always on cumulative streaming path (MediaRecorder + faster-whisper)
    if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
        _setRecordingUI('processing');
        // Small delay to let the last chunk's dataavailable fire
        setTimeout(function () {
            if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
                try {
                    _voiceMediaRecorder.stop();
                } catch (e) {
                    console.warn('[voice] MediaRecorder stop error:', e);
                    clearTimeout(_processingSafetyTimer);
                    _voiceSendInFlight = false;
                    _setRecordingUI(false);
                    _cleanupMedia();
                }
            }
        }, 300);
    } else {
        _processFinalRecording($('promptInput'));
    }
}

// ── UI helpers ──

function _finalizeText(input) {
    if (!input) return;

    _setRecordingUI(false);

    var finalText = _voiceFinalTranscript.trim();
    if (finalText) {
        input.value = _voicePrefix
            ? _voicePrefix + (_voicePrefix.endsWith(' ') || _voicePrefix.endsWith('\n') ? '' : ' ') + finalText
            : finalText;

        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 400) + 'px';

        showToast('✅ 음성 인식 완료');
    } else if (!_voicePrefix) {
        showToast('음성이 인식되지 않았습니다. 다시 시도해주세요.');
    }
}

function _setRecordingUI(state) {
    // state: true (recording), false (idle), 'processing' (STT 변환 중)
    _voiceIsRecording = (state === true || state === 'processing');
    var btn = $('voiceMicBtn');
    if (btn) {
        btn.classList.remove('recording', 'processing');
        if (state === 'processing') {
            btn.classList.add('processing');
            btn.title = '음성 변환 중... (클릭 시 중지)';
        } else if (state === true) {
            btn.classList.add('recording');
            btn.title = '녹음 중지 (클릭)';
        } else {
            btn.title = '음성 입력';
        }
    }
}

// ── Cleanup ──

function _cleanupRecognition() {
    if (_voiceRecognition) {
        try {
            _voiceRecognition.onresult = null;
            _voiceRecognition.onerror = null;
            _voiceRecognition.onend = null;
            _voiceRecognition.abort();
        } catch (e) { /* ignore */ }
        _voiceRecognition = null;
    }
    _voiceFinalTranscript = '';
}

function _cleanupMedia() {
    if (_voiceMediaRecorder && _voiceMediaRecorder.state !== 'inactive') {
        try { _voiceMediaRecorder.stop(); } catch (e) { /* ignore */ }
    }
    _voiceMediaRecorder = null;
    _voiceAudioChunks = [];
    _voiceCumulativeText = '';
    _voiceCumulativeSeq = 0;
    _voicePendingFinal = false;
    _voiceSendInFlight = false;
    _voiceLastSendTime = 0;
    _voicePendingSend = false;
    clearTimeout(_voiceThrottleTimer);
    _teardownMirror();

    if (_voiceStream) {
        _voiceStream.getTracks().forEach(function (track) { track.stop(); });
        _voiceStream = null;
    }
}

// ── MediaRecorder helpers ──

function _getSupportedMimeType() {
    var candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4',
        'audio/wav'
    ];
    for (var i = 0; i < candidates.length; i++) {
        if (MediaRecorder.isTypeSupported(candidates[i])) {
            return candidates[i];
        }
    }
    return '';
}

function _mimeToExt(mimeType) {
    if (mimeType.indexOf('webm') >= 0) return 'webm';
    if (mimeType.indexOf('ogg') >= 0) return 'ogg';
    if (mimeType.indexOf('mp4') >= 0 || mimeType.indexOf('m4a') >= 0) return 'm4a';
    if (mimeType.indexOf('wav') >= 0) return 'wav';
    return 'webm';
}

// ── Page unload ──

function cleanupVoiceInput() {
    if (_voiceIsRecording) {
        stopVoiceRecording();
    }
    _cleanupRecognition();
    _cleanupMedia();
}

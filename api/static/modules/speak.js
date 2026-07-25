/**
 * ── Agent Voice Output Module ──
 * SSE 'speak' 이벤트를 수신하여 음성 출력.
 *
 * 3-tier fallback:
 *   1. Edge TTS (ko-KR-SunHiNeural, server-side, natural) — primary
 *   2. SpeechSynthesis (Heami, browser-local) — fallback
 *   3. Silent (log only) — last resort
 *
 * Dependency: core.js ($, State), chat.js (SSE 연결)
 */

var _speakMuted = false;
var _speakVoice = null;
var _speakVoicesLoaded = false;
var _speakPendingQueue = [];
var _speakAudioCtx = null;       // shared AudioContext for Edge TTS decode
var _edgeTtsSupported = true;    // set false after first failure to skip retries
var _speakLastTime = 0;          // last speak() call timestamp for client-side cooldown
var _speakClientCooldown = 2000; // ms — minimum gap between consecutive speaks
var _speakAbortController = null; // AbortController for cancelling in-flight TTS fetch

/**
 * Initialize voice output. Pre-load SpeechSynthesis voices and set default.
 * Called from chat.js after SSE connection is established.
 */
function initSpeak() {
    // Pre-load SpeechSynthesis voices (used as fallback)
    if (typeof speechSynthesis !== 'undefined') {
        const voices = speechSynthesis.getVoices();
        if (voices.length > 0) {
            _selectBestKoreanVoice(voices);
            _speakVoicesLoaded = true;
            _flushPendingQueue();
        }

        speechSynthesis.onvoiceschanged = () => {
            const newVoices = speechSynthesis.getVoices();
            if (newVoices.length > 0 && !_speakVoicesLoaded) {
                _selectBestKoreanVoice(newVoices);
                _speakVoicesLoaded = true;
                _flushPendingQueue();
            }
        };
    } else {
        // No SpeechSynthesis at all — mark as loaded so we proceed to Edge TTS
        _speakVoicesLoaded = true;
        console.warn('[Speak] SpeechSynthesis not available, will rely on Edge TTS only');
    }

    // Load mute state from localStorage
    try {
        const saved = localStorage.getItem('daon_speak_muted');
        if (saved !== null) {
            _speakMuted = (saved === 'true');
            _updateMuteButtonUI();
        }
    } catch (e) { /* ignore */ }

    console.log('[Speak] Initialized. Muted:', _speakMuted, 'EdgeTTS:', _edgeTtsSupported);
}

/**
 * Select the best Korean voice from available SpeechSynthesis voices (fallback tier).
 * Priority: Heami (female, clear) > InJoon (male) > any Korean voice
 */
function _selectBestKoreanVoice(voices) {
    const preferred = [
        'Microsoft SunHi',
        'Microsoft InJoon',
        'Microsoft Heami',
        'SunHi', 'InJoon', 'Heami',
    ];

    for (const pref of preferred) {
        const found = voices.find(v => v.name.includes(pref) && v.lang.startsWith('ko'));
        if (found) {
            _speakVoice = found;
            console.log('[Speak] SpeechSynthesis voice:', found.name, found.lang);
            return;
        }
    }

    const koreanVoice = voices.find(v => v.lang.startsWith('ko'));
    if (koreanVoice) {
        _speakVoice = koreanVoice;
        console.log('[Speak] Fallback Korean voice:', koreanVoice.name, koreanVoice.lang);
        return;
    }

    if (voices.length > 0) {
        _speakVoice = voices[0];
        console.log('[Speak] No Korean voice, using default:', voices[0].name);
    }
}

/**
 * Main speak function. Called by SSE event handler.
 * @param {string} text - Text to speak in Korean
 */
function speak(text) {
    if (!text) return;

    if (_speakMuted) {
        console.log('[Speak] Muted, skipping:', text);
        return;
    }

    // ── Client-side cooldown: prevent back-to-back speaks within 2s ──
    var now = Date.now();
    if (now - _speakLastTime < _speakClientCooldown) {
        console.log('[Speak] Cooldown active, skipping:', text.substring(0, 40));
        return;
    }
    _speakLastTime = now;

    // If voices not loaded yet, queue the utterance
    if (!_speakVoicesLoaded) {
        _speakPendingQueue.push(text);
        return;
    }

    _doSpeak(text);
}

/**
 * 3-tier speak: Edge TTS → SpeechSynthesis → silent.
 */
function _doSpeak(text) {
    console.log('[Speak] Request:', text.substring(0, 60));

    // Tier 1: Edge TTS (server-side, natural SunHi voice)
    if (_edgeTtsSupported) {
        _speakViaEdgeTts(text);
        return;
    }

    // Tier 2: SpeechSynthesis (browser-local, Heami fallback)
    if (typeof speechSynthesis !== 'undefined' && _speakVoice) {
        _speakViaSpeechSynthesis(text);
        return;
    }

    // Tier 3: silent
    console.log('[Speak] No output method available, text ignored.');
}

// ── Tier 1: Edge TTS ────────────────────────────────────────────

function _speakViaEdgeTts(text) {
    // Cancel any in-flight TTS request to prevent overlapping audio
    if (_speakAbortController) {
        try { _speakAbortController.abort(); } catch (_) { /* ignore */ }
    }
    _speakAbortController = new AbortController();

    // TTS runs on a dedicated server (port 9091) so that long-running
    // edge-tts synthesis never blocks the main agent server (9090).
    const ttsBase = location.protocol + '//' + location.hostname + ':9091';
    const url = ttsBase + '/tts?' + new URLSearchParams({ text: text }).toString();

    fetch(url, { signal: _speakAbortController.signal })
        .then(resp => {
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.arrayBuffer();
        })
        .then(buffer => {
            if (!buffer || buffer.byteLength < 100) throw new Error('Empty audio');
            _playAudioBuffer(buffer, () => {
                // Success — Edge TTS working
            }, (err) => {
                // Audio playback failed — fall through to SpeechSynthesis
                console.warn('[Speak] Edge TTS playback failed:', err);
                _fallbackToSpeechSynthesis(text);
            });
        })
        .catch(err => {
            // Fetch or decode failed — mark Edge TTS unavailable, fall back
            console.warn('[Speak] Edge TTS fetch failed:', err.message);
            _edgeTtsSupported = false;
            _fallbackToSpeechSynthesis(text);
        });
}

function _playAudioBuffer(arrayBuffer, onSuccess, onError) {
    try {
        // Use shared AudioContext (lazy init)
        if (!_speakAudioCtx) {
            _speakAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        const ctx = _speakAudioCtx;

        // Resume if suspended (autoplay policy)
        const resumePromise = ctx.state === 'suspended' ? ctx.resume() : Promise.resolve();

        resumePromise.then(() => {
            ctx.decodeAudioData(arrayBuffer.slice(0),
                (audioBuffer) => {
                    // Stop any currently playing Edge TTS audio
                    _stopEdgeTtsAudio();

                    const source = ctx.createBufferSource();
                    source.buffer = audioBuffer;
                    source.connect(ctx.destination);
                    source.start(0);
                    _currentEdgeTtsSource = source;

                    source.onended = () => {
                        _currentEdgeTtsSource = null;
                        if (onSuccess) onSuccess();
                    };
                },
                (decodeErr) => {
                    // decodeAudioData failed — try playing raw mp3 via <audio> element
                    console.warn('[Speak] decodeAudioData failed, trying <audio> fallback:', decodeErr);
                    _playAudioViaElement(arrayBuffer, onSuccess, onError);
                }
            );
        }).catch(err => {
            if (onError) onError('AudioContext resume failed: ' + err.message);
        });
    } catch (e) {
        if (onError) onError(e.message);
    }
}

var _currentEdgeTtsSource = null;

function _stopEdgeTtsAudio() {
    if (_currentEdgeTtsSource) {
        try { _currentEdgeTtsSource.stop(); } catch (e) { /* ignore */ }
        _currentEdgeTtsSource = null;
    }
}

/**
 * Fallback: play raw MP3 via <audio> element (works even if decodeAudioData fails).
 */
function _playAudioViaElement(arrayBuffer, onSuccess, onError) {
    try {
        const blob = new Blob([arrayBuffer], { type: 'audio/mpeg' });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => {
            URL.revokeObjectURL(url);
            if (onSuccess) onSuccess();
        };
        audio.onerror = () => {
            URL.revokeObjectURL(url);
            if (onError) onError('Audio element playback error');
        };
        audio.play().catch(err => {
            URL.revokeObjectURL(url);
            if (onError) onError('Audio play() rejected: ' + err.message);
        });
    } catch (e) {
        if (onError) onError(e.message);
    }
}

// ── Tier 2: SpeechSynthesis (browser-local fallback) ────────────

function _fallbackToSpeechSynthesis(text) {
    if (typeof speechSynthesis !== 'undefined' && _speakVoice) {
        console.log('[Speak] Falling back to SpeechSynthesis');
        _speakViaSpeechSynthesis(text);
    } else {
        console.log('[Speak] No SpeechSynthesis available — silent fallback');
    }
}

function _speakViaSpeechSynthesis(text) {
    try {
        speechSynthesis.cancel();
        _stopEdgeTtsAudio();  // also stop any Edge TTS audio

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'ko-KR';
        utterance.rate = 1.1;
        utterance.pitch = 1.0;
        utterance.volume = 0.9;

        if (_speakVoice) {
            utterance.voice = _speakVoice;
        }

        utterance.onerror = (event) => {
            if (event.error !== 'canceled' && event.error !== 'interrupted') {
                console.warn('[Speak] SpeechSynthesis error:', event.error);
            }
        };

        speechSynthesis.speak(utterance);
        console.log('[Speak] SpeechSynthesis speaking');
    } catch (e) {
        console.warn('[Speak] SpeechSynthesis failed:', e.message);
    }
}

// ── Queue / Mute / Control ──────────────────────────────────────

function _flushPendingQueue() {
    if (_speakPendingQueue.length > 0) {
        console.log('[Speak] Flushing', _speakPendingQueue.length, 'queued utterances');
        const last = _speakPendingQueue[_speakPendingQueue.length - 1];
        _speakPendingQueue = [];
        _doSpeak(last);
    }
}

function toggleSpeakMute() {
    _speakMuted = !_speakMuted;
    try {
        localStorage.setItem('daon_speak_muted', _speakMuted);
    } catch (e) { /* ignore */ }
    _updateMuteButtonUI();
    showToast(_speakMuted ? '🔇 에이전트 음성 출력 꺼짐' : '🔊 에이전트 음성 출력 켜짐');

    if (_speakMuted) {
        stopSpeak();
    }
}

function _updateMuteButtonUI() {
    const btn = document.getElementById('speakMuteBtn');
    if (!btn) return;
    if (_speakMuted) {
        btn.textContent = '🔇';
        btn.title = '에이전트 음성 출력 켜기';
        btn.style.opacity = '0.5';
    } else {
        btn.textContent = '🔊';
        btn.title = '에이전트 음성 출력 끄기';
        btn.style.opacity = '1';
    }
}

function stopSpeak() {
    _stopEdgeTtsAudio();
    if (typeof speechSynthesis !== 'undefined') {
        speechSynthesis.cancel();
    }
}

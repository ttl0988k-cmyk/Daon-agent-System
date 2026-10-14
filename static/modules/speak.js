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
var _speakVolume = 0.8;          // 0.0~1.0 — 에이전트 음성 볼륨 (localStorage 저장)
var _currentEdgeTtsGain = null;  // 재생 중인 Edge TTS GainNode (실시간 볼륨 반영용)
var _currentEdgeTtsAudioEl = null; // <audio> 폴백 재생 엘리먼트 (실시간 볼륨 반영용)

// ── [목소리 선택] Supertonic 10종 (F1~F5 여성 / M1~M5 남성) ──
var SUPERTONIC_VOICES = ['F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5'];
var SUPERTONIC_VOICE_LABELS = {
    F1: '여성 F1 (차분·기본)', F2: '여성 F2', F3: '여성 F3 (밝음)', F4: '여성 F4', F5: '여성 F5',
    M1: '남성 M1 (차분·기본)', M2: '남성 M2 (뉴스)', M3: '남성 M3', M4: '남성 M4', M5: '남성 M5'
};
var _supertonicVoice = 'F1';     // 선택된 Supertonic 목소리 (localStorage 저장)

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

    // [볼륨 조절] 저장된 볼륨 복원 (0.0~1.0)
    try {
        const sv = parseFloat(localStorage.getItem('daon_speak_volume'));
        if (!isNaN(sv)) _speakVolume = Math.max(0, Math.min(1, sv));
    } catch (e) { /* ignore */ }
    _updateVolumeSliderUI();

    // [목소리 선택] 저장된 Supertonic 목소리 복원 (F1~F5/M1~M5)
    try {
        const savedVoice = localStorage.getItem('daon_supertonic_voice');
        if (savedVoice && SUPERTONIC_VOICES.indexOf(savedVoice) !== -1) {
            _supertonicVoice = savedVoice;
        }
    } catch (e) { /* ignore */ }
    _updateVoiceSelectUI();

    console.log('[Speak] Initialized. Muted:', _speakMuted, 'Volume:', Math.round(_speakVolume * 100) + '%', 'Voice:', _supertonicVoice);
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
    // Fallback: the main server also exposes /api/speak/tts (same edge-tts
    // backend) so voice still works when the dedicated TTS server is not running.
    const voiceParams = new URLSearchParams({ text: text, voice: _supertonicVoice });
    const dedicatedUrl = location.protocol + '//' + location.hostname + ':9091/tts?' + voiceParams.toString();
    const mainUrl = location.origin + '/api/speak/tts?' + voiceParams.toString();

    _fetchAndPlayTts(dedicatedUrl, text, function () {
        // Dedicated TTS server unavailable → try the main server route.
        console.warn('[Speak] Dedicated TTS server failed, trying main server /api/speak/tts');
        _fetchAndPlayTts(mainUrl, text, null);
    });
}

function _fetchAndPlayTts(url, text, onFail) {
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
            // Fetch or decode failed — try the fallback URL, else SpeechSynthesis
            console.warn('[Speak] TTS fetch failed:', url, err.message);
            if (onFail) {
                onFail();
            } else {
                _edgeTtsSupported = false;
                _fallbackToSpeechSynthesis(text);
            }
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
                    // [볼륨 조절] 게인 노드를 사이에 둬 슬라이더 값(실시간 변경 포함)을 반영한다.
                    const gain = ctx.createGain();
                    gain.gain.value = _speakVolume;
                    source.connect(gain);
                    gain.connect(ctx.destination);
                    source.start(0);
                    _currentEdgeTtsSource = source;
                    _currentEdgeTtsGain = gain;

                    source.onended = () => {
                        _currentEdgeTtsSource = null;
                        _currentEdgeTtsGain = null;
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
    _currentEdgeTtsGain = null;
}

/**
 * Fallback: play raw MP3 via <audio> element (works even if decodeAudioData fails).
 */
function _playAudioViaElement(arrayBuffer, onSuccess, onError) {
    try {
        const blob = new Blob([arrayBuffer], { type: 'audio/mpeg' });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.volume = _speakVolume;   // [볼륨 조절]
        _currentEdgeTtsAudioEl = audio;
        audio.onended = () => {
            URL.revokeObjectURL(url);
            _currentEdgeTtsAudioEl = null;
            if (onSuccess) onSuccess();
        };
        audio.onerror = () => {
            URL.revokeObjectURL(url);
            _currentEdgeTtsAudioEl = null;
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
        utterance.volume = _speakVolume;   // [볼륨 조절]

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
    // 음소거 중엔 볼륨 슬라이더도 흐리게 (상태 일관성)
    const slider = document.getElementById('speakVolumeSlider');
    if (slider) slider.style.opacity = _speakMuted ? '0.4' : '1';
}

// ── [볼륨 조절] 슬라이더 (0~100) ────────────────────────────────

/**
 * 에이전트 음성 출력 볼륨 설정. 재생 중인 오디오에도 즉시 반영되며
 * localStorage 에 저장되어 다음 실행에도 유지된다. (index.html 슬라이더에서 호출)
 */
function setSpeakVolume(percent) {
    let v = parseFloat(percent);
    if (isNaN(v)) return;
    v = Math.max(0, Math.min(100, v)) / 100;
    _speakVolume = v;
    try {
        localStorage.setItem('daon_speak_volume', String(v));
    } catch (e) { /* ignore */ }

    // 재생 중인 오디오에 실시간 반영
    if (_currentEdgeTtsGain) {
        try { _currentEdgeTtsGain.gain.value = v; } catch (e) { /* ignore */ }
    }
    if (_currentEdgeTtsAudioEl) {
        try { _currentEdgeTtsAudioEl.volume = v; } catch (e) { /* ignore */ }
    }

    _updateVolumeSliderUI();
}

function getSpeakVolume() {
    return Math.round(_speakVolume * 100);
}

function _updateVolumeSliderUI() {
    const slider = document.getElementById('speakVolumeSlider');
    if (!slider) return;
    slider.value = String(Math.round(_speakVolume * 100));
    slider.title = '에이전트 음성 볼륨: ' + Math.round(_speakVolume * 100) + '%';
    slider.style.opacity = _speakMuted ? '0.4' : '1';
}

function stopSpeak() {
    _stopEdgeTtsAudio();
    if (typeof speechSynthesis !== 'undefined') {
        speechSynthesis.cancel();
    }
}

// ── [목소리 선택] Supertonic 10종 UI ────────────────────────────

/**
 * 목소리 선택 변경 핸들러 (index.html select에서 호출).
 * @param {string} voice - 'F1'~'F5' / 'M1'~'M5'
 */
function setSpeakVoice(voice) {
    if (!voice || SUPERTONIC_VOICES.indexOf(voice) === -1) return;
    _supertonicVoice = voice;
    try {
        localStorage.setItem('daon_supertonic_voice', voice);
    } catch (e) { /* ignore */ }
    _updateVoiceSelectUI();
    showToast('🎙️ 목소리: ' + (SUPERTONIC_VOICE_LABELS[voice] || voice));
}

/**
 * 목소리 선택 UI 동기화. select가 있으면 현재 값으로 맞추고,
 * 미리듣기 버튼이 있으면 현재 목소리로 짧은 문장을 재생한다.
 */
function _updateVoiceSelectUI() {
    const sel = document.getElementById('speakVoiceSelect');
    if (sel) {
        sel.value = _supertonicVoice;
    }
}

/**
 * 현재 목소리로 미리듣기 문장 재생.
 */
function previewSpeakVoice() {
    const sample = '안녕하세요, 저는 다온입니다.';
    _speakLastTime = 0;  // cooldown 해제
    _doSpeak(sample);
}

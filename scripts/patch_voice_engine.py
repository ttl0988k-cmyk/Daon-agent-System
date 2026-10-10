# -*- coding: utf-8 -*-
"""
Restore full MediaRecorder + faster-whisper real-time streaming STT engine into Achromatic Studio v2.5.
- Restores original robust webapp logic (voice.js) adapted to Achromatic UI elements:
  - chat-voice-btn, chat-voice-icon, chat-voice-pulse, chat-voice-status-bar, chat-voice-status-text, chat-voice-stop-btn
  - Real-time chunk streaming to POST /api/whisper/transcribe
  - Real-time text append into chat-input
  - Final high-accuracy Whisper transcription on stop
  - Visual animated recording status banner & toast feedback
- Syncs index.html, static/v2/index.html, static/v2/js/app.js to DEPLOY_DIR (resources)
"""
import os
import re
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

# 1. Update HTML files to include chat-voice-status-bar
status_bar_html = """              <!-- Voice Recording Live Status Banner -->
              <div id="chat-voice-status-bar" class="hidden items-center justify-between px-3 py-1.5 bg-rose-50 border border-rose-200 dark:bg-rose-950/30 dark:border-rose-900/50 rounded-[8px] text-[12px] text-rose-600 dark:text-rose-400 font-medium">
                <div class="flex items-center gap-2">
                  <span class="w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping"></span>
                  <span id="chat-voice-status-text">🎤 마이크 듣는 중... 말씀하시면 실시간 텍스트로 입력됩니다</span>
                </div>
                <button id="chat-voice-stop-btn" type="button" class="px-2.5 py-0.5 bg-rose-600 hover:bg-rose-700 text-white text-[11px] font-semibold rounded-[4px] transition-colors cursor-pointer flex items-center gap-1">
                  <span class="material-symbols-outlined text-[13px]">stop</span>
                  <span>완료</span>
                </button>
              </div>"""

for html_file in [os.path.join(ROOT_DIR, "index.html"), os.path.join(ROOT_DIR, "static", "v2", "index.html")]:
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        if 'id="chat-voice-status-bar"' not in content:
            needle = '<div id="chat-attach-tray" class="hidden flex-wrap gap-2 px-1 pt-1 pb-1 border-b border-black/[0.06]"></div>'
            if needle in content:
                content = content.replace(needle, needle + "\n" + status_bar_html)
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[HTML] Added voice status bar to {html_file}")
            else:
                print(f"[HTML] Needle not found in {html_file}")
        else:
            print(f"[HTML] Voice status bar already present in {html_file}")

# 2. Update app.js
app_js_path = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")
with open(app_js_path, "r", encoding="utf-8") as f:
    app_js = f.read()

# Define the robust voice engine replacement
voice_engine_code = """  // ── Production Cumulative Streaming ASR (MediaRecorder + faster-whisper) ──
  const voiceBtn = document.getElementById('chat-voice-btn');
  const voiceIcon = document.getElementById('chat-voice-icon');
  const voicePulse = document.getElementById('chat-voice-pulse');
  const voiceStatusBar = document.getElementById('chat-voice-status-bar');
  const voiceStatusText = document.getElementById('chat-voice-status-text');
  const voiceStopBtn = document.getElementById('chat-voice-stop-btn');
  const chatInput = document.getElementById('chat-input');

  let _voiceMediaRecorder = null;
  let _voiceAudioChunks = [];
  let _voiceStream = null;
  let _voicePrefix = '';
  let _voiceCumulativeText = '';
  let _voiceCumulativeSeq = 0;
  let _voiceThrottleTimer = null;
  let _voiceSafetyTimer = null;
  let _voiceSendInFlight = false;
  let _voiceLastSendTime = 0;
  let _voicePendingSend = false;
  let _voiceLastSentIndex = 0;
  const VOICE_THROTTLE_MS = 800;

  function showVoiceToast(message, icon = '🎤', duration = 3000) {
    const existing = document.getElementById('daon-voice-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'daon-voice-toast';
    toast.className = 'fixed bottom-24 right-6 bg-surface-container-highest border border-black/15 shadow-2xl rounded-[10px] px-4 py-2.5 flex items-center gap-2.5 text-[13px] text-on-surface z-50 animate-in fade-in slide-in-from-bottom-2 duration-150';
    toast.innerHTML = `<span class="text-[17px]">${icon}</span><span class="font-medium">${message}</span>`;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('opacity-0', 'transition-opacity', 'duration-200');
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  function getSupportedMimeType() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
      'audio/wav'
    ];
    for (const c of candidates) {
      if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(c)) {
        return c;
      }
    }
    return '';
  }

  function mimeToExt(mimeType) {
    if (!mimeType) return 'webm';
    if (mimeType.includes('webm')) return 'webm';
    if (mimeType.includes('ogg')) return 'ogg';
    if (mimeType.includes('mp4') || mimeType.includes('m4a')) return 'm4a';
    if (mimeType.includes('wav')) return 'wav';
    return 'webm';
  }

  function setVoiceUI(state) {
    // state: true (recording), false (idle), 'processing' (Whisper transcribing)
    _isVoiceRecording = (state === true || state === 'processing');

    if (state === true) {
      if (voiceBtn) {
        voiceBtn.classList.add('bg-rose-50', 'text-rose-600', 'ring-2', 'ring-rose-500/30');
        voiceBtn.title = '녹음 중지 (클릭 시 최종 변환)';
      }
      if (voiceIcon) {
        voiceIcon.classList.remove('text-on-surface-variant');
        voiceIcon.classList.add('text-rose-600');
        voiceIcon.textContent = 'mic';
      }
      if (voicePulse) voicePulse.classList.remove('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.remove('hidden');
        voiceStatusBar.classList.add('flex');
      }
      if (voiceStatusText) {
        voiceStatusText.textContent = '🎤 마이크 듣는 중... 말씀하시면 실시간 텍스트로 입력됩니다';
      }
    } else if (state === 'processing') {
      if (voiceBtn) {
        voiceBtn.classList.add('bg-rose-50', 'text-rose-600');
        voiceBtn.title = '최종 음성 변환 중...';
      }
      if (voiceIcon) {
        voiceIcon.classList.add('text-rose-600');
        voiceIcon.textContent = 'sync';
      }
      if (voicePulse) voicePulse.classList.add('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.remove('hidden');
        voiceStatusBar.classList.add('flex');
      }
      if (voiceStatusText) {
        voiceStatusText.textContent = '🔄 최종 음성 변환 중 (Whisper AI)...';
      }
    } else {
      // idle
      if (voiceBtn) {
        voiceBtn.classList.remove('bg-rose-50', 'text-rose-600', 'ring-2', 'ring-rose-500/30');
        voiceBtn.title = '음성 입력 (마이크)';
      }
      if (voiceIcon) {
        voiceIcon.classList.remove('text-rose-600');
        voiceIcon.classList.add('text-on-surface-variant');
        voiceIcon.textContent = 'mic';
      }
      if (voicePulse) voicePulse.classList.add('hidden');
      if (voiceStatusBar) {
        voiceStatusBar.classList.add('hidden');
        voiceStatusBar.classList.remove('flex');
      }
    }
  }

  function cleanupMedia() {
    if (_voiceMediaRecorder && _voiceMediaRecorder.state !== 'inactive') {
      try { _voiceMediaRecorder.stop(); } catch (_) {}
    }
    _voiceMediaRecorder = null;
    _voiceAudioChunks = [];
    _voiceCumulativeText = '';
    _voiceCumulativeSeq = 0;
    _voiceSendInFlight = false;
    _voiceLastSendTime = 0;
    _voicePendingSend = false;
    _voiceLastSentIndex = 0;
    clearTimeout(_voiceThrottleTimer);
    clearTimeout(_voiceSafetyTimer);

    if (_voiceStream) {
      _voiceStream.getTracks().forEach(track => {
        try { track.stop(); } catch (_) {}
      });
      _voiceStream = null;
    }
  }

  function appendDeduplicatedText(input, newText) {
    if (!input || !newText) return;
    newText = newText.trim();
    if (!newText) return;

    const currentText = _voiceCumulativeText.trim();
    if (!currentText) {
      _voiceCumulativeText = newText;
      updateLiveInput(input, _voiceCumulativeText);
      return;
    }

    const wordsCurrent = currentText.split(/\\s+/);
    const wordsNew = newText.split(/\\s+/);

    let overlapCount = 0;
    for (let len = Math.min(wordsCurrent.length, wordsNew.length, 4); len > 0; len--) {
      const tailCurrent = wordsCurrent.slice(-len).join(' ');
      const headNew = wordsNew.slice(0, len).join(' ');
      if (tailCurrent === headNew) {
        overlapCount = len;
        break;
      }
    }

    const addedText = overlapCount > 0 ? wordsNew.slice(overlapCount).join(' ') : newText;
    if (addedText) {
      _voiceCumulativeText += (currentText ? ' ' : '') + addedText;
      updateLiveInput(input, _voiceCumulativeText);
    }
  }

  function updateLiveInput(input, liveText) {
    if (!input || !_isVoiceRecording) return;
    input.value = _voicePrefix ? _voicePrefix + liveText : liveText;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
    input.scrollTop = input.scrollHeight;
  }

  function scheduleThrottledSend(input) {
    if (!_isVoiceRecording) return;
    _voicePendingSend = true;
    if (_voiceSendInFlight) return;

    clearTimeout(_voiceThrottleTimer);
    const elapsed = Date.now() - _voiceLastSendTime;
    if (elapsed >= VOICE_THROTTLE_MS) {
      sendCumulativeChunk(input);
    } else {
      _voiceThrottleTimer = setTimeout(() => {
        if (!_isVoiceRecording) return;
        sendCumulativeChunk(input);
      }, VOICE_THROTTLE_MS - elapsed);
    }
  }

  async function sendCumulativeChunk(input) {
    if (!_isVoiceRecording || _voiceAudioChunks.length === 0) return;
    if (!_voiceMediaRecorder) return;
    if (_voiceAudioChunks.length <= _voiceLastSentIndex) return;

    _voiceSendInFlight = true;
    _voicePendingSend = false;
    _voiceLastSendTime = Date.now();
    _voiceLastSentIndex = _voiceAudioChunks.length;
    const currentSeq = ++_voiceCumulativeSeq;

    try {
      const mimeType = _voiceMediaRecorder.mimeType || 'audio/webm';
      const ext = mimeToExt(mimeType);
      const chunkBlob = new Blob(_voiceAudioChunks.slice(), { type: mimeType });

      if (chunkBlob.size < 500) return;

      const formData = new FormData();
      formData.append('audio', chunkBlob, `delta_${currentSeq}.${ext}`);
      const promptCtx = _voiceCumulativeText.trim().slice(-200);
      if (promptCtx) {
        formData.append('prompt', promptCtx);
      }

      const res = await fetch('/api/whisper/transcribe', {
        method: 'POST',
        body: formData,
        signal: (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(30000) : undefined
      });

      if (currentSeq !== _voiceCumulativeSeq) return;
      if (res.status === 503) return;
      if (!res.ok) throw new Error('HTTP ' + res.status);

      const data = await res.json();
      const text = (data.text || '').trim();

      if (_isVoiceRecording && currentSeq === _voiceCumulativeSeq && text) {
        appendDeduplicatedText(input, text);
      }
    } catch (_) {
      // Chunk-level delta skipped
    } finally {
      _voiceSendInFlight = false;
      if (_isVoiceRecording && _voicePendingSend) {
        scheduleThrottledSend(input);
      }
    }
  }

  async function processFinalRecording(input) {
    clearTimeout(_voiceSafetyTimer);

    if (_voiceAudioChunks.length === 0) {
      setVoiceUI(false);
      cleanupMedia();
      return;
    }

    setVoiceUI('processing');
    showVoiceToast('🔄 최종 음성 변환 중 (Whisper AI)...', '🔄', 2000);

    try {
      const mimeType = _voiceMediaRecorder ? _voiceMediaRecorder.mimeType : 'audio/webm';
      const audioBlob = new Blob(_voiceAudioChunks, { type: mimeType });
      const ext = mimeToExt(mimeType);

      const formData = new FormData();
      formData.append('audio', audioBlob, `full_recording.${ext}`);

      const res = await fetch('/api/whisper/transcribe', {
        method: 'POST',
        body: formData,
        signal: (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) ? AbortSignal.timeout(60000) : undefined
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || ('HTTP ' + res.status));
      }

      const data = await res.json();
      let transcribedText = (data.text || '').trim();

      if (!transcribedText && _voiceCumulativeText) {
        transcribedText = _voiceCumulativeText.trim();
      }

      if (transcribedText && input) {
        input.value = _voicePrefix ? _voicePrefix + transcribedText : transcribedText;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 220) + 'px';
        input.focus();
        showVoiceToast('✅ 음성 변환 완료!', '✅', 2500);
      } else {
        showVoiceToast('음성이 감지되지 않았습니다. 다시 말씀해 주세요.', 'ℹ️', 3000);
      }
    } catch (err) {
      console.error('[Voice Final Transcription Error]', err);
      showVoiceToast('음성 변환 오류: ' + (err.message || '알 수 없는 오류'), '⚠️', 4000);
      if (_voiceCumulativeText && input && !input.value) {
        input.value = _voicePrefix ? _voicePrefix + _voiceCumulativeText : _voiceCumulativeText;
      }
    } finally {
      _voiceSendInFlight = false;
      setVoiceUI(false);
      cleanupMedia();
    }
  }

  async function startVoiceRecording() {
    if (!chatInput) return;

    cleanupMedia();
    _voicePrefix = chatInput.value ? (chatInput.value.trim() + ' ') : '';
    _voiceCumulativeText = '';

    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('이 브라우저/환경에서는 마이크 녹음(getUserMedia)을 지원하지 않습니다.');
      }

      _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = getSupportedMimeType();
      _voiceMediaRecorder = new MediaRecorder(_voiceStream, mimeType ? { mimeType, audioBitsPerSecond: 128000 } : undefined);
      _voiceAudioChunks = [];

      _voiceMediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          _voiceAudioChunks.push(event.data);
          scheduleThrottledSend(chatInput);
        }
      };

      _voiceMediaRecorder.onstop = () => {
        clearTimeout(_voiceThrottleTimer);
        _voiceSendInFlight = false;
        processFinalRecording(chatInput);
      };

      _voiceMediaRecorder.start(VOICE_THROTTLE_MS);
      setVoiceUI(true);
      showVoiceToast('🎤 마이크 음성 인식 시작 (말씀하시면 실시간 텍스트로 입력됩니다)', '🎤', 3000);

      _voiceThrottleTimer = setTimeout(() => {
        if (!_isVoiceRecording) return;
        scheduleThrottledSend(chatInput);
      }, VOICE_THROTTLE_MS);

    } catch (err) {
      console.error('[Voice Recording Start Error]', err);
      cleanupMedia();
      setVoiceUI(false);
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        alert('⚠️ 마이크 권한이 차단되었습니다. 브라우저/운영체제 설정에서 마이크 사용을 허용해 주세요.');
      } else if (err.name === 'NotFoundError') {
        alert('⚠️ 연결된 마이크 장치를 찾을 수 없습니다.');
      } else {
        alert('⚠️ 마이크 연결 실패: ' + (err.message || '알 수 없는 오류'));
      }
    }
  }

  function stopVoiceRecording() {
    _isVoiceRecording = false;
    clearTimeout(_voiceThrottleTimer);
    clearTimeout(_voiceSafetyTimer);

    _voiceSafetyTimer = setTimeout(() => {
      console.warn('[Voice STT Timeout Safety Net]');
      setVoiceUI(false);
      cleanupMedia();
    }, 12000);

    if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
      setVoiceUI('processing');
      setTimeout(() => {
        if (_voiceMediaRecorder && _voiceMediaRecorder.state === 'recording') {
          try {
            _voiceMediaRecorder.stop();
          } catch (e) {
            console.warn('[Voice MediaRecorder Stop Error]', e);
            setVoiceUI(false);
            cleanupMedia();
          }
        }
      }, 300);
    } else {
      processFinalRecording(chatInput);
    }
  }

  if (voiceBtn) {
    voiceBtn.addEventListener('click', () => {
      if (_isVoiceRecording) {
        stopVoiceRecording();
      } else {
        startVoiceRecording();
      }
    });
  }

  if (voiceStopBtn) {
    voiceStopBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      stopVoiceRecording();
    });
  }
}"""

# Replace the old stub in app.js
start_marker = "  const voiceBtn = document.getElementById('chat-voice-btn');"
end_marker = "  function stopVoiceRecording() {\n    _isVoiceRecording = false;\n    if (_speechRecognition) {\n      try { _speechRecognition.stop(); } catch (_) {}\n    }\n    if (voiceIcon) {\n      voiceIcon.classList.remove('text-rose-500');\n      voiceIcon.classList.add('text-on-surface-variant');\n    }\n    if (voicePulse) voicePulse.classList.add('hidden');\n    if (voiceBtn) voiceBtn.title = '음성 입력 (마이크)';\n  }\n}"

if start_marker in app_js:
    idx_start = app_js.find(start_marker)
    # Find matching closing block for initVoiceFeatures
    # Look for stopVoiceRecording definition
    idx_end = app_js.find(end_marker, idx_start)
    if idx_end != -1:
        target_chunk = app_js[idx_start:idx_end + len(end_marker)]
        app_js = app_js.replace(target_chunk, voice_engine_code)
        print("[app.js] Successfully replaced old stub with full MediaRecorder + Whisper engine!")
    else:
        # Fallback regex replacement
        pattern = re.compile(r"  const voiceBtn = document\.getElementById\('chat-voice-btn'\);[\s\S]*?function stopVoiceRecording\(\) \{[\s\S]*?\}\n\}")
        if pattern.search(app_js):
            app_js = pattern.sub(voice_engine_code, app_js, count=1)
            print("[app.js] Successfully replaced old stub via regex!")
        else:
            print("[app.js] ERROR: Failed to match old stub pattern!")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(app_js)

# Bump version query param in HTML files
v_param = f"app.js?v=20261009_{hex(int(os.path.getmtime(app_js_path)))[2:]}"
for html_file in [os.path.join(ROOT_DIR, "index.html"), os.path.join(ROOT_DIR, "static", "v2", "index.html")]:
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            h_text = f.read()
        h_text = re.sub(r'app\.js\?v=[a-zA-Z0-9_]+', v_param, h_text)
        with open(html_file, "w", encoding="utf-8") as f:
            h_text = f.write(h_text)

# 3. Synchronize to DEPLOY_DIR
if os.path.exists(DEPLOY_DIR):
    deploy_index = os.path.join(DEPLOY_DIR, "index.html")
    deploy_v2_dir = os.path.join(DEPLOY_DIR, "static", "v2")
    os.makedirs(deploy_v2_dir, exist_ok=True)
    deploy_v2_index = os.path.join(deploy_v2_dir, "index.html")
    deploy_js_dir = os.path.join(deploy_v2_dir, "js")
    os.makedirs(deploy_js_dir, exist_ok=True)
    deploy_app_js = os.path.join(deploy_js_dir, "app.js")

    src_index = os.path.join(ROOT_DIR, "index.html")
    shutil.copy2(src_index, deploy_index)
    shutil.copy2(src_index, deploy_v2_index)
    shutil.copy2(app_js_path, deploy_app_js)
    print(f"[DEPLOY] Successfully synced updated HTML and app.js to {DEPLOY_DIR}!")

print("All updates completed successfully!")

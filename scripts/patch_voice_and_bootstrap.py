# -*- coding: utf-8 -*-
"""
Patch app.js:
1. Move voice variables (_speechRecognition, _isVoiceRecording, _autoTTS, _currentSpeakingBtn, _currentSpeakingAudio) to top under state to avoid TDZ.
2. Move early initApp() invocation from line ~200 to end of file after all functions are loaded.
3. Add global event delegation for .read-aloud-btn so TTS speaker works everywhere without fail.
4. Bind agent-mode-select-btn in composer toolbar to toggle autonomous mode or trigger persona switcher.
"""
import re

app_js_path = r"c:\daon\Daon agent System\static\v2\js\app.js"

with open(app_js_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Check if voice variables are at lines ~895
old_voice_vars = """// ── Voice Input (STT) & Voice Output (TTS) Module ─────────────────────────────

let _speechRecognition = null;
let _isVoiceRecording = false;
let _autoTTS = localStorage.getItem('daon_auto_tts') === 'true';
let _currentSpeakingBtn = null;

let _currentSpeakingAudio = null;"""

# Top voice vars definition to place after state
top_voice_vars = """// ── Voice Input (STT) & Voice Output (TTS) State Variables ─────────────────
let _speechRecognition = null;
let _isVoiceRecording = false;
let _autoTTS = localStorage.getItem('daon_auto_tts') === 'true';
let _currentSpeakingBtn = null;
let _currentSpeakingAudio = null;
"""

if old_voice_vars in content:
    content = content.replace(old_voice_vars, "// ── Voice Input (STT) & Voice Output (TTS) Module ─────────────────────────────")
    print("[1] Removed old voice vars from mid-file.")
else:
    # Try regex removal
    content = re.sub(
        r'let _speechRecognition = null;\s*let _isVoiceRecording = false;\s*let _autoTTS = [^;]+;\s*let _currentSpeakingBtn = null;\s*let _currentSpeakingAudio = null;',
        '',
        content
    )
    print("[1] Removed old voice vars via regex.")

# Insert top_voice_vars right after state declaration
state_end_needle = "lastRenderedSignature: '' // 불필요한 전체 재렌더링 방지용 서명\n};"
if state_end_needle in content:
    content = content.replace(state_end_needle, state_end_needle + "\n\n" + top_voice_vars)
    print("[2] Inserted voice vars right after state.")
else:
    # Alternative match
    content = re.sub(
        r'(const state = \{[\s\S]*?\};)',
        r'\1\n\n' + top_voice_vars,
        content
    )
    print("[2] Inserted voice vars via regex after state.")

# 2. Remove early initApp() invocation
early_init_needle = """if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}"""

if early_init_needle in content:
    content = content.replace(early_init_needle, "// (initApp deferred to end of file to guarantee all functions and variables are initialized)")
    print("[3] Removed early initApp invocation.")

# 3. Add global delegation for .read-aloud-btn and agent-mode-select-btn inside initComposer or global
btn_delegation_code = """
  // Global delegation for read-aloud voice (TTS)
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.read-aloud-btn');
    if (btn) {
      e.stopPropagation();
      const article = btn.closest('article') || btn.closest('.chat-bubble-assistant');
      let text = '';
      if (article) {
        const contentEl = article.querySelector('.chat-assistant-content') || article.querySelector('.font-body-md') || article.querySelector('div[id^=\"msg-content\"]');
        text = contentEl ? contentEl.innerText : '';
      }
      if (!text) {
        // Fallback: previous element text
        const bubble = btn.closest('div.flex-col')?.querySelector('.chat-assistant-content');
        text = bubble ? bubble.innerText : '';
      }
      if (text) {
        readAloudText(text, btn);
      }
    }
  });

  // 에이전트 모드 선택 버튼 (하단 툴바)
  const agentModeBtn = document.getElementById('agent-mode-select-btn');
  if (agentModeBtn) {
    agentModeBtn.addEventListener('click', (e) => {
      e.preventDefault();
      // 1순위: 완주 모드 버튼 토글 시도
      const autoBtn = document.getElementById('autonomous-mode-btn');
      if (autoBtn) {
        autoBtn.click();
        return;
      }
      // 2순위: 페르소나 셀렉터 열기/포커스
      const personaSelect = document.getElementById('agent-persona-select');
      if (personaSelect) {
        personaSelect.focus();
        try { personaSelect.showPicker(); } catch (_) {}
      }
    });
  }
"""

if "// Copy code delegation" in content and "Global delegation for read-aloud voice (TTS)" not in content:
    content = content.replace("// Copy code delegation", btn_delegation_code + "\n  // Copy code delegation")
    print("[4] Added global delegation for read-aloud and agent-mode-select-btn.")

# 4. Add safe initApp at the very end of file
end_init_code = """
// ── Application Safe Bootstrap (End of File) ──────────────────────────────────
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    initApp().catch(err => console.error('[Fatal Bootstrap Error]:', err));
  });
} else {
  initApp().catch(err => console.error('[Fatal Bootstrap Error]:', err));
}
"""

if "// ── Application Safe Bootstrap (End of File)" not in content:
    content = content.rstrip() + "\n" + end_init_code
    print("[5] Added safe bootstrap at end of file.")

with open(app_js_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Successfully patched app.js!")

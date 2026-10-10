# -*- coding: utf-8 -*-
"""
static/v2/js/app.js 에 BrowserViewer 인라인 뷰어 연동 주입 및 배포 폴더 동기화 스크립트
"""
import os
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
APP_JS_PATH = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\static\v2"

with open(APP_JS_PATH, "r", encoding="utf-8") as f:
    js = f.read()

# 1. 상단 import 추가
if "import { liveBrowser } from './browser_viewer.js';" not in js:
    js = "import { liveBrowser } from './browser_viewer.js';\n" + js
    print("[app.js] import { liveBrowser } 추가 완료")

# 2. handleSendMessage 중단 처리부에 liveBrowser.close() 추가
old_cancel_block = "if (state.isStreaming || state.currentStreamId || state.activeStream) {"
new_cancel_block = """if (state.isStreaming || state.currentStreamId || state.activeStream) {
    try { liveBrowser.close(false); } catch (_) {}"""

if old_cancel_block in js and "liveBrowser.close(false)" not in js:
    js = js.replace(old_cancel_block, new_cancel_block, 1)
    print("[app.js] 취소 시 브라우저 닫기 연동 완료")

# 3. onToolCall & onStep 에 브라우저 뷰어 마운트 연동
old_tool_call = """      onStep(stepData) {
        console.log('Stream step:', stepData);
      },
      onToolCall(toolData) {
        console.log('Tool call:', toolData);
      },"""

new_tool_call = """      onStep(stepData) {
        console.log('Stream step:', stepData);
        const text = JSON.stringify(stepData || {}).toLowerCase();
        if (text.includes('browser') || text.includes('navigate')) {
          liveBrowser.mount(contentEl);
        }
      },
      onToolCall(toolData) {
        console.log('Tool call:', toolData);
        const name = (toolData?.name || toolData?.function?.name || '').toLowerCase();
        if (name.includes('browser') || name.includes('navigate') || name.includes('web_')) {
          let url = '';
          try {
            const args = typeof toolData.args === 'string' ? JSON.parse(toolData.args) : (toolData.args || toolData.function?.arguments || {});
            url = args.url || '';
          } catch (_) {}
          liveBrowser.mount(contentEl, url);
        }
      },"""

if old_tool_call in js:
    js = js.replace(old_tool_call, new_tool_call, 1)
    print("[app.js] onToolCall/onStep 브라우저 뷰어 마운트 연동 완료")
else:
    print("[app.js] onToolCall 기존 블록 탐색 실패")

# 4. onDone 에 liveBrowser.close(true) 연동
old_done = """        timeEl.textContent = formatTime();
        loadSessions(); // update session titles/counts
        scrollChatToBottom(true);"""

new_done = """        try { liveBrowser.close(true); } catch (_) {}
        timeEl.textContent = formatTime();
        loadSessions(); // update session titles/counts
        scrollChatToBottom(true);"""

if old_done in js:
    js = js.replace(old_done, new_done, 1)
    print("[app.js] onDone 브라우저 뷰어 완료 처리 연동 완료")

with open(APP_JS_PATH, "w", encoding="utf-8") as f:
    f.write(js)

# 5. 배포 디렉토리 전체 동기화
if os.path.exists(DEPLOY_DIR):
    src_v2 = os.path.join(ROOT_DIR, "static", "v2")
    for root, dirs, files in os.walk(src_v2):
        rel = os.path.relpath(root, src_v2)
        target_dir = os.path.join(DEPLOY_DIR, rel) if rel != "." else DEPLOY_DIR
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            src_file = os.path.join(root, f)
            dest_file = os.path.join(target_dir, f)
            shutil.copy2(src_file, dest_file)
    print(f"[Sync] {src_v2} -> {DEPLOY_DIR} 전체 동기화 성공!")
else:
    print(f"[Sync] 배포 디렉토리 없음: {DEPLOY_DIR}")

print("=== 연동 및 동기화 완료 ===")

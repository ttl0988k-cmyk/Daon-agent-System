# -*- coding: utf-8 -*-
"""
Restore all missing UI features, modals, and buttons to static/v2/index.html & index.html:
1. Header: Engine Ready badge & Project Workspace folder button (header-workspace-btn)
2. Session Meta Bar: Agent Persona select (agent-persona-select: 라온, 토니, 빌, 셜록, 프라다 5인)
3. Composer Toolbar: agent-mode-select-btn & autonomous-mode-btn (완주 모드)
4. Modals: workspace-modal, settings-modal, new-agent-modal (hidden by default to never block clicks)
5. Sync to resources deploy directory
"""
import os
import re
import shutil

TARGET_FILES = [
    r"c:\daon\Daon agent System\static\v2\index.html",
    r"c:\daon\Daon agent System\index.html"
]

DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

# 1. Header Engine Status & Workspace Button
header_target = """        <div class="hidden sm:flex items-center gap-space-xs border border-black/10 rounded-[6px] px-space-sm py-[3px] bg-surface shrink-0">
          <span class="w-1.5 h-1.5 rounded-full bg-primary animate-pulse"></span>
          <span class="font-label-sm text-label-sm text-on-surface"><span id="engine-status-text">Daon Workspace v2.5</span></span>
        </div>
        <div class="hidden sm:block h-3 w-[1px] bg-black/10"></div>
        <span class="font-body-sm text-body-sm text-on-surface-variant truncate text-[12px] sm:text-body-sm" id="header-context-label">한국어 통합 오케스트레이션 콘솔</span>"""

header_replacement = """        <div class="hidden sm:flex items-center gap-space-xs border border-black/10 rounded-[6px] px-space-sm py-[3px] bg-surface shrink-0 cursor-default" id="engine-status-badge">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse inline-block" id="engine-status-dot"></span>
          <span class="font-label-sm text-label-sm text-on-surface"><span id="engine-status-text">Engine Ready</span></span>
        </div>
        <div class="hidden sm:block h-3 w-[1px] bg-black/10"></div>
        <button id="header-workspace-btn" type="button" class="flex items-center gap-1.5 border border-black/10 rounded-[6px] px-2.5 py-1 bg-surface hover:bg-black/[0.04] text-on-surface font-label-sm text-label-sm transition-colors cursor-pointer shrink-0" title="클릭하여 에이전트 작업 프로젝트 폴더를 변경합니다">
          <span class="material-symbols-outlined text-[15px] text-on-surface-variant">folder_open</span>
          <span class="font-medium truncate max-w-[130px] md:max-w-[210px]" id="header-workspace-label">프로젝트 폴더 선택...</span>
          <span class="material-symbols-outlined text-[13px] text-on-surface-variant">arrow_drop_down</span>
        </button>"""

# 2. Session Meta Persona Selector
session_target = """            <div class="flex items-center gap-space-sm text-on-surface-variant font-label-sm text-label-sm">
              <span class="font-code text-code text-[11px] uppercase tracking-wider text-on-surface" id="session-meta-id">SESSION #DAON-8842</span>
              <span class="text-black/20">/</span>
              <span>한국어 오케스트레이션 세션</span>
            </div>"""

session_replacement = """            <div class="flex items-center gap-space-sm text-on-surface-variant font-label-sm text-label-sm">
              <span class="font-code text-code text-[11px] uppercase tracking-wider text-on-surface" id="session-meta-id">SESSION #DAON-8842</span>
              <span class="text-black/20">/</span>
              <div class="relative flex items-center">
                <select id="agent-persona-select" class="bg-surface border border-black/15 text-on-surface font-label-sm text-[12px] rounded-[6px] pl-2 pr-6 py-0.5 appearance-none cursor-pointer hover:border-black/30 outline-none transition-colors" title="대화할 에이전트 역할을 선택하세요">
                  <option value="raon">🤖 라온 (종합 오케스트레이터)</option>
                  <option value="토니(기획)">💡 토니 (기획·설계·전략)</option>
                  <option value="빌(개발)">🔨 빌 (풀스택·백엔드·API 구현)</option>
                  <option value="셜록(검수)">🔍 셜록 (코드리뷰·QA·디버깅)</option>
                  <option value="프라다(디자인)">🎨 프라다 (UI·UX·아트 디렉터)</option>
                  <option value="__new__">➕ 새 에이전트 생성...</option>
                </select>
                <span class="material-symbols-outlined text-[14px] text-on-surface-variant absolute right-1 pointer-events-none">expand_more</span>
              </div>
            </div>"""

# 3. Toolbar Agent Mode Button & Autonomous Mode Button
toolbar_target = """                  <button class="w-8 h-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer" title="에이전트 모드 선택" type="button">
                    <span class="material-symbols-outlined text-[18px]">hub</span>
                  </button>
                  <div class="h-3 w-[1px] bg-black/10 mx-1"></div>
                  <div class="flex items-center gap-1 px-2 py-0.5 rounded-[6px] bg-surface border border-black/10 font-code text-code text-[11px] text-on-surface-variant">
                    <span>rag_mode: active</span>
                  </div>"""

toolbar_replacement = """                  <button class="w-8 h-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer" id="agent-mode-select-btn" title="에이전트 모드 선택" type="button">
                    <span class="material-symbols-outlined text-[18px]">hub</span>
                  </button>
                  <div class="h-3 w-[1px] bg-black/10 mx-1"></div>
                  <button id="autonomous-mode-btn" type="button" class="flex items-center gap-1.5 px-2.5 py-1 rounded-[6px] bg-primary text-on-primary font-code text-code text-[11px] font-medium hover:bg-black/80 transition-all cursor-pointer select-none shadow-sm" title="클릭하여 모드 전환: 읽기/검색/초안/코딩은 막힘없이 완주하고, 발송/삭제/결제 같은 비가역 작업만 마지막에 일괄 승인합니다.">
                    <span class="material-symbols-outlined text-[13px]" id="autonomous-mode-icon">bolt</span>
                    <span id="autonomous-mode-text">완주 모드: 켜짐 (범위 승인)</span>
                  </button>"""

# 4. Modals HTML (All hidden by default)
modals_html = """
  <!-- ================= 1. WORKSPACE (PROJECT FOLDER) MODAL ================= -->
  <div id="workspace-modal" class="fixed inset-0 bg-black/50 z-50 hidden items-center justify-center p-4 transition-opacity">
    <div class="bg-surface-container-lowest border border-black/10 rounded-[12px] w-full max-w-[500px] shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
      <div class="px-space-lg py-space-md border-b border-black/10 flex items-center justify-between bg-surface-container-low">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-[20px] text-primary">folder_managed</span>
          <h3 class="font-label-md font-semibold text-on-surface text-[15px]">프로젝트 작업 폴더 지정</h3>
        </div>
        <button id="workspace-modal-close" class="p-1 rounded hover:bg-black/5 text-on-surface-variant flex items-center justify-center cursor-pointer" type="button">
          <span class="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      <div class="p-space-lg flex flex-col gap-4">
        <div>
          <label class="block font-label-sm text-[12px] text-on-surface-variant mb-1.5">현재 활성 작업 폴더</label>
          <div class="flex items-center gap-2">
            <input id="workspace-path-input" type="text" class="flex-1 bg-surface border border-black/15 rounded-[8px] px-3 py-2 text-[12px] font-code text-on-surface outline-none focus:border-black transition-colors" placeholder="예: C:\\Projects\\MyAwesomeApp" />
            <button id="workspace-native-browse-btn" type="button" class="px-3 py-2 bg-surface hover:bg-black/[0.05] border border-black/15 text-on-surface rounded-[8px] font-label-sm text-[12px] font-medium flex items-center gap-1 cursor-pointer transition-colors shrink-0" title="내 컴퓨터 폴더 찾기 창을 엽니다">
              <span class="material-symbols-outlined text-[16px]">folder_open</span>
              <span>찾아보기</span>
            </button>
          </div>
          <p class="text-[11px] text-on-surface-variant mt-1.5">에이전트들이 파일 읽기, 쓰기, 코드 분석 및 도구를 실행할 기준 디렉토리입니다.</p>
        </div>

        <div>
          <label class="block font-label-sm text-[12px] text-on-surface-variant mb-1.5">최근 사용한 워크스페이스</label>
          <div id="recent-workspaces-list" class="flex flex-col gap-1 max-h-[160px] overflow-y-auto border border-black/10 rounded-[8px] p-1 bg-surface">
            <div class="text-[11px] text-on-surface-variant p-2 text-center">목록 불러오는 중...</div>
          </div>
        </div>
      </div>
      <div class="px-space-lg py-3 border-t border-black/10 bg-surface-container-low flex items-center justify-end gap-2">
        <button id="workspace-modal-cancel" type="button" class="px-4 py-1.5 border border-black/15 rounded-[8px] text-[12px] font-label-md text-on-surface hover:bg-black/5 cursor-pointer">취소</button>
        <button id="workspace-modal-save" type="button" class="px-4 py-1.5 bg-primary text-on-primary rounded-[8px] text-[12px] font-label-md font-medium hover:bg-black/80 cursor-pointer flex items-center gap-1">
          <span class="material-symbols-outlined text-[15px]">check</span>
          <span>이 폴더로 설정</span>
        </button>
      </div>
    </div>
  </div>

  <!-- ================= 2. PROVIDER & MODEL SETTINGS MODAL ================= -->
  <div id="settings-modal" class="fixed inset-0 bg-black/50 z-50 hidden items-center justify-center p-4 transition-opacity">
    <div class="bg-surface-container-lowest border border-black/10 rounded-[12px] w-full max-w-[640px] shadow-2xl flex flex-col max-h-[85vh] overflow-hidden animate-in fade-in zoom-in-95 duration-150">
      <div class="px-space-lg py-space-md border-b border-black/10 flex items-center justify-between bg-surface-container-low">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-[20px] text-primary">tune</span>
          <h3 class="font-label-md font-semibold text-on-surface text-[15px]">API 제공자 & 커스텀 모델 설정</h3>
        </div>
        <button id="settings-modal-close" class="p-1 rounded hover:bg-black/5 text-on-surface-variant flex items-center justify-center cursor-pointer" type="button">
          <span class="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      
      <div class="flex-1 overflow-y-auto p-space-lg flex flex-col gap-5">
        <!-- Provider List Section -->
        <div class="flex flex-col gap-2">
          <div class="flex items-center justify-between">
            <span class="font-label-md font-semibold text-[13px] text-on-surface">등록된 제공자 (Providers)</span>
            <button id="add-new-provider-btn" type="button" class="px-2.5 py-1 bg-primary text-on-primary rounded-[6px] text-[11px] font-label-md font-medium hover:bg-black/80 flex items-center gap-1 cursor-pointer">
              <span class="material-symbols-outlined text-[14px]">add</span>
              <span>제공자 추가</span>
            </button>
          </div>
          <div id="settings-providers-list" class="flex flex-col gap-2 mt-1">
            <div class="text-[12px] text-on-surface-variant p-3 text-center">제공자 목록 불러오는 중...</div>
          </div>
        </div>

        <!-- Add/Edit Provider Form (Expandable) -->
        <div id="provider-form-card" class="hidden border border-black/15 rounded-[10px] p-space-md bg-surface flex flex-col gap-3">
          <div class="flex items-center justify-between border-b border-black/10 pb-2">
            <h4 class="font-label-md font-semibold text-[13px] text-on-surface" id="provider-form-title">제공자 추가</h4>
            <button id="provider-form-close-btn" type="button" class="text-on-surface-variant hover:text-on-surface text-[12px] cursor-pointer">닫기</button>
          </div>
          
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">제공자 프리셋</label>
              <select id="provider-preset-select" class="w-full bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] text-on-surface outline-none cursor-pointer">
                <option value="">-- 직접 입력 --</option>
                <option value="opencode">OpenCode (DeepSeek)</option>
                <option value="openrouter">OpenRouter</option>
                <option value="deepseek">DeepSeek Official</option>
                <option value="openai">OpenAI</option>
                <option value="groq">Groq (Ultra-Fast)</option>
                <option value="ollama">Ollama (로컬)</option>
                <option value="anthropic">Anthropic Claude</option>
                <option value="custom">Custom (OpenAI 호환)</option>
              </select>
            </div>
            <div>
              <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">제공자 식별명 <span class="text-red-500">*</span></label>
              <input id="provider-name-input" type="text" placeholder="예: my-deepseek" class="w-full bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] text-on-surface outline-none focus:border-black" />
            </div>
          </div>

          <div>
            <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">API Key <span class="text-red-500">*</span></label>
            <div class="relative flex items-center">
              <input id="provider-key-input" type="password" placeholder="sk-..." class="w-full bg-surface-container-lowest border border-black/15 rounded-[6px] pl-2.5 pr-8 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />
              <button id="provider-key-toggle-btn" type="button" class="absolute right-2 text-on-surface-variant hover:text-on-surface cursor-pointer">
                <span class="material-symbols-outlined text-[16px]">visibility</span>
              </button>
            </div>
          </div>

          <div>
            <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">Base URL (엔드포인트)</label>
            <input id="provider-url-input" type="text" placeholder="https://api.openai.com/v1" class="w-full bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />
          </div>

          <div>
            <label class="block font-label-sm text-[11px] text-on-surface-variant mb-1">모델 수동 등록 (쉼표로 구분)</label>
            <div class="flex items-center gap-2">
              <input id="provider-manual-models-input" type="text" placeholder="예: deepseek-v4.1-flash, gpt-4o" class="flex-1 bg-surface-container-lowest border border-black/15 rounded-[6px] px-2.5 py-1.5 text-[12px] font-code text-on-surface outline-none focus:border-black" />
              <button id="provider-fetch-models-btn" type="button" class="px-2.5 py-1.5 bg-surface-container-high hover:bg-surface-dim rounded-[6px] text-[11px] font-label-md text-on-surface flex items-center gap-1 cursor-pointer shrink-0">
                <span class="material-symbols-outlined text-[14px]">sync</span>
                <span>모델 자동 감지</span>
              </button>
            </div>
            <div id="provider-fetch-result" class="hidden text-[11px] p-2 mt-1.5 rounded-[6px] bg-surface-container-high text-on-surface-variant"></div>
          </div>

          <div class="flex items-center justify-end gap-2 pt-2 border-t border-black/10">
            <button id="provider-form-cancel-btn" type="button" class="px-3 py-1 border border-black/15 rounded-[6px] text-[12px] text-on-surface hover:bg-black/5 cursor-pointer">취소</button>
            <button id="provider-form-save-btn" type="button" class="px-4 py-1 bg-primary text-on-primary rounded-[6px] text-[12px] font-medium hover:bg-black/80 cursor-pointer flex items-center gap-1">
              <span class="material-symbols-outlined text-[14px]">save</span>
              <span>제공자 저장</span>
            </button>
          </div>
        </div>
      </div>

      <div class="px-space-lg py-3 border-t border-black/10 bg-surface-container-low flex items-center justify-end">
        <button id="settings-modal-done" type="button" class="px-5 py-1.5 bg-primary text-on-primary rounded-[8px] text-[12px] font-label-md font-medium hover:bg-black/80 cursor-pointer">완료</button>
      </div>
    </div>
  </div>

  <!-- ================= 3. NEW AGENT PERSONA MODAL ================= -->
  <div id="new-agent-modal" class="fixed inset-0 bg-black/50 z-50 hidden items-center justify-center p-4 transition-opacity">
    <div class="bg-surface-container-lowest border border-black/10 rounded-[12px] w-full max-w-[480px] shadow-2xl flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
      <div class="px-space-lg py-space-md border-b border-black/10 flex items-center justify-between bg-surface-container-low">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-[20px] text-primary">psychology</span>
          <h3 class="font-label-md font-semibold text-on-surface text-[15px]">새 에이전트 페르소나 생성</h3>
        </div>
        <button id="new-agent-modal-close" class="p-1 rounded hover:bg-black/5 text-on-surface-variant flex items-center justify-center cursor-pointer" type="button">
          <span class="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      <div class="p-space-lg flex flex-col gap-3.5">
        <div>
          <label class="block font-label-sm text-[12px] text-on-surface-variant mb-1">에이전트 이름 <span class="text-red-500">*</span></label>
          <input id="new-agent-name" type="text" placeholder="예: 보안 취약점 감사관, SQL 최적화 마스터" class="w-full bg-surface border border-black/15 rounded-[8px] px-3 py-2 text-[12px] text-on-surface outline-none focus:border-black" />
        </div>
        <div>
          <label class="block font-label-sm text-[12px] text-on-surface-variant mb-1">아이콘 이모지</label>
          <input id="new-agent-icon" type="text" value="⚡" maxlength="2" class="w-20 bg-surface border border-black/15 rounded-[8px] px-3 py-2 text-[14px] text-center text-on-surface outline-none focus:border-black" />
        </div>
        <div>
          <label class="block font-label-sm text-[12px] text-on-surface-variant mb-1">역할 및 시스템 프롬프트 (System Instruction)</label>
          <textarea id="new-agent-prompt" rows="4" placeholder="에이전트의 전문 분야, 사고방식, 답변 규칙 등을 입력하세요..." class="w-full bg-surface border border-black/15 rounded-[8px] p-2.5 text-[12px] text-on-surface outline-none focus:border-black resize-none leading-relaxed"></textarea>
        </div>
      </div>
      <div class="px-space-lg py-3 border-t border-black/10 bg-surface-container-low flex items-center justify-end gap-2">
        <button id="new-agent-modal-cancel" type="button" class="px-4 py-1.5 border border-black/15 rounded-[8px] text-[12px] font-label-md text-on-surface hover:bg-black/5 cursor-pointer">취소</button>
        <button id="new-agent-modal-save" type="button" class="px-4 py-1.5 bg-primary text-on-primary rounded-[8px] text-[12px] font-label-md font-medium hover:bg-black/80 cursor-pointer flex items-center gap-1">
          <span class="material-symbols-outlined text-[15px]">add</span>
          <span>에이전트 등록</span>
        </button>
      </div>
    </div>
  </div>
"""

for target_path in TARGET_FILES:
    with open(target_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Header
    if header_target in html:
        html = html.replace(header_target, header_replacement)
        print(f"[{target_path}] Header updated.")
    elif "header-workspace-btn" not in html:
        # Regex replacement
        html = re.sub(
            r'<div class="hidden sm:flex items-center gap-space-xs border border-black/10 rounded-\[6px\] px-space-sm py-\[3px\] bg-surface shrink-0">.*?<span id="header-context-label">한국어 통합 오케스트레이션 콘솔</span>',
            header_replacement,
            html,
            flags=re.DOTALL
        )
        print(f"[{target_path}] Header updated via regex.")

    # 2. Session meta
    if session_target in html:
        html = html.replace(session_target, session_replacement)
        print(f"[{target_path}] Session meta updated.")
    elif "agent-persona-select" not in html:
        html = re.sub(
            r'<div class="flex items-center gap-space-sm text-on-surface-variant font-label-sm text-label-sm">\s*<span class="font-code text-code text-\[11px\] uppercase tracking-wider text-on-surface" id="session-meta-id">SESSION #DAON-8842</span>\s*<span class="text-black/20">/</span>\s*<span>한국어 오케스트레이션 세션</span>\s*</div>',
            session_replacement,
            html
        )
        print(f"[{target_path}] Session meta updated via regex.")

    # 3. Toolbar
    if toolbar_target in html:
        html = html.replace(toolbar_target, toolbar_replacement)
        print(f"[{target_path}] Toolbar buttons updated.")
    elif "autonomous-mode-btn" not in html:
        html = re.sub(
            r'<button class="w-8 h-8 flex items-center justify-center rounded-\[8px\] text-on-surface-variant hover:text-on-surface hover:bg-black/\[0\.05\] transition-colors cursor-pointer" title="에이전트 모드 선택" type="button">\s*<span class="material-symbols-outlined text-\[18px\]">hub</span>\s*</button>\s*<div class="h-3 w-\[1px\] bg-black/10 mx-1"></div>\s*<div class="flex items-center gap-1 px-2 py-0\.5 rounded-\[6px\] bg-surface border border-black/10 font-code text-code text-\[11px\] text-on-surface-variant">\s*<span>rag_mode:\s*active</span>\s*</div>',
            toolbar_replacement,
            html
        )
        print(f"[{target_path}] Toolbar buttons updated via regex.")

    # 4. Modals
    if 'id="workspace-modal"' not in html:
        html = html.replace("</body>", modals_html + "\n</body>")
        print(f"[{target_path}] Modals inserted.")
    else:
        print(f"[{target_path}] Modals already exist.")

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(html)

print("HTML files restoration complete!")

# 5. Sync to Electron Deploy Directory
if os.path.exists(DEPLOY_DIR):
    deploy_index = os.path.join(DEPLOY_DIR, "index.html")
    deploy_v2_dir = os.path.join(DEPLOY_DIR, "static", "v2")
    
    shutil.copy2(TARGET_FILES[0], deploy_index)
    os.makedirs(deploy_v2_dir, exist_ok=True)
    shutil.copy2(TARGET_FILES[0], os.path.join(deploy_v2_dir, "index.html"))
    
    # Copy js
    deploy_js_dir = os.path.join(deploy_v2_dir, "js")
    os.makedirs(deploy_js_dir, exist_ok=True)
    for js_file in ["app.js", "api.js", "browser_viewer.js"]:
        src_js = os.path.join(r"c:\daon\Daon agent System\static\v2\js", js_file)
        if os.path.exists(src_js):
            shutil.copy2(src_js, os.path.join(deploy_js_dir, js_file))
            
    print(f"Successfully synced all updated files to {DEPLOY_DIR}!")

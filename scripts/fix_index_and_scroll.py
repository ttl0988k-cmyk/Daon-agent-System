# -*- coding: utf-8 -*-
"""
정밀 수리 스크립트:
1. index.html의 손상/중복 구간을 깨끗하게 복구하고, chat-bottom-spacer를 확실하게 배치
2. app.js의 스크롤 및 안착(landOnMessage, scrollChatToBottom) 메커니즘을 혁신적으로 개선
3. 배포 폴더(resources/static/v2)로 완전 동기화
"""
import os
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\static\v2"

INDEX_PATH = os.path.join(ROOT_DIR, "static", "v2", "index.html")
APP_JS_PATH = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")

# ─────────────────────────────────────────────────────────────────────────────
# 1. index.html 복구
# ─────────────────────────────────────────────────────────────────────────────
with open(INDEX_PATH, 'r', encoding='utf-8') as f:
    html = f.read()

start_marker = '<div class="p-space-md border-t border-black/10 flex flex-col gap-space-sm bg-surface">'
end_marker = '<details class="w-full bg-surface border border-black/10 rounded-[10px] p-space-md mb-1 group transition-all">'

start_pos = html.find(start_marker)
end_pos = html.find(end_marker)

print(f"[index.html] start_pos: {start_pos}, end_pos: {end_pos}")

clean_sidebar_and_main = '''<div class="p-space-md border-t border-black/10 flex flex-col gap-space-sm bg-surface">
      <div class="flex items-center justify-between px-space-sm py-space-xs">
        <a class="flex items-center gap-space-sm group cursor-pointer" data-tab-target="chat-session">
          <div class="w-7 h-7 rounded-full bg-primary flex items-center justify-center">
            <span class="material-symbols-outlined text-on-primary text-[16px]">person</span>
          </div>
          <div class="flex flex-col">
            <span class="font-label-md text-label-md text-on-surface leading-tight">Agent Admin</span>
            <span class="font-label-sm text-label-sm text-on-surface-variant leading-none">Enterprise Matrix</span>
          </div>
        </a>
        <div class="flex items-center gap-[2px]">
          <button id="sidebar-settings-btn" class="w-7 h-7 flex items-center justify-center rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer" title="Settings" type="button">
            <span class="material-symbols-outlined text-[18px]">settings</span>
          </button>
        </div>
      </div>
    </div>
    <div id="sidebar-resizer" class="hidden md:block absolute -right-[4px] top-0 bottom-0 w-[8px] cursor-col-resize hover:bg-black/15 active:bg-primary transition-colors z-50 select-none group" title="드래그하여 세션창 크기 조절">
      <div class="w-[2px] h-10 bg-black/20 group-hover:bg-primary mx-auto my-auto absolute inset-y-0 right-[3px] rounded-full"></div>
    </div>
  </aside>

  <!-- Main Content Layout Area -->
  <div class="pl-0 layout-with-sidebar min-h-screen flex flex-col bg-surface-container-lowest" id="main-layout-container">
    <!-- Top Header -->
    <header class="fixed top-0 left-0 fixed-with-sidebar right-0 h-14 bg-surface-container-lowest border-b border-black/10 z-20 flex items-center justify-between px-space-md md:px-space-xl">
      <div class="flex items-center gap-space-md min-w-0">
        <button id="sidebar-toggle-btn" class="md:hidden p-1.5 rounded-[8px] hover:bg-black/5 text-on-surface flex items-center justify-center shrink-0 cursor-pointer" type="button" title="메뉴 열기">
          <span class="material-symbols-outlined text-[22px]">menu</span>
        </button>
        <div class="hidden sm:flex items-center gap-space-xs border border-black/10 rounded-[6px] px-space-sm py-[3px] bg-surface shrink-0">
          <span class="w-1.5 h-1.5 rounded-full bg-primary animate-pulse"></span>
          <span class="font-label-sm text-label-sm text-on-surface"><span id="engine-status-text">Daon Workspace v2.5</span></span>
        </div>
        <div class="hidden sm:block h-3 w-[1px] bg-black/10"></div>
        <span class="font-body-sm text-body-sm text-on-surface-variant truncate text-[12px] sm:text-body-sm" id="header-context-label">한국어 통합 오케스트레이션 콘솔</span>
      </div>
      <div class="flex items-center gap-space-md">
        <div class="flex items-center gap-space-xs">
          <button class="h-8 px-space-sm flex items-center gap-space-xs rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] transition-colors font-label-md text-label-md" id="export-session-btn" type="button">
            <span class="material-symbols-outlined text-[18px]">share</span>
            <span class="hidden sm:inline">Export</span>
          </button>
          <button class="h-8 w-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] transition-colors" type="button">
            <span class="material-symbols-outlined text-[18px]">more_horiz</span>
          </button>
        </div>
        <div class="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
          <span class="material-symbols-outlined text-on-primary text-[18px]">person</span>
        </div>
      </div>
    </header>

    <!-- Sticky Sub-navigation Bar for 5 Core Workspaces -->
    <nav class="fixed top-14 left-0 fixed-with-sidebar right-0 h-11 bg-surface border-b border-black/10 z-10 flex items-center px-space-md md:px-space-lg overflow-x-auto">
      <div class="flex items-center gap-1 min-w-max" id="workspace-tabs">
        <button class="workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface bg-surface-container-lowest border border-black/15 font-label-md text-label-md transition-all font-semibold" data-target="chat-session" type="button">
          <span class="material-symbols-outlined text-[16px]">chat</span>
          <span>Agent 채팅 세션</span>
          <span class="w-1.5 h-1.5 rounded-full bg-primary"></span>
        </button>
        <button class="workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all" data-target="dynamic-harness" type="button">
          <span class="material-symbols-outlined text-[16px]">bolt</span>
          <span>Dynamic Harness 세션</span>
        </button>
        <button class="workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all" data-target="agent-boardroom" type="button">
          <span class="material-symbols-outlined text-[16px]">groups</span>
          <span>정적 에이전트 회의실 (8 Slots)</span>
        </button>
        <button class="workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all" data-target="plugin-mcp" type="button">
          <span class="material-symbols-outlined text-[16px]">extension</span>
          <span>플러그인 &amp; MCP 스토어</span>
        </button>
        <button class="workspace-tab-btn flex items-center gap-1.5 px-3 py-1.5 rounded-[6px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.04] font-label-md text-label-md transition-all" data-target="agent-skills" type="button">
          <span class="material-symbols-outlined text-[16px]">psychology</span>
          <span>에이전트 스킬 (Skills)</span>
        </button>
      </div>
    </nav>

    <!-- Main Views Container -->
    <main class="relative pt-[100px] w-full flex-1 flex flex-col bg-surface-container-lowest">
      <!-- ================= TAB 1: AGENT CHAT SESSION ================= -->
      <div class="workspace-panel flex flex-col items-center w-full flex-1" id="tab-chat-session">
        <div class="w-full max-w-[768px] mx-auto px-space-lg flex-1 flex flex-col">
          <!-- Thread Meta Context / Timestamp Bar -->
          <div class="flex items-center justify-between border-b border-black/10 py-3 mb-2">
            <div class="flex items-center gap-space-sm text-on-surface-variant font-label-sm text-label-sm">
              <span class="font-code text-code text-[11px] uppercase tracking-wider text-on-surface" id="session-meta-id">SESSION #DAON-8842</span>
              <span class="text-black/20">/</span>
              <span>한국어 오케스트레이션 세션</span>
            </div>
            <div class="flex items-center gap-space-xs text-on-surface-variant font-code text-code text-[11px]">
              <span class="inline-block w-1.5 h-1.5 rounded-full bg-primary"></span>
              <span>Active Matrix v1.0.4</span>
            </div>
          </div>

          <!-- Chat Messages Container -->
          <div class="flex flex-col w-full gap-space-2xl pt-2" id="chat-messages-container">
            <!-- Messages populated dynamically by app.js -->
          </div>

          <!-- Bottom Spacer: 프롬프트 창 위 시원하고 넉넉한 랜딩 공간 확보 -->
          <div id="chat-bottom-spacer" class="w-full pointer-events-none" style="min-height: 520px; height: 65vh;"></div>
        </div>

        <!-- Pinned Bottom Floating Composer for Chat -->
        <div class="fixed bottom-0 left-0 fixed-with-sidebar right-0 z-20 pointer-events-none pb-4 pt-8 bg-gradient-to-t from-surface-container-lowest via-surface-container-lowest/95 to-transparent floating-composer-container">
          <div class="w-full max-w-[768px] mx-auto px-space-lg pointer-events-auto flex flex-col gap-space-xs">
            <div class="w-full bg-surface-container-lowest border border-black/10 rounded-[12px] p-space-sm flex flex-col gap-space-sm focus-within:border-black transition-colors shadow-sm">
              <input type="file" id="chat-file-input" multiple accept="image/*,video/*,.pdf,.txt,.py,.js,.html,.json,.csv,.zip" class="hidden" />
              <div id="chat-attach-tray" class="hidden flex-wrap gap-2 px-1 pt-1 pb-1 border-b border-black/[0.06]"></div>
              <div class="w-full">
                <textarea class="w-full bg-transparent resize-none border-0 outline-none text-on-surface placeholder:text-on-surface-variant/60 font-body-md text-body-md leading-relaxed px-1" id="chat-input" placeholder="메시지를 입력하세요 (Daon Agent와 대화하기)..." rows="2"></textarea>
              </div>
              <div class="flex items-center justify-between pt-1 border-t border-black/[0.06]">
                <div class="flex items-center gap-space-xs">
                  <button class="w-8 h-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer" id="chat-attach-btn" title="파일, 이미지, 영상 첨부" type="button">
                    <span class="material-symbols-outlined text-[18px]">attach_file</span>
                  </button>
                  <button class="w-8 h-8 flex items-center justify-center rounded-[8px] text-on-surface-variant hover:text-on-surface hover:bg-black/[0.05] transition-colors cursor-pointer" title="에이전트 모드 선택" type="button">
                    <span class="material-symbols-outlined text-[18px]">hub</span>
                  </button>
                  <div class="h-3 w-[1px] bg-black/10 mx-1"></div>
                  <div class="flex items-center gap-1 px-2 py-0.5 rounded-[6px] bg-surface border border-black/10 font-code text-code text-[11px] text-on-surface-variant">
                    <span>rag_mode: active</span>
                  </div>
                </div>
                <div class="flex items-center gap-space-xs">
                  <span class="font-code text-code text-[11px] text-on-surface-variant/70 hidden sm:inline mr-1">⌘ + ↵</span>
                  <button class="h-8 px-space-md flex items-center justify-center gap-1.5 bg-primary hover:bg-tertiary-container active:bg-black text-on-primary rounded-[8px] font-label-md text-label-md font-medium transition-colors cursor-pointer" id="send-button" type="button">
                    <span>전송</span>
                    <span class="material-symbols-outlined text-[15px]">arrow_upward</span>
                  </button>
                </div>
              </div>
            </div>

            <!-- Model & Reasoning Settings Matrix (Accordion) -->
            '''

if start_pos != -1 and end_pos != -1:
    new_html = html[:start_pos] + clean_sidebar_and_main + html[end_pos:]
    with open(INDEX_PATH, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print("[index.html] 복구 완료!")
else:
    print("[index.html] 마커 탐색 실패!")

# ─────────────────────────────────────────────────────────────────────────────
# 2. app.js 스크롤 & 랜딩 로직 고도화
# ─────────────────────────────────────────────────────────────────────────────
with open(APP_JS_PATH, 'r', encoding='utf-8') as f:
    js = f.read()

# landOnMessage 및 scrollChatToBottom 정의 부분 교체
# 기존:
# function landOnMessage(element) { ... }
# function scrollChatToBottom(forceSmooth = false) { ... }
# window.scrollChatToBottom = scrollChatToBottom;
# window.landOnMessage = landOnMessage;

new_landing_logic = """// ── Message Landing & Smart Auto-Scroll (프롬프트 창 위 완벽 안착) ─────────────

function getComposerTop() {
  const composer = document.querySelector('.floating-composer-container');
  if (composer) {
    const rect = composer.getBoundingClientRect();
    return rect.top;
  }
  return window.innerHeight - 240;
}

/**
 * 에이전트 메시지 생성 시:
 * 에이전트 메시지(또는 직전 사용자 질문)를 화면 상단(헤더 아래 105px)으로 매끄럽게 안착(랜딩).
 * 이로써 에이전트 답변이 시작될 때 프롬프트 입력창 훨씬 위의 넓은 뷰포트 공간에 100% 노출됩니다.
 */
function landOnMessage(element) {
  if (!element) return;
  requestAnimationFrame(() => {
    // 이전 사용자 질문이 있다면 질문부터 시야에 들어오도록 하되,
    // 화면 높이가 좁으면 에이전트 메시지 자체를 기준점으로 잡습니다.
    const prevEl = element.previousElementSibling;
    let target = element;
    if (prevEl && prevEl.tagName === 'ARTICLE') {
      const prevRect = prevEl.getBoundingClientRect();
      // 질문이 너무 길지 않다면 질문부터 보여줌
      if (prevRect.height < 180) {
        target = prevEl;
      }
    }

    const headerOffset = 105; // 고정 상단 헤더(56px) + 탭 네비(44px) + 여백
    const rect = target.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const targetTop = rect.top + scrollTop - headerOffset;

    window.scrollTo({
      top: Math.max(0, targetTop),
      behavior: 'smooth'
    });
  });
}

/**
 * 실시간 스트리밍 중(onToken, onReasoning) 및 답변 완료 시(onDone):
 * 에이전트 답변의 최신 끝부분(바닥)이 프롬프트 입력창 상단보다 '40px 위'에 항상 머물도록 오토스크롤!
 * 프롬프트 창 뒤로 파묻히는 현상을 수학적으로 100% 차단합니다.
 */
function scrollChatToBottom(forceSmooth = false) {
  requestAnimationFrame(() => {
    const container = document.getElementById('chat-messages-container');
    if (!container) return;

    const lastArticle = container.lastElementChild;
    if (!lastArticle) return;

    // 현재 렌더링 중인 최신 텍스트 영역 또는 생각 블록 끝점
    const targetEl = lastArticle.querySelector('.response-body') || 
                     lastArticle.querySelector('.reasoning-block') || 
                     lastArticle;

    const rect = targetEl.getBoundingClientRect();
    const composerTop = getComposerTop();

    // 프롬프트 입력창 윗선보다 40px 위의 안전지대에 최신 답변이 안착(Landing)
    const safeLandingLine = composerTop - 40;
    const overflow = rect.bottom - safeLandingLine;

    // 답변의 바닥이 안전선을 뚫고 내려가 프롬프트 창에 접근하거나 가려지려 하면 스크롤!
    if (overflow > 1) {
      window.scrollBy({
        top: overflow,
        behavior: forceSmooth ? 'smooth' : 'auto'
      });
    }
  });
}
window.scrollChatToBottom = scrollChatToBottom;
window.landOnMessage = landOnMessage;
"""

# 찾아서 교체
marker_search = "// ── Message Landing & Smart Auto-Scroll (프롬프트 창 위 완벽 안착) ─────────────"
marker_end = "// ── Model & Reasoning Effort Selection ────────────────────────────────────────"

p1 = js.find(marker_search)
p2 = js.find(marker_end)

if p1 != -1 and p2 != -1:
    new_js = js[:p1] + new_landing_logic + "\n" + js[p2:]
    with open(APP_JS_PATH, 'w', encoding='utf-8') as f:
        f.write(new_js)
    print("[app.js] 스크롤 및 랜딩 로직 업데이트 완료!")
else:
    print("[app.js] 마커 탐색 실패!", p1, p2)

# ─────────────────────────────────────────────────────────────────────────────
# 3. 배포 폴더로 완전 동기화 (Force Sync to Deploy Directory)
# ─────────────────────────────────────────────────────────────────────────────
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
    print(f"[Sync] {src_v2} -> {DEPLOY_DIR} 전체 파일 복사 완료!")
else:
    print(f"[Sync] 배포 폴더를 찾을 수 없습니다: {DEPLOY_DIR}")

print("=== 모든 수리 및 동기화 완료 ===")

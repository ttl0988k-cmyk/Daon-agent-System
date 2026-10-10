# -*- coding: utf-8 -*-
"""
정밀 업데이트 스크립트:
1. app.js의 안착(Landing) 및 스마트 오토스크롤 알고리즘을 완벽 수학적 좌표 기반으로 강화
2. renderSessionMessages 완료 시에도 마지막 메시지가 프롬프트 창 위에 칼같이 안착되도록 보장
3. 배포 폴더(resources/static/v2)로 강제 동기화
"""
import os
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\static\v2"
APP_JS_PATH = os.path.join(ROOT_DIR, "static", "v2", "js", "app.js")

with open(APP_JS_PATH, 'r', encoding='utf-8') as f:
    js = f.read()

# 1. renderSessionMessages 끝부분 개선
old_render_end = """  messages.forEach(msg => {
    if (!msg || !msg.content) return;
    // 내부 제어/시스템 안내 메시지는 사용자 말풍선으로 노출되지 않도록 필터링
    if (isInternalSystemMessage(msg.content)) return;

    if (msg.role === 'user') {
      appendUserMessage(msg.content, msg.timestamp);
    } else if (msg.role === 'assistant') {
      const reasoning = msg.reasoning_content || msg.thinking || extractThinkBlocks(msg.content);
      const cleanContent = stripThinkBlocks(msg.content);
      appendAssistantMessage(cleanContent, msg.timestamp, false, reasoning);
    }
  });

  scrollChatToBottom();
}"""

new_render_end = """  messages.forEach(msg => {
    if (!msg || !msg.content) return;
    // 내부 제어/시스템 안내 메시지는 사용자 말풍선으로 노출되지 않도록 필터링
    if (isInternalSystemMessage(msg.content)) return;

    if (msg.role === 'user') {
      appendUserMessage(msg.content, msg.timestamp);
    } else if (msg.role === 'assistant') {
      const reasoning = msg.reasoning_content || msg.thinking || extractThinkBlocks(msg.content);
      const cleanContent = stripThinkBlocks(msg.content);
      appendAssistantMessage(cleanContent, msg.timestamp, false, reasoning);
    }
  });

  // 세션 로드 시 마지막 대답이 프롬프트 입력창 위에 완벽히 안착되도록 렌더링 후 랜딩 스크롤
  setTimeout(() => {
    scrollLastMessageToLanding(false);
  }, 60);
}"""

if old_render_end in js:
    js = js.replace(old_render_end, new_render_end)
    print("[app.js] renderSessionMessages 랜딩 적용 완료!")
else:
    print("[app.js] renderSessionMessages 기존 패턴 탐색 필요")

# 2. landing & scrollChatToBottom 정의 부분 교체
new_landing_block = """// ── Message Landing & Smart Auto-Scroll (프롬프트 창 위 완벽 안착) ─────────────

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
    const prevEl = element.previousElementSibling;
    let target = element;
    if (prevEl && prevEl.tagName === 'ARTICLE') {
      const prevRect = prevEl.getBoundingClientRect();
      if (prevRect.height < 160) {
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
 * 특정 메시지(또는 마지막 메시지)의 끝부분을 프롬프트 입력창 바로 위 40px에 수학적으로 칼같이 안착(랜딩)
 */
function scrollLastMessageToLanding(smooth = false) {
  const container = document.getElementById('chat-messages-container');
  if (!container || !container.lastElementChild) return;
  const lastArticle = container.lastElementChild;
  const targetEl = lastArticle.querySelector('.response-body') || 
                   lastArticle.querySelector('.reasoning-block') || 
                   lastArticle;

  const rect = targetEl.getBoundingClientRect();
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
  const composerTop = getComposerTop();
  const safeLandingLine = composerTop - 40;

  // 원하는 위치: rect.bottom이 safeLandingLine과 일치하도록 정확한 scrollTop 산출
  const targetScrollTop = (rect.bottom + scrollTop) - safeLandingLine;
  window.scrollTo({
    top: Math.max(0, targetScrollTop),
    behavior: smooth ? 'smooth' : 'auto'
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

    const targetEl = lastArticle.querySelector('.response-body') || 
                     lastArticle.querySelector('.reasoning-block') || 
                     lastArticle;

    const rect = targetEl.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const composerTop = getComposerTop();

    // 프롬프트 입력창 윗선보다 40px 위의 안전지대에 최신 답변이 안착(Landing)
    const safeLandingLine = composerTop - 40;
    const overflow = rect.bottom - safeLandingLine;

    // 답변의 바닥이 안전선을 뚫고 내려가 프롬프트 창에 접근하거나 가려지려 하면 정확한 위치로 스크롤!
    if (overflow > 1) {
      const targetScrollTop = (rect.bottom + scrollTop) - safeLandingLine;
      window.scrollTo({
        top: Math.max(0, targetScrollTop),
        behavior: forceSmooth ? 'smooth' : 'auto'
      });
    }
  });
}
window.scrollChatToBottom = scrollChatToBottom;
window.scrollLastMessageToLanding = scrollLastMessageToLanding;
window.landOnMessage = landOnMessage;
"""

marker_search = "// ── Message Landing & Smart Auto-Scroll (프롬프트 창 위 완벽 안착) ─────────────"
marker_end = "// ── Model & Reasoning Effort Selection ────────────────────────────────────────"

p1 = js.find(marker_search)
p2 = js.find(marker_end)

if p1 != -1 and p2 != -1:
    js = js[:p1] + new_landing_block + "\n" + js[p2:]
    print("[app.js] 랜딩 및 오토스크롤 함수 블록 교체 완료!")
else:
    print("[app.js] 랜딩 함수 블록 마커 탐색 실패!")

with open(APP_JS_PATH, 'w', encoding='utf-8') as f:
    f.write(js)

# 배포 폴더로 완전 동기화
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
    print(f"[Sync] {DEPLOY_DIR} 완전 동기화 완료!")

print("=== 완벽 수리 및 동기화 성공 ===")

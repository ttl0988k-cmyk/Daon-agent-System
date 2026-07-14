# DAON UI 레이아웃 동적 전환 설계 (ChatGPT 스타일)

## 목표

| 모드 | 레이아웃 | 에디터 |
|------|----------|--------|
| 💬 **Chat** | 2컬럼 (좌측 사이드바 + 넓은 채팅) | 완전 숨김 |
| ⚖️ **토론** | 2컬럼 (좌측 사이드바 + 넓은 토론) | 완전 숨김 |
| ⚡ **Harness** | 3컬럼 (좌측 + 에디터 + 하네스 콘솔) | 표시 |

---

## 1. CSS 변경사항 (`static/styles.css`)

### 1.1 기본 그리드 (Harness 모드 = 기존 유지)

```css
.app-container {
  display: grid;
  grid-template-columns: 280px 6px 1fr 6px 360px;
  height: 100vh;
  overflow: hidden;
}
```

### 1.2 Chat/토론 모드 그리드

```css
/* Chat / 토론 모드: 2컬럼 */
.app-container.layout-chat {
  grid-template-columns: 280px 0px 0px 0px 1fr;
}

.app-container.layout-chat .middle-panel {
  display: none;
}

.app-container.layout-chat #resizerLeft,
.app-container.layout-chat #resizerRight {
  display: none;
}
```

### 1.3 ChatGPT 스타일 채팅 영역

```css
/* Chat 모드일 때 채팅 메시지 중앙 정렬 + 최대 너비 */
.app-container.layout-chat #chatMessages,
.app-container.layout-chat #debateMessages {
  max-width: 768px;
  margin: 0 auto;
  width: 100%;
}

.app-container.layout-chat .chat-input-area {
  max-width: 768px;
  margin: 0 auto;
  width: 100%;
}

/* Chat 모드일 때 right-panel 전체를 채팅 전용으로 */
.app-container.layout-chat .right-panel {
  grid-column: 5;
  /* 1fr로 확장됨 */
}
```

### 1.4 전환 애니메이션 (선택사항)

```css
.app-container {
  transition: grid-template-columns 0.3s ease;
}

.middle-panel {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.app-container.layout-chat .middle-panel {
  opacity: 0;
  transform: translateX(-20px);
}
```

---

## 2. JS 변경사항 (`static/modules/harness.js`)

### 2.1 `switchMode()` 함수 수정

```javascript
function switchMode(mode) {
  const appContainer = document.querySelector('.app-container');

  if (mode === 'chat') {
    // UI 상태
    $('modeChatBtn').classList.add('active');
    $('modeHarnessBtn').classList.remove('active');
    $('toggleDebateModeBtn').classList.remove('active');
    $('chatModeContent').style.display = 'flex';
    $('harnessModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(false);
    }
    // ★ 레이아웃 전환: 2컬럼 (ChatGPT 스타일)
    appContainer.classList.add('layout-chat');
    appContainer.classList.remove('layout-harness');

  } else if (mode === 'harness') {
    // UI 상태
    $('modeHarnessBtn').classList.add('active');
    $('modeChatBtn').classList.remove('active');
    $('toggleDebateModeBtn').classList.remove('active');
    $('harnessModeContent').style.display = 'flex';
    $('chatModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(false);
    }
    // ★ 레이아웃 전환: 3컬럼 (Harness)
    appContainer.classList.remove('layout-chat');
    appContainer.classList.add('layout-harness');

  } else if (mode === 'debate') {
    // UI 상태
    $('toggleDebateModeBtn').classList.add('active');
    $('modeChatBtn').classList.remove('active');
    $('modeHarnessBtn').classList.remove('active');
    $('chatModeContent').style.display = 'flex';
    $('harnessModeContent').style.display = 'none';
    if (typeof toggleDebateModeUI === 'function') {
      toggleDebateModeUI(true);
    }
    // ★ 레이아웃 전환: 2컬럼 (ChatGPT 스타일, 토론용)
    appContainer.classList.add('layout-chat');
    appContainer.classList.remove('layout-harness');
  }
}
```

### 2.2 초기화 시 기본 모드 설정

```javascript
// 페이지 로드 시 기본 레이아웃 클래스 설정
document.addEventListener('DOMContentLoaded', function() {
  const appContainer = document.querySelector('.app-container');
  // 기본값: Harness 모드가 아니면 layout-chat
  appContainer.classList.add('layout-chat');
});
```

---

## 3. HTML 변경사항 (`index.html`)

### 3.1 별도 변경 불필요 (CSS 클래스 기반이므로)

현재 HTML 구조를 그대로 유지합니다. 모든 변경은 CSS와 JS에서 처리됩니다.

단, 확인 필요:
- [`toggleLeftBtn`](index.html:884), [`toggleExplorerBtn`](index.html:885), [`toggleRightBtn`](index.html:886) 이 Chat 모드에서 어떻게 동작할지
- Chat 모드에서는 이 버튼들이 숨겨진 middle-panel 안에 있으므로 자연스럽게 접근 불가 → OK

### 3.2 (선택) Chat 모드 전용 툴바 추가

Chat 모드일 때 좌측 사이드바를 접을 수 있는 작은 햄버거 버튼을 채팅 영역 상단에 추가:

```html
<!-- Chat 모드에서만 보이는 햄버거 -->
<button class="chat-sidebar-toggle" id="chatSidebarToggle" 
  style="display:none;" title="사이드바 열기/닫기">☰</button>
```

---

## 4. 레이아웃 시각화

### Chat / 토론 모드

```
┌────────────┬─────────────────────────────────────┐
│ LEFT PANEL │        CHAT AREA (1fr)              │
│  280px     │                                     │
│            │  ┌─────────────────────────────┐    │
│ 🏠 세션    │  │  max-width: 768px           │    │
│ 💬 채팅    │  │  margin: 0 auto             │    │
│ ⏱️ 작업    │  │                             │    │
│ 🧩 스킬    │  │  사용자: 안녕하세요          │    │
│ 🧠 기억    │  │  AI: 안녕하세요! ...        │    │
│ 📂 작업공간│  │                             │    │
│ 👤 프로필  │  │  ┌─────────────────────┐    │    │
│ 📝 할일    │  │  │ 입력창...      [전송]│    │    │
│ ...        │  │  └─────────────────────┘    │    │
│            │  └─────────────────────────────┘    │
└────────────┴─────────────────────────────────────┘
   MIDDLE PANEL = 숨김 (display: none)
```

### Harness 모드

```
┌──────────┬──┬──────────────┬──┬──────────┐
│LEFT PANEL│R │ MIDDLE PANEL │R │  RIGHT   │
│ 280px    │E │   1fr        │E │  360px   │
│          │S │ 파일탐색기    │S │ ⚡Harness│
│          │Z │ 에디터       │Z │ 콘솔     │
│          │E │ CLI콘솔      │E │          │
└──────────┴──┴──────────────┴──┴──────────┘
```

---

## 5. 변경 파일 목록

| 파일 | 변경 내용 | 우선순위 |
|------|----------|---------|
| [`static/styles.css`](static/styles.css:238) | `.layout-chat` 클래스 추가, 채팅 중앙정렬 스타일 | 🔴 필수 |
| [`static/modules/harness.js`](static/modules/harness.js:95) | `switchMode()` 함수에 레이아웃 클래스 토글 추가 | 🔴 필수 |
| [`index.html`](index.html:1053) | (선택) 햄버거 버튼 추가 | 🟡 선택 |

---

## 6. 리스크 및 고려사항

1. **리사이저 충돌**: Chat 모드에서 resizer가 숨겨지므로 리사이저 이벤트 핸들러가 에러를 내지 않는지 확인
2. **브라우저 뷰**: Chat 모드에서 브라우저 뷰에 접근 불가 → 의도된 동작
3. **파일 탐색기**: Chat 모드에서 완전히 숨겨짐 → 필요하면 채팅 메시지에서 파일 경로 클릭 시 Harness 모드로 전환
4. **기존 `toggleRightBtn`**: 이 버튼이 Chat 모드에서 숨겨진 middle-panel 안에 있으므로 사용 불가 → 의도된 동작
5. **반응형**: 모바일(좁은 화면)에서는 Chat 모드가 기본이 되도록 미디어 쿼리 추가 고려

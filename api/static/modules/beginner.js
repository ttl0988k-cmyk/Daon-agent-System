// ── Beginner Mode: 대화형 마법사 + 레이아웃 전환 ──
// 오버레이 없이 실제 채팅창을 첫 화면으로 사용.
// - beginnerMode ON  : middle(탐색기/에디터)을 접고 채팅을 전면으로 (초보자 첫 화면)
// - beginnerMode OFF : 전체 IDE 레이아웃 (전문가 모드)
// - 카드 선택 → 채팅창 안에서 2~3단계 선택지 질문 → 브리프 자동 조립 → Harness 자동 발동

const BEGINNER_MODE_KEY = 'daon_beginner_mode'; // '1' = 초보자(채팅전면), '0' = 전문가(전체IDE)

// ── 마법사 플로우 정의 ──
// 각 카드는 steps(선택지 질문) + build(답변→상세 브리프) 로 구성.
const WIZARD_FLOWS = {
    app: {
        title: '🎯 앱 만들기',
        steps: [
            {
                q: '어떤 앱을 만들까요?', opts: [
                    { l: '✅ 할 일 관리 앱', v: '할 일(Todo) 관리 앱' },
                    { l: '🧮 계산기', v: '계산기 앱' },
                    { l: '📝 메모장', v: '메모장 앱' },
                    { l: '⏱️ 모도로 타이머', v: '뽀모도로 타이머 앱' },
                ]
            },
            {
                q: '어떤 느낌으로 만들까요?', opts: [
                    { l: '⬛ 다/깔끔', v: '다크 테마의 깔끔한 미니멀 디자인' },
                    { l: '⬜ 밝고 심플', v: '라이트 테마의 심플한 디자인' },
                    { l: '🌈 컬러풀', v: '밝고 컬러풀한 디자인' },
                ]
            },
            {
                q: '어디서 돌아가게 할까요?', opts: [
                    { l: '🌐 웹(브라우저)', v: 'HTML/CSS/JS 단일 파일 웹앱' },
                    { l: '📱 모바일', v: 'React Native 모바일 앱' },
                    { l: '🖥️ 데스크톱', v: 'Electron 데스크톱 앱' },
                ]
            },
        ],
        build: (a) => `${a[0]}을 만들어줘. 디자인 방향: ${a[1]}. 플랫폼: ${a[2]}. 핵심 기능 3~4개를 갖추고 바로 실행 가능한 완성된 코드로 만들어줘.`,
    },
    web: {
        title: '🌐 웹사이트 만들기',
        steps: [
            {
                q: '무슨 사이트를 만들까요?', opts: [
                    { l: '💼 포트폴리오', v: '개인 포트폴리오 사이트' },
                    { l: '🚀 제품 랜딩', v: '제품/서비스 소개 랜딩페이지' },
                    { l: '🍽️ 가게 소개', v: '카페/가게 소개 사이트' },
                    { l: '📰 블로그', v: '블로그/아티클 사이트' },
                ]
            },
            {
                q: '분위기는?', opts: [
                    { l: '⬛ 모던/다크', v: '모던한 다크 테마' },
                    { l: '🤍 깔끔/미니멀', v: '깔끔한 미니멀 화이트 테마' },
                    { l: '✨ 화려/그래픽', v: '화려한 그래픽 중심 디자인' },
                ]
            },
            {
                q: '필수 섹션은?', opts: [
                    { l: ' 기본 3종', v: '히어로/소개/연락처 섹션' },
                    { l: '🖼️ 갤러리 포함', v: '히어로/소개/갤러리/연락처 섹션' },
                    { l: '💰 가격표 포함', v: '히어로/기능/가격표/연락처 섹션' },
                ]
            },
        ],
        build: (a) => `${a[0]}을 만들어줘. 분위기: ${a[1]}. 구성: ${a[2]}. 반응형(모바일 대응) 단일 페이지 HTML/CSS/JS로 완성해줘.`,
    },
    agent: {
        title: '🤖 AI 에이전트 만들기',
        steps: [
            {
                q: '무엇을 도와주는 에이전트?', opts: [
                    { l: '📧 이메일 요약', v: '이메일을 요약해주는' },
                    { l: '📅 일정 정리', v: '일정을 정리해주는' },
                    { l: '🔍 리서치', v: '주제를 조사해 리포트해주는' },
                    { l: '💬 상담/비서', v: '질문에 답하는 개인 비서' },
                ]
            },
            {
                q: '결과물은 어떤 형태?', opts: [
                    { l: '🐍 Python 스립트', v: 'Python 스크립트' },
                    { l: '🌐 웹 챗봇', v: '웹 챗봇 UI' },
                    { l: '⚙️ API 서버', v: 'FastAPI 서버' },
                ]
            },
            {
                q: '난이도/범위는?', opts: [
                    { l: '🌱 데모(빠르게)', v: '최소 기능 데모' },
                    { l: '🛠️ 실사용', v: '에러 처리까지 갖춘 실사용 수준' },
                ]
            },
        ],
        build: (a) => `${a[0]} AI 에이전트를 만들어줘. 형태: ${a[1]}. 범위: ${a[2]}. 실행 방법(README 포함)까지 완성해줘.`,
    },
    youtube: {
        title: '📺 유튜브 자동화',
        steps: [
            {
                q: '어떤 자동화?', opts: [
                    { l: '✍️ 스크립트 작성', v: '영상 스크립트를 자동 작성하는' },
                    { l: '🖼️ 썸네일 문구', v: '썸네일 텍스트를 생성하는' },
                    { l: '📑 제목/설명', v: '제목/설명/해시태그를 생성하는' },
                    { l: '🔗 전체 파이프라인', v: '스크립트→썸네일→제목 전 과정을 잇는' },
                ]
            },
            {
                q: '장르/톤은?', opts: [
                    { l: '📚 정보/교육', v: '정보 전달/교육 톤' },
                    { l: '😄 엔터/유머', v: '엔터테인먼트/유머 톤' },
                    { l: '💼 비즈니스', v: '비즈니스/마케팅 톤' },
                ]
            },
            {
                q: '구현 형태?', opts: [
                    { l: '🐍 Python 스립트', v: 'Python 스크립트' },
                    { l: '🌐 웹 도구', v: '입력폼 있는 웹 도구' },
                ]
            },
        ],
        build: (a) => `${a[0]} 유튜브 자동화 도구를 만들어줘. 톤: ${a[1]}. 형태: ${a[2]}. 바로 돌릴 수 있게 완성해줘.`,
    },
};

const WIZARD_CARDS = [
    { key: 'app', icon: '🎯', label: '앱 만들기', sub: '모바일 / 데스크톱' },
    { key: 'web', icon: '🌐', label: '웹사이트 만들기', sub: '랜딩 / 포트폴리오' },
    { key: 'agent', icon: '🤖', label: 'AI 에이전트 만들기', sub: '자동화 / 챗봇' },
    { key: 'youtube', icon: '📺', label: '유튜브 자동화', sub: '스크립트 / 콘텐츠' },
];

// ── 스타일 1회 주입 ──
function _injectStyles() {
    if (document.getElementById('beginnerStyles')) return;
    const st = document.createElement('style');
    st.id = 'beginnerStyles';
    st.textContent = `
    .main-grid { transition: grid-template-columns .32s cubic-bezier(.4,0,.2,1); }
    .beginner-welcome-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:12px; }
    .beginner-wcard { background:var(--bg2); border:1px solid var(--border2); border-radius:12px; padding:16px 12px; cursor:pointer; text-align:center; transition:all .2s; }
    .beginner-wcard:hover { border-color:var(--accent); transform:translateY(-3px); box-shadow:0 8px 24px rgba(0,0,0,.3); }
    .beginner-wcard[disabled] { opacity:.45; cursor:default; transform:none; box-shadow:none; }
    .beginner-wcard .bw-ic { font-size:26px; }
    .beginner-wcard .bw-lb { font-size:13px; font-weight:600; color:var(--text); margin-top:6px; }
    .beginner-wcard .bw-sb { font-size:10px; color:var(--muted); margin-top:3px; }
    .beginner-choice-wrap { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    .beginner-choice { background:var(--bg3); border:1px solid var(--border2); color:var(--text); border-radius:10px; padding:9px 13px; font-size:13px; cursor:pointer; transition:all .15s; }
    .beginner-choice:hover:not([disabled]) { border-color:var(--accent); transform:translateY(-2px); }
    .beginner-choice[disabled] { opacity:.4; cursor:default; transform:none; }
    .beginner-wiz-q { font-size:14px; font-weight:600; color:var(--text); }
    .beginner-wiz-step { font-size:11px; color:var(--muted); margin-bottom:6px; }
  `;
    document.head.appendChild(st);
}

// ── 레이아웃 전환 ──
function enterBeginnerMode(animate) {
    if (typeof State !== 'undefined') {
        State.beginnerMode = true;
        State.leftPanelVisible = true; // 초보자도 세션 확인 가능
    }
    const btn = document.getElementById('showBeginnerBtn');
    if (btn) { btn.innerHTML = '🛠️ IDE 열기'; btn.title = '전체 IDE 화면 열기 (전문가 모드)'; }
    if (animate === false) {
        const g = document.querySelector('.main-grid');
        if (g) { const t = g.style.transition; g.style.transition = 'none'; _relayout(); requestAnimationFrame(() => { g.style.transition = t; }); }
    } else {
        _relayout();
    }
}

function exitBeginnerMode() {
    if (typeof State !== 'undefined') State.beginnerMode = false;
    const btn = document.getElementById('showBeginnerBtn');
    if (btn) { btn.innerHTML = '🌱 처음'; btn.title = '초보자 모드 (채팅 전면)'; }
    _relayout();
    // 슬라이드 후 에디터 재배치
    setTimeout(() => { if (typeof State !== 'undefined' && State.editor) State.editor.layout(); }, 360);
}

function toggleBeginnerMode() {
    const on = typeof State !== 'undefined' && State.beginnerMode;
    if (on) { exitBeginnerMode(); localStorage.setItem(BEGINNER_MODE_KEY, '0'); }
    else { enterBeginnerMode(true); localStorage.setItem(BEGINNER_MODE_KEY, '1'); _showWelcomeWizard(); }
}

function _relayout() { if (typeof window.updateLayout === 'function') window.updateLayout(); }

// ── 채팅창 내 버블 유틸 ──
function _chatBox() { return document.getElementById('chatMessages'); }

function _assistantBubble(html) {
    const box = _chatBox();
    if (!box) return null;
    const b = document.createElement('div');
    b.className = 'message-bubble assistant';
    b.innerHTML = html;
    box.appendChild(b);
    if (typeof scrollToChatBottom === 'function') scrollToChatBottom();
    return b;
}

function _userBubble(text) {
    const box = _chatBox();
    if (!box) return;
    const b = document.createElement('div');
    b.className = 'message-bubble user';
    b.textContent = text;
    box.appendChild(b);
    if (typeof scrollToChatBottom === 'function') scrollToChatBottom();
}

// ── 마법사 환영 (카드: 접었다 펼 수 있음) ──
function _showWelcomeWizard() {
    const box = _chatBox();
    if (!box) return;
    box.innerHTML = '';
    const cards = WIZARD_CARDS.map(c =>
        `<div class="beginner-wcard" data-key="${c.key}">
       <div class="bw-ic">${c.icon}</div>
       <div class="bw-lb">${c.label}</div>
       <div class="bw-sb">${c.sub}</div>
     </div>`).join('');
    const html =
        `<div style="font-size:22px;">🌱</div>
     <div style="font-size:16px; font-weight:700; margin:6px 0 2px;">DAON에 오신 걸 환영해요!</div>
     <div style="font-size:13px; color:var(--muted);">무엇을 만들어볼까요? 아래 입력창에 바로 적거나, 카드를 펼쳐서 골라보세요.</div>
     <div class="beginner-cards-toggle" style="display:inline-flex; align-items:center; gap:6px; margin-top:12px; cursor:pointer; user-select:none; font-size:12px; font-weight:600; color:var(--accent);">
       <span class="bct-arrow">▸</span> 빠른 시작 카드
     </div>
     <div class="beginner-welcome-cards" style="display:none;">${cards}</div>
     <div style="font-size:11px; color:var(--muted); margin-top:12px;">💡 예: 블로그 API 만들어줘, 포트폴리오 웹사이트 만들어줘</div>`;
    const bubble = _assistantBubble(html);
    if (!bubble) return;
    // 카드 접기/펼치기 토글
    const toggle = bubble.querySelector('.beginner-cards-toggle');
    const grid = bubble.querySelector('.beginner-welcome-cards');
    const arrow = bubble.querySelector('.bct-arrow');
    if (toggle && grid) {
        toggle.addEventListener('click', () => {
            const open = grid.style.display !== 'none';
            grid.style.display = open ? 'none' : 'grid';
            if (arrow) arrow.textContent = open ? '▸' : '▾';
        });
    }
    // 카드 클릭 → 마법사 시작
    bubble.querySelectorAll('.beginner-wcard').forEach(el => {
        el.addEventListener('click', () => {
            bubble.querySelectorAll('.beginner-wcard').forEach(x => x.setAttribute('disabled', ''));
            startWizard(el.getAttribute('data-key'));
        });
    });
}

// ── 마법사 시작/진행 ──
function startWizard(key) {
    const flow = WIZARD_FLOWS[key];
    if (!flow) return;
    _userBubble(flow.title + ' 시작');
    const answers = [];
    renderStep(key, 0, answers);
}

function renderStep(key, idx, answers) {
    const flow = WIZARD_FLOWS[key];
    const step = flow.steps[idx];
    const opts = step.opts.map((o, i) =>
        `<button class="beginner-choice" data-i="${i}">${o.l}</button>`).join('');
    const html =
        `<div class="beginner-wiz-step">질문 ${idx + 1} / ${flow.steps.length}</div>
     <div class="beginner-wiz-q">${step.q}</div>
     <div class="beginner-choice-wrap">${opts}</div>`;
    const bubble = _assistantBubble(html);
    if (!bubble) return;
    bubble.querySelectorAll('.beginner-choice').forEach(btn => {
        btn.addEventListener('click', () => {
            bubble.querySelectorAll('.beginner-choice').forEach(x => x.setAttribute('disabled', ''));
            const i = parseInt(btn.getAttribute('data-i'), 10);
            answers[idx] = step.opts[i].v;
            _userBubble(step.opts[i].l);
            if (idx + 1 < flow.steps.length) renderStep(key, idx + 1, answers);
            else finishWizard(key, answers);
        });
    });
}

function finishWizard(key, answers) {
    const flow = WIZARD_FLOWS[key];
    const brief = flow.build(answers);
    // 자율 플래닝(Harness) 자동 ON
    const t = document.getElementById('planningModeToggle');
    if (t) t.checked = true;
    // IDE 슬라이드 인 후 전송
    exitBeginnerMode();
    localStorage.setItem(BEGINNER_MODE_KEY, '0');
    const inp = document.getElementById('promptInput');
    if (inp) inp.value = brief;
    setTimeout(() => { if (typeof sendPrompt === 'function') sendPrompt(); }, 360);
}

// ── 초기화 (모든 스크립트 로드 후) ──
function _initBeginner() {
    _injectStyles();
    // 앱 시작 시 무조건 초보자 모드 (localStorage 이전 값 무시)
    enterBeginnerMode(false);
    const box = _chatBox();
    if (box && box.children.length === 0) _showWelcomeWizard();
}

if (document.readyState === 'complete') _initBeginner();
else window.addEventListener('load', _initBeginner);

// 헤더 버튼 등에서 호출 가능하도록 전역 노출
window.toggleBeginnerMode = toggleBeginnerMode;
window.showBeginnerOverlay = function () { enterBeginnerMode(true); localStorage.setItem(BEGINNER_MODE_KEY, '1'); _showWelcomeWizard(); };

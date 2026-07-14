# Creative Director + Reference Library 설계 문서

> **Daon Agent System** — 디자인 레퍼런스 수집·분석·적용 프레임워크  
> Version: 1.0 | Status: Draft | Author: raon

---

## 1. 개요

### 1.1 문제 정의

AI 에이전트가 생성하는 UI/UX 디자인은 "AI틱한(-looking)" 결과물이 되는 경향이 있다:
- 모든 디자인이 동일한 템플릿 패턴으로 수렴
- 실제 디자이너의 의사결정 과정(DNA)을 반영하지 못함
- 레퍼런스 없이 학습 데이터에만 의존 → 진부한 결과물

### 1.2 솔루션

**Reference Library + Creative Director** 시스템은:

1. **Style Card** — 단순 이미지 스크랩이 아닌 **메타데이터 기반 디자인 레퍼런스** (색상, 타이포그래피, 레이아웃, 애니메이션, DO/DON'T)
2. **Creative Director Agent** — 5단계 프로세스로 의도 분석 → 레퍼런스 수집 → 자동 평가 → 디자인 DNA 추출 → 디자인 브리프 생성
3. **MCP 파이프라인** — Playwright·GitHub·Figma MCP를 통한 실시간 외부 레퍼런스 수집
4. **Demo→Style Card** — 사용자 행동 기반 자동 스타일 추출 (기존 Demo→Skill 패턴과 동일한 아키텍처)

---

## 2. 핵심 개념: Style Card

### 2.1 정의

Style Card는 **이미지가 아닌 구조화된 메타데이터**로 디자인 레퍼런스를 표현한다. 마치 포켓몬 카드처럼 각 카드는 하나의 디자인 스타일을 완결적으로 표현하며, 컴포넌트 단위로 매칭된다.

### 2.2 Style Card 데이터 구조

```yaml
# Style Card Schema v1.0
style_card:
  # ── 식별자 ──
  id: "hero-glassmorphism-tech-01"
  name: "Glassmorphism Tech Hero"
  version: "1.0.0"
  created: "2026-07-13"
  source_url: "https://dribbble.com/shots/xxxxx"  # 원본 레퍼런스 출처
  source_type: "mcp_collected"  # mcp_collected | user_submitted | demo_extracted

  # ── 분류 ──
  category: "hero"               # hero, navbar, gallery, features, pricing, cta, footer, card, modal, ...
  sub_category: "tech-saas"      # 세부 장르
  tags:
    - glassmorphism
    - dark-theme
    - gradient
    - geometric
    - futuristic

  # ── 디자인 DNA ──
  design_dna:
    # 색상 시스템
    colors:
      primary: "#6366F1"         # Indigo-500
      accent: "#06B6D4"          # Cyan-500
      background: "#0F172A"      # Slate-900
      surface: "rgba(30,41,59,0.6)"
      text_primary: "#F8FAFC"
      text_secondary: "#94A3B8"
      palette_name: "Midnight Aurora"
      palette_harmony: "complementary"  # complementary | analogous | triadic | monochromatic

    # 타이포그래피
    typography:
      heading_font: "Inter"
      body_font: "Inter"
      mono_font: "JetBrains Mono"
      scale: "minor-third"       # major-second | minor-second | major-third | perfect-fourth
      heading_weight: 700
      body_weight: 400
      letter_spacing_heading: "-0.03em"
      line_height_body: 1.6

    # 레이아웃
    layout:
      grid: "12-column"          # 12-column | bento | masonry | asymmetric | single-column | split
      max_width: "1280px"
      padding_desktop: "80px"
      padding_mobile: "24px"
      alignment: "center"        # center | left | right | space-between
      glass_effect: true
      border_radius: "16px"
      backdrop_blur: "20px"

    # 애니메이션
    animation:
      entrance: "fade-up"        # fade-up | scale-in | slide-left | reveal | stagger
      hover: "scale-105 + glow"
      scroll: "parallax"
      page_transition: "crossfade"
      duration_base: "400ms"
      easing: "cubic-bezier(0.16, 1, 0.3, 1)"  # spring-out
      motion_intensity: 3        # 1-10 scale

    # 공간감
    spacing:
      density: "airy"            # compact | moderate | airy | luxurious
      section_gap: "120px"
      element_gap: "24px"

  # ── 구성 규칙 ──
  composition:
    hero_structure:
      - "Badge (상단 중앙, pill)"
      - "Heading H1 (2줄, gradient text)"
      - "Subtitle (1줄, muted)"
      - "CTA Button (glass, glow)"
      - "Dashboard mockup (floating, 3D rotate)"

  # ── DO / DON'T ──
  guidelines:
    do:
      - "배경에 CSS grid 오버레이로 깊이감 추가"
      - "텍스트에 gradient 적용 시 clip-path 사용 필수"
      - "CTA에 backdrop-blur로 glass 효과 유지"
      - "모바일에서는 grid 대신 flex-column으로 전환"
    dont:
      - "순수 흰색(#FFF) 텍스트 사용 금지 — 항상 tint"
      - "박스 쉐도우 과도하게 쓰지 말 것"
      - "10px 이하로 border-radius 축소 금지"
      - "배경에 단색 사용 금지 — 항상 gradient나 mesh 활용"

  # ── 호환성 ──
  compatible_with:
    - "taste-design"
    - "premium-ui"
  conflicts_with:
    - "minimalist-ui"
    - "brutalist-ui"

  # ── 평가 ──
  evaluation:
    score: 8.5                    # 1-10
    originality: 8
    accessibility: 7
    responsiveness: 9
    trend_relevance: "high"
    reviewed: true
    review_date: "2026-07-13"
```

### 2.3 Style Card 저장 경로

```
~/.hermes/profiles/raon/
└── references/
    ├── hero/
    │   ├── glassmorphism-tech-01.yaml
    │   ├── editorial-minimal-02.yaml
    │   └── brutalist-tactical-03.yaml
    ├── navbar/
    │   ├── sticky-glass-01.yaml
    │   └── sidebar-elegant-02.yaml
    ├── gallery/
    ├── features/
    ├── pricing/
    ├── cta/
    ├── footer/
    ├── card/
    ├── modal/
    └── index.yaml             # 전체 Style Card 인덱스 (빠른 검색용)
```

---

## 3. Creative Director Agent

### 3.1 4인의 페르소나

Creative Director는 단일 에이전트가 아닌 **4개의 내부 페르소나**로 구성된다:

| 페르소나 | 역할 | Trigger |
|---------|------|---------|
| **🎨 Art Director** | 시각적 방향 결정, 스타일 평가, 미학적 판단 | 디자인 품질 평가, 스타일 매칭 |
| **🔍 UX Researcher** | 사용자 니즈 분석, 접근성 검증, 사용성 평가 | 의도 분석, DO/DON'T 추출 |
| **📚 Design Librarian** | Style Card 검색·분류·태깅·관리 | 레퍼런스 검색, 인덱싱 |
| **🎬 Creative Director** | 최종 디자인 브리프 생성, 전체 조율 | 최종 Design Brief 출력 |

### 3.2 5단계 프로세스

```
사용자 요청
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 1: Intent Analysis (UX Researcher + Art Director)     │
├─────────────────────────────────────────────────────────────┤
│ · 사용자 요청에서 디자인 의도 추출                            │
│ · 타겟 오디언스, 브랜드 톤, 기능적 요구사항 파악              │
│ · 검색 키워드 생성 (예: "glassmorphism hero saas dark")      │
│ · DESIGN_VARIANCE / MOTION_INTENSITY / VISUAL_DENSITY 결정   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 2: Reference Collection (Design Librarian + MCP)      │
├─────────────────────────────────────────────────────────────┤
│ · Local Reference Library 먼저 검색 (index.yaml → Semantic) │
│ · MCP 파이프라인으로 외부 실시간 수집:                        │
│   ├── Playwright MCP → Dribbble, Behance, Awwwards 스크린샷 │
│   ├── GitHub MCP → 디자인 시스템 레포 검색 (shadcn, Radix)   │
│   └── Brave Search MCP → 최신 디자인 트렌드 글 검색          │
│ · 수집된 레퍼런스 → Style Card 메타데이터 추출 (LLM)         │
│ · 중복 제거 및 태깅 자동화                                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 3: Auto-Evaluation (Art Director)                     │
├─────────────────────────────────────────────────────────────┤
│ · 각 Style Card에 점수 부여 (originality, accessibility,    │
│   responsiveness, trend_relevance)                          │
│ · 사용자 의도와의 정합성 평가                                 │
│ · Top 3~5 Style Card 선별                                    │
│ · 충돌 검사 (compatible_with / conflicts_with)               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 4: Design DNA Extraction (Art Director + UX Research) │
├─────────────────────────────────────────────────────────────┤
│ · 선별된 Style Card들의 공통 패턴 추출                        │
│ · 컬러 팔레트, 타이포그래피, 레이아웃, 애니메이션 통합        │
│ · DO/DON'T 규칙 병합 및 우선순위화                            │
│ · 최종 Design DNA YAML 생성                                  │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 5: Design Brief Generation (Creative Director)        │
├─────────────────────────────────────────────────────────────┤
│ · 최종 Design Brief (마크다운) 생성                           │
│ · 컴포넌트별 스타일 가이드 포함                               │
│ · 구현 우선순위 및 체크리스트                                  │
│ · 실제 코드 생성을 위한 구체적 지침                           │
│ · taste-design 스킬과 연동 가능한 형식으로 출력               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
[Design Brief] → [Frontend Agent] → [구현 코드]
```

### 3.3 Design Brief 예시 출력

```markdown
## 🎬 Creative Director Design Brief

### Mission
"AI 스타트업 랜딩 페이지 — 미래지향적이면서도 신뢰감 있는 첫인상"

### Design DNA
- **Palette**: Midnight Aurora (Indigo + Cyan on Dark Slate)
- **Typography**: Inter (Heading 700, Body 400) / Minor Third Scale
- **Layout**: 12-column grid, 1280px max-width, airy spacing
- **Animation**: Spring-out easing, fade-up entrance, parallax scroll
- **Glass Effect**: backdrop-blur(20px) on surface elements

### Applied References
1. glassmorphism-tech-01 (Hero) — Score: 8.5/10
2. editorial-minimal-02 (Typography) — Score: 8.2/10
3. sticky-glass-01 (Navbar) — Score: 7.9/10

### Key Rules
- ✅ 순수 흰색(#FFF) 사용 금지, 항상 tint 처리
- ✅ 모든 CTA에 spring-bounce feedback 적용
- ✅ 모바일에서 grid → flex-column 전환
- ❌ box-shadow 중첩 금지
- ❌ 단색 배경 금지

### Implementation Priority
1. Hero Section → glassmorphism tech style
2. Navbar → sticky glass with blur
3. Features Grid → bento layout
4. CTA Section → glow + gradient
5. Footer → minimal dark
```

---

## 4. MCP 파이프라인 — 외부 레퍼런스 수집

### 4.1 아키텍처

```
Creative Director Agent
    │
    ├── Local 검색 (SemanticSkillRetriever 재활용)
    │   └── ~/.hermes/profiles/raon/references/index.yaml
    │
    ├── MCP: Playwright (브라우저 기반 레퍼런스 수집)
    │   ├── browser_navigate → Dribbble search
    │   ├── browser_take_screenshot → Hero section 캡처
    │   ├── browser_click → 페이지 네비게이션
    │   └── browser_snapshot → 접근성 트리 분석
    │
    ├── MCP: GitHub (디자인 시스템 검색)
    │   ├── search_repositories → "ui design system"
    │   └── get_file_contents → README, design tokens
    │
    └── MCP 추가 확장 가능
        ├── Brave Search MCP → 트렌드 검색
        ├── Figma MCP → 커뮤니티 파일 직접 접근
        └── Puppeteer MCP → Headless 브라우저
```

### 4.2 MCP 호출 코드 패턴 (Creative Director 내부)

```python
# Creative Director가 MCP를 통해 레퍼런스를 수집하는 내부 로직 (의사코드)

def collect_references(search_keywords: list[str], components: list[str]) -> list[StyleCard]:
    mcp = get_mcp_manager()
    style_cards = []

    for component in components:
        # 1. 로컬 레퍼런스 라이브러리 먼저 검색
        local_cards = search_local_references(component, search_keywords)

        # 2. 부족하면 MCP로 외부 수집
        if len(local_cards) < 3:
            external_cards = collect_from_mcp(component, search_keywords, mcp)
            local_cards.extend(external_cards)

        style_cards.extend(local_cards)

    return style_cards

def collect_from_mcp(component, keywords, mcp) -> list[StyleCard]:
    cards = []

    # Playwright MCP — Dribbble 검색
    mcp.call_tool('playwright', 'browser_navigate', {
        'url': f'https://dribbble.com/search/{component}-{keywords[0]}'
    })
    screenshot = mcp.call_tool('playwright', 'browser_take_screenshot', {})

    # LLM으로 스크린샷 → Style Card 메타데이터 추출
    card = llm_extract_style_card(screenshot, component)
    cards.append(card)

    return cards
```

### 4.3 필요한 신규 MCP 서버

기존 [`MCP_PRESETS`](api/mcp_client.py:486)에 추가할 서버:

```python
# api/mcp_client.py — MCP_PRESETS에 추가
'brave_search': {
    'label': '🔍 Brave Search MCP',
    'command': 'npx',
    'args': ['-y', '@anthropic/mcp-server-brave-search'],
    'env': {'BRAVE_API_KEY': '${BRAVE_API_KEY}'},
    'description': 'Brave Search API를 통한 웹 검색 (디자인 트렌드, 레퍼런스 검색)',
},
'figma': {
    'label': '🎨 Figma MCP',
    'command': 'npx',
    'args': ['-y', '@anthropic/mcp-server-figma'],
    'description': 'Figma 디자인 파일 및 커뮤니티 리소스 접근',
},
```

---

## 5. 듀얼 모드 적용 전략

> "모든건...일반 에이전트모드와..다이나믹 하네스모드 둘다 적용이 되어야해."

### 5.1 모드별 역할

| 모드 | Creative Director 역할 | 활성화 방식 |
|------|----------------------|------------|
| **일반 에이전트 모드** | SKILL.md로 프롬프트에 주입 | 사용자 요청 시 `creative-director` 스킬 자동 발동 |
| **다이나믹 하네스 모드** | DAG 노드로 Planner에 의해 배치 | `get_integrated_persona()` + 컴파일러 키워드 매핑 |

### 5.2 일반 에이전트 모드 상세

```
사용자: "AI 스타트업 랜딩페이지 만들어줘"

→ skill_matches_platform() 통과
→ _find_all_skills() → creative-director SKILL.md 로드
→ SemanticSkillRetriever.retrieve("AI 스타트업 랜딩페이지") → 관련 Style Card 검색
→ Creative Director SKILL.md가 프롬프트에 주입됨
→ 에이전트가 5단계 프로세스 수행 후 Design Brief + 코드 생성
```

**핵심 파일**: `~/.hermes/profiles/raon/skills/creative/creative-director/SKILL.md`

이 SKILL.md는:
- [`purpose`](skills/taste-design.md:9)에 5단계 프로세스 명시
- [`graph_requires`](skills/taste-design.md:14)에 MCP 서버 필요조건 명시
- [`when_to_use`](skills/taste-design.md:11)에 "디자인 요청", "UI 생성" 등 트리거 명시
- [`graph_compatible`](skills/taste-design.md:16)에 taste-design, premium-ui 등과 호환 명시

### 5.3 다이나믹 하네스 모드 상세

```
Planner.plan("AI 스타트업 랜딩페이지 만들어줘")
    │
    ▼
DAG Plan:
    Node 0: creative_director  → Design Brief 생성 (5단계)
    Node 1: frontend_agent     → Design Brief 기반 코드 구현
    Node 2: code_reviewer      → 품질 검증
    │
    Edge: 0 → 1 → 2
    │
    ▼
Compiler.compile() → 각 노드에 get_integrated_persona() 적용
    │
    creative_director 노드:
    · SOUL.md 로드 (없으면 role manual)
    · creative-director SKILL.md 컨텍스트 주입
    · MCP 도구 목록 제공
    · local references/*.yaml 파일 목록 제공
    │
    ▼
ParallelRunner → HermesDynamicRunner.run()
```

**핵심 변경점** ([`api/dynamic/compiler.py`](api/dynamic/compiler.py:18)):

```python
# get_integrated_persona() 함수에 creative_director 키워드 매핑 추가
def get_integrated_persona(agent_name: str, agent_role: str) -> str:
    # ... 기존 코드 ...
    
    if agent_name in ("creative_director", "creative-director", "creative director"):
        # 4가지 페르소나 통합 프롬프트 로드
        # Style Card 인덱스 로드
        # MCP 도구 목록 제공
        return CREATIVE_DIRECTOR_PERSONA_PROMPT
```

---

## 6. Semantic Style Card 검색

### 6.1 기존 인프라 재활용

[`SemanticSkillRetriever`](api/dynamic/skill_retriever.py:161)와 [`KeywordEmbeddingBackend`](api/dynamic/skill_retriever.py:31)를 Style Card 검색에도 동일하게 사용:

```python
class StyleCardRetriever:
    """Style Card 전용 시맨틱 검색기 (SkillRetriever와 동일 패턴)"""
    
    def __init__(self):
        self.backend = KeywordEmbeddingBackend()
        self._index = {}  # style_card_id → TF vector
    
    def build_index(self, references_dir: Path):
        """모든 Style Card를 로드하여 TF-IDF 인덱스 구축"""
        for yaml_file in references_dir.rglob("*.yaml"):
            if yaml_file.name == "index.yaml":
                continue
            card = yaml_load(yaml_file.read_text(encoding='utf-8'))
            # tags + name + category + guidelines.do + guidelines.dont → 하나의 문서로 토큰화
            doc = f"{card['name']} {' '.join(card['tags'])} {card['category']} ..."
            self._index[card['id']] = self.backend.embed(doc)
    
    def retrieve(self, query: str, top_k: int = 5) -> list[StyleCard]:
        """의도 분석 결과를 쿼리로 가장 유사한 Style Card 검색"""
        query_vec = self.backend.embed(query)
        scores = []
        for card_id, card_vec in self._index.items():
            score = self._cosine_similarity(query_vec, card_vec)
            scores.append((card_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [load_style_card(card_id) for card_id, _ in scores[:top_k]]
```

### 6.2 검색 인덱스 파일

`~/.hermes/profiles/raon/references/index.yaml`:

```yaml
# 자동 생성 — Style Card가 추가/수정될 때마다 재생성
version: "1"
last_updated: "2026-07-13T17:30:00+09:00"
total_cards: 12

# 카테고리별 인덱스
categories:
  hero: [glassmorphism-tech-01, editorial-minimal-02, brutalist-tactical-03]
  navbar: [sticky-glass-01, sidebar-elegant-02]
  gallery: []
  features: [bento-grid-01]
  pricing: []
  cta: [glow-gradient-01]
  footer: [minimal-dark-01]

# 태그 클라우드 (검색 가중치 부여용)
tag_weights:
  glassmorphism: 3
  dark-theme: 5
  minimal: 4
  brutalist: 2
  editorial: 3
  futuristic: 4
```

---

## 7. Demo → Style Card 파이프라인

### 7.1 기존 Demo → Skill 패턴 미러링

[`api/dynamic/skill_extractor.py`](api/dynamic/skill_extractor.py:48)의 `_extract_and_save_skill()`과 동일한 패턴으로 `_extract_and_save_style_card()` 구현:

```python
# 신규: api/dynamic/style_card_extractor.py

def _extract_and_save_style_card(
    url: str, 
    component: str, 
    screenshot_path: str, 
    session_id: str
) -> None:
    """
    Background task to distill a Style Card from a user-collected reference.
    Mirror of _extract_and_save_skill() but for visual design references.
    """
    
    def _worker():
        system_instruction = (
            "You are the Design Librarian persona. Analyze this screenshot "
            "and extract structured design metadata following the Style Card "
            "schema. Output ONLY valid YAML matching the schema.\n\n"
            f"Component: {component}\n"
            f"Source URL: {url}\n"
        )
        
        # LLM 호출 → Style Card YAML 추출
        result = _call_direct(system_instruction, screenshot_path)
        
        # 저장
        card = yaml_load(result)
        card_id = f"{component}-{_slugify(card['name'])}-{_short_uuid()}"
        
        ref_dir = get_references_dir() / component
        ref_dir.mkdir(parents=True, exist_ok=True)
        
        yaml_path = ref_dir / f"{card_id}.yaml"
        yaml_path.write_text(result, encoding='utf-8')
        
        # 인덱스 재생성
        rebuild_reference_index()
    
    threading.Thread(target=_worker, daemon=True).start()
```

### 7.2 사용자 워크플로우

```
사용자가 Playwright MCP로 Dribbble 페이지 방문
    │
    ▼
"이 Hero 디자인을 Style Card로 저장해줘"
    │
    ▼
Playwright MCP → browser_take_screenshot
    │
    ▼
_extract_and_save_style_card() 백그라운드 실행
    │
    ▼
LLM이 스크린샷 분석 → Design DNA 추출 → YAML 저장
    │
    ▼
references/index.yaml 자동 갱신
    │
    ▼
다음 Creative Director 실행 시 새 Style Card 활용 가능
```

---

## 8. 구현 로드맵 (3 Phase)

### Phase 1: 데이터 기반 구축

| 작업 | 파일 | 설명 |
|------|------|------|
| 1.1 | `api/style_card.py` (신규) | StyleCard 데이터 클래스, YAML 직렬화, 검증 |
| 1.2 | `api/skill_registry.py` | `SkillEntry`에 `style_card_refs` 필드 추가 |
| 1.3 | `~/.hermes/profiles/raon/references/` | 디렉토리 구조 생성 + `index.yaml` |
| 1.4 | `api/dynamic/style_card_retriever.py` (신규) | `StyleCardRetriever` (SemanticSkillRetriever 미러링) |
| 1.5 | 샘플 Style Card 3개 | hero, navbar, cta 각 1개씩 수동 작성 |

### Phase 2: Creative Director Agent

| 작업 | 파일 | 설명 |
|------|------|------|
| 2.1 | `skills/creative-director.md` | Creative Director SKILL.md (프로젝트 스킬) |
| 2.2 | `~/.hermes/profiles/raon/skills/creative/creative-director/SKILL.md` | 프로필 스킬 복사본 |
| 2.3 | `api/dynamic/compiler.py` | `get_integrated_persona()`에 `creative_director` 매핑 추가 |
| 2.4 | `api/mcp_client.py` | Brave Search + Figma MCP 프리셋 추가 |

### Phase 3: Demo → Style Card + 통합

| 작업 | 파일 | 설명 |
|------|------|------|
| 3.1 | `api/dynamic/style_card_extractor.py` (신규) | `_extract_and_save_style_card()` |
| 3.2 | `api/routes/style_card_routes.py` (신규) | `/api/style-cards` CRUD 엔드포인트 |
| 3.3 | `api/routes/__init__.py` | Style Card 라우트 등록 |
| 3.4 | `static/modules/references.js` (신규) | Reference Library UI 패널 |
| 3.5 | 통합 테스트 | 일반 모드 + 하네스 모드 모두 검증 |

---

## 9. 파일 구조 (최종)

```
c:/daon/Daon agent System/
├── api/
│   ├── dynamic/
│   │   ├── skill_extractor.py         # 기존 (Demo→Skill)
│   │   ├── skill_retriever.py         # 기존 (시맨틱 스킬 검색)
│   │   ├── style_card_extractor.py    # [신규] Demo→StyleCard
│   │   └── style_card_retriever.py    # [신규] 시맨틱 StyleCard 검색
│   ├── routes/
│   │   ├── __init__.py                # [수정] Style Card 라우트 등록
│   │   └── style_card_routes.py       # [신규] Style Card API
│   ├── skill_registry.py              # [수정] SkillEntry 확장
│   ├── style_card.py                  # [신규] StyleCard 데이터 클래스
│   └── mcp_client.py                  # [수정] Brave Search/Figma 프리셋
│
├── skills/
│   └── creative-director.md           # [신규] 프로젝트 스킬
│
└── ~/.hermes/profiles/raon/
    ├── skills/creative/
    │   └── creative-director/
    │       └── SKILL.md               # [신규] 프로필 스킬
    └── references/                    # [신규] Reference Library
        ├── index.yaml
        ├── hero/
        │   └── glassmorphism-tech-01.yaml
        ├── navbar/
        ├── gallery/
        ├── features/
        ├── pricing/
        ├── cta/
        ├── footer/
        └── ...
```

---

## 10. 설계 원칙 요약

| 원칙 | 적용 |
|------|------|
| **기존 인프라 재활용** | SkillRegistry, SemanticRetriever, MCPManager, Demo→Skill 패턴 |
| **듀얼 모드** | SKILL.md (일반) + DAG 노드 (하네스) 동시 지원 |
| **메타데이터 중심** | 이미지 저장이 아닌 구조화된 Design DNA |
| **MCP 네이티브** | 외부 수집은 100% MCP 파이프라인 |
| **점진적 확장** | 3 Phase로 나누어 검증하며 구현 |
| **프로필 스코프** | 모든 데이터는 활성 프로필(`raon`) 아래에 저장 |

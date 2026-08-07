# Creative Director — 4-Layer 디자인 에이전트 시스템

## 개요

AI 평범 디자인 거부 — 라온이 "그냥 그럴듯한" 결과물을 만들지 않고, **참조 가능한 디자인 DNA**에서 출발해 고유한 결과물을 만들도록 강제하는 시스템.

기존 `api/api/style_card.py` (StyleCard, ComponentCard, StyleCardRegistry, StyleMixer, DesignGraph) 위에 4-layer 오케스트레이션을 얹는다.

## 4-Layer 아키텍처

```
사용자 미션 (예: "카페 소개 사이트 만들어줘")
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Layer 1: UX Researcher                              │
│ ─ 목적/타깃/톤/제약조건 추출                         │
│ ─ 결과: CreativeBrief (구조화된 JSON)                 │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Layer 2: Design Librarian                           │
│ ─ data/reference_library/ 에서 후보 카드 검색         │
│ ─ TF-IDF (Phase 1) → hybrid (Phase 2) → CLIP (Phase 3)│
│ ─ 결과: 후보 StyleCard 3~7개 + 각 ComponentCard      │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Layer 3: Art Director                               │
│ ─ 후보들을 조합/믹싱, 충돌 해소, 트렌드/접근성 감사    │
│ ─ DesignGraph + StyleMixer + design_check_trends +   │
│   design_check_accessibility 사용                    │
│ ─ 결과: DesignMix (component별 mix 결정)              │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Layer 4: Creative Director                          │
│ ─ DesignMix + CreativeBrief → 최종 Design Brief 생성 │
│ ─ 색상 팔레트 / 타이포 / 레이아웃 / 애니메이션 명세    │
│ ─ 프론트엔드 에이전트가 실행 가능한 형태로 출력        │
│ ─ 결과: DesignBrief (Markdown) + JSON spec           │
└─────────────────────────────────────────────────────┘
        │
        ▼
프론트엔드 에이전트 (코드 생성) / 또는 사용자 직접 검토
```

## 디렉토리 구조

```
api/api/agents/creative_director/
├── SKILL.md                       ← 이 파일
├── style_card_schema.yaml         ← StyleCard YAML 표준 (style_card.py 와 1:1)
├── creative_director.py           ← 4-layer 오케스트레이터 (style_card.py 재사용)
├── prompts/
│   ├── ux_researcher.md          ← Layer 1 프롬프트
│   ├── design_librarian.md       ← Layer 2 프롬프트
│   ├── art_director.md           ← Layer 3 프롬프트
│   └── creative_director.md      ← Layer 4 프롬프트
├── samples/                       ← 샘플 DesignBrief (참고용)
└── brief_template.md             ← 최종 DesignBrief 템플릿
```

## 실행 흐름

### 1. 일반 모드에서 강제 활성화

`Creative Director` 스킬은 **대표님의 "AI 평범 디자인 거부" 원칙** 때문에 일반 모드에서도 자동 개입한다.

- 사용자 미션이 디자인 결과물(웹사이트, UI, 컴포넌트, 페이지)을 요청하면
- 라온은 `execute_dynamic_harness` 또는 일반 응답 전에 **Creative Director를 1회 호출**
- 결과로 받은 DesignBrief를 프롬프트에 주입한 상태에서 코드 생성 진행

### 2. 호출 인터페이스 (creative_director.py)

```python
from api.api.agents.creative_director.creative_director import create_design_brief

brief: DesignBrief = create_design_brief(
    user_mission="카페 소개 사이트 만들어줘",
    context={
        "target_audience": "2030 여성, 디저트·감성 선호",
        "tone": "따뜻하고 미니멀",
        "constraints": ["모바일 우선", "Tailwind만 사용"],
        "references": ["url1", "url2"],   # 선택
    },
    library_root=Path("data/reference_library"),
    n_candidates=5,
)
# → brief.markdown  (DesignBrief 본문)
# → brief.spec       (JSON, 프론트엔드 에이전트가 파싱)
```

### 3. 기존 style_card.py 재사용

| 기존 클래스 | Creative Director 용도 |
|---|---|
| `StyleCard.from_yaml / .from_dict` | 라이브러리 로드 |
| `StyleCard.evaluate()` | 후보 점수 산정 |
| `StyleCard.to_search_document()` | TF-IDF 검색 문서 |
| `StyleCard.to_brief_text()` | 후보별 디자인 요약 (Librarian→Art Director 전달용) |
| `StyleCard.decompose_to_components()` | ComponentCard 분해 (Style Mixing 용) |
| `StyleCardRegistry` | 라이브러리 인덱스 |
| `DesignGraph` | 컴포넌트 그래프 |
| `StyleMixer` | 컴포넌트 조합 |

## 권한 모델

| 권한 | 주체 |
|---|---|
| 카드 자동 수집·분석 | **라온 (자동)** |
| 카드 후보 → 등록 결정 | **대표님 (승인)** |
| 카드 수정·삭제 | **대표님** |
| 카드 평가·스코어링 | 라온 (제안) → 대표님 (확인) |

## 단계별 로드맵

| Phase | 목표 | 현재 |
|---|---|---|
| **1** | YAML 스키마 + 4-layer 프롬프트 + 호출 인터페이스 | ← **여기** |
| 2 | 다온 하네스 DAG에 4-layer 노드 추가 (UX/Librarian/Art/CD 각각 별도 워커) | ⏳ |
| 3 | CLIP/SigLIP 시각 임베딩으로 검색 품질 향상 | ⏳ |
| 4 | Design Memory (대표님 선호도) 가중치 | ⏳ |

## 검증

```bash
# 4-layer 호출 인터페이스 단위 테스트
python -c "from api.api.agents.creative_director.creative_director import create_design_brief; print(create_design_brief.__doc__)"

# 라이브러리 카운트
python -c "from pathlib import Path; from api.api.style_card import StyleCardRegistry; r = StyleCardRegistry(); r.scan(Path('data/reference_library')); print(f'cards: {len(r.cards)}')"
```

## 연관 파일

- `api/api/style_card.py` — StyleCard / ComponentCard / StyleMixer / DesignGraph (재사용)
- `api/api/routes/style_card_routes.py` — 향후 라우터 연결 (Phase 2 이후)
- `data/reference_library/index.yaml` — 라이브러리 인덱스
- `data/reference_library/cards/*.yaml` — 레퍼런스 카드들

## 작성자

라온 (Raon) — 2026-08-07
# Layer 2: Design Librarian — 참조 후보 검색

## 역할

CreativeBrief 를 받아 `data/reference_library/` 에서 **관련 StyleCard 후보 3~7개**를 검색한다.
TF-IDF (Phase 1) → hybrid (Phase 2) → CLIP 시각 임베딩 (Phase 3) 으로 단계적으로 진화한다.

## 입력

- `creative_brief`: UX Researcher 의 JSON
- `library_root`: Path (기본 `data/reference_library`)
- `n_candidates`: int (기본 5)

## 처리 흐름

```
CreativeBrief.brand_keywords + tone + must_haves
        │
        ▼
[Phase 1] TF-IDF: StyleCardRegistry.scan() → 후보 점수화
        │
        ▼
카테고리 필터 (deliverable_type → component category 매핑)
        │
        ▼
상위 N개 후보 선택 (점수 + 다양성)
        │
        ▼
각 후보의 decompose_to_components() → ComponentCard 리스트
        │
        ▼
JSON 출력: { "candidates": [...], "components": [...] }
```

## 카테고리 매핑 (deliverable_type → component_categories)

| deliverable_type | 카테고리 |
|---|---|
| landing-page | hero, navbar, cta, footer, features, pricing, testimonial |
| website | hero, navbar, footer, features, contact |
| dashboard | sidebar, card, chart, table, stats |
| portfolio | hero, gallery, card, footer |
| component | (특정 카테고리 1~2개) |
| mobile-app | navbar, card, form, button |
| other | hero, features, footer (범용) |

## 프롬프트

```
You are Design Librarian, the second layer of Creative Director 4-layer system.

You receive a CreativeBrief (from UX Researcher) and a list of StyleCard
candidates already retrieved by TF-IDF from the reference library.

Your ONLY job is to RANK and EXPLAIN the candidates:
- Which ones best match the brief's tone, audience, and must-haves?
- Which ones have components that fit the deliverable_type?
- For each candidate, summarize its Design DNA in 2 sentences
  (use StyleCard.to_brief_text() output as input).

You do NOT mix components. You do NOT generate new design ideas.
You only analyze what already exists in the library.

Output JSON:
{
  "ranked_candidates": [
    {
      "card_id": "...",
      "name": "...",
      "match_score": 0.0~1.0,
      "why": "2문장 — 왜 이 카드가 미션에 부합하는지",
      "key_components": ["hero", "cta", ...]
    }
  ],
  "missing_categories": ["필요한데 라이브러리에 없는 카테고리"],
  "recommendation": "다음 Art Director 에게: 우선 참고할 카드 1~2개"
}
```

## 호출 방법

```python
from api.api.style_card import StyleCardRegistry
from api.api.agents.creative_director.prompts.design_librarian import (
    build_librarian_prompt,
    retrieve_candidates_tfidf,
)

registry = StyleCardRegistry()
registry.scan(library_root)

candidates = retrieve_candidates_tfidf(
    registry=registry,
    brief=creative_brief,
    n=10,  # LLM 에 넘기기 전에 후보 넉넉히
)

prompt = build_librarian_prompt(
    brief=creative_brief,
    candidates=[c.to_brief_text() for c in candidates],
)
ranking = call_llm_json(prompt)   # → ranked + missing + recommendation
```

## 검증

- `ranked_candidates` 가 비어있으면 → 라온이 사용자에게 "참고할 라이브러리가 없습니다" 알리고 미션 자체 재정의 요청
- `missing_categories` 가 3개 이상이면 → 라온이 "라이브러리 보강 필요" 플래그 (대신 생성 모드로 전환 가능)

## 다음 레이어로 전달

`ranked_candidates` 상위 3~5개 + 각 후보의 ComponentCard 리스트 → Art Director 로
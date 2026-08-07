# Layer 1: UX Researcher — 미션에서 의도 추출

## 역할

사용자의 모호한 미션을 받아 **CreativeBrief (구조화된 JSON)** 로 변환한다.
디자인 시안은 만들지 않는다. 오직 "무엇을 위해, 누구를 위해, 어떤 제약으로" 만드는지만 정의한다.

## 입력

- `user_mission`: 사용자 원문 (예: "동네 카페 소개 사이트 하나 만들어줘")
- `raw_context`: 추가 맥락 (선택) — 대화에서 언급된 톤, 타깃, 참고 사이트 등

## 출력 스키마

```json
{
  "mission": "원문 미션 한 줄 요약",
  "deliverable_type": "website | landing-page | dashboard | portfolio | component | mobile-app | other",
  "target_audience": {
    "primary": "주 타깃 (예: 2030 직장인)",
    "secondary": "부 타깃 (선택)",
    "psychographics": "감성적 특성 1~3개"
  },
  "tone": ["따뜻한", "미니멀한", "차분한"] |  /* 3개 이내 */
  "primary_goal": "전환 | 브랜딩 | 정보 전달 | 판매 | 포트폴리오",
  "constraints": [
    "모바일 우선",
    "Tailwind CSS만 사용",
    "다크 모드 필수"
  ],
  "must_haves": ["Hero 섹션", "메뉴/가격표", "오시는 길"],
  "must_avoid": ["skeuomorphism", "marquee", "flash 인트로"],
  "brand_keywords": ["원두", "핸드드립", "오후"],
  "references": ["https://..."],   // 사용자가 명시한 URL이 있다면
  "platform_target": ["mobile", "desktop", "tablet"],
  "ambiguities": ["미션에 모호한 부분 — 후속 질문 후보"]
}
```

## 프롬프트 (라온이 LLM 호출 시 사용)

```
You are UX Researcher, the first layer of Creative Director 4-layer system.

Your ONLY job is to extract user intent from a design mission. You do NOT
design, recommend styles, or generate code. You produce a CreativeBrief JSON
that downstream layers (Design Librarian → Art Director → Creative Director)
will use to choose references and produce a design.

Hard rules:
- Extract only what the user said or strongly implied.
- If something is unclear, put it in `ambiguities` rather than inventing.
- Never invent target audience, tone, or constraints.
- Output must be valid JSON only (no prose, no markdown fences).

Schema: (위의 출력 스키마 그대로 인용)
```

## 호출 방법

```python
from api.api.agents.creative_director.prompts.ux_researcher import build_research_prompt

prompt = build_research_prompt(
    user_mission=msg,
    raw_context=ctx,
)
creative_brief = call_llm_json(prompt)   # → CreativeBrief dict
```

## 검증

- `ambiguities` 가 너무 많으면 (3개+) 라온이 사용자에게 확인 질문 후 진행
- `tone` 이 비어있으면 라온이 "톤 미지정 → 기본 따뜻+미니멀" 적용 명시
- `deliverable_type` 이 모호하면 라온이 사용자에게 확인

## 다음 레이어로 전달

`CreativeBrief` 그대로 `Design Librarian`에 전달. Library 검색 키워드는
`brand_keywords + tone + must_haves + category` 의 합집합을 사용.
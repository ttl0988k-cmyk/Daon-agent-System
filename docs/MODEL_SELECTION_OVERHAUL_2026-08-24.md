# 모델 선별 시스템 전면 개편 (2026-08-24)

> 대표님 4대 지시사항에 따른 Dynamic Harness 모델 선별 엔진 개편 기록.
>
> 1. **작업 난이도/특성에 맞는 모델을 동적으로 구성할 것**
> 2. **사용자가 등록한 모델들을 모두 후보군에 포함시킬 것**
> 3. **8차원 가중치에서 비용 관계는 가중치에 포함하지 말 것**
> 4. **지금까지의 대화 내용을 정리해서 문제점들을 수정할 것**

---

## 1. 배경 — "더 나은 모델이 있는데도 하위 모델이 코딩/디자인을 맡는다"

실전 사용에서 발견된 증상. 원인 분석 결과 다음 5가지가 겹쳐 있었다.

| # | 문제 | 위치 | 영향 |
|---|------|------|------|
| P1 | creative/design 축이 Intel 매핑에 없음 | `model_intel.py` `STRENGTH_CAPABILITY_MAP` | 디자이너 노드는 Intel 블렌딩이 아예 작동하지 않고 이름 매칭 점수로 만능화 → 능력 격차 소멸 |
| P2 | CEO 프롬프트의 벤더 하드코딩 비용 규칙 | `planner.py` "COST OPTIMIZATION / MODEL SELECTION BY COST" | "저비용 작업 → deepseek-v3/minimax-m3" 식으로 특정 벤더를 저가형으로 몰아서 열등한 모델이 당선 |
| P3 | preferred_model 보너스 +0.15 과대 | `model_selector.py` `select_for_node` | strength 가중치(0.32~0.38) 대비 실제 능력 격차 0.4를 완전히 상쇄 |
| P4 | 템플릿 model_preference 하드코딩 (사망 데이터) | `agents/**.yaml`, `_add_capability.py` | 카테고리 수준 MODEL_PREF가 100개 템플릿에 찍혔지만 런타임 미소비 — 혼란만 유발 (런타임 해악은 없었음) |
| P5 | 이름 기반 strengths 추론 무료 만점 | `model_selector.py` `_load_custom_profiles._infer_strengths` | 'pro/max' 등 이름만으로 code+reasoning 만점 → Intel 증거와 무관하게 고득점 |

## 2. 변경 내용

### 2.1 비용 차원 완전 제거 — 8차원 → 7차원 (`model_selector.py`)

- `_score_model()`에서 Cost Efficiency 채점 블록 삭제, `max_budget` 파라미터 삭제.
- 스코어카드 헤더를 "7-Dimensional"로 갱신하고 "Cost is intentionally NOT scored
  (CEO decision 2026-08-24)" 명시. Cost/1M 수치는 참고 정보로만 표시.
- 체인 항목의 `_cost` 필드는 참고용으로만 유지 (선별 로직 미사용).

### 2.2 난이도별 가중치 프리셋 도입 (`model_selector.py`)

```python
_WEIGHT_PRESETS = {
    "heavy":    {strength:0.38, success_rate:0.22, reliability:0.15, context:0.13, latency:0.04, status:0.04, load:0.04},
    "standard": {strength:0.32, success_rate:0.22, reliability:0.12, context:0.12, latency:0.12, status:0.05, load:0.05},
    "light":    {strength:0.22, success_rate:0.15, reliability:0.10, context:0.06, latency:0.35, status:0.06, load:0.06},
}
```

- 각 프리셋 합은 항상 1.0. cost 차원은 설계상 부재.
- `infer_difficulty(role, required_strength, task)` 신설:
  - 1순위: 작업 본문+역할 키워드 (architect/refactor/migration/security → heavy,
    typo/rename/comments/readme/simple fix → light)
  - 2순위: strength 기본값 (reasoning/debug → heavy, fast → light, 그 외 standard)
  - 비용 관련 단어는 의도적으로 패턴에 없음 — 난이도는 순수하게 작업 특성만으로 판단.
- `select_for_node(..., difficulty=None)` — 명시 지정 없으면 자동 추론.
  결과는 `context_info["difficulty"]`와 선택 로그에 기록(관측성).

### 2.3 preferred_model 보너스 축소 (+0.15 → +0.05)

동점 수준의 타이브레이커 역할만 수행하도록 축소. 이제 선호 모델이 명백히
열등하면 프리셋 가중치가 그 격차를 이긴다.

### 2.4 CEO 프롬프트 탈벤더화 (`planner.py`)

- 삭제: "COST OPTIMIZATION", "MODEL SELECTION BY COST" (deepseek/minimax 직접 지명 규칙)
- 신설:
  - **TEMPLATE COST FIT** — [COST] 마커는 에이전트 작업량 크기 결정용일 뿐,
    모델 선택에는 절대 영향을 주면 안 됨을 명시.
  - **MODEL SELECTION BY TASK FIT (MANDATORY)** — AVAILABLE MODELS 안에서
    스코어카드+Intel 카드 근거로만 선택. 프로바이더 스테레오타입 금지.
    비용 절감을 위한 모델 강등 금지.
- 스코어카드 안내문에 난이도 프리셋(heavy/standard/light) 설명 추가.
- Model Selection Log 라인에 `difficulty=` 필드 추가, 레버 안내문을
  "difficulty weight presets or the intel DB"로 수정.

### 2.5 Intel 매핑 보강 (`model_intel.py`)

```python
STRENGTH_CAPABILITY_MAP = {
    ...,            # code→coding, reasoning→reasoning, debug/qa/review→debugging
    "creative": "coding",
    "design":   "coding",
}
```

- 디자이너 노드도 프론트엔드 '코드'를 생산하며 `ROLE_CAPABILITY_MAP["designer"]="coding"`
  과 정렬 → DAON 필드 증거(designer 역할 실행 결과)가 creative 조회에 반영됨.
- 이제 designer 노드에서도 Intel Bayesian 블렌딩이 작동해 이름 매칭 만능화가 해소됨.

### 2.6 후보군 커버리지 (요구사항 2 — 이미 충족, 재확인)

- `_DEFAULT_PROFILES = []` — 하드코딩 프로필 없음.
- `_load_custom_profiles()`가 `custom_providers.json` 경유 `model_manager.get_available_models()`
  (채팅/토론 UI와 동일 단일 소스)에서 전부 로드.
- `select_for_node`는 등록되지 않은 옛 싱글톤 프로필을 차단(`_registered_models` 필터).
- → **사용자가 등록한 모든 모델이 자동으로 후보군에 포함**된다.

## 3. 남은 과제 (후속 세션)

| 항목 | 내용 |
|------|------|
| 템플릿 YAML 사망 데이터 정리 | `model_preference`/`preferred_providers`/`capability_score`/`cost_profile` 필드는 런타임 미소비. 플래너가 참조하는 것도 아니므로 일괄 제거 또는 주석 처리 권장 (100개 파일) |
| 이름 기반 strengths 추론 고도화 | `_infer_strengths`는 여전히 이름 휴리스틱. Intel DB가 비어 있는 신규 모델의 초기값으로만 기능하도록 문서화 필요. 장기적으로 Intel seed 자동 갱신 |
| plan_validator 모델-역할 정합 검사 | 노드의 assigned model이 해당 역할 요구 strength에서 최소 점수를 넘는지 검증하는 체크 추가 여지 |
| 실전 A/B 관찰 | Selection Log(difficulty 포함)를 몇 회 실행 후 확인, 프리셋 수치 미세 조정 |

## 4. 검증 기록

```
[OK] preset heavy/standard/light: sum=1.00, dims=7 (cost 없음)
[OK] infer_difficulty: refactor/architecture→heavy, typo/readme→light,
     creative→standard, reasoning→heavy, fast→light, qa→standard
[OK] STRENGTH_CAPABILITY_MAP creative/design -> coding
[OK] select_for_node/_score_model: max_budget 제거, difficulty 추가
ALL PROBES PASSED (py_compile 통과)
```

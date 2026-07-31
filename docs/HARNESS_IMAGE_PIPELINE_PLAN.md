# 다이나믹 하네스 — 이미지 모델 도구 통합 설계

> 작성일: 2026-08-01 | 상태: **구현 완료 (2026-08-01)**
> 관련 커밋: ec12f43 (미디어 도구 주입), 9c5665d (이미지 버그 수정), 본 구현(갭 1·2)

## 1. 목표 (핵심 방향)

**다이나믹 하네스의 어떤 노드든 필요할 때 이미지/영상 모델을 도구처럼 호출**할 수 있게 만든다.
고정 파이프라인을 하드코딩하지 않는다. 다이나믹 하네스는 본래 CEO(오케스트레이터) 에이전트가
총괄하며 작업마다 동적으로 DAG를 짜는 구조이므로, 이미지 생성도 그 동적 작업 흐름 안에서
"필요한 노드가 `generate_image` 도구를 호출"하는 일반 능력으로 제공한다.

### 핵심 원칙
- **동적 DAG 유지**: CEO 에이전트가 미션을 분해해 노드/에지를 동적으로 생성 (기존 planner/compiler 그대로).
- **도구는 범용**: `generate_image`/`generate_video`를 특정 노드 타입에 묶지 않고,
  미디어 모델이 등록되어 있으면 하네스 노드에서 사용 가능하게 함.
- **결과물은 파일로**: 생성 이미지를 워크스페이스에 저장해 HTML/CSS가 상대경로로 참조 가능하게 함.

## 2. 참고 예시 — 홈페이지 제작 시나리오 (고정 구조 아님)

아래는 CEO가 동적으로 구성할 수 있는 DAG의 **예시**일 뿐, 하드코딩 대상이 아니다.

```
CEO Agent          "브랜드 홈페이지 제작 필요. 이미지 포함."
  ↓
Designer Agent     "히어로 이미지 필요" → generate_image 호출 (직접)
  ↓                또는 Image 전담 노드로 위임
Frontend Agent     "생성된 이미지(assets/hero.png) 적용해 랜딩페이지 제작"
  ↓
QA Agent           "렌더링 확인. 수정 필요 여부 전달"
  ↓
CEO Agent          최종 승인
```

> 이미지 생성은 Designer 노드가 직접 `generate_image`를 호출할 수도, CEO가 Image 전담 노드를
> 따로 만들 수도 있다. 어느 쪽이든 도구가 하네스 노드에서 활성화되어 있어야 한다.

## 3. 기존 인프라 매핑

| 파이프라인 단계 | 기존 인프라 | 위치 |
|---|---|---|
| 노드 간 메시지 전달 | `[MSG to=X]...[/MSG]` + inbox 주입 | `memory_store.py:801/921`, `runner.py:410/433` |
| DAG 노드 실행 순서 | `enabled_toolsets=node["tools"]` | `runner.py:261` |
| 이미지 생성 도구 | `generate_image` (media-generation toolset) | `streaming.py:1176` |
| 파일 쓰기 (HTML/이미지) | `write_file` / `patch` | `runner.py:294` |
| 이미지 노드 타입 분기 | `image_tool`/`image_gen` → toolset 추가 | `compiler.py:158-159` |
| QA 점수 피드백 루프 | `_extract_review_score` + pass_threshold | `runner.py:450-454` |

## 4. 구현한 갭 (2개) — 완료

### 갭 1 — 하네스 노드에 media-generation toolset 연결 ✅
- **문제**: `compiler.py:158`은 노드 타입 `image_gen`일 때 hermes FAL.ai 기반 `image_gen` toolset만 추가.
  등록 프로바이더 기반 `media-generation` toolset은 채팅 에이전트(`streaming.py`)에만 주입됨.
- **구현**:
  - `compiler.py` — template 경로·legacy 경로 **양쪽**에서 모든 노드 `enabled_toolsets`에
    `media-generation`을 일반 능력으로 추가 (특정 노드 타입에 한정하지 않음).
  - `media_generation.py` — `register_media_generation_tools(registry)` 공용 함수 신설.
    registry에 `generate_image`/`generate_video`를 멱등 등록 + `media` alias + 채팅용 OpenAI 스키마 반환.
  - `streaming.py` — 기존 인라인 주입 블록을 공용 함수 호출로 교체 (중복 제거).
  - `runner.py` — 하네스 노드 실행 시 공용 함수 호출로 registry 등록 보장
    (채팅이 먼저 실행되지 않아도 toolset 존재).

### 갭 2 — generate_image에 파일 저장(save_path) 기능 ✅
- **문제**: `generate_image`는 URL(`data:` base64 또는 http)만 반환.
  HTML 적용 시 워크스페이스 상대경로(`assets/hero.png`) 참조가 필요하나 저장 기능 없음.
- **구현** (`media_generation.py`):
  - `save_generated_media(source, save_path, index)` — data: base64 디코딩 / http URL 다운로드 후
    워크스페이스(`TERMINAL_CWD` 또는 cwd)에 저장, 워크스페이스 기준 **상대경로** 반환.
  - 스키마에 `save_path`(선택) 파라미터 추가. 디렉토리(`assets/`)면 자동 이름, 파일명이면 그 경로에 저장.
  - 응답에 `saved_paths`(상대경로 배열) 추가 → Frontend Agent가 `<img src="assets/hero.png">`로 임베드.

## 5. 구현 후 동작 예시 (CEO가 동적으로 구성)

아래는 CEO 에이전트가 "홈페이지 제작" 미션을 받아 **스스로** DAG를 짠 결과의 예시다.
노드 구성·위임 여부는 미션마다 달라질 수 있다 (고정 구조 아님).

1. **CEO Agent**: 미션 분해 → DAG 동적 생성 (예: Designer → Frontend → QA)
2. **Designer Agent**: 브랜드 컨셉 정의 + 필요 판단 시 **직접** `generate_image(prompt=..., model=wan2.7-image-pro, size=1792x1024, save_path=assets/hero.png)` 호출
   - 또는 CEO가 Image 전담 노드를 따로 뒀다면 `[MSG to=image_agent]...[/MSG]`로 위임
3. **Frontend Agent**: inbox/입력 수신 → `write_file`로 `index.html` 작성 (`<img src="assets/hero.png">`)
4. **QA Agent**: 렌더링/접근성 검수 → 점수 < threshold면 Frontend로 피드백 루프
5. **CEO Agent**: 최종 승인

> 핵심: 어떤 노드가 이미지를 만들든, `generate_image` 도구가 하네스에서 활성화되어 있고
> 결과를 파일로 저장할 수 있으면 파이프라인이 성립한다.

## 6. 참고 — 에이전트 간 통신 한계

- 비동기 inbox 기반 (실시간 양방향 아님). DAG 실행 순서 의존.
- A→B 메시지는 B가 **아직 실행 전**일 때만 주입 효과.
- `run_id`로 같은 실행 내 통신 추적.

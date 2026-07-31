# 다이나믹 하네스 — 브랜드 홈페이지 이미지 파이프라인 설계

> 작성일: 2026-08-01 | 상태: 설계(내일 구현 예정)
> 관련 커밋: ec12f43 (미디어 도구 주입), 9c5665d (이미지 버그 수정)

## 1. 목표

다이나믹 하네스 안에서 여러 에이전트가 협업해 **이미지 모델로 생성한 이미지를 랜딩페이지/홈페이지에 적용**하는 파이프라인을 구축한다.

## 2. 사용자 제시 구조 (DAG)

```
CEO Agent
  ↓  "브랜드 홈페이지 제작 필요. 이미지 포함."
Designer Agent
  ↓  "히어로 이미지 필요. 이미지 생성 요청"
Image Agent
  ↓  Provider Manager → Image Model 실행
Image Agent
  ↓  "생성 완료. 결과 전달"
Frontend Agent
  ↓  "이미지 적용해서 랜딩페이지 제작"
QA Agent
  ↓  "렌더링 확인. 수정 필요 여부 전달"
CEO Agent
  ↓  최종 승인
```

## 3. 기존 인프라 매핑

| 파이프라인 단계 | 기존 인프라 | 위치 |
|---|---|---|
| 노드 간 메시지 전달 | `[MSG to=X]...[/MSG]` + inbox 주입 | `memory_store.py:801/921`, `runner.py:410/433` |
| DAG 노드 실행 순서 | `enabled_toolsets=node["tools"]` | `runner.py:261` |
| 이미지 생성 도구 | `generate_image` (media-generation toolset) | `streaming.py:1176` |
| 파일 쓰기 (HTML/이미지) | `write_file` / `patch` | `runner.py:294` |
| 이미지 노드 타입 분기 | `image_tool`/`image_gen` → toolset 추가 | `compiler.py:158-159` |
| QA 점수 피드백 루프 | `_extract_review_score` + pass_threshold | `runner.py:450-454` |

## 4. 구현해야 할 갭 (2개)

### 갭 1 — 하네스 노드에 media-generation toolset 연결
- **현재**: `compiler.py:158`은 노드 타입 `image_gen`일 때 hermes FAL.ai 기반 `image_gen` toolset만 추가.
  우리가 만든 등록 프로바이더 기반 `media-generation` toolset은 채팅 에이전트(`streaming.py:1181`)에만 주입됨.
- **수정**: `compiler.py`에서 이미지 노드 타입일 때 `media-generation` toolset도 `enabled_toolsets`에 추가.
  또는 미디어 모델이 등록되어 있으면 모든 하네스 노드에 기본 주입.

### 갭 2 — generate_image에 파일 저장(save_path) 기능
- **현재**: `generate_image`(`streaming.py:1140`)는 URL(`data:` base64 또는 http)만 반환.
  HTML 적용 시 워크스페이스 상대경로(`assets/hero.png`) 참조가 필요하나 저장 기능 없음.
- **수정**: `generate_image` 스키마에 `save_path`(선택) 파라미터 추가.
  - http URL → 다운로드 후 워크스페이스에 저장
  - base64 → 디코딩 후 워크스페이스에 저장
  - 반환값에 `saved_path`(상대경로) 추가 → Frontend Agent가 `<img src="assets/hero.png">`로 바로 임베드

## 5. 구현 후 동작 시나리오

1. **CEO Agent**: 미션 분해 → DAG 생성 (Designer → Image → Frontend → QA → CEO)
2. **Designer Agent**: 브랜드 컨셉 정의, Image Agent에게 `[MSG to=image_agent]히어로: 미니멀 그라데이션, 16:9[/MSG]`
3. **Image Agent**: inbox 수신 → `generate_image(prompt=..., model=wan2.7-image-pro, size=1792x1024, save_path=assets/hero.png)` → Frontend에게 `[MSG to=frontend_agent]assets/hero.png 저장 완료[/MSG]`
4. **Frontend Agent**: inbox 수신 → `write_file`로 `index.html` 작성 (`<img src="assets/hero.png">`)
5. **QA Agent**: 렌더링/접근성 검수 → 점수 < threshold면 Frontend로 피드백 루프
6. **CEO Agent**: 최종 승인

## 6. 참고 — 에이전트 간 통신 한계

- 비동기 inbox 기반 (실시간 양방향 아님). DAG 실행 순서 의존.
- A→B 메시지는 B가 **아직 실행 전**일 때만 주입 효과.
- `run_id`로 같은 실행 내 통신 추적.

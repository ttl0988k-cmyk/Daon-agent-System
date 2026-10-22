# 회의실 워커 모델 상태 배지 UI 명세

- **대상**: 회의실 슬롯 07(코덱스 워커), 08(클로드 워커)
- **프로젝트**: `C:\daon\Daon agent System`
- **범위**: UI/UX 구현 명세만. 이 문서 작성 과정에서는 HTML·JS·CSS 및 서버 코드를 수정하지 않는다.
- **관련 UI**: `index.html`의 `div[data-slot="07"]`, `div[data-slot="08"]`; `.boardroom-model-select`, `.slot-status-badge`
- **현행 JS 훅**: `static/v2/js/app.js::setBoardroomSlotState(slot, status)`는 회의 진행 상태(READY/RUNNING/DONE/ERROR)를 갱신한다.

## 1. 설계 결정

회의 진행 상태와 워커 모델 상태는 서로 다른 축이므로 한 배지나 한 상태값으로 합치지 않는다.

1. 기존 상단 우측 `.slot-status-badge`는 READY/RUNNING/DONE/ERROR 전용으로 유지한다.
2. 모델 적용 상태는 드롭다운 바로 아래에 **컴팩트한 상태 행**으로 추가한다. 기본 형식은 `[아이콘] [상태 라벨] · [짧은 모델명]`이며, 한 줄에서 넘치는 모델명은 말줄임한다.
3. ‘격리됨’은 모델 적용 성공/실패와 동시에 참일 수 있는 **보안 프로필 속성**이다. 따라서 단일 상태로 성공 여부를 가리지 말고, 주 상태 행의 상태 배지와 분리된 보조 배지로 표시한다. 예: `[적용됨] muse-spark-1.3 · OpenCode Go   [격리됨]`.
4. ‘실패’일 때만 상태 행 아래에 원인 요약을 두 번째 줄로 표시한다. 전체 원인은 `title` 및 접근 가능한 설명으로 제공하되, 툴팁만으로 오류를 숨기지 않는다.
5. 서버에서 상태를 아직 받지 못한 최초 로딩 중에는 상태를 추측하지 않는다. 상태 행은 숨기거나 중립적인 `상태 확인 중…`으로 표시하고, 응답을 받은 뒤 다섯 상태 중 하나를 적용한다. 이는 영속 상태가 아니라 로딩 표시다.

## 2. 배치와 레이아웃

현재 워커 카드의 순서는 헤더(참여 체크 및 실행 배지 포함) → 설명 → profile/kind footer → 모델 선택 → 작업 지시 버튼이다. 이 순서를 보존하고, **`.boardroom-model-select` 바로 다음, `.agent-dispatch-btn` 바로 앞**에 상태 행을 삽입한다.

- 상태 행: `w-full min-w-0 flex items-center gap-1.5`, 최소 높이 약 18px, 글자 10–11px.
- 모델명: `min-w-0 flex-1 truncate`; 전체 문자열은 `title`과 `aria-label`에 제공한다.
- 격리 배지: 공간이 부족하면 모델명보다 먼저 줄바꿈하지 않도록 `shrink-0`; 초소형 폭에서는 상태 행이 두 줄로 자연스럽게 늘어나는 것을 허용한다.
- 실패 설명: 상태 행 아래에 `text-[10px] leading-tight`, 최대 2줄, 긴 원인은 툴팁/aria 설명에서 전문 제공.
- `min-h-[175px]`는 최소 높이이지 고정 높이가 아니다. 고정 높이·overflow 숨김을 추가하지 말고, 상태 설명이 나타날 때 카드가 필요한 만큼 높아지게 한다. 주변 슬롯 그리드는 행 높이를 맞추되 카드 콘텐츠를 잘라내지 않아야 한다.
- 모바일/좁은 카드에서도 모델 선택과 작업 지시 버튼의 클릭 영역을 줄이지 않는다.

## 3. 상태 라벨·색상·아이콘

색상은 Tailwind의 기존 emerald/amber/rose/on-surface-variant 계열을 사용한다. 색상만으로 상태를 구분하지 말고 항상 라벨과 아이콘을 함께 표시한다.

| 상태 | 화면 라벨 | 색상 클래스 제안 | Material Symbols 아이콘 | 의미/노출 규칙 |
|---|---|---|---|---|
| 적용됨 | **적용됨** | `text-emerald-700` + `bg-emerald-500/10` | `task_alt` | 선택한 모델이 실제 워커 런타임에서 확인된 상태. 선택값만 저장된 시점에는 표시하지 않는다. |
| 재시작 후 복원됨 | **재시작 후 복원됨** | `text-amber-700` + `bg-amber-500/10` | `history` | 재기동 후 영속 저장값을 읽어 워커 설정에 복원했음을 나타낸다. 실제 적용 확인도 됐다면 이 상태를 우선 라벨로 표시하고, 재확인 후 다음 상태 갱신에서 ‘적용됨’으로 전환할 수 있다. |
| 기본값 | **기본값** | `text-on-surface-variant` + `bg-black/[0.04]` | `settings_backup_restore` | 사용자가 모델을 지정하지 않았고 슬롯의 명시된 기본 폴백 모델로 실행 중. 짧은 모델명을 함께 표시한다. |
| 격리됨 | **격리됨** | `text-emerald-700` + `bg-emerald-500/10` | `shield_lock` | 워커가 개인 `~/.codex` 설정과 분리된 슬롯 전용 프로필로 실행 중. 주 모델 상태 옆의 별도 보조 배지로 표시한다. 성공을 뜻하는 ‘적용됨’의 대체 상태로 사용하지 않는다. |
| 실패 | **실패** | `text-rose-700` + `bg-rose-500/10` | `error` | 적용·복원·기동·런타임 확인 중 오류. 원인 요약을 배지 아래에 항상 노출하고 전문은 `title`/`aria-describedby`로 제공한다. 실패 상태는 다른 모델 상태보다 우선한다. |

### 상태 우선순위 및 조합 규칙

- 모델 적용 주 상태는 `실패 > 재시작 후 복원됨 > 적용됨 > 기본값` 우선순위로 하나만 표시한다.
- 격리는 독립 속성이므로 `data-isolated="true"`일 때 주 상태와 함께 보조 배지를 표시한다. 예를 들어 복원된 Codex 슬롯은 `재시작 후 복원됨`과 `격리됨`을 동시에 표시할 수 있다.
- 모델 상태 배지의 `slot-model-applied`, `slot-model-restored`, `slot-model-default`, `slot-model-failed` 클래스는 기존 `slot-running`, `slot-done`, `slot-error` 클래스와 역할을 분리한다. 실행 상태 클래스 갱신이 모델 상태 스타일을 지우면 안 된다.

## 4. HTML 초안

아래는 슬롯 07·08 각각의 기존 모델 선택 요소와 작업 지시 버튼 사이에 넣을 구조 초안이다. 실제 구현 시 ID는 슬롯별로 유일해야 한다.

```html
<div class="slot-model-state mt-1.5 flex min-w-0 items-center gap-1.5"
     id="slot-model-state-07"
     data-slot="07"
     data-model-state="default"
     aria-live="polite"
     aria-atomic="true">
  <span class="slot-model-state-badge slot-model-default inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-code text-[10px]"
        id="slot-model-badge-07">
    <span class="material-symbols-outlined text-[13px]" aria-hidden="true">settings_backup_restore</span>
    <span class="slot-model-state-label">기본값</span>
  </span>
  <span class="slot-model-name min-w-0 flex-1 truncate font-code text-[10px] text-on-surface-variant"
        id="slot-model-name-07"
        title="muse-spark-1.3-contributor (OpenCode Go)">
    muse-spark-1.3 · OpenCode Go
  </span>
  <span class="slot-model-isolation hidden shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-code text-[10px] text-emerald-700 bg-emerald-500/10"
        id="slot-model-isolation-07"
        title="개인 ~/.codex 설정과 분리된 슬롯 전용 프로필">
    <span class="material-symbols-outlined text-[13px]" aria-hidden="true">shield_lock</span>
    <span>격리됨</span>
  </span>
</div>
<p class="slot-model-error hidden mt-1 text-[10px] leading-tight text-rose-700"
   id="slot-model-error-07"
   role="alert"></p>
```

슬롯 08은 동일 구조에서 `07`을 `08`로 바꾸고 전체 모델명/실제 provider 정보를 사용한다. 각 카드의 모델 상태 행은 해당 카드의 `.boardroom-model-select[data-slot="07|08"]`에 대응시킨다.

### 클래스·접근성 계약

- 상태 뱃지의 상태별 클래스를 변경할 때 `className` 전체를 덮어써 ID/기본 레이아웃 클래스가 사라지지 않게 한다. 상태 전환은 상태 전용 클래스만 교체한다.
- 아이콘은 장식용 `aria-hidden="true"`; 상태 라벨은 텍스트로 스크린리더에 전달한다.
- 오류 문단은 `role="alert"`로 즉시 공지하고 배지와 `aria-describedby` 연결을 권장한다.
- 키보드 포커스는 모델 선택기에 유지되며 상태 표시를 위해 포커스를 강제로 이동하지 않는다.

## 5. 모델명 표기 규칙

1. 화면에는 **모델 ID와 provider를 식별 가능한 짧은 표기**로 노출한다. 예: `muse-spark-1.3 · OpenCode Go`, `deepseek-v4.1 · OpenCode Go`.
2. 화면 폭이 좁으면 CSS ellipsis로 축약하되, 제목/접근성 이름에는 정식 이름과 provider를 보존한다. 예: `title="muse-spark-1.3-contributor (OpenCode Go)"`.
3. 약칭은 모델 ID가 서로 구분되는 범위에서만 사용한다. 모델 ID의 핵심 버전·모델 계열을 제거하거나 임의 별칭만 노출하지 않는다.
4. 드롭다운의 실제 `value`와 선택 저장값은 정식 모델 ID를 유지한다. 표시명 축약은 프레젠테이션 계층에서만 수행한다.
5. 상태 설명/오류 전문은 모델명 축약과 별도로 제공한다. 선택 모델과 실제 실행 모델이 다르면 둘 다 명시한다(예: `요청: … / 실제: …`).

## 6. JS 갱신 훅 명세 (빌 구현 항목)

### 신규 함수 권장: `setBoardroomWorkerModelState(slot, payload)`

`setBoardroomSlotState(slot, status)`는 기존대로 회의 진행 배지와 `slot-running/slot-done/slot-error` 클래스만 관리한다. 모델 상태는 별도 신규 함수에서 갱신해 두 상태 축의 결합 및 초기화 충돌을 방지한다.

신규 함수는 슬롯별 상태 컨테이너를 찾아 아래 데이터를 표시해야 한다.

- `state`: `applied | restored | default | failed`
- `requested_model`: 사용자가 지정한 정식 ID(없으면 빈 값)
- `resolved_model`: 워커에 실제 적용·확인된 정식 ID
- `provider`: 실제 provider
- `isolated`: 슬롯 전용 프로필 사용 여부(boolean)
- `error` 또는 `error_message`: 실패 원인(없으면 오류 행 숨김)
- `source`: `selection_restore | runtime_confirm | worker_start | worker_error` 등 상태의 근거

함수는 응답의 사실만 렌더링하고, `<select>` 값만 보고 `applied`라고 추정해서는 안 된다. 서버 응답은 모델/provider 문자열을 DOM에 넣을 때 HTML로 직접 해석하지 않고 `textContent`로 삽입한다.

### 호출 지점

1. **초기 상태 복원**: `GET /api/boardroom/selection` 복원 완료 뒤, 슬롯 07·08 각각의 저장값과 서버가 반환한 실제/복원 상태를 표시한다. 영속 설정값을 읽어 복원 중임이 확인되면 `restored`; 기본값 정책으로 해석되었으면 `default`를 표시한다.
2. **모델 선택 저장/적용**: `.boardroom-model-select[data-slot="07|08"]` 변경 후 저장 요청 성공만으로 적용 완료라 표시하지 않는다. 저장은 됐지만 워커 재기동/런타임 반영 확인 전에는 기존의 검증된 상태를 유지하거나 중립 상태를 보여준다. 실제 워커 설정 확인 응답에서 성공한 경우에만 `applied`로 갱신한다.
3. **워커 기동/재기동 결과**: Herdr 상태 조회 또는 워커 기동 완료 결과에서 요청 모델과 실제 실행 모델/provider를 비교해 상태를 갱신한다. 저장값을 사용해 재기동한 것이 확인되면 `restored`; 런타임 모델이 일치하면 `applied`로 전환한다.
4. **오류 경로**: 모델 적용 API 오류, provider/인증 오류, 프로필 격리 실패, 워커 spawn/health 오류를 모두 `failed`로 표시하고 원인을 사용자용 한글 요약과 상세 `title`로 전달한다. 조용히 기본 모델로 바뀐 뒤 성공처럼 보이게 하지 않는다.
5. **회의 SSE 이벤트**: 현재 `onBoardroomSlot`/`onBoardroomReply`는 `setBoardroomSlotState()`로 RUNNING/DONE/ERROR를 표시한다. 이벤트에 `requested_model`, `resolved_model`, `provider`, `worker_model_state`, `isolated`, `error`가 포함될 경우 신규 모델 상태 함수에도 전달한다. 기존 `d.model`은 회의록 라벨에 쓰는 값과 실제 실행 모델 확인값을 혼동하지 않도록 서버 payload 의미를 확인한 뒤 사용한다.
6. **초기화/리셋**: `resetBoardroomSlots()`는 실행 배지만 READY로 초기화한다. 모델 상태는 서버가 재확인하기 전까지 유지하고, 실행 리셋을 이유로 `applied/restored/default/failed` 정보를 지우지 않는다.

## 7. 동기화 대상 및 검수

`index.html`은 프로젝트 내 사본이 여러 곳에 있으므로 구현 시 단일 파일만 수정하고 끝내지 않는다. 최소한 다음 배포 사본의 구조와 캐시버스터를 함께 확인·동기화한다.

- 프로젝트 소스: `C:\daon\Daon agent System\index.html`
- 서빙용 `resources` 사본(프로젝트의 실제 패키징 경로에서 대응 파일 확인)
- `dist_new` 배포 사본(프로젝트의 실제 배포 경로에서 대응 파일 확인)

저장소에 다른 활성 진입점(예: `static/v2/index.html`)이 있으면 실제 앱이 로드하는 HTML인지 확인하고, 활성 사본에도 같은 마크업/스타일을 반영한다. `index.html`을 참조하는 실제 스크립트가 `static/v2/js/app.js`인지 확인해 훅을 올바른 JS 사본에 배선한다.

### 완료 검수 항목

- 슬롯 07·08 각각의 모델 상태가 회의 READY/RUNNING/DONE/ERROR와 독립적으로 표시된다.
- 선택 모델 저장만으로 ‘적용됨’을 띄우지 않으며, 서버 런타임 확인 모델과 표시 모델이 일치한다.
- 재시작 후 복원 상태와 기본값 상태가 구분되고, 프로필 격리 배지가 동시에 표시될 수 있다.
- 오류 때 실패 라벨과 원인 요약이 카드에서 바로 보인다. 장문 원인은 툴팁/스크린리더 설명으로도 제공된다.
- `~/.codex` 프로필 분리 여부가 확인되지 않은 경우 ‘격리됨’을 표시하지 않는다.
- 넓은 화면/좁은 화면에서 카드 버튼·드롭다운이 잘리거나 겹치지 않으며, 카드 콘텐츠가 늘어날 때 높이가 자연스럽게 증가한다.
- 키보드만으로 선택기를 조작할 수 있고, 상태가 색상에만 의존하지 않는다.

---

**구현 담당 전달 요약**: `setBoardroomSlotState()`는 실행 상태 전용으로 유지하고, `setBoardroomWorkerModelState(slot, payload)`를 신규로 두어 selection 복원·선택 적용/런타임 확인·워커 기동·오류·SSE 지점에서 호출한다. 실제 모델/provider 및 격리 확인 응답을 기준으로만 표시한다.

# 다온 × DeepSeek-Bot 벤치마킹 — 통합 로드맵

> 작성: 라온(총괄기획) · 2026-10-12 · 대상 소스: FeiZhuLulu/DeepSeek-Bot (⭐909, Apache-2.0)
> 하위 설계서: `01_steward_design.md` · `02_fts5_design.md` · `03_secrets_design.md`

---

## 0. 대전제 — "도입"이 아니라 "개념 이식"

- ds-bot은 **JS(Node) · DSH 플러그인** → 다온(Python·Electron·hermes-agent)에 **코드 직접 이식 불가**.
- 다온의 기반 **hermes-agent에 이미 대응 인프라가 존재** → ds-bot은 "새로 만드는 것"이 아니라 **"기존 모듈 보강 참조"**.

| 벤치마킹 대상 | 다온 현행 대응 | 실제 격차 |
|---|---|---|
| steward.js (switch/compact) | `agent/context_compressor.py` (임계값 무조건 압축) | **판단 로직 없음** — 최대 격차 |
| chat-index.js (FTS5 trigram) | `hermes_state.py` (FTS5 + **이미 BM25 rank**) | **CJK trigram 토크나이저만 부재** |
| secrets.js (시크릿 카드) | `plugin_credentials.py` + 셸 env ContextVar | **요청 카드 + 값기반 redactor 부재** |

---

## 1. 3개 설계 요약

### ① Steward — switch vs compact 이분 판단  [체감효과 ●●● / 리스크 ●●●]
- **핵심**: 체크라인(usable×0.8) 초과 시 **소형 모델 1회 판단** → 작업 완료면 `switch`(핸드오프 노트+새 파트) / 진행중이면 `compact`(제자리 요약, 최근 사용자 메시지 원문 보존).
- **다온 약점 해소**: 현재는 작업 도중에도 무조건 압축 → 맥락 끊김. 이걸 정면 해결.
- **신규**: `agent/context_steward.py` (`ContextSteward.decide`, `parse_decision`, `compute_lines` 포팅).
- **개편**: `context_compressor.py` — `decide_action` 추가, `compress(action=...)`에 switch 경로 신설, `check/hard` 2단 임계.
- **트리거**: `run_agent.py`(판단→압축), `streaming.py` `compressed` 이벤트에 action 분기 + `handoff` 신설.
- **안전장치**: hard 초과 시 즉시 compact, mid_turn 시 switch 금지, 실패 시 기본 compact 폴백.

### ② FTS5 원문검색  [체감효과 ●● / 리스크 ●●]
- **핵심 발견**: 다온 `search_messages`는 **이미 `ORDER BY rank`(BM25) 적용** → BM25 이식 불필요.
- **진짜 격차**: 기본 unicode61 토크나이저라 **CJK 다중 음절 검색이 LIKE 폴백(시간순·BM25 없음)으로 퇴화**.
- **보강(권고 B안)**: unicode61 유지 + `messages_fts_trigram` 병행 가상테이블 추가(영문 회귀 0, 트리거 동기화 확장).
- **부수**: `get_around(sid, mid, before=6, after=7)` around 검색, `excerpt`/`anchor` 스니펫, `session_search`에 `mode="summary"|"raw"|"around"` 하위호환 추가.
- **재활용**: 삭제 안 되는 Hermes 트랜스크립트(`sessions/session_{sid}.json`)를 인덱스 소스로.

### ③ 시크릿 카드  [체감효과 ●●● / 리스크 ●● / 난이도 ●(최저)]
- **갭 단 2개**: (a) 에이전트가 값을 **요청하는 카드 자체가 없음**, (b) 다온 마스킹은 **패턴 기반**이라 임의 등록값이 규칙 밖이면 노출.
- **다온 강점**: "셸 환경변수 주입"이 이미 `env_passthrough` ContextVar + `set_plugin_credential_env`로 **세션 스코프 구현됨** → 그대로 재사용, 난이도 대폭↓.
- **설계**: 신규 `daon_secrets.json` + `daon_secrets.py`(기존 경로 규칙 재사용), `[secret:NAME]` 플레이스홀더, 값기반 redactor를 tool-result·terminal/file·outbound LLM·log **4개 출구**에 결합(패턴→값 순서).
- **정합성**: `DAON_SECRET_` 접두가 기존 `_ENV_ASSIGN_RE`(SECRET 포함 변수명)와 자연 정합 → 2차 방어선 자동 확보.

---

## 2. 우선순위 & 추천 순서

| 순위 | 항목 | 이유 | 착수 난이도 |
|:---:|---|---|:---:|
| **1** | ① Steward | 체감 효과 최대(맥락 끊김 해소). 다만 코어 수정이라 리스크 관리 필요 | 중 |
| **2** | ③ 시크릿 카드 | 난이도 최저(셸 env 배관 재사용). 보안 즉효 | 하 |
| **3** | ② FTS5 trigram | 이미 BM25 존재로 격차 작음. 인덱스 2배 비용 | 중 |

> **추천 착수 순서**: ③(빠른 승리) → ①(핵심 개선) → ②(검색체력). 단 ①이 대표님 체감 최우선이면 ① 먼저.

---

## 3. 의존성 & 공통 리스크

- **독립성**: ①②③ 상호 독립 → 병렬 착수 가능(단, ①②는 hermes-agent 코어, ③은 api/ + 프론트).
- **공통 함정 (모든 항목 필수 준수)**:
  - 🚨 **rebuild=false 재기동 함정**: 소스만 고치면 재기동이 설치본 `resources/dist_new` 미러로 덮어씀 → **소스 · 설치본 resources · resources/dist_new 3곳 동기화** 필수.
  - 필수 파일 수정 = patch 도구 위험(구조 파일 JSON 금지) → write_file/바이트 치환.
  - ①②는 서버 재기동 필요(`request_server_update`), ③는 api 라우트 추가라 상대적으로 저위험.
- **검증 원칙**: 핸드오버 `Restart Status: failed` 오판 금지 — 항상 라이브 검증(포트/프로세스/로그).

---

## 4. 다음 단계 (승인 시 실행)

1. 착수 항목 확정 → 각 설계서의 "단계별 구현 순서" 체크리스트대로 구현.
2. ①: 신규 `context_steward.py` 작성 → `context_compressor.py` 개편 → unit test 통과 → 재기동.
3. ②: `messages_fts_trigram` 마이그레이션 → `search_messages` 분기 → `session_search` mode 확장.
4. ③: `daon_secrets.py` + REST + 웹UI 카드 → 4개 출구 redactor 결합.
5. 각 항목 실측 검증(설계서 §검증 시나리오) → 로드맵 갱신.

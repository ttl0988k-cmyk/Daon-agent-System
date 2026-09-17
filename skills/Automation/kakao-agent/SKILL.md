# 다온 응대 (카톡) — Kakao Agent

## 언제 사용

- 카카오톡 대화방에서 **호출에 응대**해야 할 때
- OCR로 읽은 카톡 화면에서 **답할지 말지 판정**해야 할 때
- 봇(`kaotalk_bot.py`)에 **판정 로직을 연결**할 때

## 핵심 구조 — 손발과 두뇌 분리

```
[kaotalk_bot.py]  손발 (기계 — 판단하지 않는다)
  · PrintWindow 캡처 → OCR → 텍스트 추출
  · SendInput UNICODE 로 타이핑·전송
  · 쿨다운 · 화면해시락 · max_reply  (스팸 방지)

[다온(응대)]       두뇌 (판단)
  · SOUL.md  = 인격·말투·범위
  · AGENTS.md = 트리거·금지·JSON출력·스팸규칙
```

**판정 함수를 코드에 하드코딩하지 않는다.** 규칙은 `AGENTS.md`에 있고, 바꾸려면 그 문서만 고친다.

## 파일 위치

| 자산 | 경로 |
|---|---|
| 프로필 인격 | `C:\Users\ttl09\.hermes\profiles\다온(응대)\SOUL.md` |
| 작동 규범 | `C:\Users\ttl09\.hermes\profiles\다온(응대)\AGENTS.md` |
| 프로필 설정 | `C:\Users\ttl09\.hermes\profiles\다온(응대)\config.yaml` |
| 조작 엔진 | `C:\daon\_kakao_test\kaotalk_bot.py` |
| 봇 설정 | `C:\daon\_kakao_test\bot_config.json` |
| 실행 로그 | `C:\daon\_kakao_test\bot_runtime_*.log` |

## 프로필 검증

```bash
# 서버가 프로필을 인식하는지 확인
curl -s -m 10 "http://127.0.0.1:9090/api/profiles" | grep -o '"name":"[^"]*"'
# → "다온(응대)" 가 목록에 있어야 한다
```

프로필은 `~/.hermes/profiles/` 하위 **디렉토리 스캔**으로 인식된다(`api/api/profiles.py:list_profiles_api`).
별도 등록 절차 없이 폴더 + `config.yaml` + `SOUL.md`면 충분하다.

## 절차 — 봇 배선

1. `AGENTS.md`를 시스템 프롬프트로 로드
   ```python
   AGENT_DIR = Path.home() / ".hermes" / "profiles" / "다온(응대)"
   prompt = (AGENT_DIR / "SOUL.md").read_text(encoding="utf-8") + "\n\n" + \
            (AGENT_DIR / "AGENTS.md").read_text(encoding="utf-8")
   ```
2. OCR 텍스트 + 내 발신 + 이미 답한 호출 + 현재 시각을 **하나의 지속 세션**으로 전달
3. 응답에서 JSON 한 줄 파싱
   ```python
   {"action":"reply","text":"..."}   → SendInput 으로 전송
   {"action":"skip","reason":"..."}  → 로그만 남기고 전송 안 함
   ```
4. **파싱 실패는 `skip`** (안전 우선 — 스팸보다 침묵)

## OCR 함정 — 최대 난관

카톡 창을 읽는 것은 **전처리가 전부**다. 아래를 지키지 않으면 호출어를 0개 감지해 봇이 침묵한다.

| 단계 | 정답 | 오답 (실패) |
|---|---|---|
| 크롭 | `find_chat_area()` 자식컨트롤 `EVA_VH_ListControl` | 고정 비율 크롭 |
| 확대 | LANCZOS **3배** | NEAREST |
| 대비 | `autocontrast(0)` | 미적용 |
| 이진화 | **140** 임계 | 미적용 (원본 회색 배경) |
| 샤픈 | 샤픈 적용 | 미적용 |
| PSM | **4** (2패스: 샤픈+PSM4 / PSM6) | PSM3 기본값 |

추가 함정:
- **TSV 정렬은 y→x 순서** (x→y로 정렬하면 `'라온아'`가 `'온아라'`로 뒤집힌다)
- **에스컬레이션 문자(ESC) 절대 금지** — 카톡에서 대화방이 닫힌다. 09-17 실사고 발생
- 창이 **화면에 보여야** PrintWindow가 동작한다 (최소화 금지)

## 스팸 방지 — 봇 계층 필수 장치

에이전트에 위임하지 말고 **봇에 하드코딩으로 유지**한다.

| 장치 | 값 | 목적 |
|---|---|---|
| 화면 해시 락 | 응답 직후 화면 동일 시 차단 | 반복 응답 차단 |
| 쿨다운 | public 60초 / private 5초 | 연속 발언 억제 |
| 연속 응답 | 최대 2회 | 대화 독점 방지 |
| 창 검증 | 목표 방 이름 재확인 | 엉뚱한 방 전송 방지 |
| JSON 파싱 실패 | skip | 안전 우선 |

**실사고(09-17):** `replied_keys` 미등록 + OCR 변형으로 같은 메시지에 2~3회 응답 → 스팸. 봇 강제 종료.

## 호출어

- 정식: **`다온아`**
- 별칭: **`라온아`** (기존 커뮤니티 공지 호환 — 과도기 유지)
- OCR 오독 복구: `'온 아 라'`·`'다 은 아'`·`'Qe 다 온 아'` → 모두 호출로 읽는다

## 검증 체크리스트

- [ ] `/api/profiles` 응답에 `다온(응대)` 포함
- [ ] `has_env: true` (API 키 로드 가능)
- [ ] SOUL.md + AGENTS.md 로드 성공 (길이 확인)
- [ ] 실제 로그 10건 판정 **10/10** 통과
- [ ] 중복 응답 0건 (3회 연속 스캔)
- [ ] `다온아 C:\daon 보여줘` → 거절 응답
- [ ] JSON 파싱 실패 시 skip 동작

## 관련

- 스킬 `kakao-talk-bidirectional-limits` — 카톡 PC 자동화의 구조적 한계와 실측 함정 Q-11~17
- 스킬 `daon-static-agent-creation` — 정적 specialist YAML 등록 (다른 계층)
- `api/api/profiles.py` — 프로필 목록·전환·생성
- `api/api/dynamic/compiler.py:24` — 정적 프로필 별칭 라우팅

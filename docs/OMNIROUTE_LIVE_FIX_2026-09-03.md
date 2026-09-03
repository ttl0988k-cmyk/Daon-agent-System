# OmniRoute 무료/다중 모델 라우팅 복원 (2026-09-03, 최종 수정)

## 목표

> **OmniRoute의 "무료/다중 모델 라우팅" 기반을 살린다.**
> MiniMax를 OmniRoute 안에 주입하는 게 아니라, 무료 프로바이더들이 한 엔드포인트
> (`http://localhost:20128/v1`) 아래에서 자동 라우팅 되게 하는 것.

## 최종 상태 (전부 실측 검증 완료)

- 서버: `omniroute serve --port 20128 --no-open --no-tray --daemon` (v3.8.50, 데몬)
- **활성 콤보 `free-auto`** (priority, 전부 $0.00):
  1. `groq/openai/gpt-oss-120b` ← Primary (200 응답 확인)
  2. `groq/qwen/qwen3.8-27b`   (fallback)
  3. `groq/groq/compound`       (fallback)
  4. `nvidia/deepseek-ai/deepseek-v4-pro-0813` (fallback)
  5. `nvidia/deepseek-ai/deepseek-v4-flash-0731` (fallback)
- `auto` → `groq/openai/gpt-oss-120b` (200, "OK")
- `auto/coding` → `groq/openai/gpt-oss-120b` (200)
- 스트리밍(SSE) 정상 청크 수신 (`model: openai/gpt-oss-120b`)
- Daon `custom_providers.json` omniroute 모델 목록을 실측 ID로 교체
  (auto / auto/coding / groq 3종 / nvidia 2종)

## 있었던 문제와 해결 (시행착오 포함)

1. **OmniRoute에 구성된 프로바이더 연결이 없었다**
   → `auto`가 noauth 무료 엔드포인트(opencode free `oc/*`, felo-web)만 순회하며 전멸:
   - `oc/*` 무료: IP당 rate limit → **429** (`account=noauth`)
   - `oc/muse-spark-1.2`: opencode 키 없음 → **402**
   - `oc/hy3-free`: 업스트림에서 모델 삭제 → **401**
   - felo-*: 브릿지가 400 반환
2. **이전 시도의 minimax 주입이 남아 있었다**
   → `key_value`에 `auto:auto → minimax` 라우팅 캐시가 남아 `auto`가 계속
   MiniMax로 갔음. DB에서 `auto:*` 캐시 삭제 + minimax 연결/캐시 제거.
3. **포트 오작동**: `node server-ws.mjs`를 직접 실행하면 3000번으로 뜸.
   → **반드시 `omniroute serve --port 20128 --daemon`** 으로 실행 (기본이 20128).

각 프로바이더 라이브 상태:
| provider | 상태 | 확인 |
| --- | --- | --- |
| groq | ✅ active (key 유효, 잔여 999+/1000) | `groq/openai/gpt-oss-120b` 200 |
| nvidia | ✅ active (key 유효) | 단, 응답이 느려 15s 로컬 큐에 걸릴 수 있음 → 콤보 하위 fallback으로 배치 |
| pollinations | ❌ 키 invalid (401) | 무료 익명 티어 폐지됨 — 문서/안내의 "$0 키 없음"은 현재 유효하지 않음 |
| cloudflare-ai | ⚠️ Account ID 누락 (502) | API 토큰 + Account ID 설정 필요 |
| longcat | ❌ credits_exhausted (402) | 키는 유효하나 토큰 쿼터 소진 |
| opencode | ❌ 429/402 | opencode API 키 등록 시 `oc/*` 복원 가능 |
| felo-web | ❌ 400 | 브릿지 오류 |

## 실행 커맨드

```powershell
# 1) 서버 기동 (가장 중요: 반드시 omniroute CLI 통해서)
omniroute serve --port 20128 --no-open --no-tray --daemon

# 2) 콤보 확인
omniroute combo list
omniroute simulate "hi" --combo free-auto --explain

# 3) 프로바이더 상태
omniroute providers list
```

## 참고

- OmniRoute `/v1/models` 는 `omniroute-local` 키로 401 (기존 특성). Daon은
  저장된 정적 목록을 쓰므로 무영향.
- Daon 쪽 fallback 체인(MiniMax 직결)은 그대로 유지 — OmniRoute 장애 시 안전망.
- 작업용 임시 스크립트는 `c:\daon\Daon agent System\_probe\omni_*.mjs`.

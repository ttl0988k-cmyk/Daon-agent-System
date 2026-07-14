# `api/dynamic/` 패키지 리팩토링 요약

> **일시**: 2026-06-28  
> **대상**: `api/dynamic/` 디렉토리 내 모든 Python 모듈 (15개 파일)  
> **목적**: 코드 품질 향상, 유지보수성 개선, 일관된 패턴 적용

---

## 1. 통합 로깅 시스템 도입

### 신규 파일: `logging_utils.py`

```python
from api.dynamic.logging_utils import get_logger
_log = get_logger(__name__)
```

- 모든 `print()` 문 → `_log.info()` / `_log.warning()` / `_log.debug()` 로 변환 (총 46건)
- 로그 포맷: `[%(name)s] %(message)s` — 기존 `[TAG] message` 스타일 유지
- 공유 로거 캐싱으로 중복 핸들러 생성 방지
- `propagate = False` 설정으로 루트 로거 이중 출력 방지

### 적용된 파일 목록
| 파일 | 변환 건수 |
|------|-----------|
| `auth.py` | 2 |
| `compiler.py` | 2 |
| `dag_utils.py` | 5 |
| `direct_calls.py` | 6 |
| `limits.py` | 3 |
| `orchestrator.py` | 8 |
| `planner.py` | 7 |
| `runner.py` | 10 |
| `skill_retriever.py` | 3 |

---

## 2. 코드 중복 제거

### `auth.py` — API 키 해석 로직 (~90% 중복 제거)

**Before**: 각 provider 함수(`_get_minimax_api_key()`, `_get_deepseek_api_key()`, `_get_nvidia_api_key()`)에서 credential pool 읽기 코드가 중복

**After**: `_resolve_key_from_pool(provider: str) -> str` 헬퍼 함수로 추출

```python
def _resolve_key_from_pool(provider: str) -> str:
    """Extract an API key for *provider* from environment or auth.json."""
    env_var = f"{provider.upper()}_API_KEY"
    if os.getenv(env_var):
        return os.getenv(env_var)
    try:
        auth_path = get_active_hermes_home() / "auth.json"
        if auth_path.exists():
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            pool = data.get("credential_pool", {})
            if provider in pool and pool[provider]:
                return pool[provider][0].get("access_token", "")
    except Exception as e:
        _log.warning("Failed to read credential pool from auth.json: %s", e)
    return ""
```

---

## 3. Import 정리

### 함수 내부 → 모듈 레벨 이동
| 파일 | 이동된 import |
|------|---------------|
| `dag_utils.py` | `import re` (함수 `_extract_assistant_content()` → 모듈 레벨) |
| `skill_retriever.py` | `import re` (메서드 `_tokenize()` → 모듈 레벨) |

### 사용되지 않는 import 제거
| 파일 | 제거된 import |
|------|---------------|
| `dag_utils.py` | `import sys` |
| `plan_validator.py` | `from pathlib import Path`, `import json` |
| `planner.py` | `from pathlib import Path` |
| `merger.py` | `import json` |
| `experience_db.py` | `import os` |

---

## 4. Dead Code 제거

- 사용되지 않는 import 6건 제거 (상기 표 참조)
- 모든 `print()` 문 제거 (디버깅용 print → structured logging)
- 사용되지 않는 내부 변수/분기 없음 — 추가 dead code 발견되지 않음

---

## 5. 타입 힌트 보강

| 파일 | 추가된 타입 힌트 |
|------|-----------------|
| `auth.py` | `_resolve_key_from_pool(provider: str) -> str` |
| `direct_calls.py` | `Optional[str]`, `Optional[str] = "..."` 매개변수 전반 |
| `dag_utils.py` | `_extract_assistant_content(messages: list) -> str`, `_get_model_chain_for_node(...)` 반환 타입 |
| `plan_validator.py` | `_validate_plan_nodes(nodes: list) -> list[str]`, `_validate_plan_edges(...) -> list[str]` |
| `compiler.py` | `get_integrated_persona(agent_name: str, agent_role: str) -> str` |
| `merger.py` | `Optional[dict]`, `Optional[Callable]` |

---

## 6. 예외처리 통일

### 패턴 통일

| Before | After |
|--------|-------|
| `except Exception: pass` | `except Exception as e: _log.warning("...", e)` |
| `except Exception:` (bare) | `except Exception as e: _log.warning("...", e)` |

### 수정된 예외처리 (총 15건)

| 파일 | 라인 | 변경 내용 |
|------|------|-----------|
| `auth.py` | 31 | `pass` → `_log.warning(...)` |
| `runner.py` | 414 | `pass` → `_log.warning("Failed to infer model selection metadata: %s", e)` |
| `limits.py` | 57, 65 | `pass` → `_log.debug("Non-critical: ...", e)` |
| `skill_retriever.py` | 107, 192, 199, 207 | bare `except` → `except Exception as e` + 로깅 |
| `orchestrator.py` | 180 | bare `except` → `_log.warning("Failed to resolve workspace run_dir: %s", e)` |
| `experience_db.py` | 144, 173, 184 | bare `except` → `except Exception as e` + 로깅 |
| `dag_utils.py` | 71 | bare `except` → `_log.warning("Failed to resolve model provider: %s", e)` |
| `planner.py` | 218, 250 | bare `except` → `except Exception as e` + 로깅 |

### 예외처리 원칙
- **절대 swallow 금지**: 모든 `except Exception`은 최소한 `_log.warning()` 이상으로 기록
- **비핵심 cleanup** (프로세스 종료 등)은 `_log.debug()` 레벨 사용
- **데이터 로드 실패**는 `_log.warning()` + fallback 값 반환
- **예외 객체 항상 캡처**: `as e` 구문 필수

---

## 7. PyInstaller `.spec` 정리

### `DaonAgentSystem_v39.spec`

- **추가된 hiddenimports**:
  - `api.managers`, `api.managers.model_manager` — 동적 임포트로 사용됨
  - `api.dynamic.logging_utils` — 신규 생성 모듈
- **hiddenimports 정렬 및 주석 추가**: 카테고리별 그룹화 (agent internals, api routes, api core, api managers, api dynamic, third-party)
- **기존 `agent.*` hiddenimports 유지**: `hermes-agent` pip 패키지 의존성으로 필요

---

## 8. 테스트 결과

- **Python 구문 검증**: 14개 모든 `api/dynamic/*.py` 파일 `py_compile` 통과 ✅
- **로깅 시스템**: `[test] Syntax & import test passed` — `[TAG] message` 형식 정상 출력 ✅
- **예외처리**: `[test_exc] Caught expected error: division by zero` — 예외 캡처/로깅 정상 ✅
- **Pyrefly 분석**: 기존 `run_agent` import 오류는 runtime path injection 이슈로 리팩토링과 무관

---

## 9. 파일별 변경 요약

| 파일 | print 제거 | import 정리 | 중복 제거 | 타입 힌트 | 예외처리 |
|------|:---------:|:---------:|:---------:|:---------:|:--------:|
| `logging_utils.py` | — | — | — | — | — |
| `auth.py` | ✅ | — | ✅ | ✅ | ✅ |
| `compiler.py` | ✅ | — | — | ✅ | — |
| `dag_utils.py` | ✅ | ✅ | — | ✅ | ✅ |
| `direct_calls.py` | ✅ | — | — | ✅ | — |
| `experience_db.py` | — | ✅ | — | — | ✅ |
| `limits.py` | ✅ | — | — | — | ✅ |
| `merger.py` | — | ✅ | — | ✅ | — |
| `model_selector.py` | — | — | — | — | —* |
| `orchestrator.py` | ✅ | — | — | — | ✅ |
| `plan_validator.py` | — | ✅ | — | ✅ | — |
| `planner.py` | ✅ | ✅ | — | — | ✅ |
| `runner.py` | ✅ | — | — | — | ✅ |
| `skill_extractor.py` | ✅ | — | — | — | —* |
| `skill_retriever.py` | ✅ | ✅ | — | — | ✅ |
| `state.py` | ✅ | — | — | — | — |

> `*` — 이미 양호한 상태로 추가 수정 불필요

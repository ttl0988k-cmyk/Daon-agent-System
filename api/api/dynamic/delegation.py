"""
갭 D: 재귀적 위임(recursive delegation) 통치 구조.

하위 에이전트가 자기 작업에 필요한 하위 조직을 스스로 구성할 수 있게 하면서도
"Agent 폭발"을 막기 위한 최소 통치 계층을 제공한다:

- 스레드 로컬 위임 컨텍스트: 각 노드 스레드가 자기 깊이/혈통을 읽을 수 있게 한다.
  (delegate_team 도구가 가드 판정 시 읽는다.)
- 스폰 예산 카운터: 루트 미션 단위 총 위임 횟수 상한(max_total_spawns)을 원자적으로 강제한다.
- 가드 체크: 컨텍스트 존재/깊이 여유/생성 이유(spawn_reason) 필수 검증을 순수 함수로 제공한다.

설계 원칙:
- 위임은 "도구 호출"이다 — 러너를 고치지 않고 노드가 delegate_team 도구를 쓰는 것으로
  중첩 DAG가 시작된다.
- 거부는 절대 노드를 죽이지 않는다 — 도구는 구조화된 JSON 거부 사유를 반환하고
  에이전트는 자기 능력으로 직접 처리하면 된다(fail-open).
"""

import threading

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# ── 스레드 로컬 위임 컨텍스트 ──
# ParallelRunner가 각 노드 스레드 시작 시 set_current_delegation()으로 주입한다.
# 컨텍스트 구조:
#   {
#     "run_id": 현재 실행의 run_id,
#     "root_run_id": 최상위(깊이 0) 실행의 run_id,
#     "parent_run_id": 나를 위임한 실행의 run_id (루트는 None),
#     "depth": 0(루트)부터 시작하는 위임 깊이,
#     "run_dir": 현재 실행의 산출물 디렉토리 (자식 run_dir의 기준),
#     "allowed_providers": (선택) 루트가 허용한 프로바이더 목록,
#   }
_tls = threading.local()


def set_current_delegation(ctx: dict) -> None:
    """현재 노드 스레드의 위임 컨텍스트를 설정한다."""
    _tls.ctx = ctx if isinstance(ctx, dict) else None


def get_current_delegation() -> dict | None:
    """현재 노드 스레드의 위임 컨텍스트를 반환한다. 하네스 밖에서는 None."""
    return getattr(_tls, "ctx", None)


def clear_current_delegation() -> None:
    """노드 종료 시 스레드 로컬 컨텍스트를 정리한다."""
    _tls.ctx = None


# ── 스폰 예산 카운터 (루트 미션당 총 위임 횟수) ──
_SPAWN_COUNTER: dict = {}
_SPAWN_LOCK = threading.Lock()


def count_spawns(root_run_id: str) -> int:
    """루트 미션에서 지금까지 소비된 위임 횟수를 반환한다."""
    with _SPAWN_LOCK:
        return _SPAWN_COUNTER.get(root_run_id, 0)


def try_consume_spawn_budget(root_run_id: str, max_total: int) -> tuple:
    """원자적으로 위임 슬롯 1개를 소비한다.

    반환: (성공 여부, 소비 후 누적 횟수). 상한 도달 시 (False, 현재 횟수).
    """
    try:
        max_total = int(max_total)
    except (TypeError, ValueError):
        max_total = 0
    if max_total <= 0 or not root_run_id:
        return False, 0
    with _SPAWN_LOCK:
        current = _SPAWN_COUNTER.get(root_run_id, 0)
        if current >= max_total:
            return False, current
        _SPAWN_COUNTER[root_run_id] = current + 1
        return True, current + 1


def reset_spawn_budget(root_run_id: str) -> None:
    """루트 미션의 위임 카운터를 제거한다 (정리/테스트용)."""
    with _SPAWN_LOCK:
        _SPAWN_COUNTER.pop(root_run_id, None)


# ── 가드 체크 (순수 함수 — 예산 소비 없음) ──
def check_delegation_guard(ctx: dict | None, limits: dict, spawn_reason: str) -> tuple:
    """위임 요청의 사전 가드 판정.

    반환: (허용 여부, 사유 문자열).
    체크 항목:
      1) 하네스 노드 내부인지 (ctx 존재)
      2) 깊이 여유 (depth + 1 <= max_depth)
      3) 생성 이유(spawn_reason) 필수 — "왜 하위 에이전트가 필요한가" 기록 강제
    """
    if not isinstance(ctx, dict):
        return False, "Delegation is only available inside a Dynamic Harness node (no delegation context)."
    try:
        depth = int(ctx.get("depth", 0))
    except (TypeError, ValueError):
        depth = 0
    max_depth = int((limits or {}).get("delegation", {}).get("max_depth", 1))
    if depth + 1 > max_depth:
        return False, (
            f"Delegation depth limit reached: this node is at depth {depth} "
            f"and max_depth is {max_depth}. Handle the subtask yourself with your own tools."
        )
    if not str(spawn_reason or "").strip():
        return False, (
            "spawn_reason is required: you must record WHY a sub-team is needed "
            "before delegating. Handle the subtask yourself or provide a reason."
        )
    return True, "ok"

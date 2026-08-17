#!/usr/bin/env python3
"""
Delegate Team Tool (갭 D-1)

다이나믹 하네스 노드가 자기 서브태스크를 위해 하위 에이전트 팀(자식 실행)을
만들 수 있게 하는 위임 도구. "위임은 도구 호출이다" 원칙에 따라, 재귀적
위임은 러너 코어 변경 없이 도구 호출 + 거버넌스 가드로 구현된다.

거버넌스 (Agent 폭발 방지):
- 깊이 가드(max_depth): 현재 노드의 depth가 한계이면 거부.
- 생성 사유 필수(spawn_reason): "왜 하위 팀이 필요한가"를 먼저 기록해야 한다.
- 총 생성 예산(max_total_spawns): 루트 미션 전체의 위임 횟수 상한.
- 거부 시 에이전트를 죽이지 않고 "직접 처리하라"는 구조화 JSON을 반환(fail-open).

자식 실행은 도구 호출 안에서 동기 실행되며 결과가 도구 결과로 반환된다.
자식 실행도 갭 C의 수용 기준 검증/자기 치유 루프를 그대로 받는다(프랙탈 품질 게이트).
자식 실행은 clarification/승인 게이트를 거치지 않는다 (runner.run 직접 호출 경로).
"""

import sys
import uuid
from pathlib import Path
from typing import Optional

# OpenAI Function-Calling Schema
DELEGATE_TEAM_SCHEMA = {
    "name": "delegate_team",
    "description": (
        "Delegate a self-contained subtask to a freshly spawned sub-team "
        "(a nested Dynamic Harness run that plans its own agent DAG, executes it, "
        "and verifies the result against acceptance criteria). "
        "Use ONLY when the subtask is genuinely multi-agent scale work that would "
        "overload your own context if you handled it alone (e.g. a full research "
        "branch, a separate component build, an independent verification pass). "
        "Do NOT use for small steps you can do with your own tools. "
        "spawn_reason is MANDATORY: you must state WHY a sub-team is needed before "
        "delegating. Delegation is budget-limited (depth and total spawn count); "
        "if refused, handle the subtask yourself with your own tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The complete, self-contained subtask for the sub-team. Must include "
                    "all context the sub-team needs (it does not see your conversation)."
                ),
            },
            "spawn_reason": {
                "type": "string",
                "description": (
                    "REQUIRED: why a separate sub-team is needed for this subtask instead "
                    "of handling it yourself. Recorded in the delegation lineage for audit."
                ),
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of specific, verifiable completion conditions for the "
                    "sub-team's output. The nested run verifies against them and self-heals "
                    "until met. If omitted, criteria are extracted automatically."
                ),
            },
            "preferred_model": {
                "type": "string",
                "description": "Optional preferred model for the sub-team orchestration.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional skill names to delegate to the sub-team (capability "
                    "delegation). Injected as mandatory directives into the sub-planner."
                ),
            },
        },
        "required": ["task", "spawn_reason"],
    },
}


def _refusal(reason: str) -> str:
    """가드 거부 응답: 에이전트가 직접 처리하도록 안내하는 구조화 JSON."""
    return tool_result(
        delegated=False,
        reason=reason,
        instruction=(
            "Delegation was refused by the governance guard. Do NOT retry "
            "delegate_team for this subtask. Handle it yourself with your own tools."
        ),
    )


def _ensure_child_acceptance(task: str, preferred_model: Optional[str],
                             acceptance_criteria: Optional[list]) -> str:
    """자식 실행에도 수용 기준 마커를 보장한다 (갭 C 프랙탈). 실패 시 fail-open."""
    try:
        from api.dynamic.clarifier import ensure_acceptance_criteria
        criteria = [str(c).strip() for c in (acceptance_criteria or []) if str(c).strip()]
        return ensure_acceptance_criteria(task, preferred_model, precomputed=criteria)
    except Exception as exc:
        print(f"[DelegateTeam] Warning: acceptance criteria attach failed: {exc}", flush=True)
        return task


def delegate_team(task: str, spawn_reason: str, acceptance_criteria: Optional[list] = None,
                  preferred_model: Optional[str] = None, skills: Optional[list] = None) -> str:
    """위임 가드 판정 후 자식 다이나믹 하네스 실행을 동기 수행한다."""
    if not task or not str(task).strip():
        return tool_error("task is required.")

    task = str(task).strip()
    spawn_reason = str(spawn_reason or "").strip()

    # ── 1) 위임 컨텍스트 + 가드 판정 ──
    try:
        from api.dynamic.delegation import (
            get_current_delegation,
            check_delegation_guard,
            try_consume_spawn_budget,
            get_delegation_log_callback,
        )
    except Exception as exc:
        return _refusal(f"Delegation subsystem unavailable: {exc}")

    ctx = get_current_delegation()
    try:
        from api.dynamic.limits import _load_harness_limits
        limits = _load_harness_limits()
    except Exception:
        limits = {}

    allowed, guard_msg = check_delegation_guard(ctx, limits, spawn_reason)
    if not allowed:
        return _refusal(guard_msg)

    # ── 2) 총 생성 예산 차감 (루트 미션 단위) ──
    root_run_id = ctx.get("root_run_id") or ctx.get("run_id") or ""
    max_total = int((limits.get("delegation") or {}).get("max_total_spawns", 6) or 0)
    ok, used = try_consume_spawn_budget(root_run_id, max_total)
    if not ok:
        return _refusal(
            f"Total spawn budget exhausted: this mission already used {used}/{max_total} "
            f"delegations."
        )

    max_children = int((limits.get("delegation") or {}).get("max_children_per_spawn", 4) or 4)

    # ── 3) 자식 실행 ID + 혈통 등록 (부모 취소 시 연쇄 취소의 근거) ──
    try:
        depth = int(ctx.get("depth", 0))
    except (TypeError, ValueError):
        depth = 0
    parent_run_id = ctx.get("run_id") or ""
    child_run_id = f"dlg_{uuid.uuid4().hex[:12]}"

    try:
        import api.dynamic_jobs as dj
        dj.register_lineage(child_run_id, parent_run_id, root_run_id, depth + 1, spawn_reason)
    except Exception as e:
        print(f"[DelegateTeam] Warning: lineage registration failed: {e}", flush=True)

    # ── 4) 자식 실행 작업 디렉터리 (부모 run_dir/delegated/ 아래) ──
    child_run_dir = None
    parent_run_dir = ctx.get("run_dir")
    if parent_run_dir:
        try:
            child_run_dir = Path(parent_run_dir) / "delegated" / child_run_id
            child_run_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            child_run_dir = None

    child_ctx = {
        "run_id": child_run_id,
        "root_run_id": root_run_id,
        "parent_run_id": parent_run_id,
        "depth": depth + 1,
        "run_dir": str(child_run_dir) if child_run_dir else None,
        "spawn_reason": spawn_reason,
    }

    # ── 5) 위임 지시문 주입 + 수용 기준 부착 ──
    directive = (
        "[위임 지시문] 이 작업은 상위 에이전트가 위임한 서브팀 미션이다.\n"
        f"- 위임 사유: {spawn_reason}\n"
        f"- 서브팀 규모 상한: 최대 {max_children}개의 에이전트로 구성할 것 (초과 금지)\n"
        "- 깊이 제한: 이 실행 안에서는 delegate_team 도구를 다시 호출하지 말 것.\n"
    )
    run_task = directive + "\n" + task
    run_task = _ensure_child_acceptance(run_task, preferred_model, acceptance_criteria)

    # ── 6) 러너 임포트 ──
    try:
        from api.dynamic_hermes import HermesDynamicRunner
    except ImportError:
        root_dir = Path(__file__).resolve().parent.parent.parent
        if str(root_dir) not in sys.path:
            sys.path.append(str(root_dir))
        try:
            from api.dynamic_hermes import HermesDynamicRunner
        except ImportError as exc:
            return tool_error(f"Failed to import HermesDynamicRunner: {exc}")

    # ── 7) 자식 실행 동기 수행 ──
    # 갭 D-3: 부모 실행이 등록한 로그 콜백을 중계 레지스트리에서 조회한다.
    # 자식 로그가 부모 잡의 로그 스트림으로 전달되어야 UI에 서브팀 카드가 생긴다.
    parent_log_cb = None
    try:
        parent_log_cb = get_delegation_log_callback(parent_run_id)
    except Exception:
        parent_log_cb = None

    def log_callback(agent_name: str, content: str, status: str = "running"):
        try:
            print(f"[DelegateTeam:{child_run_id}] [{agent_name}] ({status}): "
                  f"{str(content).strip()}", flush=True)
        except Exception:
            pass
        # 갭 D-3: 부모 스트림으로 중계 (서브팀 배지 접두어 부착).
        # 전달 실패해도 자식 실행은 계속된다 (fail-open).
        if parent_log_cb is not None:
            try:
                parent_log_cb(f"서브팀·{agent_name}", content, status)
            except Exception:
                pass

    try:
        print(f"[DelegateTeam] Spawning sub-team (run_id={child_run_id}, depth={depth + 1}, "
              f"reason='{spawn_reason[:120]}')", flush=True)
        runner = HermesDynamicRunner()
        res = runner.run(
            task=run_task,
            preferred_model=preferred_model,
            log_callback=log_callback,
            run_dir=str(child_run_dir) if child_run_dir else None,
            run_id=child_run_id,
            forced_skills=skills or None,
            delegation_context=child_ctx,
        )
        final_output = (res or {}).get("final_output", "")
        saved_paths = (res or {}).get("saved_paths", {})
        return tool_result(
            delegated=True,
            child_run_id=child_run_id,
            depth=depth + 1,
            spawn_reason=spawn_reason,
            final_output=final_output,
            saved_paths=saved_paths,
        )
    except Exception as e:
        return tool_error(
            f"Delegated sub-team execution failed: {e}. Handle the subtask yourself "
            f"with your own tools."
        )
    finally:
        # 실행 종료 후 혈통 정리 (취소 연쇄는 실행 중에만 필요)
        try:
            import api.dynamic_jobs as dj
            dj.unregister_lineage_subtree(child_run_id)
        except Exception:
            pass


def check_delegate_team_requirements() -> bool:
    """Delegate tool requires the dynamic_hermes engine (same as execute_dynamic_harness)."""
    try:
        from api.dynamic_hermes import HermesDynamicRunner  # noqa: F401
        return True
    except Exception:
        pass
    root_dir = Path(__file__).resolve().parent.parent.parent
    candidates = (
        root_dir / "api" / "api" / "dynamic_hermes.py",
        root_dir / "api" / "dynamic_hermes.py",
    )
    return any(p.exists() for p in candidates)


# --- Registry ---
from tools.registry import registry, tool_error, tool_result

registry.register(
    name="delegate_team",
    toolset="delegation",
    schema=DELEGATE_TEAM_SCHEMA,
    handler=lambda args, **kw: delegate_team(
        task=args.get("task", ""),
        spawn_reason=args.get("spawn_reason", ""),
        acceptance_criteria=args.get("acceptance_criteria") or None,
        preferred_model=args.get("preferred_model"),
        skills=args.get("skills") or None,
    ),
    check_fn=check_delegate_team_requirements,
    emoji="🤝",
)

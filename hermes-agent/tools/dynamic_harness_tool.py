#!/usr/bin/env python3
"""
Dynamic Harness Tool Module

Exposes the Dynamic Harness (multi-agent JIT compiler DAG) to the Hermes agent
as a tool, allowing autonomous multi-agent execution directly from the chat window.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# OpenAI Function-Calling Schema
DYNAMIC_HARNESS_SCHEMA = {
    "name": "execute_dynamic_harness",
    "description": (
        "Execute the Hermes Dynamic Harness engine (multi-agent JIT compiler DAG) "
        "for complex multi-agent tasks (e.g. planning, detailed research, software design, "
        "writing detailed reports, code generation/review, and complex orchestration). "
        "Runs a pool of specialized agents in a topological DAG to complete the requested task. "
        "PRE-FLIGHT CHECKLIST (gap A): before calling, make sure you have (1) confirmed the "
        "user's goal/scope when it is ambiguous, (2) identified the expected deliverable, and "
        "(3) collected completion conditions as acceptance_criteria when the task is vague. "
        "The harness verifies the final output against these criteria and self-heals until they are met."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The complex, multi-agent task description to execute.",
            },
            "preferred_model": {
                "type": "string",
                "description": "Preferred model name for orchestration (e.g., 'MiniMax-M3', 'deepseek-chat'). Defaults to 'MiniMax-M3'.",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of skill names the user explicitly wants the harness to use. "
                    "Injected as a MANDATORY directive into the planner prompt (same as the HTTP API 'skills' field)."
                ),
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of acceptance criteria (completion conditions) gathered during the conversation. "
                    "Each criterion must be specific and verifiable. The harness verification agent judges the "
                    "final output against them and triggers self-healing re-plans when unmet. "
                    "If omitted, the harness extracts criteria automatically."
                ),
            },
        },
        "required": ["task"],
    },
}

def _ensure_acceptance_criteria(task: str, preferred_model: Optional[str], acceptance_criteria: Optional[list]) -> str:
    """갭 A: 챗 경로에서도 수용 기준 마커 섹션을 보장한다.

    갭 C 검증 에이전트(orchestrator._verify_acceptance)는 task에서 수용 기준을
    파싱하므로, 챗 경로도 섹션을 부착해야 자기 치유 루프가 동작한다.
    실패 시 원본 task를 반환해 파이프라인을 막지 않는다(fail-open).
    """
    try:
        from api.dynamic.clarifier import ensure_acceptance_criteria
        criteria = [str(c).strip() for c in (acceptance_criteria or []) if str(c).strip()]
        return ensure_acceptance_criteria(task, preferred_model, precomputed=criteria)
    except Exception as exc:
        print(f"[DynamicHarnessTool] Warning: acceptance criteria attach failed: {exc}", flush=True)
        return task


def execute_dynamic_harness(task: str, preferred_model: Optional[str] = None, forced_skills: Optional[list] = None,
                            acceptance_criteria: Optional[list] = None) -> str:
    """
    Run the Hermes Dynamic Harness on the requested task.
    """
    if not task or not task.strip():
        return tool_error("Task description is required.")

    task = task.strip()
    
    # Import HermesDynamicRunner dynamically to ensure PYTHONPATH is resolved properly
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

    try:
        print(f"[DynamicHarnessTool] Initializing runner for task: '{task}'...")
        runner = HermesDynamicRunner()
        
        # SSE put 함수를 실행 시작 전에 한 번만 캡처한다.
        # log_callback은 ThreadPoolExecutor가 만드는 새 스레드 안에서 호출되는데,
        # threading.local()은 스레드 간 공유가 안 되므로 미리 클로저로 캡처해야 한다.
        _captured_put_fn = None
        try:
            from api.streaming import get_current_thread_put
            _captured_put_fn = get_current_thread_put()
        except Exception:
            pass
        
        # Define a log callback to sync with Virtual Office Bridges & Agent Monitor
        def log_callback(agent_name: str, content: str, status: str = "running"):
            # Output progress to standard output so it shows in console logs
            print(f"[DynamicHarnessTool] [{agent_name}] ({status}): {content.strip()}", flush=True)
            
            # Chat SSE stream sync (Agent Monitor display)
            # _captured_put_fn은 메인 SSE 스레드에서 미리 캡처한 값이므로
            # ThreadPoolExecutor 자식 스레드에서도 안전하게 사용 가능하다.
            if _captured_put_fn:
                try:
                    _captured_put_fn('agent_log', {
                        'agent_id': agent_name,
                        'content': content,
                        'status': status
                    })
                except Exception as sse_err:
                    print(f"[DynamicHarnessTool] Warning: Failed to stream log to SSE: {sse_err}", flush=True)
            
            # Virtual Office Sync (Bridge integration)
            try:
                import re
                import time
                
                root_dir = Path(__file__).resolve().parent.parent.parent
                office_bridges_dir = root_dir / "daon-agent-office" / "bridges"
                if office_bridges_dir.exists():
                    base_name = str(agent_name).split(' (')[0]
                    safe_name = re.sub(r'[^a-zA-Z0-9가-힣]', '', base_name)
                    char_id = f"dyn_{safe_name}"
                    
                    # spawn 파일: 매번 덮어써서 fs.watch()가 확실히 감지하도록 함
                    # (파일이 이미 존재해도 내용이 바뀌어야 watch 이벤트가 발생)
                    if status == "running":
                        spawn_path = office_bridges_dir / f"spawn_{char_id}.json"
                        with open(spawn_path, "w", encoding="utf-8") as f:
                            json.dump({
                                "action": "spawn",
                                "agent": char_id,
                                "config": {
                                    "id": char_id,
                                    "name": agent_name,
                                    "role": "자율 에이전트",
                                    "color": 0x10b981
                                },
                                "timestamp": time.time()  # 매번 달라져서 watch 트리거 보장
                            }, f, ensure_ascii=False)
                    
                    # status 파일: 에이전트 현재 상태 갱신
                    status_path = office_bridges_dir / f"{char_id}.json"
                    with open(status_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "action": "status",
                            "agent": char_id,
                            "status": "thinking" if status == "running" else "completed",
                            "log": (content[:100] + "...") if content and len(content) > 100 else content,
                            "timestamp": time.time()
                        }, f, ensure_ascii=False)
                        
            except Exception as bridge_err:
                print(f"[DynamicHarnessTool] Warning: Failed to sync with virtual office: {bridge_err}", flush=True)
        
        # Execute the dynamic compiler run with log callback
        # forced_skills: 사용자가 명시적으로 지정한 스킬 목록 (HTTP API 경로의 'skills' 필드와 동일)
        # 갭 A: 챗 경로에서도 수용 기준 섹션을 보장한다 (갭 C 검증 에이전트의 판정 근거).
        # 표시용으로는 원본 task를 유지하고, enriched task를 runner에 전달한다.
        run_task = _ensure_acceptance_criteria(task, preferred_model, acceptance_criteria)
        res = runner.run(task=run_task, preferred_model=preferred_model, log_callback=log_callback, forced_skills=forced_skills)
        
        # Process and format results
        final_output = res.get("final_output", "")
        saved_paths = res.get("saved_paths", {})
        runner_results = res.get("runner_results", [])
        
        # Build node execution summary
        nodes_summary = []
        for node in runner_results:
            status_emoji = "✅" if node.get("status") == "success" else "❌"
            nodes_summary.append(
                f"- {status_emoji} **{node.get('name')}** ({node.get('role')}): {node.get('status')}"
            )
        nodes_summary_str = "\n".join(nodes_summary) if nodes_summary else "실행된 에이전트 없음"

        # Format final output for the agent response
        formatted_md = (
            f"### 🎯 다이나믹 하네스 실행 완료\n\n"
            f"**요청된 작업:** {task}\n"
            f"**사용 모델:** {preferred_model or 'MiniMax-M3'}\n\n"
            f"#### 📋 에이전트 팀 실행 요약\n"
            f"{nodes_summary_str}\n\n"
            f"#### 📄 최종 병합 보고서\n\n"
            f"{final_output}\n\n"
            f"--------------------------------------------------\n"
            f"*결과 파일 및 메타데이터가 다음 경로에 저장되었습니다:*\n"
            f"- **최종 출력 파일:** `{saved_paths.get('final_output_file')}`\n"
            f"- **실행 메타데이터:** `{saved_paths.get('metadata_file')}`\n"
            f"- **성능 메트릭스:** `{saved_paths.get('metrics_file')}`\n"
        )
        
        return tool_result(
            success=True,
            task=task,
            summary=nodes_summary_str,
            final_output=final_output,
            saved_paths=saved_paths,
            formatted_output=formatted_md
        )
        
    except Exception as e:
        import traceback
        err_msg = f"Dynamic Harness execution failed: {e}\n{traceback.format_exc()}"
        print(f"[DynamicHarnessTool] Error: {err_msg}")
        return tool_error(err_msg)

def check_dynamic_harness_requirements() -> bool:
    """Dynamic Harness tool is available as long as the dynamic_hermes engine is present."""
    # Fast path: engine already importable. At server runtime the workspace root
    # and <root>/api are on sys.path, so `api.dynamic_hermes` resolves to
    # api/api/dynamic_hermes.py (regular package wins over the namespace dir).
    try:
        from api.dynamic_hermes import HermesDynamicRunner  # noqa: F401
        return True
    except Exception:
        pass
    # Fallback: file-existence check covering both layouts.
    # After the root-as-truth refactor (7c06a6e) the engine lives at
    # <root>/api/api/dynamic_hermes.py in dev, while PyInstaller builds
    # (daon-server.spec) flatten it back to <root>/api/dynamic_hermes.py.
    root_dir = Path(__file__).resolve().parent.parent.parent
    candidates = (
        root_dir / "api" / "api" / "dynamic_hermes.py",
        root_dir / "api" / "dynamic_hermes.py",
    )
    return any(p.exists() for p in candidates)

# --- Registry ---
from tools.registry import registry, tool_error, tool_result

registry.register(
    name="execute_dynamic_harness",
    toolset="dynamic_harness",
    schema=DYNAMIC_HARNESS_SCHEMA,
    handler=lambda args, **kw: execute_dynamic_harness(
        task=args.get("task", ""),
        preferred_model=args.get("preferred_model"),
        forced_skills=args.get("skills") or None,
        acceptance_criteria=args.get("acceptance_criteria") or None
    ),
    check_fn=check_dynamic_harness_requirements,
    emoji="🎯",
)

"""
Main Dynamic Hermes Orchestrator: coordinates the entire JIT compiler flow.

Provides:
- HermesDynamicRunner: Planner → Compiler → ParallelRunner → Merger pipeline
  with recovery re-planning for failed nodes and CodeReviewer pass
"""

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from api.dynamic.state import HermesStateManager, MissionMetrics, NodeMetrics
from api.dynamic.limits import _load_harness_limits, cleanup_harness_artifacts
from api.dynamic.planner import HermesPlanner
from api.dynamic.compiler import AgentCompiler
from api.dynamic.runner import ParallelRunner
from api.dynamic.merger import ResultMerger
from api.dynamic.skill_extractor import _extract_and_save_skill
from api.dynamic.direct_calls import _call_direct
from api.dynamic.model_selector import get_skill_history, extract_task_context, build_context_keys
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


# Safe fallback for _send_bridge_complete if not injected globally
if '_send_bridge_complete' not in globals() and '_send_bridge_complete' not in locals():
    def _send_bridge_complete(msg: str) -> None:
        _log.info("[Bridge COMPLETE] %s", msg)


class HermesDynamicRunner:
    """Convenience class to coordinate the entire dynamic JIT compiler flow."""

    def __init__(self) -> None:
        self.planner = HermesPlanner()
        self.compiler = AgentCompiler()
        self.runner = ParallelRunner()
        self.merger = ResultMerger()

    def _run_recovery_plan(
        self,
        failed_nodes: list[dict],
        runner_results: list[dict],
        state_manager: HermesStateManager,
        task: str,
        mission_tracker: dict,
        plan: dict,
        compiled_agents: list[dict],
        run_dir: str = None,
        session_id: str = None,
        run_id: str = None,
        allowed_providers: list = None,
    ) -> tuple[list[dict], dict, list[dict]]:
        """Handle JIT re-planning for failed nodes and execute the recovery DAG."""
        _log.info(
            "Failure detected in nodes: %s. Triggering dynamic re-planning...",
            [f['name'] for f in failed_nodes],
        )
        successful_outputs = state_manager.get_all_success_values()
        initial_outputs = [
            {
                "output_key": r["output_key"],
                "name": r["name"],
                "role": r["role"],
                "content": r["output"],
                "generation": r.get("generation", 0),
                "parents": r.get("parents", []),
            }
            for r in runner_results
            if r["status"] == "success"
        ]
        failed_info = "\n".join([f"- Node '{f['name']}': {f.get('output', 'Unknown error')}" for f in failed_nodes])
        replan_prompt = (
            f"We are executing a multi-agent system to solve this task: {task}\n\n"
            "We have already successfully executed several nodes and generated the following outputs:\n"
            f"{json.dumps(successful_outputs, ensure_ascii=False, indent=2)}\n\n"
            f"However, during execution, the following nodes failed:\n{failed_info}\n\n"
            "Please generate a new EXECUTABLE DAG of agents to complete the REMAINING parts of the task, focusing on fixing the reported failures.\n"
            "You MUST use the already successfully generated outputs as input keys where appropriate.\n"
            "Return a valid JSON object matching the standard Nodes and Edges schema."
        )
        _log.info("Calling Planner for dynamic rerouting plan...")
        replan = self.planner.plan(replan_prompt, mission_tracker=mission_tracker)
        _log.info("Generated recovery plan. Summary: %s", replan.get('plan_summary'))

        combined_nodes = [
            {
                "name": out["name"],
                "type": "llm",
                "role": out["role"],
                "system_prompt": "",
                "subtask": "",
                "input": "",
                "output": out["output_key"],
            }
            for out in initial_outputs
        ]
        combined_nodes.extend(replan.get("nodes", []))

        from api.dynamic.plan_validator import semantic_validate
        cycle_errors = semantic_validate({"nodes": combined_nodes, "edges": list(replan.get("edges", []))})
        if cycle_errors:
            raise ValueError(f"JIT Re-planning generated a cyclic or invalid cumulative DAG: {cycle_errors}")

        recompiled_agents = self.compiler.compile(replan)
        _log.info("Recompiled %d agents for recovery.", len(recompiled_agents))
        recovery_results = self.runner.run(
            recompiled_agents,
            replan.get("edges", []),
            task,
            initial_outputs=initial_outputs,
            state_manager=state_manager,
            generation=1,
            mission_tracker=mission_tracker,
            run_dir=str(run_dir) if run_dir else None,
            session_id=session_id, run_id=run_id)
        merged_results = [r for r in runner_results if r["status"] == "success"] + recovery_results
        merged_plan = {"first_run_plan": plan, "recovery_plan": replan}
        merged_agents = compiled_agents + recompiled_agents
        return merged_results, merged_plan, merged_agents

    def _run_code_reviewer(self, final_output: str, check_timeout, preferred_model: str = None) -> str:
        """Run CodeReviewer on the merged output if code blocks are present."""
        if "```" not in final_output:
            _log.info("No code blocks detected. CodeReviewer skipped.")
            return final_output
        check_timeout()
        _log.info("Code detected in output. Running CodeReviewer...")
        _system = (
            "You are a Senior Code Reviewer. Review the document below and fix code quality issues:\n"
            "- Spaghetti Code: refactor deeply nested blocks (>3 levels) into helpers.\n"
            "- Duplication: extract repeated logic into reusable functions.\n"
            "- Conventions: enforce snake_case for functions/vars, PascalCase for classes, and add missing docstrings.\n"
            "- Strict preservation: Keep all Korean text, Markdown structure, and headings intact."
        )
        try:
            reviewed = _call_direct(
                f"Please review and improve the following document for code quality:\n\n{final_output}",
                _system,
                preferred_model=preferred_model
            )
            if reviewed and reviewed.strip():
                _log.info("CodeReviewer applied improvements.")
                return reviewed
            _log.info("CodeReviewer returned empty. Keeping original output.")
        except Exception as review_err:
            _log.warning("CodeReviewer skipped due to error: %s", review_err)
        return final_output

    def run(self, task: str, preferred_model: str = None, log_callback=None, run_dir=None, planning_mode: bool = False, session_id: str = None, run_id: str = None, allowed_providers: list = None) -> dict:
        from api.dynamic.model_selector import set_allowed_providers
        set_allowed_providers(allowed_providers)
        
        limits = _load_harness_limits()
        mission_start = time.time()
        mission_timeout = limits["mission"]["max_total_wall_time_seconds"]

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"dynamic_run_{timestamp}"

        # Initialize Mission Tracker
        mission_metrics = MissionMetrics(task=task, start_time=mission_start)

        def check_timeout() -> None:
            elapsed = time.time() - mission_start
            if elapsed > mission_timeout:
                raise TimeoutError(f"Mission execution wall-time limit exceeded ({elapsed:.1f}s / {mission_timeout}s)")

        def add_node_metrics(name: str, metrics: NodeMetrics) -> None:
            mission_metrics.nodes[name] = metrics

        mission_tracker = {"check_timeout": check_timeout, "add_node_metrics": add_node_metrics}

        # Pre-determine workspace path so we can pass it to planner
        if not run_dir:
            try:
                if hasattr(sys, '_MEIPASS'):
                    ws_path = Path(sys.executable).parent.parent.resolve() / "_workspace"
                else:
                    ws_path = Path(__file__).resolve().parent.parent.parent / "_workspace"
                ws_path.mkdir(parents=True, exist_ok=True)
                run_dir = ws_path / "dynamic_runs" / run_id
            except Exception as e:
                _log.warning("Failed to resolve workspace run_dir: %s", e)
                run_dir = Path.cwd() / "dynamic_runs" / run_id
        else:
            run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        _log.info("Starting dynamic compilation run (ID: %s) for task: '%s'", run_id, task)

        final_output = ""
        saved_paths: dict = {}
        compiled_agents: list[dict] = []
        runner_results: list[dict] = []
        plan: dict = {}

        try:
            # 0. Initialize State Manager
            self.state_manager = HermesStateManager()
            state_manager = self.state_manager

            if log_callback:
                log_callback("CEO", f"Planning task: '{task}'...", "running")
            check_timeout()
            plan = self.planner.plan(task, mission_tracker=mission_tracker, preferred_model=preferred_model,
                                     log_callback=log_callback, run_dir=run_dir, planning_mode=planning_mode)
            if log_callback:
                log_callback("CEO", f"Generated plan: {plan.get('plan_summary')}", "running")
            # Determine if we have planner nodes in the plan
            planner_nodes = []
            if planning_mode:
                planner_nodes = [
                    n for n in plan.get("nodes", [])
                    if "planner" in n.get("name", "").lower()
                    or "planner" in n.get("role", "").lower()
                    or "plan" in n.get("name", "").lower()
                ]

            if planning_mode and planner_nodes and session_id:
                # --- PHASE 1: Execute only the Planner agent ---
                if log_callback:
                    log_callback("System", "Planning Mode enabled. Compiling & running Planner agent first...", "running")
                
                planner_plan = {
                    "plan_summary": plan.get("plan_summary"),
                    "skills": plan.get("skills"),
                    "nodes": planner_nodes,
                    "edges": [
                        e for e in plan.get("edges", [])
                        if e[0] in [n["name"] for n in planner_nodes]
                        and e[1] in [n["name"] for n in planner_nodes]
                    ]
                }
                
                compiled_planner_agents = AgentCompiler.compile(planner_plan)
                check_timeout()
                
                planner_results = self.runner.run(
                    agents=compiled_planner_agents,
                    edges=planner_plan.get("edges", []),
                    main_task=task,
                    state_manager=state_manager,
                    generation=0,
                    mission_tracker=mission_tracker,
                    log_callback=log_callback,
                    run_dir=str(run_dir) if run_dir else None,
                    session_id=session_id, run_id=run_id)
                
                # Verify that the planner completed successfully
                failed_planners = [r for r in planner_results if r["status"] == "failed"]
                if failed_planners:
                    return {
                        "status": "failed",
                        "error": f"Planner node failed: {failed_planners[0].get('output')}"
                    }
                
                # Find if a plan.md file was created in run_dir
                plan_file_path = "plan.md"
                if run_dir:
                    actual_plan_path = run_dir / "plan.md"
                    if not actual_plan_path.exists():
                        # Look for case-insensitive plan.md or design-spec.md
                        for child in run_dir.iterdir():
                            if child.name.lower() in ("plan.md", "design-spec.md"):
                                plan_file_path = child.name
                                break
                
                # --- PHASE 2: Wait for user approval on the plan.md ---
                if log_callback:
                    log_callback("CEO", f"Planner finished. Displaying {plan_file_path} in editor and waiting for user approval...", "running")
                
                from api.approval import set_pending, has_pending, get_history
                import uuid
                preview_id = uuid.uuid4().hex[:16]
                
                # Set pending approval specifically targeting the plan.md
                set_pending(session_id, {
                    'preview_id': preview_id,
                    'path': plan_file_path,
                    'line_changes': [],
                    'source_agent': 'Planner',
                    'message': f"제품 기획서({plan_file_path}) 작성이 완료되었습니다. 검토 후 승인하시면 개발 에이전트들이 구현을 시작합니다.",
                    'created_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })
                
                from api.config import STREAMS
                q = STREAMS.get(session_id)
                if q:
                    q.put(('approval', {
                        'preview_id': preview_id,
                        'path': plan_file_path,
                        'line_changes': [],
                        'message': f"제품 기획서({plan_file_path}) 작성이 완료되었습니다. 검토 후 승인하시면 개발 에이전트들이 구현을 시작합니다.",
                        'status': 'pending'
                    }))
                
                # Block until approval is resolved
                while has_pending(session_id):
                    check_timeout()
                    time.sleep(1.0)
                
                # Check history to see if it was rejected
                hist = get_history(session_id, limit=1)
                if hist and hist[-1].get('preview_id') == preview_id:
                    if hist[-1].get('status') == 'rejected':
                        if log_callback:
                            log_callback("CEO", "User REJECTED the plan.md. Harness stopping.", "error")
                        return {"status": "failed", "error": "User rejected the execution plan"}
                
                if log_callback:
                    log_callback("CEO", "User APPROVED the plan.md. Resuming execution for implementation agents...", "running")
                
                # --- PHASE 3: Execute remaining implementation agents ---
                other_nodes = [n for n in plan.get("nodes", []) if n not in planner_nodes]
                if other_nodes:
                    if log_callback:
                        log_callback("System", "Compiling implementation agents...", "running")
                    
                    other_plan = {
                        "plan_summary": plan.get("plan_summary"),
                        "skills": plan.get("skills"),
                        "nodes": other_nodes,
                        "edges": [
                            e for e in plan.get("edges", [])
                            if e[0] in [n["name"] for n in other_nodes]
                            and e[1] in [n["name"] for n in other_nodes]
                        ]
                    }
                    
                    compiled_agents = AgentCompiler.compile(other_plan)
                    if log_callback:
                        log_callback("System", f"Compiled {len(compiled_agents)} implementation agents.", "running")
                    
                    # Convert planner_results to initial_outputs format
                    initial_outputs = [
                        {
                            "output_key": r["output_key"],
                            "name": r["name"],
                            "role": r["role"],
                            "content": r["output"],
                            "generation": r.get("generation", 0),
                            "parents": r.get("parents", []),
                        }
                        for r in planner_results
                        if r["status"] == "success"
                    ]
                    
                    if log_callback:
                        log_callback("System", "Starting parallel execution for implementation...", "running")
                    
                    check_timeout()
                    runner_results = self.runner.run(
                        agents=compiled_agents,
                        edges=plan.get("edges", []),  # Use original edges to resolve parent-child context correctly
                        main_task=task,
                        initial_outputs=initial_outputs,
                        state_manager=state_manager,
                        generation=0,
                        mission_tracker=mission_tracker,
                        log_callback=log_callback,
                        run_dir=str(run_dir) if run_dir else None,
                        session_id=session_id, run_id=run_id)
                    
                    # Combine planner results and implementation results
                    runner_results = planner_results + runner_results
                else:
                    runner_results = planner_results
                    compiled_agents = compiled_planner_agents
            else:
                # --- Standard Non-Planning Flow ---
                if log_callback:
                    log_callback("System", "Compiling agents...", "running")
                check_timeout()
                compiled_agents = AgentCompiler.compile(plan)
                if log_callback:
                    log_callback("System", f"Compiled {len(compiled_agents)} agents.", "running")
                
                if log_callback:
                    log_callback("System", "Starting parallel execution...", "running")
                check_timeout()
                edges = plan.get("edges", [])
                runner_results = self.runner.run(
                    agents=compiled_agents,
                    edges=edges,
                    main_task=task,
                    state_manager=state_manager,
                    generation=0,
                    mission_tracker=mission_tracker,
                    log_callback=log_callback,
                    run_dir=str(run_dir) if run_dir else None,
                    session_id=session_id, run_id=run_id)


            # 4. Check for failures and run Dynamic Re-planning Loop (up to max_recovery_attempts)
            max_recovery = limits.get("mission", {}).get("max_recovery_attempts", 5)
            recovery_attempt = 0
            while recovery_attempt < max_recovery:
                failed_nodes = [r for r in runner_results if r["status"] == "failed"]
                if not failed_nodes:
                    break
                recovery_attempt += 1
                _log.info("Recovery attempt %d/%d for %d failed node(s)", recovery_attempt, max_recovery, len(failed_nodes))
                check_timeout()
                if log_callback:
                    log_callback("System", f"Recovery attempt {recovery_attempt}/{max_recovery}...", "running")
                runner_results, plan, compiled_agents = self._run_recovery_plan(
                    failed_nodes, runner_results, state_manager, task, mission_tracker, plan, compiled_agents, run_dir=str(run_dir) if run_dir else None, session_id=session_id, run_id=run_id, allowed_providers=allowed_providers)
            if recovery_attempt >= max_recovery:
                still_failed = [r for r in runner_results if r["status"] == "failed"]
                if still_failed:
                    _log.warning("Exhausted all %d recovery attempts. %d node(s) still failed: %s",
                                 max_recovery, len(still_failed), [f['name'] for f in still_failed])

            # 5. Merge results
            if log_callback:
                log_callback("Merger", "Merging results...", "running")
            check_timeout()
            final_output = self.merger.merge(runner_results, task, mission_tracker=mission_tracker,
                                             preferred_model=preferred_model, log_callback=log_callback)
            if log_callback:
                log_callback("Merger", "Merged results. Generation complete.", "running")

            # 6. CodeReviewer pass
            final_output = self._run_code_reviewer(final_output, check_timeout, preferred_model=preferred_model)

            # --- Record model execution results for DynamicModelSelector ---
            try:
                from api.dynamic.model_selector import get_model_selector
                _selector = get_model_selector()
                for r in runner_results:
                    _node_role = r.get("role", "")
                    _model_used = r.get("model_used", "")
                    _status = r.get("status", "failed")
                    _latency = r.get("duration_seconds", 0) * 1000
                    if _node_role and _model_used:
                        _selector.record_result(
                            role=_node_role,
                            model_id=_model_used,
                            success=(_status == "success"),
                            latency_ms=_latency,
                        )
                _log.info("Recorded %d node results in ModelSelector history", len(runner_results))
            except Exception as e:
                _log.warning("Failed to record model results: %s", e)

            # --- Record skill execution results for SkillHistory ---
            try:
                _skill_history = get_skill_history()
                _task_context = extract_task_context(task)
                _context_keys = build_context_keys(_task_context)
                _plan_skills: set[str] = set(plan.get("skills", []))
                for _node in plan.get("nodes", []):
                    for _sk in (_node.get("skills") or []):
                        _plan_skills.add(_sk)
                _mission_success = (mission_metrics.status == "success")
                for _skill_name in _plan_skills:
                    if _skill_name:
                        _skill_history.record_use(
                            skill_name=_skill_name,
                            success=_mission_success,
                            context_keys=_context_keys,
                        )
                if _plan_skills:
                    _log.info("Recorded %d skill(s) in SkillHistory: %s",
                              len(_plan_skills), ', '.join(sorted(_plan_skills)))
            except Exception as e:
                _log.warning("Failed to record skill history: %s", e)

            mission_metrics.status = "success"

        except Exception as e:
            _log.error("Dynamic execution failed with error: %s", e)
            final_output = f"Execution failed: {e}"
            mission_metrics.status = "failed"
            mission_metrics.error = str(e)

        finally:
            mission_metrics.end_time = time.time()
            mission_metrics.total_wall_time = mission_metrics.end_time - mission_metrics.start_time

            # --- Record DAG topology + agent combo in Experience Database ---
            try:
                from api.dynamic.experience_db import get_experience_db
                _exp_db = get_experience_db()
                _agent_roles: dict[str, str] = {}
                for _agent in (compiled_agents or []):
                    _aname = _agent.get("name", "")
                    _arole = _agent.get("role", "")
                    if _aname and _arole:
                        _agent_roles[_aname] = _arole
                _model_assignments: dict[str, str] = {}
                for _r in (runner_results or []):
                    _rname = _r.get("name", "")
                    _rmodel = _r.get("model_used", "")
                    if _rname and _rmodel:
                        _model_assignments[_rname] = _rmodel
                _all_skills: list[str] = list(set(plan.get("skills", [])) if plan else [])
                for _node in (plan.get("nodes", []) if plan else []):
                    for _sk in (_node.get("skills") or []):
                        if _sk not in _all_skills:
                            _all_skills.append(_sk)
                _exp_db.record_dag_run(
                    task=task,
                    nodes=plan.get("nodes", []) if plan else [],
                    edges=plan.get("edges", []) if plan else [],
                    skills_used=_all_skills,
                    agent_roles=_agent_roles,
                    model_assignments=_model_assignments,
                    success=(mission_metrics.status == "success"),
                    wall_time_ms=mission_metrics.total_wall_time * 1000,
                )
            except Exception as _exp_err:
                _log.warning("Failed to record DAG run in ExperienceDB: %s", _exp_err)

            cleanup_harness_artifacts(run_id)

            # Save output physically to workspace
            try:
                if run_dir:
                    (run_dir / "final_output.md").write_text(final_output, encoding="utf-8")

                    metadata = {
                        "task": task,
                        "timestamp": timestamp,
                        "plan": plan,
                        "agents": compiled_agents,
                        "runner_results": runner_results,
                    }
                    (run_dir / "metadata.json").write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                    )

                    metrics_data = {
                        "task": mission_metrics.task,
                        "start_time": mission_metrics.start_time,
                        "end_time": mission_metrics.end_time,
                        "total_wall_time": mission_metrics.total_wall_time,
                        "status": mission_metrics.status,
                        "error": mission_metrics.error,
                        "nodes": {k: asdict(v) for k, v in mission_metrics.nodes.items()},
                    }
                    (run_dir / "metrics.json").write_text(
                        json.dumps(metrics_data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    _log.info("Saved outputs/metrics to: %s", run_dir)

                    saved_paths = {
                        "run_dir": str(run_dir),
                        "final_output_file": str(run_dir / "final_output.md"),
                        "metadata_file": str(run_dir / "metadata.json"),
                        "metrics_file": str(run_dir / "metrics.json"),
                    }
            except Exception as e:
                _log.warning("Failed to save run outputs to workspace: %s", e)

            # [AutoSkillExtractor] Ask user before saving as Skill (approval-based)
            if mission_metrics.status == "success":
                try:
                    from api.approval import set_skill_save_pending
                    set_skill_save_pending(session_id, task, plan, final_output, run_id)
                except Exception as _e:
                    _log.warning("Failed to set skill-save approval: %s", _e)
                _send_bridge_complete("[전체 미션 완료] 모든 파이프라인 작업이 종료되었습니다. 요원들이 퇴근합니다.")
            else:
                _send_bridge_complete(f"[전체 미션 종료] 파이프라인이 중단되었습니다. 요원들이 철수합니다. 사유: {mission_metrics.error}")

        return {
            "plan": plan,
            "agents": compiled_agents,
            "runner_results": runner_results,
            "final_output": final_output,
            "saved_paths": saved_paths,
            "state_manager": (
                {k: [x.to_dict() for x in v] for k, v in self.state_manager.store.items()}
                if hasattr(self, "state_manager")
                else {}
            ),
        }

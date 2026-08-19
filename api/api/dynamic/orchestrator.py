"""
Main Dynamic Hermes Orchestrator: coordinates the entire JIT compiler flow.

Provides:
- HermesDynamicRunner: Planner → Compiler → ParallelRunner → Merger pipeline
  with recovery re-planning for failed nodes and CodeReviewer pass
"""

import json
import os
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
from api.dynamic.capability_resolver import (
    CapabilityResolver,
    RESOLVED_BY_SKILL,
    RESOLVED_BY_AGENT,
    NEEDS_BUILDER,
)
from api.dynamic.builder_agent import (
    dispatch_builder_requests,
    DISPATCH_SPAWNED,
    DISPATCH_DENIED,
    DISPATCH_ERROR,
)

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
        # 갭 E-L1: 결핍 능력 결정 사슬 (지연 구성, 주입 가능 — _resolve_missing_capabilities 참조)
        self.capability_resolver = None
        # 갭 E-L2: Builder 승인 게이트/스포너 (기본 None = 게이트 거부 — 리스크 5 안전 기본값)
        self.builder_approver = None
        self.builder_spawner = None

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

    # 실파일 검증 CodeReviewer가 읽을 텍스트 소스 확장자 (바이너리 제외)
    _REVIEW_SOURCE_EXTS = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss",
        ".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".sh", ".bat", ".cmd",
        ".sql", ".vue", ".svelte", ".xml", ".ini", ".cfg", ".env", ".gitignore",
    }

    def _collect_written_files(self, run_dir, start_time: float, max_files: int = 25,
                               max_bytes: int = 16000) -> list:
        """run_dir 안에서 이번 실행(start_time 이후)에 쓰인 텍스트 파일을 수집한다.

        에이전트가 디스크에 실제로 쓴 산출물을 CodeReviewer가 검증할 수 있도록,
        (상대경로, 내용) 튜플 리스트를 반환한다. 바이너리/메타 파일과 용량 초과
        파일은 잘라내거나 건너뛴다.
        """
        results: list = []
        if not run_dir:
            return results
        try:
            base = Path(run_dir)
            if not base.exists():
                return results
            candidates = []
            for p in base.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                    # orchestrator가 쓰는 메타 파일은 리뷰 대상이 아니다.
                    if p.name in ("final_output.md", "metadata.json", "metrics.json",
                                  "code_review_report.md"):
                        continue
                    if p.suffix.lower() not in self._REVIEW_SOURCE_EXTS:
                        continue
                    # 이번 실행 이전에 존재하던 파일은 제외
                    if p.stat().st_mtime < start_time:
                        continue
                    candidates.append(p)
                except Exception:
                    continue
            # 최근 수정 파일 우선
            candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            for p in candidates[:max_files]:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    truncated = ""
                    if len(text) > max_bytes:
                        text = text[:max_bytes]
                        truncated = "\n... [truncated]"
                    rel = p.relative_to(base).as_posix()
                    results.append((rel, text + truncated))
                except Exception:
                    continue
        except Exception as e:
            _log.warning("Failed to collect written files for review: %s", e)
        return results

    def _run_code_reviewer(self, final_output: str, check_timeout, preferred_model: str = None,
                           run_dir=None, log_callback=None, start_time: float = 0.0) -> str:
        """Run CodeReviewer.

        두 단계로 검증한다:
        1) 디스크 실파일 검증 — 이번 실행에 run_dir에 쓰인 실제 파일을 읽어
           문법 오류/누락/명백한 버그를 검토하고 보고서를 code_review_report.md로 저장.
        2) 병합 문서 리뷰 — final_output의 코드블록 품질을 다듬는다 (기존 동작).
        """
        # ── 1) 디스크에 실제로 쓰인 파일 검증 ──
        written = self._collect_written_files(run_dir, start_time) if run_dir else []
        if written:
            check_timeout()
            if log_callback:
                log_callback("CodeReviewer", f"디스크에 쓰인 {len(written)}개 파일을 검증하는 중...", "running")
            _log.info("CodeReviewer verifying %d file(s) written to disk...", len(written))
            file_dump = "\n\n".join(
                f"===== FILE: {rel} =====\n{content}" for rel, content in written
            )
            _system_files = (
                "You are a Senior Code Reviewer performing a REAL verification pass on files "
                "that were actually written to disk by an automated agent.\n"
                "Carefully inspect the real file contents below and report:\n"
                "- Syntax errors, broken imports, unclosed tags/brackets, invalid JSON/YAML.\n"
                "- Missing pieces: referenced but undefined functions/files, dead links, "
                "incomplete implementations, TODO stubs left in production paths.\n"
                "- Obvious runtime bugs and logic errors.\n"
                "- Cross-file consistency problems (e.g. an HTML file referencing a JS/CSS file "
                "that was never created).\n"
                "Output a concise Markdown review report in Korean:\n"
                "1) 한 줄 종합 판정 (PASS / NEEDS_FIX)\n"
                "2) 파일별 발견된 문제 (심각도: critical/major/minor, 위치 포함)\n"
                "3) 구체적인 수정 제안\n"
                "Do NOT rewrite entire files — report findings only."
            )
            try:
                report = _call_direct(
                    f"아래는 이번 작업으로 디스크에 실제로 쓰인 파일들입니다. 검증 보고서를 작성하세요.\n\n{file_dump}",
                    _system_files,
                    preferred_model=preferred_model
                )
                if report and report.strip():
                    try:
                        if run_dir:
                            (Path(run_dir) / "code_review_report.md").write_text(report, encoding="utf-8")
                    except Exception:
                        pass
                    if log_callback:
                        log_callback("CodeReviewer", "실파일 검증 완료. 보고서: code_review_report.md", "running")
                    _log.info("CodeReviewer wrote disk-file verification report.")
            except Exception as review_err:
                _log.warning("Disk-file CodeReviewer skipped due to error: %s", review_err)

        # ── 2) 병합 문서의 코드블록 품질 리뷰 (기존 동작) ──
        if "```" not in final_output:
            _log.info("No code blocks detected in merged output. Document review skipped.")
            return final_output
        check_timeout()
        _log.info("Code detected in output. Running document CodeReviewer...")
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

    # ─── 갭 C: 수용 기준 검증 에이전트 + 능력 결핍 피드백 재계획 ───

    _ACCEPTANCE_VERIFY_SYSTEM = (
        "당신은 DAON 시스템의 수용 기준 검증 에이전트(Validation Agent)입니다.\n"
        "병합된 최종 산출물이 각 수용 기준을 충족하는지 증거에 기반해 판정합니다.\n"
        "산출물이나 디스크 파일에서 구체적인 증거를 확인할 수 있을 때만 충족으로 판정하세요.\n"
        "반드시 한국어로, 아래 JSON 형식으로만 응답하세요:\n"
        "{\n"
        '  "verdict": "pass" 또는 "fail",\n'
        '  "unmet_criteria": ["충족되지 않은 기준1", ...],\n'
        '  "missing_capabilities": ["부족한 기능/능력1", ...],\n'
        '  "reasoning": "판정 근거"\n'
        "}\n"
        "verdict는 unmet_criteria가 비어 있을 때만 pass입니다."
    )

    def _verify_acceptance(self, final_output: str, task: str, check_timeout,
                           preferred_model: str = None, run_dir=None,
                           log_callback=None, start_time: float = 0.0) -> dict:
        """갭 C 검증 에이전트: 병합 산출물을 수용 기준에 비춰 판정한다.

        반환: {"verdict": "pass"|"fail", "unmet_criteria": [...],
               "missing_capabilities": [...], "reasoning": str}
        오류 시 파이프라인을 막지 않도록 fail-open(pass) 처리한다.
        """
        try:
            from api.dynamic.clarifier import parse_acceptance_criteria
            criteria = parse_acceptance_criteria(task)
        except Exception:
            criteria = []
        if not criteria:
            _log.info("No acceptance criteria found in task - acceptance verification skipped.")
            return {"verdict": "pass", "unmet_criteria": [], "missing_capabilities": [],
                    "reasoning": "no acceptance criteria"}

        if log_callback:
            log_callback("Verifier", f"🔍 수용 기준 {len(criteria)}개 검증 중...", "running")

        # 증거 수집: 병합 산출물 + 디스크에 실제로 쓰인 파일
        evidence = f"### 병합 산출물 (final_output)\n{str(final_output)[:12000]}"
        try:
            written = self._collect_written_files(run_dir, start_time, max_files=15, max_bytes=8000) if run_dir else []
        except Exception:
            written = []
        if written:
            evidence += "\n\n### 디스크에 실제로 쓰인 파일\n" + "\n\n".join(
                f"===== FILE: {rel} =====\n{content}" for rel, content in written
            )

        criteria_text = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
        prompt = (
            f"## 원본 작업\n{str(task)[:4000]}\n\n"
            f"## 수용 기준\n{criteria_text}\n\n"
            f"## 증거 (산출물)\n{evidence}\n\n"
            "각 수용 기준이 충족되었는지 판정하고 JSON 형식으로 응답하세요."
        )

        try:
            check_timeout()
            from api.dynamic.clarifier import _parse_json_response
            raw = _call_direct(prompt, self._ACCEPTANCE_VERIFY_SYSTEM, preferred_model=preferred_model)
            data = _parse_json_response(raw)
            verdict = str(data.get("verdict", "")).strip().lower()
            unmet = [str(c).strip() for c in (data.get("unmet_criteria") or []) if str(c).strip()]
            caps = [str(c).strip() for c in (data.get("missing_capabilities") or []) if str(c).strip()]
            if verdict not in ("pass", "fail"):
                verdict = "fail" if unmet else "pass"
            if verdict == "pass":
                unmet = []
            result = {"verdict": verdict, "unmet_criteria": unmet,
                      "missing_capabilities": caps, "reasoning": str(data.get("reasoning", ""))}
            _log.info("Acceptance verification: verdict=%s unmet=%d caps=%s",
                      verdict, len(unmet), caps)
            if log_callback:
                if verdict == "pass":
                    log_callback("Verifier", "✅ 수용 기준 충족 확인", "success")
                else:
                    log_callback("Verifier", f"❌ 미충족 기준 {len(unmet)}개: {'; '.join(unmet[:3])}", "warning")
            return result
        except Exception as e:
            _log.warning("Acceptance verification failed (fail-open): %s", e)
            return {"verdict": "pass", "unmet_criteria": [], "missing_capabilities": [],
                    "reasoning": f"verification error: {e}"}

    def _resolve_missing_capabilities(self, missing_caps, log_callback=None):
        """갭 E-L1: 결핍 능력 목록에 0A절 결정 사슬을 실행한다.

        결정 사슬(순서 강제, 단축 평가): 기존 스킬 검색 -> 다른 에이전트 배정 -> Builder 요청.
        반환: (resolutions, builder_queue, guidance_lines)
        절대 raise 하지 않는다 — 해결 실패 시 빈 결과를 반환하고 재계획은 기존 경로로 진행된다.
        """
        if not missing_caps:
            return [], [], []
        try:
            resolver = self.capability_resolver
            if resolver is None:
                resolver = CapabilityResolver()
            resolutions, builder_queue = resolver.resolve(missing_caps)
        except Exception as e:
            _log.warning("Capability resolution failed (replan continues without guidance): %s", e)
            return [], [], []
        guidance_lines = []
        for rec in resolutions:
            cap = rec.get("capability", "")
            outcome = rec.get("outcome", "")
            detail = rec.get("detail") or {}
            if outcome == RESOLVED_BY_SKILL:
                guidance_lines.append(f"{cap} -> 기존 스킬 '{detail.get('skill', '')}' 사용")
            elif outcome == RESOLVED_BY_AGENT:
                guidance_lines.append(f"{cap} -> 전문 에이전트 '{detail.get('agent', '')}' 배정")
            elif detail.get("builder_request"):
                guidance_lines.append(
                    f"{cap} -> 기존 스킬/에이전트 없음. 능력 제작 요청 등록(Builder 핸드오프). "
                    "현재 계획 범위에서 우회 방안을 반영하라.")
            else:
                guidance_lines.append(
                    f"{cap} -> 해결 불가(제작 단계 없음). 기존 수단으로 대체 방안을 반영하라.")
        if log_callback:
            n_skill = sum(1 for r in resolutions if r.get("outcome") == RESOLVED_BY_SKILL)
            n_agent = sum(1 for r in resolutions if r.get("outcome") == RESOLVED_BY_AGENT)
            log_callback("Resolver",
                         f"결핍 능력 판정: 스킬 {n_skill}건, 에이전트 {n_agent}건, "
                         f"제작 요청 {len(builder_queue)}건 (총 {len(resolutions)}건)",
                         "running")
        return resolutions, builder_queue, guidance_lines

    def _dispatch_builder_queue(self, builder_queue, log_callback=None,
                                preferred_model: str = None):
        """갭 E-L2: E-L1이 남긴 builder_queue를 소비해 Builder 서브팀 스폰을 디스패치한다.

        승인 게이트(self.builder_approver) 통과 시에만 delegate_team 서브팀이 스폰된다.
        approver 미등록 시 게이트는 기본 거부(리스크 5 안전 기본값).
        반환: 디스패치 레코드 목록. 절대 raise 하지 않는다.
        """
        if not builder_queue:
            return []
        try:
            records = dispatch_builder_requests(
                builder_queue,
                spawner=self.builder_spawner,
                approver=self.builder_approver,
                preferred_model=preferred_model,
                log_callback=log_callback,
            )
        except Exception as e:
            _log.warning("Builder dispatch failed (replan continues): %s", e)
            return []
        if log_callback and records:
            n_spawn = sum(1 for r in records if r.get("status") == DISPATCH_SPAWNED)
            n_deny = sum(1 for r in records if r.get("status") == DISPATCH_DENIED)
            n_err = sum(1 for r in records if r.get("status") == DISPATCH_ERROR)
            log_callback("Builder",
                         f"제작 요청 디스패치: 스폰 {n_spawn}건, 게이트 거부 {n_deny}건, "
                         f"오류 {n_err}건 (총 {len(records)}건)",
                         "running")
        return records or []

    def _run_acceptance_replan(self, unmet: list, missing_caps: list, final_output: str,
                               task: str, state_manager, mission_tracker: dict,
                               plan: dict, runner_results: list, compiled_agents: list,
                               check_timeout, preferred_model: str = None,
                               log_callback=None, run_dir=None,
                               session_id: str = None, run_id: str = None,
                               generation: int = 2) -> tuple:
        """갭 C 재계획: 미충족 기준/결핍 능력을 플래너에 피드백해 보충 DAG를 만들고 재병합한다.

        갭 E-L1: missing_caps가 있으면 능력 결정 사슬(스킬 검색 -> 에이전트 배정 -> Builder)을
        실행해 능력별 해결 판정을 재계획 프롬프트에 주입하고, 제작 요청을 merged_plan에 남긴다.

        반환: (new_final_output, merged_runner_results, merged_plan, merged_agents)
        """
        # 갭 E-L1: 결핍 능력 결정 사슬 (스킬 검색 -> 에이전트 배정 -> Builder 요청)
        resolutions, builder_queue, cap_guidance = [], [], []
        if missing_caps:
            resolutions, builder_queue, cap_guidance = self._resolve_missing_capabilities(
                missing_caps, log_callback=log_callback)
        if cap_guidance:
            caps_line = (
                f"검증 에이전트가 식별한 결핍 능력(missing capabilities): {', '.join(missing_caps)}\n"
                "능력별 해결 판정(스킬 검색 -> 에이전트 배정 -> Builder 순서):\n"
                + "\n".join(f"- {g}" for g in cap_guidance) + "\n"
                "위 판정을 계획에 반영하라: 스킬로 해결된 능력은 해당 스킬을 사용하고, "
                "에이전트가 배정된 능력은 해당 에이전트 노드를 배치하며, "
                "제작 요청이 등록된 능력은 기존 수단으로 최대한 우회하라.\n\n"
            )
        else:
            caps_line = (
                f"검증 에이전트가 식별한 결핍 능력(missing capabilities): {', '.join(missing_caps)}\n\n"
                if missing_caps else ""
            )
        replan_prompt = (
            f"다음 작업을 해결하기 위한 멀티 에이전트 시스템입니다: {task}\n\n"
            f"지금까지 생성된 산출물(병합 출력):\n\"\"\"\n{str(final_output)[:8000]}\n\"\"\"\n\n"
            "그러나 수용 기준 검증 에이전트가 다음 기준을 미충족으로 판정했습니다:\n"
            + "\n".join(f"- {c}" for c in unmet) + "\n\n"
            + caps_line
            + "부족한 능력만 보완하여 완전한 최종 산출물을 만드는 새로운 EXECUTABLE DAG를 생성하세요. "
            "처음부터 다시 만들지 말고 기존 산출물/디스크 파일을 기반으로 보완해야 합니다.\n"
            "표준 Nodes and Edges 스키마에 맞는 유효한 JSON을 반환하세요."
        )
        _log.info("Acceptance re-planning for %d unmet criteria...", len(unmet))
        if log_callback:
            log_callback("CEO", f"🔁 미충족 기준 {len(unmet)}개 보완을 위한 재계획 수립 중...", "running")
        check_timeout()
        replan = self.planner.plan(replan_prompt, mission_tracker=mission_tracker,
                                   preferred_model=preferred_model)

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
            if r.get("status") == "success"
        ]
        recompiled = self.compiler.compile(replan)
        _log.info("Compiled %d agents for acceptance re-plan.", len(recompiled))
        check_timeout()
        extra_results = self.runner.run(
            recompiled,
            replan.get("edges", []),
            task,
            initial_outputs=initial_outputs,
            state_manager=state_manager,
            generation=generation,
            mission_tracker=mission_tracker,
            log_callback=log_callback,
            run_dir=str(run_dir) if run_dir else None,
            session_id=session_id, run_id=run_id)

        merged_results = [r for r in runner_results if r.get("status") == "success"] + extra_results
        if log_callback:
            log_callback("Merger", "수용 기준 재계획 결과 재병합 중...", "running")
        check_timeout()
        new_final = self.merger.merge(merged_results, task, mission_tracker=mission_tracker,
                                       preferred_model=preferred_model, log_callback=log_callback)
        merged_plan = {"first_run_plan": plan, "acceptance_replan": replan}
        if resolutions:
            merged_plan["capability_resolutions"] = resolutions
        if builder_queue:
            merged_plan["builder_queue"] = builder_queue
            # 갭 E-L2: 제작 요청 큐 소비 — 승인 게이트 통과 시 Builder 서브팀 스폰
            dispatches = self._dispatch_builder_queue(
                builder_queue, log_callback=log_callback,
                preferred_model=preferred_model)
            if dispatches:
                merged_plan["builder_dispatches"] = dispatches
        return new_final, merged_results, merged_plan, compiled_agents + recompiled

    def run(self, task: str, preferred_model: str = None, log_callback=None, run_dir=None, planning_mode: bool = False, session_id: str = None, run_id: str = None, allowed_providers: list = None, forced_skills: list = None, delegation_context: dict = None) -> dict:
        from api.dynamic.model_selector import set_allowed_providers
        set_allowed_providers(allowed_providers)

        # 갭 D: 위임된 자식 실행 여부 (루트 실행 종료 시에만 생성 예산을 반납한다)
        try:
            _is_delegated_child = bool(delegation_context) and int((delegation_context or {}).get("depth", 0) or 0) > 0
        except (TypeError, ValueError):
            _is_delegated_child = bool(delegation_context)
        
        limits = _load_harness_limits()
        mission_start = time.time()
        mission_timeout = limits["mission"]["max_total_wall_time_seconds"]

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # IMPORTANT: dynamic_jobs.start_harness_job 이 생성한 run_id(uuid)를 반드시 유지한다.
        # 여기서 run_id 를 덮어쓰면 아래의 set_job_awaiting_approval / set_job_running /
        # is_job_cancelled / register_running_agent / set_skill_save_pending 가 모두
        # 존재하지 않는 키로 호출되어 하네스 승인·취소·완료 상태 전파가 깨진다.
        # (harness 프론트엔드는 start_harness_job 이 반환한 uuid 로 /api/dynamic/status/{run_id} 를 폴링한다.)
        # run_id 가 전달되지 않은 독립 실행 경로에서만 사람이 읽기 좋은 ID 를 붙인다.
        if not run_id:
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
                    ws_path = Path(__file__).resolve().parent.parent.parent.parent / "_workspace"
                ws_path.mkdir(parents=True, exist_ok=True)
                run_dir = ws_path / "dynamic_runs" / run_id
            except Exception as e:
                _log.warning("Failed to resolve workspace run_dir: %s", e)
                run_dir = Path.cwd() / "dynamic_runs" / run_id
        else:
            run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)

        # ── 갭 D: 위임 컨텍스트 구성 ──
        # 루트 실행이면 depth 0 컨텍스트를 새로 만들고, 위임된 실행이면
        # delegate_team 도구가 전달한 컨텍스트를 사용한다. mission_tracker에
        # 실어 ParallelRunner가 각 노드 워커 스레드(thread-local)에 전달하게 한다.
        if delegation_context is None:
            delegation_context = {
                "run_id": run_id,
                "root_run_id": run_id,
                "parent_run_id": None,
                "depth": 0,
            }
        delegation_context.setdefault("root_run_id", run_id)
        delegation_context["run_dir"] = str(run_dir)
        mission_tracker["delegation"] = delegation_context

        # ── 갭 D-3: 위임 로그 중계 등록 ──
        # 자식 실행(delegate_team)이 부모의 로그 스트림으로 로그를 전달할 수 있도록
        # 이 실행의 log_callback을 run_id 키로 레지스트리에 등록한다. 종료 시 finally에서 해제.
        if callable(log_callback):
            try:
                from api.dynamic.delegation import register_delegation_log_callback
                register_delegation_log_callback(run_id, log_callback)
            except Exception:
                pass

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
                                     log_callback=log_callback, run_dir=run_dir, planning_mode=planning_mode,
                                     forced_skills=forced_skills)

            if log_callback:
                log_callback("CEO", f"Generated plan: {plan.get('plan_summary')}", "running")
            # Determine if we have planner nodes in the plan.
            # 승인 게이트는 이 planner_nodes가 비어있으면 통째로 건너뛰어지므로,
            # CEO가 노드 이름을 다르게 지어도 planning-phase 노드를 안정적으로 식별해야 한다.
            # 감지 우선순위: (1) planning 전용 template_id  (2) name/role의 planner/plan 키워드
            # (3) subtask의 plan.md/prd 언급  (4) 매칭 노드로 흘러들어가는 upstream 엣지 폐로.
            planner_nodes = []
            if planning_mode:
                _PLANNING_TEMPLATES = {"prd-writer", "task-decomposer"}
                all_nodes = plan.get("nodes", [])
                matched_names = set()
                for n in all_nodes:
                    _tid = (n.get("template_id") or "").lower()
                    _name = (n.get("name") or "").lower()
                    _role = (n.get("role") or "").lower()
                    _subtask = (n.get("subtask") or "").lower()
                    if (
                        _tid in _PLANNING_TEMPLATES
                        or "planner" in _name or "planner" in _role
                        or "plan" in _name
                        or "prd" in _name
                        or "plan.md" in _subtask
                        or "제품 기획" in _subtask or "prd" in _subtask
                    ):
                        matched_names.add(n.get("name"))
                # upstream 폐로: 매칭된 노드(예: plan_planner)에 입력을 주는 선행 노드
                # (예: prd_planner)도 planning phase에 포함시킨다. 구현 노드는 planning
                # 노드의 downstream이므로 절대 포함되지 않는다.
                _edges = plan.get("edges", [])
                _changed = True
                while _changed:
                    _changed = False
                    for _e in _edges:
                        try:
                            _src, _dst = _e[0], _e[1]
                        except (IndexError, TypeError):
                            continue
                        if _dst in matched_names and _src not in matched_names:
                            matched_names.add(_src)
                            _changed = True
                planner_nodes = [n for n in all_nodes if n.get("name") in matched_names]

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
                
                from api.approval import set_pending, has_pending, get_history, approve as _auto_approve
                import uuid
                preview_id = uuid.uuid4().hex[:16]
                _plan_msg = f"제품 기획서({plan_file_path}) 작성이 완료되었습니다. 검토 후 승인하시면 개발 에이전트들이 구현을 시작합니다."
                
                # Set pending approval specifically targeting the plan.md
                # NOTE: 'status': 'pending' is REQUIRED — the frontend approval poller
                # (approval.js) and showInlineApproval() both gate on status === 'pending'.
                set_pending(session_id, {
                    'preview_id': preview_id,
                    'path': plan_file_path,
                    'line_changes': [],
                    'is_plan': True,
                    'source_agent': 'Planner',
                    'message': _plan_msg,
                    'status': 'pending',
                    'created_at': time.strftime('%Y-%m-%dT%H:%M:%S')
                })
                
                # Mark the dynamic job as awaiting_approval so the harness frontend
                # poller (/api/dynamic/status) surfaces the approval banner too.
                if run_id:
                    try:
                        from api.dynamic_jobs import set_job_awaiting_approval
                        set_job_awaiting_approval(run_id, _plan_msg)
                    except Exception as _jae:
                        _log.warning("Failed to set job awaiting_approval: %s", _jae)
                
                from api.config import STREAMS
                q = STREAMS.get(session_id)
                if q:
                    q.put(('approval', {
                        'preview_id': preview_id,
                        'path': plan_file_path,
                        'line_changes': [],
                        'is_plan': True,
                        'message': _plan_msg,
                        'status': 'pending'
                    }))
                
                # Block until approval is resolved, or auto-approve after timeout.
                # [E] 사용자가 오랜 시간 응답하지 않으면 45초(설정 가능) 후 자동 승인한다.
                # skill_save는 이 흐름을 타지 않으므로(전용 set_skill_save_pending 경로)
                # 스킬 저장 승인 로직은 영향받지 않는다.
                # 설정 우선순위: approvals.file_tool_auto_timeout (config.yaml)
                #   → env HERMES_AUTO_APPROVE_SECONDS → 기본 45초. (chat 쪽
                #   check_file_tool_approval 과 동일한 키를 공유한다.)
                _auto_timeout = 45
                try:
                    from hermes_cli.config import load_config as _load_hf_config
                    _cfg_approvals = (_load_hf_config() or {}).get("approvals", {}) or {}
                    _auto_timeout = int(_cfg_approvals.get("file_tool_auto_timeout", 45))
                except Exception:
                    _auto_timeout = 45
                try:
                    _auto_timeout = int(os.getenv("HERMES_AUTO_APPROVE_SECONDS", _auto_timeout))
                except (ValueError, TypeError):
                    pass
                if _auto_timeout <= 0:
                    _auto_timeout = None  # 자동 승인 비활성화 — 사용자 응답까지 대기
                _approval_started = time.time()
                _auto_done = False
                while has_pending(session_id):
                    check_timeout()
                    if not _auto_done and _auto_timeout is not None and (time.time() - _approval_started) >= _auto_timeout:
                        _auto_done = True
                        try:
                            _auto_approve(session_id, reviewer="auto")
                        except Exception as _aae:
                            _log.warning("Auto-approve plan failed: %s", _aae)
                        # SSE로 자동 승인 이벤트 전송 → 프론트가 읽기 전용 완료 카드로 교체
                        if q:
                            q.put(('approval', {
                                'preview_id': preview_id,
                                'path': plan_file_path,
                                'line_changes': [],
                                'is_plan': True,
                                'message': f"응답 없음 — {_auto_timeout}초 후 실행 계획이 자동 승인되었습니다.",
                                'status': 'auto_approved'
                            }))
                        if log_callback:
                            log_callback("CEO", f"⏱️ 응답 없음 — {_auto_timeout}초 후 실행 계획이 자동 승인되었습니다. 구현을 시작합니다.", "running")
                    time.sleep(1.0)
                
                # Restore job status to running now that approval is resolved
                if run_id:
                    try:
                        from api.dynamic_jobs import set_job_running
                        set_job_running(run_id)
                    except Exception:
                        pass
                
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

            # 6. 갭 C: 수용 기준 검증 + 능력 결핍 피드백 재계획 루프
            #    - 검증 에이전트가 병합 산출물을 수용 기준에 비춰 판정
            #    - fail 시 결핍 능력을 플래너에 피드백해 재계획 → 재실행 → 재병합
            #    - 무한 루프 방지: max_acceptance_retries(2) + 개선 증거(미충족 집합 감소) 요구
            max_acceptance = limits.get("mission", {}).get("max_acceptance_retries", 2)
            acceptance_attempt = 0
            prev_unmet = None
            while True:
                verdict = self._verify_acceptance(
                    final_output, task, check_timeout, preferred_model=preferred_model,
                    run_dir=run_dir, log_callback=log_callback, start_time=mission_start)
                unmet = verdict.get("unmet_criteria", [])
                if verdict.get("verdict") == "pass" or not unmet:
                    break
                if acceptance_attempt >= max_acceptance:
                    _log.warning("Acceptance re-plan retries exhausted (%d). Unmet criteria remain: %s",
                                 max_acceptance, unmet)
                    if log_callback:
                        log_callback("Verifier", f"⚠️ 재시도 한도 도달 — 미충족 기준 {len(unmet)}개 남음", "warning")
                    break
                cur_unmet = set(unmet)
                if prev_unmet is not None and cur_unmet >= prev_unmet:
                    _log.warning("No improvement evidence (unmet set not shrunk). Stopping acceptance loop.")
                    if log_callback:
                        log_callback("Verifier", "⚠️ 재시도에도 개선 증거 없음 — 수용 기준 루프 중단", "warning")
                    break
                prev_unmet = cur_unmet
                acceptance_attempt += 1
                _log.info("Acceptance re-plan attempt %d/%d for %d unmet criteria",
                          acceptance_attempt, max_acceptance, len(unmet))
                final_output, runner_results, plan, compiled_agents = self._run_acceptance_replan(
                    unmet, verdict.get("missing_capabilities", []), final_output, task,
                    state_manager, mission_tracker, plan, runner_results, compiled_agents,
                    check_timeout, preferred_model=preferred_model, log_callback=log_callback,
                    run_dir=run_dir, session_id=session_id, run_id=run_id,
                    generation=1 + acceptance_attempt)

            # 7. CodeReviewer pass (디스크 실파일 검증 + 병합 문서 리뷰)
            final_output = self._run_code_reviewer(
                final_output, check_timeout, preferred_model=preferred_model,
                run_dir=run_dir, log_callback=log_callback, start_time=mission_start)

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

            # 갭 D: 루트 실행 종료 시 생성 예산 카운터 반납 (메모리 잔존 방지).
            if not _is_delegated_child:
                try:
                    from api.dynamic.delegation import reset_spawn_budget
                    reset_spawn_budget(run_id)
                except Exception:
                    pass

            # 갭 D-3: 위임 로그 중계 레지스트리 해제 (메모리 잔존 방지).
            try:
                from api.dynamic.delegation import unregister_delegation_log_callback
                unregister_delegation_log_callback(run_id)
            except Exception:
                pass

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

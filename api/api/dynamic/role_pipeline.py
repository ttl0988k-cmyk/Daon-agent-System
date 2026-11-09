"""
Single-Task Multi-Role & Specialist Subagent Pipeline (Ruflo-style Role Harness).
Leverages the 105 domain-specialist agent templates in api/agents/
(developer, debugger, designer, specialist, reviewer, qa, writer, etc.)
allowing Raon's individual chat tasks and single operations to delegate subtasks
to specialized personas sharing a single cost-effective model (DeepSeek V4.1 Flash)
with zero-latency failover.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = BASE_DIR / "api" / "agents"
SKILLS_DIR = BASE_DIR / "skills"
HERMES_SKILLS_DIR = BASE_DIR / "hermes-agent" / "skills"


def get_all_specialist_templates() -> Dict[str, Dict[str, Any]]:
    """Loads all 105 specialist YAML templates from api/agents/."""
    try:
        from api.dynamic.template_loader import load_all_templates
        templates = load_all_templates()
        if templates:
            return templates
    except Exception as e:
        _log.warning(f"[RolePipeline] Failed to load via template_loader: {e}")
        
    # Direct fallback loader
    results = {}
    if AGENTS_DIR.exists():
        import yaml
        for yml in AGENTS_DIR.rglob("*.yaml"):
            if yml.name.startswith("_"):
                continue
            try:
                data = yaml.safe_load(yml.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "id" in data:
                    data["_category"] = yml.parent.name
                    data["_file"] = str(yml)
                    results[data["id"]] = data
            except Exception as e:
                _log.debug(f"Error reading {yml}: {e}")
    return results


def select_specialists_for_task(task_prompt: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """
    Selects top-k specialist agents from the 105-catalog matching task keywords.
    """
    templates = get_all_specialist_templates()
    if not templates:
        return []
        
    tokens = set(task_prompt.lower().replace(",", " ").replace(".", " ").replace("/", " ").split())
    scored = []
    
    for tid, t in templates.items():
        score = 0
        tags = [str(x).lower() for x in t.get("tags", [])]
        cat = str(t.get("category", "")).lower()
        disp = str(t.get("display_name", "")).lower()
        desc = str(t.get("description", "")).lower()
        
        # Avoid match
        avoid = [str(x).lower() for x in t.get("avoid_when", [])]
        if any(a in task_prompt.lower() for a in avoid if len(a) > 3):
            score -= 15
            
        for tok in tokens:
            if len(tok) < 2:
                continue
            if tok in tags:
                score += 5
            if tok in disp:
                score += 4
            if tok in desc:
                score += 2
            if tok in cat:
                score += 2
                
        scored.append((score, tid, t))
        
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[2] for item in scored[:top_k]]


def _sanitize_report_output(text: str, fallback_summary: str = "") -> str:
    """
    Strips raw DSML/tool-call markup (<｜DSML｜ ... </｜DSML｜>) and restores clean markdown.
    If the output was pure DSML with no actual explanation, creates a structured summary from fallback.
    """
    if not text:
        return fallback_summary
    
    import re
    # 1. Remove complete <...DSML...calls> ... </...DSML...calls> blocks
    cleaned = re.sub(r'<\s*[｜\|\s]*DSML[｜\|\s]*calls\s*>.*?<\s*/\s*[｜\|\s]*DSML[｜\|\s]*calls\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # 2. Remove complete <...DSML...invoke> ... </...DSML...invoke> blocks
    cleaned = re.sub(r'<\s*[｜\|\s]*DSML[｜\|\s]*invoke\b.*?>.*?<\s*/\s*[｜\|\s]*DSML[｜\|\s]*invoke\s*>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # 3. Remove any stray remaining DSML tags (including closing tags like < / | DSML | ...>)
    cleaned = re.sub(r'<\s*/?\s*[｜\|\s]*DSML[｜\|\s]*[^>]*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    
    if len(cleaned) < 20 and fallback_summary:
        clean_fallback = _sanitize_report_output(fallback_summary, "")
        if clean_fallback:
            return f"하위 전문 에이전트들의 작업 및 분석 결과를 종합한 보고입니다.\n\n{clean_fallback}"
        return "하위 전문 에이전트들의 작업 및 분석이 완료되었습니다."
    return cleaned or fallback_summary


def execute_specialist_node(
    template: Dict[str, Any],
    task_prompt: str,
    context: str = "",
    previous_steps: Optional[List[Dict[str, Any]]] = None,
    preferred_model: Optional[str] = None,
    event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Executes a single specialist agent using its exact persona, tools, and success criteria.
    Shares a single cost-effective model instance with fast failover.
    """
    from api.dynamic.direct_calls import _call_direct

    if not preferred_model:
        preferred_model = "muse-spark-1.3-contributor"

    agent_id = template.get("id", "specialist")
    disp_name = template.get("display_name", agent_id)
    category = template.get("category", template.get("_category", "general"))
    persona = template.get("system_prompt", f"You are a specialist in {category}.")
    success_criteria = template.get("success_criteria", [])
    
    if event_callback:
        event_callback("role_start", agent_id, {
            "title": f"[{category.upper()}] {disp_name}",
            "status": "running"
        })
        
    step_history_text = ""
    if previous_steps:
        step_history_text = "\n\n### 이전 단계 전문 에이전트 산출물:\n"
        for s in previous_steps:
            step_history_text += f"\n--- [{s.get('title', s.get('agent_id'))}] ---\n{s.get('output', '')}\n"

    crit_text = ""
    if success_criteria:
        crit_text = "\n### 당신의 작업 성공 기준 (MANDATORY):\n" + "\n".join(f"- {c}" for c in success_criteria)

    # 템플릿의 도구 목록을 hermes toolset 목록으로 매핑하여 전문 에이전트가 실제 작업 수행 가능하도록 함
    tmpl_tools = template.get("tools", [])
    tool_map = {
        "terminal": "terminal",
        "file": "file",
        "web_search": "web",
        "search": "search",
        "browser": "browser",
        "web": "web",
    }
    resolved_toolsets = set()
    for t in tmpl_tools:
        if t in tool_map:
            resolved_toolsets.add(tool_map[t])
    if not resolved_toolsets:
        resolved_toolsets = {"terminal", "file"}
    else:
        resolved_toolsets.add("terminal")
        resolved_toolsets.add("file")

    system_instruction = (
        f"{persona}\n\n"
        f"에이전트 ID: {agent_id} ({disp_name})\n"
        f"분야: {category}\n"
        f"{crit_text}\n\n"
        "작업 지침:\n"
        "1. 필요시 제공된 터미널과 파일 도구를 적극 활용하여 파일 구조와 코드를 직접 확인하고 실제 작업을 완수하십시오.\n"
        "2. 도구 호출은 시스템 도구 인터페이스를 통해 정상 실행하십시오.\n"
        "3. 작업이 끝나면 당신의 역할에 맞는 구체적인 분석/설계/구현 결과물을 완성도 높은 한국어 마크다운으로 작성하여 대표님께 보고하십시오."
    )
    
    full_prompt = (
        f"### 전체 작업 목표:\n{task_prompt}\n\n"
        f"### 시스템 컨텍스트:\n{context}\n"
        f"{step_history_text}\n\n"
        f"[{disp_name}] 전문 에이전트로서 당신의 전문 역할에 맞는 구체적인 결과물을 출력하세요."
    )
    
    start_time = time.time()
    try:
        output = _call_direct(
            prompt=full_prompt,
            system_instruction=system_instruction,
            preferred_model=preferred_model,
            enabled_toolsets=list(resolved_toolsets),
            max_iterations=8
        )
        output = _sanitize_report_output(output, f"[{disp_name}] 역할 작업 완료")
        elapsed = round(time.time() - start_time, 2)
        
        result = {
            "agent_id": agent_id,
            "title": f"[{category.upper()}] {disp_name}",
            "category": category,
            "status": "done",
            "output": output,
            "elapsed_sec": elapsed
        }
        if event_callback:
            event_callback("role_done", agent_id, result)
        return result
    except Exception as e:
        elapsed = round(time.time() - start_time, 2)
        _log.error(f"[RolePipeline] Specialist {agent_id} failed: {e}")
        err_res = {
            "agent_id": agent_id,
            "title": f"[{category.upper()}] {disp_name}",
            "category": category,
            "status": "error",
            "error": str(e),
            "output": f"[에러 발생] {e}",
            "elapsed_sec": elapsed
        }
        if event_callback:
            event_callback("role_error", agent_id, err_res)
        return err_res


def run_delegated_pipeline(
    task_prompt: str,
    context: str = "",
    specialist_ids: Optional[List[str]] = None,
    preferred_model: Optional[str] = None,
    event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Main entry point: Runs a complete multi-specialist pipeline for Raon's tasks.
    Progression:
      1. Architect / Requirements Planner
      2. Domain Specialist(s) selected from 105 catalog (e.g. frontend-react, python-backend, etc.)
      3. Quality / Security Reviewer (e.g. security-audit, code-review)
      4. Raon CEO Synthesizer
    """
    if not preferred_model:
        preferred_model = "muse-spark-1.3-contributor"

    templates = get_all_specialist_templates()
    
    # 1. 3개 전문 조 (Trio Matrix: 1조 설계 -> 2조 구현 -> 3조 검증) 엄격 확정
    selected_templates = []
    if specialist_ids and len(specialist_ids) >= 3:
        for sid in specialist_ids:
            if sid in templates:
                selected_templates.append(templates[sid])
    else:
        # [1조: 설계/아키텍트] 요구사항 분해 및 구조/파일 수정 전략 수립
        architect_tmpl = templates.get("architect") or templates.get("tech-lead") or templates.get("task-decomposer") or {
            "id": "architect",
            "display_name": "소프트웨어 아키텍트 (1조)",
            "category": "planner",
            "system_prompt": "You are a software architect. Design system boundaries, define API contracts, and create execution plans.",
            "success_criteria": ["모듈 경계 명확", "기술 선정 근거", "트레이드오프 문서화"]
        }

        # [2조: 전문 구현 개발자] 105개 카탈로그 중 작업 내용에 가장 정확히 일치하는 전문 개발자 1인 선발
        domain_candidates = [t for t in select_specialists_for_task(task_prompt, top_k=6) if t.get("category") in ("developer", "specialist", "debugger", "designer")]
        coder_tmpl = domain_candidates[0] if domain_candidates else (templates.get("fullstack") or templates.get("python-backend"))
        if not coder_tmpl:
            coder_tmpl = {
                "id": "senior-developer",
                "display_name": "시니어 핵심 개발자 (2조)",
                "category": "developer",
                "system_prompt": "You are the core lead developer. Implement precise, clean, production-grade code according to the architect's design.",
                "success_criteria": ["문법 오류 없는 완전한 코드", "엣지케이스 처리", "프로덕션 품질"]
            }

        # [3조: 품질 및 보안 검증관] 구현된 코드와 설계의 결함, 보안 취약점, 엣지케이스 정밀 검증
        reviewer_tmpl = templates.get("security-audit") or templates.get("code-review") or {
            "id": "security-audit",
            "display_name": "품질 및 보안 검증관 (3조)",
            "category": "reviewer",
            "system_prompt": "You are a strict security and code quality auditor. Review the architecture and implementation for bugs, syntax errors, and vulnerabilities.",
            "success_criteria": ["문법/타입 에러 0건", "보안 취약점 검증 통과", "요구사항 부합성 확인"]
        }

        # 진짜 3개 조 확정: 1조 설계 -> 2조 구현 -> 3조 검증
        selected_templates = [architect_tmpl, coder_tmpl, reviewer_tmpl]
            
    if event_callback:
        event_callback("pipeline_start", "raon_ceo", {
            "total_agents": len(selected_templates) + 1,
            "specialists": [t.get("display_name", t.get("id")) for t in selected_templates],
            "task": task_prompt[:180]
        })
        
    start_total = time.time()
    steps_results = []
    
    for t in selected_templates:
        res = execute_specialist_node(
            template=t,
            task_prompt=task_prompt,
            context=context,
            previous_steps=steps_results,
            preferred_model=preferred_model,
            event_callback=event_callback
        )
        steps_results.append(res)
        
    # Final Synthesis: Raon CEO
    from api.dynamic.direct_calls import _call_direct
    synthesis_instruction = (
        "당신은 DAON Agent System의 총괄 AI CEO 라온(Raon)입니다.\n"
        "하위 전문 에이전트들이 도출한 작업 결과물을 취합하여 대표님께 깔끔하고 명확한 최종 종합 보고서를 작성하세요.\n\n"
        "지침:\n"
        "1. 하위 전문 에이전트들의 핵심 작업 내용과 성과를 빠짐없이 종합하여 완성도 높은 마크다운 한국어 보고서를 작성하십시오.\n"
        "2. 필요시 파일 상태나 실행 결과를 직접 확인할 수 있습니다.\n"
        "3. 최종 출력은 대표님께 보고드릴 완성된 최종 요약 및 작업 완료 리포트 형식이어야 합니다."
    )
    history_summary = "\n\n".join([f"### [{s['title']}]\n{s['output']}" for s in steps_results])
    synthesis_prompt = (
        f"### 대표님 지시 작업:\n{task_prompt}\n\n"
        f"### 하위 전문 에이전트 작업 결과:\n{history_summary}\n\n"
        f"위 결과물을 바탕으로 대표님께 보고할 최종 종합 결과와 핵심 요약을 작성하세요."
    )
    
    if event_callback:
        event_callback("role_start", "raon_synthesizer", {"title": "라온 CEO 종합 보고", "status": "running"})
        
    try:
        final_report = _call_direct(
            prompt=synthesis_prompt,
            system_instruction=synthesis_instruction,
            preferred_model=preferred_model,
            enabled_toolsets=["terminal", "file"],
            max_iterations=5
        )
        final_report = _sanitize_report_output(final_report, history_summary)
        if event_callback:
            event_callback("role_done", "raon_synthesizer", {"title": "라온 CEO 종합 보고", "status": "done"})
    except Exception as e:
        final_report = f"작업 종합 중 일부 오류가 발생했습니다: {e}\n\n{history_summary}"
        if event_callback:
            event_callback("role_error", "raon_synthesizer", {"title": "라온 CEO 종합 보고", "status": "error"})

    total_elapsed = round(time.time() - start_total, 2)
    summary = {
        "status": "completed",
        "total_elapsed_sec": total_elapsed,
        "specialists_count": len(selected_templates),
        "steps": steps_results,
        "final_output": final_report
    }
    
    if event_callback:
        event_callback("pipeline_done", "raon_ceo", summary)
        
    return summary

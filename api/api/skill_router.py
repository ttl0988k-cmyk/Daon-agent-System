"""
TRACE-inspired Skill Router for Daon Agent System.

Reimagines TRACE's "MoE Gate Routing" (neural LoRA weight routing)
as a lightweight rule-based + LLM classifier that decides which
skills and MCP servers to activate before a task begins.

Instead of:
    y = base_linear(x) + Σᵢ gᵢ · LoRAᵢ(x)   [millions of parameters]

We do:
    if task_requires("API Reading"): activate("Context7 MCP", "read_docs skill")

Architecture:
    User Task → Skill Router → Activated Skills/MCPs → Agent runs with context

The router consults:
    1. Capability diagnosis history (which capabilities are weak)
    2. Task classification (what does this task need?)
    3. Available skills/MCPs registry
"""
import json
import logging
import re
from typing import Any

_logger = logging.getLogger(__name__)

# ── Task → Capability mapping (fallback / legacy) ─────────────────────────
# Maps task keywords/patterns to required capabilities.
# These are used as a FALLBACK when no skill.yaml triggers match.

TASK_CAPABILITY_MAP = [
    # (regex pattern, required_capability, priority 1-5)
    (r'(api|sdk|library|패키지|pip install|npm install|import)\b.*(사용|이용|개발|구현|만들|작성|써)',
     "API Reading", 4),
    (r'(문서|docs|reference|레퍼런스|사용법|메뉴얼|가이드)',
     "API Reading", 5),
    (r'(설계|아키텍처|구조|계획|plan|architecture|design)\b',
     "Planning", 4),
    (r'(리팩토링|refactor|수정|변경|개선|최적화|optimize)',
     "Planning", 3),
    (r'(디버그|debug|버그|bug|오류|에러|error|fix|고쳐|수리)',
     "Error Recovery", 5),
    (r'(테스트|test|테스트 코드|unit test|integration test)',
     "Error Recovery", 3),
    (r'(검색|찾아|find|search|탐색|scan|어디|위치)',
     "Tool Selection", 3),
    (r'(코드 리뷰|review|검토|품질|quality|컨벤션|스타일)',
     "Code Quality", 4),
    (r'(문서화|document|주석|README|설명서|작성.*문서)',
     "Communication", 3),
    (r'(요약|summarize|설명|explain|알려줘|무엇|어떻게)',
     "Communication", 2),
    (r'(복잡|complex|대규모|large|프로젝트 전체|여러 파일)',
     "Planning", 3),
    (r'(배포|deploy|CI|CD|pipeline|파이프라인|자동화|automation)',
     "Planning", 4),
]

# ── Capability → Skill/MCP mapping (fallback / legacy) ─────────────────────
# Static fallback. Dynamic skill.yaml triggers take precedence at runtime.

CAPABILITY_SKILL_MAP = {
    "API Reading": {
        "skills": ["auto-documenter", "full-output"],
        "mcps": ["Context7", "github"],
        "prompt_hint": "작업 전에 공식 문서나 레퍼런스를 검색 도구로 먼저 확인하세요.",
    },
    "Planning": {
        "skills": ["sequential-thinking"],
        "mcps": ["sequential_thinking"],
        "prompt_hint": "복잡한 작업은 먼저 단계별 계획을 수립한 후 순서대로 실행하세요.",
    },
    "Tool Selection": {
        "skills": ["full-output"],
        "mcps": ["filesystem"],
        "prompt_hint": "파일 시스템을 먼저 탐색하여 프로젝트 구조를 파악한 후 적절한 도구를 선택하세요.",
    },
    "Error Recovery": {
        "skills": ["self-reflection", "sherlock-qa"],
        "mcps": [],
        "prompt_hint": "오류 발생 시 원인을 분석하고 대안 전략을 제시하세요. 같은 실수를 반복하지 마세요.",
    },
    "Context Awareness": {
        "skills": ["auto-documenter"],
        "mcps": ["filesystem"],
        "prompt_hint": "워크스페이스 구조를 먼저 파악한 후 작업을 시작하세요.",
    },
    "Code Quality": {
        "skills": ["code-review", "security"],
        "mcps": [],
        "prompt_hint": "코드 작성 후 자체 검토를 수행하고, 일관된 스타일과 모범 사례를 따르세요.",
    },
    "Instruction Following": {
        "skills": ["self-reflection"],
        "mcps": [],
        "prompt_hint": "사용자 지시사항을 작업 시작 전에 다시 한번 확인하고, 모든 요구사항을 충족했는지 검증하세요.",
    },
    "Self-Correction": {
        "skills": ["self-reflection", "sherlock-qa"],
        "mcps": [],
        "prompt_hint": "각 단계 완료 후 결과를 검토하고, 오류가 있으면 즉시 수정하세요.",
    },
    "Knowledge Gap": {
        "skills": ["auto-documenter"],
        "mcps": ["Context7", "github"],
        "prompt_hint": "모르는 기술이나 도메인은 검색 도구로 먼저 학습한 후 작업하세요.",
    },
    "Communication": {
        "skills": ["full-output"],
        "mcps": [],
        "prompt_hint": "작업 완료 후 변경 사항, 결과, 다음 단계를 명확히 요약하세요.",
    },
}


# ── Dynamic trigger index from skill.yaml ──────────────────────────────────
# Built lazily from the SkillRegistry; maps trigger keywords → skill names.

_trigger_index_cache: dict | None = None
_trigger_index_ts: float = 0.0


def _build_trigger_index() -> list[tuple[str, str, str]]:
    """Build a trigger index from all registered skills' skill.yaml triggers.

    Returns a list of (trigger_keyword_lower, skill_name, category) tuples.
    Cached for 60 seconds to avoid re-scanning on every request.
    """
    global _trigger_index_cache, _trigger_index_ts
    import time
    now = time.time()
    if _trigger_index_cache is not None and (now - _trigger_index_ts) < 60:
        return _trigger_index_cache

    index: list[tuple[str, str, str]] = []
    try:
        from api.skill_registry import get_skill_registry
        registry = get_skill_registry()
        for entry in registry._all_entries:
            for trigger_kw in (entry.trigger or []):
                kw = str(trigger_kw).strip().lower()
                if kw:
                    index.append((kw, entry.name, entry.category))
    except Exception as e:
        _logger.warning("[SkillRouter] Failed to build trigger index: %s", e)

    _trigger_index_cache = index
    _trigger_index_ts = now
    return index


def route_skills_for_task(task_description: str, diagnosis_history: list[dict] = None) -> dict:
    """
    Given a user task, determine which skills and MCP servers should be activated.

    Routing pipeline:
        Step 0: Dynamic trigger matching from skill.yaml (highest priority)
        Step 1: Legacy capability classification (fallback patterns)
        Step 2: Diagnosis history weighting
        Step 3: Capability → skills/MCPs mapping (static fallback)
        Step 4: Build response

    Args:
        task_description: The user's task/message text (Korean or English)
        diagnosis_history: List of previous diagnosis results (optional, for personalization)

    Returns:
        dict with:
            activated_skills: [list of skill names to activate]
            activated_mcps: [list of MCP server IDs to activate]
            prompt_additions: [list of prompt hints to inject]
            routing_explanation: human-readable explanation of routing decisions
            required_capabilities: [capabilities detected as needed for this task]
    """
    task_lower = task_description.lower()

    activated_skills = set()
    activated_mcps = set()
    prompt_additions = []
    routing_explanation_parts = []

    # ── Step 0: Dynamic trigger matching from skill.yaml ───────────────────
    # Match task text against trigger keywords defined in each skill's skill.yaml.
    # This is the PRIMARY routing mechanism for the new categorized structure.
    trigger_index = _build_trigger_index()
    trigger_matched_skills: dict[str, list[str]] = {}  # skill_name → [matched keywords]

    for trigger_kw, skill_name, category in trigger_index:
        if trigger_kw in task_lower:
            trigger_matched_skills.setdefault(skill_name, []).append(trigger_kw)

    if trigger_matched_skills:
        for skill_name, matched_kws in sorted(trigger_matched_skills.items()):
            activated_skills.add(skill_name)
            routing_explanation_parts.append(
                f"[trigger] {skill_name}: 매칭 키워드 '{', '.join(matched_kws)}'"
            )

    # ── Step 1: Legacy capability classification (fallback) ────────────────
    required_caps = set()
    cap_reasons = {}

    for pattern, capability, priority in TASK_CAPABILITY_MAP:
        if re.search(pattern, task_lower):
            required_caps.add(capability)
            if capability not in cap_reasons or priority > cap_reasons[capability][0]:
                cap_reasons[capability] = (priority, pattern)

    # If no trigger matches AND no capability patterns match, default to basic
    if not trigger_matched_skills and not required_caps:
        required_caps = {"Planning", "Communication"}

    # ── Step 2: Weight by diagnosis history ────────────────────────────────
    lacking_caps = set()
    if diagnosis_history:
        for diag in diagnosis_history[-3:]:  # Last 3 diagnoses
            for cap in diag.get("capabilities", []):
                if cap.get("label") == "LACKING" and cap.get("confidence", 0) >= 0.6:
                    lacking_caps.add(cap.get("name", ""))

    # Merge: required + lacking (lacking gets priority)
    all_caps = required_caps | lacking_caps

    # ── Step 3: Map capabilities → skills/MCPs (static fallback) ───────────
    for cap_name in sorted(all_caps):
        mapping = CAPABILITY_SKILL_MAP.get(cap_name, {})
        skills = mapping.get("skills", [])
        mcps = mapping.get("mcps", [])
        hint = mapping.get("prompt_hint", "")

        for s in skills:
            activated_skills.add(s)
        for m in mcps:
            activated_mcps.add(m)
        if hint:
            prompt_additions.append(f"[{cap_name}] {hint}")

        is_lacking = cap_name in lacking_caps
        is_required = cap_name in required_caps
        tags = []
        if is_lacking:
            tags.append("이전 진단에서 부족")
        if is_required:
            tags.append("작업에 필요")
        routing_explanation_parts.append(
            f"{cap_name}: {', '.join(tags)} → Skills: {skills or '없음'}, MCPs: {mcps or '없음'}"
        )

    # ── Step 4: Build response ─────────────────────────────────────────────
    return {
        "ok": True,
        "activated_skills": sorted(activated_skills),
        "activated_mcps": sorted(activated_mcps),
        "prompt_additions": prompt_additions,
        "routing_explanation": routing_explanation_parts,
        "required_capabilities": sorted(required_caps),
        "lacking_capabilities": sorted(lacking_caps),
        "trigger_matched_skills": sorted(trigger_matched_skills.keys()),
        "summary": _build_routing_summary(activated_skills, activated_mcps, all_caps),
    }


def _build_routing_summary(skills: set, mcps: set, caps: set) -> str:
    """Build a human-readable routing summary."""
    parts = []
    if caps:
        parts.append(f"작업에 필요한 역량: {', '.join(sorted(caps))}")
    if skills:
        parts.append(f"활성화 스킬: {', '.join(sorted(skills))}")
    if mcps:
        parts.append(f"활성화 MCP: {', '.join(sorted(mcps))}")
    return " | ".join(parts) if parts else "특별한 라우팅 없음"


def get_all_capability_mappings() -> dict:
    """Return the full capability → skill/MCP mapping for UI display."""
    return {
        "task_patterns": [
            {"pattern": p, "capability": c, "priority": pr}
            for p, c, pr in TASK_CAPABILITY_MAP
        ],
        "capability_skill_map": {
            cap: {
                "skills": mapping["skills"],
                "mcps": mapping["mcps"],
                "prompt_hint": mapping["prompt_hint"],
            }
            for cap, mapping in CAPABILITY_SKILL_MAP.items()
        },
    }

"""
Planner module: breaks down a task into a Node-Edge DAG of specialized subtasks.

Provides:
- HermesPlanner: CEO agent that generates executable multi-agent plans
  * Semantic Skill Retriever → Top-K recommendations
  * Skill History (context-aware success rates)
  * Skill Graph (requires/compatible/conflicts relationships)
  * Retriever ≠ Auto-Select enforcement (CEO must decide)
  * Experience Database (organizational learning insights)
"""

import json
import re
import time

from api.skill_registry import get_skill_registry
from api.dynamic.limits import _load_harness_limits
from api.dynamic.state import StreamLogBuffer
from api.dynamic.plan_validator import validate_plan_schema, semantic_validate
from api.dynamic.direct_calls import _call_direct
from api.dynamic.model_selector import get_skill_history, extract_task_context, build_context_keys
from api.dynamic.template_loader import get_catalog_text, load_all_templates
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


# ── 갭 B: CEO 선택지 확장 — MCP / Plugin / 실행환경 카탈로그 빌더 ──
# 플래너가 계획 단계에서 "이 노드에는 어떤 MCP가 필요한가", "이 노드는 격리
# 환경에서 돌려야 하는가"를 선택할 수 있도록 CEO 프롬프트에 주입하는 블록들.
# 스킬 카탈로그 주입 패턴(get_catalog_text)을 차용했다.

def _build_mcp_catalog_block() -> str:
    """등록된 MCP 서버 목록을 CEO 프롬프트용 텍스트 블록으로 구성한다.

    각 서버의 ID/라벨/연결 상태/도구 이름을 나열해, CEO가 노드별
    'mcp_servers' 필드로 선택적 바인딩을 할 수 있게 한다.
    """
    try:
        from api.mcp_client import get_mcp_manager
        servers = get_mcp_manager().list_servers()
    except Exception as e:
        _log.info("MCP catalog unavailable: %s", e)
        return ""
    if not servers:
        return ""
    lines: list[str] = []
    for srv in servers:
        sid = srv.get("server_id", "")
        label = srv.get("label", sid)
        status = "CONNECTED" if srv.get("connected") else "NOT CONNECTED"
        tool_names = [t.get("name", "") for t in (srv.get("tools") or []) if t.get("name")]
        if tool_names:
            tools_str = ", ".join(tool_names[:12])
            if len(tool_names) > 12:
                tools_str += f", ... (+{len(tool_names) - 12} more)"
        else:
            tools_str = "(no tools discovered)"
        lines.append(f"  - '{sid}' [{status}] {label} — tools: {tools_str}")
    return (
        "\n[AVAILABLE MCP SERVERS — Per-Node Binding Catalog]\n"
        "MCP (Model Context Protocol) servers provide external tools (file ops, search, browser, etc.) to agents.\n"
        "For each node you MAY set the optional 'mcp_servers' field to control which servers are bound:\n"
        "- mcp_servers omitted or null → ALL connected MCP servers are injected (default).\n"
        "- mcp_servers: [] → NO MCP tools for this node.\n"
        '- mcp_servers: ["id1", "id2"] → ONLY those servers\' tools are available to the node.\n'
        "Prefer SELECTIVE binding: give each node only the MCP servers its subtask actually needs — "
        "fewer tools means less confusion and lower token cost. Only CONNECTED servers are injected at runtime.\n"
        + "\n".join(lines) + "\n"
        "[End MCP Server Catalog]\n"
    )


def _build_plugin_catalog_block() -> str:
    """설치된 플러그인 목록을 CEO 프롬프트용 텍스트 블록으로 구성한다."""
    try:
        from api.plugin_gateway import list_installed_plugins
        plugins = list_installed_plugins()
    except Exception as e:
        _log.info("Plugin catalog unavailable: %s", e)
        return ""
    if not plugins:
        return ""
    lines: list[str] = []
    for p in plugins:
        pname = p.get("name", "")
        desc = p.get("description", "") or ""
        if pname:
            lines.append(f"  - '{pname}' — {desc}")
    if not lines:
        return ""
    return (
        "\n[INSTALLED PLUGINS — Per-Node Injection Catalog]\n"
        "Plugins provide additional skills/tools. For each node you MAY set the optional 'plugins' field "
        "to inject specific plugins' skills into that node only:\n"
        "- plugins omitted or null → no extra plugin skills (session-active plugins are still merged automatically).\n"
        '- plugins: ["name"] → that plugin\'s skills are injected into the node\'s system prompt.\n'
        + "\n".join(lines) + "\n"
        "[End Plugin Catalog]\n"
    )


def _build_environment_options_block() -> str:
    """노드별 실행환경(local/sandbox) 선택지를 CEO 프롬프트에 주입하는 블록."""
    return (
        "\n[EXECUTION ENVIRONMENT OPTIONS — Per-Node]\n"
        "Each node runs in an execution environment. You MAY set the optional 'environment' field per node:\n"
        '- "local" (default): the agent works in the SHARED workspace directory. Files it writes are part of the final deliverable.\n'
        '- "sandbox": the agent works in an ISOLATED temporary directory. Use this for experimental or risky operations '
        "(running untrusted scripts, throwaway prototypes, test installs) so they cannot pollute the real workspace. "
        "Sandbox files are NOT part of the deliverable unless the agent reports their content in its output.\n"
        "[End Environment Options]\n"
    )


class HermesPlanner:
    """Master Orchestrator that analyzes a task and generates a valid DAG plan."""

    def plan(self, task: str, mission_tracker: dict = None, preferred_model: str = None,
             log_callback=None, run_dir=None, planning_mode: bool = False,
             forced_skills: list = None) -> dict:
        """Analyze the task and break it down into a Node-Edge DAG of specialized subtasks,
        with retry and schema validation."""
        limits = _load_harness_limits()
        max_attempts = limits["plan"]["max_attempts"]

        if mission_tracker and "check_timeout" in mission_tracker:
            mission_tracker["check_timeout"]()

        # --- Skill Registry Integration ---
        skill_registry = get_skill_registry()
        skill_catalog = skill_registry.get_catalog_text()

        # --- Semantic Skill Retriever (Embedding-based) ---
        try:
            from api.dynamic.skill_retriever import get_skill_retriever
            semantic_retriever = get_skill_retriever(backend="auto")
            semantic_retriever.rebuild_index(skill_registry)
            semantic_skill_block = semantic_retriever.retrieve_for_ceo_prompt(task, top_k=10)
        except Exception as e:
            _log.info("SemanticSkillRetriever unavailable, using Rule-based only: %s", e)
            semantic_skill_block = ""

        # --- Skill History (Context-aware Success Rates) ---
        try:
            task_context = extract_task_context(task)
            context_keys = build_context_keys(task_context)
            skill_history = get_skill_history()

            skill_history_lines: list[str] = []
            for sname, sentry in skill_registry._skills.items():
                if sentry.lifecycle != "approved" and sentry.source != "curated":
                    continue
                if "@" in sname and sname.split("@")[0] in skill_registry._skills:
                    continue
                formatted = skill_history.format_for_ceo(sentry.name, context_keys)
                if formatted:
                    skill_history_lines.append(formatted)

            if skill_history_lines:
                skill_history_block = (
                    "\n[SKILL HISTORY — Context-Aware Success Rates]\n"
                    "The following shows past performance of each skill in similar contexts.\n"
                    "✅ = excellent (>90%), ⚠️ = moderate (60-90%), ❌ = poor (<60%).\n"
                    "PAY ATTENTION: A skill that works well in one language/framework may fail in another.\n"
                    "Use this data to INFORM (not dictate) your skill selection.\n"
                    + "\n".join(skill_history_lines) + "\n"
                    "[End Skill History]\n"
                )
            else:
                skill_history_block = (
                    "\n[SKILL HISTORY — No Prior Data]\n"
                    "(No historical performance data yet. This is the first run for this context.)\n"
                    "[End Skill History]\n"
                )
        except Exception as e:
            _log.info("SkillHistory unavailable: %s", e)
            skill_history_block = ""

        # --- Skill Graph (Relationship Constraints) ---
        try:
            all_skill_names = [
                sname for sname, sentry in skill_registry._skills.items()
                if (sentry.lifecycle == "approved" or sentry.source == "curated")
                and "@" not in sname
            ]
            skill_graph_block = skill_registry.get_skill_graph_context(all_skill_names)
            if skill_graph_block:
                skill_graph_block = "\n" + skill_graph_block + "\n[End Skill Graph]\n"
        except Exception as e:
            _log.info("SkillGraph unavailable: %s", e)
            skill_graph_block = ""

        # --- Experience Database Insights (Task Type + Historical Patterns) ---
        try:
            from api.dynamic.experience_db import get_experience_db
            _exp_db = get_experience_db()
            experience_block = _exp_db.format_for_ceo(task, min_samples=1)
        except Exception as e:
            _log.info("ExperienceDatabase unavailable: %s", e)
            experience_block = (
                "\n[EXPERIENCE DATABASE — No Prior Data]\n"
                "(This is the first execution. No historical patterns available yet.\n"
                "The system will learn from this run and improve future plans.)\n"
                "[End Experience Database]\n"
            )

        # --- Agent Template Catalog ---
        try:
            template_catalog_text = get_catalog_text()
        except Exception as e:
            _log.warning("Failed to load template catalog: %s", e)
            template_catalog_text = "(Template catalog unavailable)"

        # --- User-Forced Skills (CEO directive) ---
        if forced_skills:
            forced_skills_block = (
                "\n[USER-SPECIFIED SKILLS — MANDATORY DIRECTIVE]\n"
                "The user has EXPLICITLY requested the following skill(s) for this task:\n"
                + "\n".join(f"- {s}" for s in forced_skills) + "\n\n"
                "You MUST incorporate these skills into your plan:\n"
                "1. Include them in the plan-level 'skills' array so they are injected into relevant agents.\n"
                "2. Select agent templates whose domain/capability aligns with these skills.\n"
                "3. In each relevant agent's subtask, reference the skill methodology explicitly.\n"
                "4. You decide WHICH agents need the skill — not every agent requires it.\n"
                "This is a user directive, not a suggestion. Treat it as a hard constraint.\n"
                "[End User-Specified Skills]\n\n"
            )
        else:
            forced_skills_block = ""

        system_instruction = (
            "You are the Master Orchestrator (CEO) of a multi-agent system.\n"
            "Your job: SELECT agent templates from the catalog below, assign subtasks, and define execution order.\n"
            "You do NOT need to write system_prompts or define tools/skills — templates provide those automatically.\n\n"
            "Respond ONLY with a valid JSON object matching this structure:\n"
            "{\n"
            '  "plan_summary": "Short execution description.",\n'
            '  "skills": ["skill-name-1", "skill-name-2"],\n'
            '  "nodes": [\n'
            "    {\n"
            '      "name": "agent_name (alphanumeric and underscores only)",\n'
            '      "template_id": "MUST be an exact ID from the AGENT TEMPLATE CATALOG below",\n'
            '      "subtask": "the specific subtask this agent will execute (be detailed and concrete)",\n'
            '      "input": "input_key_from_dependency (null if none)",\n'
            '      "output": "output_key_for_this_agent",\n'
            '      "model": "Assign the optimal model. MUST match one in AVAILABLE MODELS.",\n'
            '      "system_prompt": "(OPTIONAL) extra instructions beyond the template default, keep SHORT 1-3 lines",\n'
            '      "mcp_servers": "(OPTIONAL) list of MCP server IDs to bind to this node — see AVAILABLE MCP SERVERS catalog. Omit/null = ALL connected servers, [] = none",\n'
            '      "plugins": "(OPTIONAL) list of plugin names to inject into this node — see INSTALLED PLUGINS catalog. Omit/null = none",\n'
            '      "environment": "(OPTIONAL) \'local\' (default: shared workspace) or \'sandbox\' (isolated temp dir) — see EXECUTION ENVIRONMENT OPTIONS"\n'
            "    }\n"
            "  ],\n"
            '  "edges": [\n'
            '    ["source_agent_name", "target_agent_name"]\n'
            "  ]\n"
            "}\n"
            "Ensure the JSON output is valid without markdown blocks.\n\n"
            "[AGENT TEMPLATE CATALOG — 100 pre-built specialist agents]\n"
            "Select template_id from this catalog. Each template includes system_prompt, tools, skills, and model preferences.\n"
            + template_catalog_text + "\n"
            "[End Agent Template Catalog]\n\n"
            + experience_block + "\n"
            "[CEO DECISION-MAKING CHECKLIST]\n"
            "Before generating the nodes and edges, you MUST mentally evaluate:\n"
            "1. What is the Task Difficulty/Complexity?\n"
            "2. How should the multi-agent DAG be structured? (You MUST always create a multi-agent team — see MULTI-AGENT ENFORCEMENT below)\n"
            "3. Which template_id(s) from the AGENT TEMPLATE CATALOG best fit each subtask?\n"
            "4. What is the optimal model for each agent based on task difficulty?\n"
            "5. What is the concrete Success Criteria for the task?\n"
            "6. You MUST include a Reviewer/QA agent to verify correctness.\n"
            "7. Does this task involve API routes, response formats, or message structures? If YES, follow the SHARED SCHEMA CONTRACT rules below.\n"
            "8. Does any node need external tools (MCP servers), plugin skills, or an isolated execution environment? If YES, set that node's optional 'mcp_servers' / 'plugins' / 'environment' fields per the catalogs below.\n\n"
            "[TEMPLATE SELECTION RULES]\n"
            "- You MUST select template_id from the AGENT TEMPLATE CATALOG above. Do NOT invent template IDs.\n"
            "- Each template already includes: system_prompt, tools, skills, and model preferences.\n"
            "- Your job is to: (a) pick the right template, (b) write a detailed subtask, (c) assign a model.\n"
            "- The optional 'system_prompt' field is for EXTRA instructions only (1-3 lines). The template's base prompt is auto-injected.\n"
            "- You can still add plan-level 'skills' that apply to ALL agents (e.g., 'self-reflection').\n"
            "- For Contract Validation: use a reviewer template + 'contract-validator' skill in plan-level skills.\n"
            "- AVOID_WHEN ENFORCEMENT: Each catalog entry has [AVOID: ...] markers. If the task matches ANY avoid_when condition of a template, you MUST NOT select that template. Example: frontend-react has [AVOID: backend_only_task, native_mobile_app] — do NOT use it for a pure backend API task.\n"
            "- DOMAIN MATCHING: Check the 'capability' and 'domain' fields. Select the template whose domain best matches the subtask requirement.\n"
            "- COST OPTIMIZATION: Each catalog entry has [COST: low/mid/high] marker. For simple tasks (CRUD, boilerplate, docs), prefer low-cost templates + cheaper models. Reserve high-cost templates for complex reasoning/architecture tasks. Example: simple API endpoint → python-backend [COST: mid] + deepseek-v3, NOT architect [COST: high] + claude-sonnet.\n"
            "- MODEL SELECTION BY COST: low-cost tasks → deepseek-v3/minimax-m3; mid-cost → deepseek-v3/qwen-coder; high-cost complex reasoning → claude-sonnet/gpt-4o. Always prefer the cheapest model that can handle the task quality requirement.\n\n"
            "[RETRIEVER ≠ AUTO-SELECT — CRITICAL ENFORCEMENT]\n"
            "The Semantic Skill Retriever provides Top-K RECOMMENDATIONS based on embedding similarity.\n"
            "These are SUGGESTIONS ONLY. You are the CEO and YOU make the FINAL decision.\n"
            "Cross-check recommendations against Skill History (past success rates) and Skill Graph (conflicts/requirements).\n"
            "[End Retriever ≠ Auto-Select]\n\n"
            + semantic_skill_block + "\n"
            + skill_history_block + "\n"
            + f"{skill_catalog}\n\n"
            + skill_graph_block + "\n"
            + forced_skills_block
            + _build_mcp_catalog_block()
            + _build_plugin_catalog_block()
            + _build_environment_options_block()
            + "[SHARED SCHEMA CONTRACT — MANDATORY FOR ALL AGENTS]\n"
            "- ALL agents MUST follow the shared Schema contract defined in shared/schema.py and shared/schema.js.\n"
            "- Do NOT invent API routes, response fields, or message formats.\n"
            "- Contract Validator: Run BEFORE any Backend/Frontend code is written. MUST return VERIFIED PASS.\n"
            "- Schema is the Single Source of Truth. No agent may deviate from it.\n\n"
            "[CRITICAL CONSTRAINTS]\n"
            "- PORT/PROCESS SAFETY: Agents must NEVER kill the active backend process on port 9090.\n"
            "- Component Modularization: For UI tasks, split into specialized agents.\n"
            "\n[MASTER ORCHESTRATION RULES]\n"
            "1. SCHEMA-FIRST DAG ORDER (For API/Message tasks): Schema Agent → Contract Validator → Implementation → QA/Review.\n"
            "2. MULTI-AGENT ENFORCEMENT (MANDATORY): You MUST ALWAYS create a multi-agent DAG with at least 2 nodes. "
            "NEVER generate a plan with only 1 node. Even for simple tasks, split into at least: "
            "(a) an implementation agent AND (b) a reviewer/QA agent. "
            "You are the CEO — your job is to DELEGATE work to a team of specialist agents, NOT to do everything yourself. "
            "A single-node plan means you are failing at your job.\n"
            "3. CONTEXT INTEGRITY: Each downstream agent must receive context via 'input'/'output' dependencies.\n"
            "4. ROLE-SPECIFIC SUCCESS CRITERIA: Templates include success_criteria. Enforce them.\n"
            "5. ENDLESS LOOP PREVENTION: Max QA corrections: 2. Then escalate. Then terminate.\n"
        )

        try:
            from api.managers import model_manager
            available_models_data = model_manager.get_available_models()
            model_list: list[str] = []
            for group in available_models_data:
                for m in group.get('models', []):
                    model_list.append(m['id'])
            dynamic_model_list = ", ".join(f"'{m}'" for m in model_list) if model_list else "(no models registered — add a provider in Settings)"
        except Exception as e:
            _log.warning("Failed to resolve dynamic model list: %s", e)
            dynamic_model_list = "(no models registered — add a provider in Settings)"

                # --- Dynamic Model Selector Recommendations ---
        try:
            from api.dynamic.model_selector import DynamicModelSelector
            _selector = DynamicModelSelector()
            _role_recommendations: list[str] = []
            for _role, _strength, _ctx in [
                ("developer", "code", 32000),
                ("reviewer", "qa", 48000),
                ("designer", "creative", 16000),
                ("planner", "reasoning", 64000),
                ("debugger", "debug", 32000),
            ]:
                _chain, _ctx_info = _selector.select_for_node(
                    role=_role, task=task,
                    required_strength=_strength,
                    required_context=_ctx, top_k=3,
                )
                
                # Format Top 3 for this role
                _lines = []
                for i, c in enumerate(_chain):
                    if c["model"] not in model_list:
                        continue
                    m_id = c["model"]
                    score = int(c.get("_selector_score", 0) * 100)
                    cost = c.get("_cost", 0.0)
                    bd = c.get("_breakdown", {})
                    
                    qual = int((bd.get("success_rate", 0) + bd.get("strength", 0)) * 100)
                    spd = int(bd.get("latency", 0) * 10) # 0-10
                    rel = int(bd.get("reliability", 0) * 100)

                    _intel_info = bd.get("_intel") or {}
                    _intel_flag = _intel_info.get("flag")
                    _intel_n = int(_intel_info.get("daon_samples", 0) or 0)
                    if _intel_flag == "high_potential_unverified":
                        _intel_tag = " [Intel: high potential / field-unverified]"
                    elif _intel_flag == "lineage_estimate":
                        _intel_tag = " [Intel: lineage estimate - performance not inherited]"
                    elif _intel_info.get("public") is not None or _intel_n > 0:
                        _conf = "high" if _intel_n >= 15 else ("mid" if _intel_n >= 5 else "low")
                        _intel_tag = f" [Intel: DAON {_intel_n} samples, confidence {_conf}]"
                    else:
                        _intel_tag = ""

                    _lines.append(f"      {i+1}. {m_id} (Score: {score} | Quality: {qual} | Speed: {spd}/10 | Rel: {rel} | Cost/1M: ${cost}){_intel_tag}")
                
                if _lines:
                    _role_recommendations.append(f"  - Role '{_role}':\n" + "\n".join(_lines))

            # --- Model Intelligence cards (star-compressed benchmark view) ---
            _intel_cards: list[str] = []
            try:
                from api.dynamic.model_intel import get_model_intel, ModelIntel
                _intel = get_model_intel()
                _daon_view = ModelIntel.daon_stats_from_history(_selector._history)
                for _mid in model_list:
                    _card = _intel.format_ceo_line(_mid, daon_stats=_daon_view)
                    if _card:
                        _intel_cards.append("  " + _card.replace("\n", "\n  "))
            except Exception as _ie:
                _log.warning("Model intel cards unavailable: %s", _ie)

            _model_rec_block = (
                "\n[Dynamic Model Selector — 8-Dimensional Role-based Scorecards]\n"
                "The system has evaluated all available models on 8 dimensions: Task Fit, Success Rate, Cost, Latency, Context Window, JSON Reliability, Health, and Load.\n"
                "Use the scorecards below to select the optimal model for each role. (Higher is better for Score/Quality/Speed/Rel. Lower is better for Cost).\n"
                "High Risk/Complex tasks -> Prioritize Quality & Reliability.\n"
                "Simple/QA tasks -> Prioritize Speed & Cost.\n"
                + "\n".join(_role_recommendations) + "\n"
                "\n[End Dynamic Model Selector Recommendations]\n"
            ) if _role_recommendations else ""

            if _intel_cards:
                _model_rec_block += (
                    "\n[Model Intelligence — Benchmark Capability Cards]\n"
                    "Star ratings (1-5) per capability, blended from PUBLIC benchmarks and DAON field results via the Evidence layer.\n"
                    "As DAON sample counts grow, field evidence dominates the blend. A model marked\n"
                    "'잠재력 높음 / 실전 검증 부족' has strong public benchmarks but no DAON track record yet -\n"
                    "treat it as a strong candidate, not a proven one.\n"
                    + "\n".join(_intel_cards) + "\n"
                    "\n[End Model Intelligence Cards]\n"
                )

            # --- Phase 2: recent selection log (observation view) ---
            # Lets the CEO see what the selector chose for each role and why,
            # so blend weights can be tuned from real behaviour.
            try:
                _sel_log = _selector.get_selection_log(limit=10)
                if _sel_log:
                    _log_lines = []
                    for _entry in _sel_log:
                        _role = _entry.get("role", "?")
                        _strength = _entry.get("required_strength", "?")
                        _top = (_entry.get("chain") or [{}])[0]
                        _top_model = _top.get("model", "?")
                        _top_score = _top.get("score", 0.0)
                        _bd = _top.get("breakdown") or {}
                        _intel_ev = _bd.get("_intel") or {}
                        _intel_n = int(_intel_ev.get("daon_samples", 0) or 0)
                        _intel_flag = _intel_ev.get("flag")
                        _flag_txt = ""
                        if _intel_flag == "high_potential_unverified":
                            _flag_txt = " [high potential / field-unverified]"
                        elif _intel_flag == "lineage_estimate":
                            _flag_txt = " [lineage estimate]"
                        _log_lines.append(
                            f"  - role={_role} strength={_strength} -> {_top_model} "
                            f"(score {round(_top_score, 3)}, intel DAON {_intel_n}){_flag_txt}"
                        )
                    _model_rec_block += (
                        "\n[Model Selection Log — recent choices]\n"
                        "These are the selector's most recent picks per role. If a choice looks wrong,\n"
                        "the blend weights (strength/success/cost/latency) or the intel DB are the levers.\n"
                        + "\n".join(_log_lines) + "\n"
                        "\n[End Model Selection Log]\n"
                    )
            except Exception:
                pass  # observation view must never break planning
        except Exception as e:
            import traceback
            _log.warning(f"Failed to compute model recommendations: {e}\\n{traceback.format_exc()}")
            _model_rec_block = ""

        system_instruction += (
            "\n[IMPORTANT MODEL SELECTION GUIDE: FULL AUTONOMY]\n"
            + _model_rec_block +
            "You are the CEO. You MUST evaluate the difficulty of each subtask and autonomously assign the optimal model in the 'model' field.\n"
            "CRITICAL: To prevent 404 API errors, you MUST select ONLY from the exact strictly validated model strings currently available in the user's environment:\n"
            f"AVAILABLE MODELS: {dynamic_model_list}\n"
            "Do NOT hallucinate model names. Use ONLY the exact strings provided in the AVAILABLE MODELS list above.\n"
        )

        if planning_mode:
            system_instruction += (
                "\n[PLANNING MODE ENABLED]\n"
                "The user has enabled Planning Mode. You MUST generate the FULL multi-agent DAG containing the requirements/planning agents (first) and all subsequent implementation agents (Developer, QA, etc.) in a single plan.\n"
                "[PRD-FIRST RULE — MANDATORY for non-trivial new builds]\n"
                "Users are often NON-DEVELOPERS who give short, vague requests (e.g. '카페 홈페이지 하나 만들어줘'). To raise output quality, when the task is a NEW product/website/app/agent build (NOT a simple bug fix / single-file change / refactor), you MUST:\n"
                "1. Add a PRD node FIRST using template_id 'prd-writer'. Name this node EXACTLY 'prd_planner' (the name MUST contain 'planner' so it runs in the pre-approval phase).\n"
                "2. Then add the plan.md writing node using template_id 'task-decomposer' (the DEFAULT for turning requirements into an executable plan). Name it 'plan_planner'. Use 'architect' instead ONLY for genuinely large/complex systems needing module boundaries, tech-stack trade-offs, or scalability analysis (e.g. multi-service backends, microservices) — do NOT use architect for a typical single website/app/agent build, that is overkill.\n"
                "3. Wire them so the PRD feeds the plan: set plan_planner.input = prd_planner.output, and add edge ['prd_planner', 'plan_planner'].\n"
                "4. The 'plan_planner' node MUST physically write a detailed `plan.md` file in the workspace using the `write_file` tool, BASED ON the PRD it receives as input. Do NOT just output text without saving the file.\n"
                "5. Implementation agents (Developer, QA, etc.) come AFTER, depending on plan_planner.\n"
                "[QA / REVIEW NODE — MANDATORY]\n"
                "You MUST always end the implementation phase with at least one verification agent. Do NOT ship code without review.\n"
                "- Add a code review node using template_id 'code-review' (name it e.g. 'code_reviewer') that depends on the Developer node(s).\n"
                "- When the task produces runnable/testable code, ALSO add a testing node using template_id 'unit-tester' (name it e.g. 'qa_tester') that depends on the Developer node(s).\n"
                "- Wire the edges so implementation -> code_reviewer (and -> qa_tester when applicable). The reviewer/tester must run AFTER the code is written.\n"
                "A plan with implementation agents but NO review/QA node is INVALID and will be rejected.\n"
                "Skip the PRD node ONLY for trivial tasks matching prd-writer's AVOID conditions (simple_bug_fix, single_file_change, refactoring, ui_styling, content_writing).\n"
                "The orchestrator will automatically execute the pre-approval planning agents (prd_planner -> plan_planner) first, display `plan.md` to the user, pause for their approval, and then execute the remaining implementation agents.\n"
                "Therefore, define the full plan (e.g. prd_planner -> plan_planner -> Developer -> code_reviewer / qa_tester) now in your response."
            )


        if run_dir:
            system_instruction += (
                f"\n[WORKSPACE DIRECTORY]\n"
                f"You MUST instruct your agents to use the following absolute directory path as their base workspace for ALL file operations:\n"
                f"'{str(run_dir)}'\n"
                f"If any agent needs to save files (e.g., index.html), ensure they explicitly save them into this exact directory.\n"
            )

        prompt = f"User Task: {task}"
        last_error_msg = ""

        for attempt in range(max_attempts):
            if mission_tracker and "check_timeout" in mission_tracker:
                mission_tracker["check_timeout"]()

            current_prompt = prompt
            if last_error_msg:
                error_prefix = "[VALIDATION WARNING]"
                if "Circular dependency" in last_error_msg:
                    error_prefix = "[CRITICAL CIRCULAR DEPENDENCY ERROR]"
                elif "JSON Decode Error" in last_error_msg:
                    error_prefix = "[CRITICAL JSON PARSING ERROR]"
                elif "invalid type" in last_error_msg:
                    error_prefix = "[CRITICAL NODE TYPE ERROR]"

                current_prompt += (
                    f"\n\n{error_prefix}\n"
                    f"Your previous output was invalid. Validation failed with these errors:\n"
                    f"{last_error_msg}\n\n"
                    f"Please analyze the failure, adjust the DAG logic or format, and output a corrected, fully compliant JSON plan."
                )

            buffer = StreamLogBuffer(f"CEO ({preferred_model or 'default'})", log_callback)
            def stream_cb(chunk):
                buffer.write(chunk)

            raw_response = _call_direct(current_prompt, system_instruction, preferred_model=preferred_model, stream_callback=stream_cb)
            buffer.flush()
            if log_callback:
                log_callback(f"CEO ({preferred_model or 'default'})", "\n", "done")

            clean_text = raw_response.strip()
            if "```" in clean_text:
                match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_text, re.DOTALL)
                if match:
                    clean_text = match.group(1).strip()

            if not clean_text.startswith("{") and "{" in clean_text and "}" in clean_text:
                start = clean_text.find("{")
                end = clean_text.rfind("}") + 1
                clean_text = clean_text[start:end]

            try:
                plan_dict = json.loads(clean_text)
                errors = validate_plan_schema(plan_dict)
                if not errors:
                    errors = semantic_validate(plan_dict)
                if not errors:
                    return plan_dict

                last_error_msg = "\n".join(errors)
                _log.info("Validation failed (Attempt %d/%d):\n%s", attempt + 1, max_attempts, last_error_msg)
            except json.JSONDecodeError as e:
                last_error_msg = f"JSON Decode Error: {e}"
                _log.info("JSON parsing failed (Attempt %d/%d):\n%s", attempt + 1, max_attempts, last_error_msg)

            time.sleep(1)

        raise ValueError(
            f"Planner failed to generate a valid plan conforming to the schema after {max_attempts} attempts. Last error: {last_error_msg}"
        )

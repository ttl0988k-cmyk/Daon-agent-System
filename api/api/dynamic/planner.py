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
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


class HermesPlanner:
    """Master Orchestrator that analyzes a task and generates a valid DAG plan."""

    def plan(self, task: str, mission_tracker: dict = None, preferred_model: str = None,
             log_callback=None, run_dir=None, planning_mode: bool = False) -> dict:
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

        system_instruction = (
            "You are the Master Orchestrator and Agent Compiler of a multi-agent system.\n"
            "Generate a valid EXECUTABLE DAG of agents for the task.\n"
            "Respond ONLY with a valid JSON object matching this structure:\n"
            "{\n"
            '  "plan_summary": "Short execution description.",\n'
            '  "skills": ["skill-name-1", "skill-name-2"],\n'
            '  "nodes": [\n'
            "    {\n"
            '      "name": "agent_name (alphanumeric and underscores only)",\n'
            '      "type": "llm | llm+web_search | llm+image_tool | llm+terminal",\n'
            '      "role": "specialist role",\n'
            '      "skills": ["skill-name"],\n'
            '      "system_prompt": "specific instructions for this agent (keep SHORT, 3-5 lines). Skills will be auto-injected by the harness.",\n'
            '      "subtask": "the subtask this agent will execute.",\n'
            '      "input": "input_key_from_dependency (null if none)",\n'
            '      "output": "output_key_for_this_agent",\n'
            '      "model": "Assign the optimal model. MUST match one in AVAILABLE MODELS."\n'
            "    }\n"
            "  ],\n"
            '  "edges": [\n'
            '    ["source_agent_name", "target_agent_name"]\n'
            "  ]\n"
            "}\n"
            "Ensure the JSON output is valid without markdown blocks.\n\n"
            + experience_block + "\n"
            "[CEO DECISION-MAKING CHECKLIST]\n"
            "Before generating the nodes and edges, you MUST mentally evaluate:\n"
            "1. What is the Task Difficulty/Complexity?\n"
            "2. Is a single agent sufficient, or is a multi-agent DAG required?\n"
            "3. What specific roles are needed (e.g., Designer, Developer, Integrator)?\n"
            "4. Which skills from the AVAILABLE SKILLS CATALOG should each agent use?\n"
            "5. What is the optimal model for each role based on task difficulty?\n"
            "6. What is the concrete Success Criteria for the task?\n"
            "7. Is a Reviewer/QA agent needed to verify correctness?\n"
            "8. Does this task involve API routes, response formats, or message structures? If YES, follow the SHARED SCHEMA CONTRACT rules below.\n\n"
            "[SKILL SELECTION RULES]\n"
            "- You select skill NAMES only. The harness will read actual skill files and inject their full content into agent system_prompts automatically.\n"
            "- For Development/Coding agents: assign 'bill-dev' + 'self-reflection' skills.\n"
            "- For QA/Review agents: assign 'sherlock-qa' + 'self-reflection' skills.\n"
            "- For Contract Validation agents: assign 'contract-validator' skill (MUST run BEFORE Backend/Frontend agents).\n"
            "- For UI/Design agents: assign 'taste' + 'landing-page' + 'self-reflection' skills.\n"
            "- For Security-sensitive tasks: assign 'security' skill.\n"
            "- You can assign multiple skills per agent. Only assign relevant skills.\n"
            "- Do NOT embed skill content in 'system_prompt'. Keep system_prompt SHORT (3-5 lines focusing on the unique subtask).\n\n"
            "[RETRIEVER ≠ AUTO-SELECT — CRITICAL ENFORCEMENT]\n"
            "The Semantic Skill Retriever above provides Top-K RECOMMENDATIONS based on embedding similarity.\n"
            "These are SUGGESTIONS ONLY. You are the CEO and YOU make the FINAL decision.\n"
            "RULES YOU MUST FOLLOW:\n"
            "  1. The highest similarity score does NOT guarantee the best skill for the task.\n"
            "  2. You MUST cross-check Retriever recommendations against Skill History (past success rates).\n"
            "     A skill with high similarity but low historical success in this context is SUSPECT.\n"
            "  3. You MUST cross-check against the Skill Graph for conflicts, requirements, and compatibilities.\n"
            "  4. If the Retriever's top recommendation conflicts with context-aware history, OVERRIDE it.\n"
            "  5. You may select skills that are NOT in the Retriever's Top-10 if the Skill Graph requires them.\n"
            "  6. Document your rationale: when you deviate from Retriever recommendations, explain why briefly in plan_summary.\n"
            "[End Retriever ≠ Auto-Select]\n\n"
            + semantic_skill_block + "\n"
            + skill_history_block + "\n"
            + f"{skill_catalog}\n\n"
            + skill_graph_block + "\n"
            "[SHARED SCHEMA CONTRACT — MANDATORY FOR ALL AGENTS]\n"
            "- ALL agents MUST follow the shared Schema contract defined in shared/schema.py and shared/schema.js.\n"
            "- Do NOT invent API routes, response fields, or message formats. If the schema is missing, request it before implementation.\n"
            "- Contract Validator agents (skill: 'contract-validator'): Run BEFORE any Backend/Frontend code is written. Verify endpoint consistency, request/response schema alignment, and common field parity. MUST return VERIFIED PASS before implementation agents are generated. Block agent generation on FAIL.\n"
            "- Frontend agents: Use Schema.createUserMessage(), Schema.validateMessage(), Schema.SSEClientEvent constants only.\n"
            "- Backend agents: Use Message.create_user(), validate_message(), to_response(), to_dict() from shared/schema.py only.\n"
            "- QA agents: Validate against integration_checklist.yaml. All checks MUST achieve VERIFIED PASS.\n"
            "- Integrator agents: Run scripts/run_integration_checks.py after every code change. Exit code 0 required before delivery.\n"
            "- Schema is the Single Source of Truth. No agent may deviate from it.\n\n"
            "[CRITICAL AGENT COMPILING CONSTRAINTS]\n"
            "- Thin Prompting: system_prompt should be 3-5 concise lines. Skill content is auto-injected.\n"
            "- Component Modularization: For UI tasks, split into specialized agents.\n"
            "- PORT/PROCESS SAFETY: Agents must NEVER kill the active backend process on port 9090.\n"
            "\n[MASTER ORCHESTRATION RULES]\n"
            "1. SCHEMA-FIRST DAG ORDER (For API/Message tasks): You MUST strictly follow this execution sequence:\n"
            "   a. Schema Agent: Create/update shared schema files (e.g., shared/schema.py, shared/schema.js).\n"
            "   b. Contract Validator: Create an agent (skill: 'contract-validator') to verify endpoint/schema/field parity BEFORE implementation.\n"
            "   c. Validation Gate: The Contract Validator MUST return VERIFIED PASS. If FAIL, fix the contract. Do NOT proceed with violations.\n"
            "   d. Implementation: Generate Backend/Frontend agents ONLY after passing validation. Inject the schema reference into their prompts.\n"
            "   e. QA/Review: Perform final validation (ensure a QA agent is assigned to validate against integration_checklist.yaml).\n"
            "2. MINIMAL VIABLE DAG: Prefer the smallest viable DAG. Do not create additional agents unless they materially improve output quality or pipeline reliability. Single-agent execution is highly encouraged for simple tasks.\n"
            "3. CONTEXT INTEGRITY: Each downstream agent must receive all necessary context explicitly via 'input' and 'output' dependencies. Never assume shared memory or implicit context between agents.\n"
            "4. ROLE-SPECIFIC SUCCESS CRITERIA: You MUST assign and enforce strict success criteria tailored to each agent's role:\n"
            "   - Planner: DAG logically complete, No missing dependencies, No circular dependencies.\n"
            "   - Schema/Contract: Validation Pass, Strict parity between Frontend/Backend fields.\n"
            "   - Backend: Build/Execution Success, Tests Pass, Lint Pass, No regressions.\n"
            "   - Frontend: Build Success, No Console Errors, UI matches requirements perfectly.\n"
            "   - QA/Review: Integration validation passed, Edge cases tested, Requirements alignment verified.\n"
            "5. ENDLESS LOOP PREVENTION: Avoid endless review-fix cycles. You MUST strictly adhere to these limits:\n"
            "   - Max QA iterative corrections: 2 times.\n"
            "   - If it fails the 2nd time -> Escalate to the highest-reasoning model available.\n"
            "   - If it still fails after escalation -> Terminate the agent chain and report failure.\n"
        )

        try:
            from api.managers import model_manager
            available_models_data = model_manager.get_available_models()
            model_list: list[str] = []
            for group in available_models_data:
                provider_lower = group.get('provider', '').lower()
                if provider_lower in ('minimax', 'deepseek', 'nvidia'):
                    for m in group.get('models', []):
                        model_list.append(m['id'])
            dynamic_model_list = ", ".join(f"'{m}'" for m in model_list) if model_list else "'MiniMax-M3', 'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'"
        except Exception as e:
            _log.warning("Failed to resolve dynamic model list: %s", e)
            dynamic_model_list = "'MiniMax-M3', 'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner'"

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
                    
                    _lines.append(f"      {i+1}. {m_id} (Score: {score} | Quality: {qual} | Speed: {spd}/10 | Rel: {rel} | Cost/1M: ${cost})")
                
                if _lines:
                    _role_recommendations.append(f"  - Role '{_role}':\n" + "\n".join(_lines))

            _model_rec_block = (
                "\n[Dynamic Model Selector — 8-Dimensional Role-based Scorecards]\n"
                "The system has evaluated all available models on 8 dimensions: Task Fit, Success Rate, Cost, Latency, Context Window, JSON Reliability, Health, and Load.\n"
                "Use the scorecards below to select the optimal model for each role. (Higher is better for Score/Quality/Speed/Rel. Lower is better for Cost).\n"
                "High Risk/Complex tasks -> Prioritize Quality & Reliability.\n"
                "Simple/QA tasks -> Prioritize Speed & Cost.\n"
                + "\n".join(_role_recommendations) + "\n"
                "\n[End Dynamic Model Selector Recommendations]\n"
            ) if _role_recommendations else ""
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
                "The user has enabled Planning Mode. You MUST generate the FULL multi-agent DAG containing both the Planner Agent (first) and all subsequent implementation agents (Developer, QA, etc.) in a single plan.\n"
                "The Planner Agent MUST physically write a detailed `plan.md` file in the workspace using the `write_file` tool. Do NOT just output text without saving the file.\n"
                "The orchestrator will automatically execute the Planner Agent first, display `plan.md` to the user, pause for their approval, and then execute the remaining implementation agents.\n"
                "Therefore, define the full plan (e.g. Planner -> Developer -> QA) now in your response."
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

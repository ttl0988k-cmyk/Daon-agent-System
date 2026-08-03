"""
Agent persona resolution and AgentCompiler.

Provides:
- get_integrated_persona(): loads agent persona from profile SOUL/AGENTS.md or role manuals
- AgentCompiler: validates and compiles the CEO's plan into executable agent definitions
  * Supports template_id-based resolution (new) and legacy inline definitions (backward compat)
"""

from pathlib import Path

from api.skill_registry import get_skill_registry
from api.dynamic.plan_validator import semantic_validate
from api.dynamic.template_loader import resolve_template_for_node
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def get_integrated_persona(agent_name: str, agent_role: str) -> str:
    """에이전트 이름 및 역할 정보를 기반으로 프로필의 SOUL/AGENTS 또는 공용 역할 템플릿을 로드합니다."""

    # [1] 정적 에이전트 프로필 폴더 매핑 정의
    profile_mapping = {
        "prada": "프라다(디자인)", "design": "프라다(디자인)", "디자": "프라다(디자인)",
        "bill": "빌(개발)", "dev": "빌(개발)", "개발": "빌(개발)",
        "sherlock": "셜록(검수)", "qa": "셜록(검수)", "검수": "셜록(검수)",
        "tony": "토니(기획)", "planner": "토니(기획)", "기획": "토니(기획)",
        "creative_director": "크리에이티브 디렉터", "cd": "크리에이티브 디렉터",
        "creative-director": "크리에이티브 디렉터", "크리에이티브": "크리에이티브 디렉터",
    }

    # [2] 공용 역할 매뉴얼 파일 매핑 정의 (skills/roles/ 디렉토리 기준)
    manual_mapping = {
        "review": "reviewer.md", "리뷰": "reviewer.md",
        "debug": "debugger.md", "디버그": "debugger.md", "에러": "debugger.md", "오류": "debugger.md",
        "refactor": "refactorer.md", "리팩토": "refactorer.md",
        "test": "tester.md", "테스트": "tester.md",
        "document": "documenter.md", "문서": "documenter.md",
        "explain": "explainer.md", "설명": "explainer.md",
        "write": "writer.md", "글쓰기": "writer.md", "교정": "writer.md",
    }

    search_text = f"{agent_name.lower()} {agent_role.lower()}"

    # --- 1순위: 정적 프로필 매핑 검사 ---
    for kw, folder in profile_mapping.items():
        if kw in search_text:
            profile_dir = Path.home() / ".hermes" / "profiles" / folder
            soul_file = profile_dir / "SOUL.md"
            agents_file = profile_dir / "AGENTS.md"

            persona_parts: list[str] = []
            if soul_file.exists():
                persona_parts.append(f"### [Core Persona: {folder}]\n{soul_file.read_text(encoding='utf-8')}")
            if agents_file.exists():
                persona_parts.append(f"### [Operating Protocols: {folder}]\n{agents_file.read_text(encoding='utf-8')}")
            if persona_parts:
                _log.info("Injected static profile persona '%s' for agent '%s'", folder, agent_name)
                return "\n\n".join(persona_parts)

    # --- 2순위: 공용 역할 매뉴얼 매핑 검사 ---
    for kw, filename in manual_mapping.items():
        if kw in search_text:
            manual_file = Path(__file__).resolve().parent.parent.parent.parent / "skills" / "roles" / filename
            if manual_file.exists():
                _log.info("Injected dynamic role manual '%s' for agent '%s'", filename, agent_name)
                return f"### [Role Manual: {filename.replace('.md', '').upper()}]\n{manual_file.read_text(encoding='utf-8')}"

    return ""


class AgentCompiler:
    """Validates and compiles the node definitions from the plan.

    Key behavior: Reads skill files from SkillRegistry and injects
    their content into each agent's system_prompt at compile time.
    CEO selects skill NAMES → Compiler reads .md files → Injects into agents.
    """

    @staticmethod
    def compile(plan: dict) -> list[dict]:
        # Run compile-time semantic check
        errors = semantic_validate(plan)
        if errors:
            raise ValueError("Compiler rejected plan due to semantic violations:\n" + "\n".join(errors))

        skill_registry = get_skill_registry()

        # Plan-level skills (applied to ALL agents as baseline)
        plan_level_skills: list[str] = plan.get("skills", [])

        nodes_list = plan.get("nodes", [])
        compiled_nodes: list[dict] = []

        for n in nodes_list:
            # --- Template-based resolution (new path) ---
            if n.get("template_id"):
                resolved = resolve_template_for_node(n)
                name = resolved.get("name", "agent").strip().lower().replace(" ", "_")

                # Merge plan-level skills with template skills
                node_skills = list(plan_level_skills)
                for s in (resolved.get("skills") or []):
                    if s not in node_skills:
                        node_skills.append(s)

                # Load skill content from registry
                skill_content = skill_registry.load_skills(node_skills)

                # Build full prompt: template system_prompt + env + messaging + skills
                base_prompt = resolved.get("system_prompt", "")
                env_note = AgentCompiler._get_env_note()
                messaging_note = AgentCompiler._get_messaging_note()
                full_prompt = base_prompt + env_note + messaging_note
                if skill_content:
                    full_prompt += f"\n\n{skill_content}"

                # Tools from template (already resolved)
                enabled_toolsets = list(resolved.get("tools", ["file", "terminal"]))
                # 미디어 생성은 고정 파이프라인이 아닌 일반 능력: 모든 노드가 필요 시
                # generate_image/generate_video 도구를 호출할 수 있도록 toolset을 부여한다.
                if "media-generation" not in enabled_toolsets:
                    enabled_toolsets.append("media-generation")
                # Inject MCP tools
                enabled_toolsets = AgentCompiler._inject_mcp_tools(enabled_toolsets)

                if node_skills:
                    _log.info("Injected skills into '%s' (template: %s): %s",
                              name, resolved.get("template_id"), node_skills)

                compiled_nodes.append({
                    "name": name,
                    "type": resolved.get("type", "llm"),
                    "role": resolved.get("role", "specialist"),
                    "system_prompt": full_prompt,
                    "subtask": resolved.get("subtask", ""),
                    "tools": enabled_toolsets,
                    "input": resolved.get("input") or "",
                    "output": resolved.get("output") or (name + "_output"),
                    "model": resolved.get("model") or "",
                    "skills": node_skills,
                    "template_id": resolved.get("template_id"),
                    "_display_name": resolved.get("_display_name", ""),
                    "_model_prefs": resolved.get("_model_prefs", {}),
                    "_success_criteria": resolved.get("_success_criteria", []),
                    "_runtime": resolved.get("_runtime", {}),
                    "_capability_score": resolved.get("_capability_score", {}),
                    "_cost_profile": resolved.get("_cost_profile", {}),
                    "_model_preference": resolved.get("_model_preference", {}),
                })
                continue

            # --- Legacy path (no template_id, backward compatibility) ---
            name = n.get("name", "agent").strip().lower().replace(" ", "_")
            node_type = n.get("type", "llm").strip().lower()
            enabled_toolsets: list[str] = ["file", "terminal"]
            # 미디어 생성은 모든 노드의 일반 능력으로 부여 (특정 노드 타입에 한정하지 않음).
            enabled_toolsets.append("media-generation")
            enabled_toolsets = AgentCompiler._inject_mcp_tools(enabled_toolsets)

            if "web_search" in node_type:
                enabled_toolsets.append("web_search")
            if "image_tool" in node_type or "image_gen" in node_type:
                enabled_toolsets.append("image_gen")

            # Merge plan-level skills with node-level skills (deduplicated)
            node_skills = list(plan_level_skills)
            for s in (n.get("skills") or []):
                if s not in node_skills:
                    node_skills.append(s)

            # Build system_prompt: original + env/messaging + injected skill content
            # NOTE: get_integrated_persona() 호출 제거 — 정적 에이전트(Bill/Tony/Prada/Sherlock)
            # 페르소나는 다이나믹 하네스에 주입하지 않는다. 100개 템플릿 카탈로그 체계에서는
            # template_id 경로가 system_prompt를 완전히 제공하므로 레거시 경로에서도
            # 정적 프로필 SOUL.md 덮어쓰기(충돌)를 방지한다.
            base_prompt = n.get("system_prompt", "")
            skill_content = skill_registry.load_skills(node_skills)

            env_note = AgentCompiler._get_env_note()
            messaging_note = AgentCompiler._get_messaging_note()

            full_prompt = base_prompt + env_note + messaging_note
            if skill_content:
                full_prompt += f"\n\n{skill_content}"

            if node_skills:
                _log.info("Injected skills into '%s': %s", name, node_skills)

            compiled_nodes.append({
                "name": name,
                "type": node_type,
                "role": n.get("role", "Assistant"),
                "system_prompt": full_prompt,
                "subtask": n.get("subtask", ""),
                "tools": enabled_toolsets,
                "input": n.get("input") or "",
                "output": n.get("output") or (name + "_output"),
                "model": n.get("model") or "",
                "skills": node_skills,
            })
        return compiled_nodes

    @staticmethod
    def _get_env_note() -> str:
        """Generate OS/environment awareness note for system_prompt injection."""
        import sys as _csys, os as _cos
        _cplatform = _csys.platform
        _cis_windows = _cplatform == "win32"
        _cos_name = "Windows" if _cis_windows else ("macOS" if _cplatform == "darwin" else "Linux")

        _cshell = _cos.environ.get('SHELL', '') or _cos.environ.get('COMSPEC', '')
        _cis_bash = 'bash' in _cshell.lower()
        _cshell_label = _cshell if _cshell else ('cmd.exe' if _cis_windows else 'bash')

        env_note = f"\n\n[ENVIRONMENT]\nOS: {_cos_name} | Shell: {_cshell_label}\n"
        if _cis_windows and not _cis_bash:
            env_note += ("CRITICAL: cmd.exe does NOT support heredoc(<<), cat, or Unix commands. "
                        "Use the write_file tool to create files. Use PowerShell for scripts.\n")
        elif _cis_bash:
            env_note += "Bash available: heredoc, Unix commands (ls, find, grep, cat) are supported. Prefer POSIX paths.\n"
        return env_note

    @staticmethod
    def _get_messaging_note() -> str:
        """에이전트 간 메시징(to/cc/inbox) 프로토콜 지시문.

        각 노드 실행 전 수신함이 시스템 프롬프트에 주입되고, 출력의
        [MSG to=X cc=Y]...[/MSG] 블록은 runner가 자동 파싱·발송·정제한다.
        """
        return (
            "\n\n[AGENT MESSAGING]\n"
            "You are part of a multi-agent harness. You can exchange messages with other agents.\n"
            "- Before you run, any unread messages addressed to you are injected above as "
            "'[에이전트 수신함]'. Read and act on them.\n"
            "- To send a message to another agent, embed a block in your final output exactly like:\n"
            "  [MSG to=recipient_name cc=optional_cc_name]your message body[/MSG]\n"
            "- 'to' is required (the target agent's name). 'cc' is optional (comma-separated names).\n"
            "- The block is delivered to the recipient's inbox and stripped from your visible output, "
            "so put only the message inside it; keep your normal answer outside the block.\n"
            "- Use messaging to hand off results, request help, or coordinate — not for content meant "
            "for the user.\n"
        )

    @staticmethod
    def _inject_mcp_tools(toolsets: list[str]) -> list[str]:
        """Inject connected MCP server tool IDs into the toolset list."""
        try:
            from api.mcp_client import get_mcp_manager
            _mgr = get_mcp_manager()
            for _srv_id, _conn in _mgr._connections.items():
                if _conn.connected:
                    mcp_id = f"mcp-{_srv_id}"
                    if mcp_id not in toolsets:
                        toolsets.append(mcp_id)
        except Exception:
            pass
        return toolsets

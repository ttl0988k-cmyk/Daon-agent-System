"""
Agent persona resolution and AgentCompiler.

Provides:
- get_integrated_persona(): loads agent persona from profile SOUL/AGENTS.md or role manuals
- AgentCompiler: validates and compiles the CEO's plan into executable agent definitions
"""

from pathlib import Path

from api.skill_registry import get_skill_registry
from api.dynamic.plan_validator import semantic_validate
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
            manual_file = Path(__file__).resolve().parent.parent.parent / "skills" / "roles" / filename
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
            name = n.get("name", "agent").strip().lower().replace(" ", "_")
            node_type = n.get("type", "llm").strip().lower()
            enabled_toolsets: list[str] = ["file", "terminal"]

            try:
                from api.mcp_client import get_mcp_manager
                _mgr = get_mcp_manager()
                for _srv_id, _conn in _mgr._connections.items():
                    if _conn.connected:
                        enabled_toolsets.append(f"mcp-{_srv_id}")
            except Exception:
                pass

            if "web_search" in node_type:
                enabled_toolsets.append("web_search")
            if "image_tool" in node_type or "image_gen" in node_type:
                enabled_toolsets.append("image_gen")

            # Merge plan-level skills with node-level skills (deduplicated)
            node_skills = list(plan_level_skills)
            for s in (n.get("skills") or []):
                if s not in node_skills:
                    node_skills.append(s)

            # Build system_prompt: original + persona/manual + injected skill content
            base_prompt = n.get("system_prompt", "")
            persona_content = get_integrated_persona(name, n.get("role", ""))
            skill_content = skill_registry.load_skills(node_skills)

            # Inject OS/environment awareness into system_prompt
            import sys as _csys, os as _cos
            _cplatform = _csys.platform
            _cis_windows = _cplatform == "win32"
            _cos_name = "Windows" if _cis_windows else ("macOS" if _cplatform == "darwin" else "Linux")

            # Detect actual shell
            _cshell = _cos.environ.get('SHELL', '') or _cos.environ.get('COMSPEC', '')
            _cis_bash = 'bash' in _cshell.lower()
            _cshell_label = _cshell if _cshell else ('cmd.exe' if _cis_windows else 'bash')

            env_note = f"\n\n[ENVIRONMENT]\nOS: {_cos_name} | Shell: {_cshell_label}\n"
            if _cis_windows and not _cis_bash:
                env_note += ("CRITICAL: cmd.exe does NOT support heredoc(<<), cat, or Unix commands. "
                            "Use the write_file tool to create files. Use PowerShell for scripts.\n")
            elif _cis_bash:
                env_note += "Bash available: heredoc, Unix commands (ls, find, grep, cat) are supported. Prefer POSIX paths.\n"

            full_prompt = base_prompt + env_note
            if persona_content:
                full_prompt += f"\n\n{persona_content}"
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

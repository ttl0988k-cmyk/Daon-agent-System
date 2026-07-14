"""
Experience Database — Unified Execution Experience Repository.

The central nervous system of Hermes' organizational learning.
Aggregates data from ModelHistory, SkillHistory, and adds two new dimensions:
- DAG History: which topology patterns work best for which task types
- Agent Combo History: which agent set configurations produce optimal results

Provides cross-dimensional queries that the CEO uses to make data-driven decisions.

Storage: ~/.hermes/experience_db.json

Architecture:
    ModelHistory ─┐
    SkillHistory ─┤
    DAG History  ─┼── ExperienceDatabase ──→ CEO Prompt Insights
    Agent Combo  ─┘
"""

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

_EXP_DB_PATH = Path.home() / ".hermes" / "experience_db.json"


# ---------------------------------------------------------------------------
# Task Type Classifier
# ---------------------------------------------------------------------------

_TASK_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("landing_page", ["landing page", "landing-page", "랜딩 페이지", "promo site", "marketing page"]),
    ("api_development", ["api", "endpoint", "rest", "graphql", "route", "백엔드 api"]),
    ("frontend_ui", ["frontend", "ui", "ux", "component", "css", "html", "react component", "vue"]),
    ("fullstack_app", ["fullstack", "full stack", "full-stack", "app", "application", "웹앱"]),
    ("refactoring", ["refactor", "refactoring", "리팩토링", "clean up", "cleanup"]),
    ("bug_fix", ["bug", "fix", "debug", "디버그", "error", "issue", "hotfix"]),
    ("testing", ["test", "testing", "unit test", "coverage", "qa", "테스트"]),
    ("documentation", ["document", "docs", "readme", "문서", "documentation", "주석"]),
    ("security_audit", ["security", "보안", "vulnerability", "취약점", "audit", "penetration"]),
    ("devops", ["deploy", "docker", "ci/cd", "pipeline", "배포", "infrastructure"]),
    ("data_analysis", ["data", "analysis", "analytics", "데이터 분석", "통계", "report"]),
    ("integration", ["integration", "연동", "webhook", "slack", "notion", "plugin"]),
    ("schema_design", ["schema", "contract", "스키마", "계약", "validation", "검증"]),
]


def classify_task_type(task: str) -> str:
    """Classify a task into a broad task type category using keyword matching."""
    task_lower = task.lower()
    scores: dict[str, int] = defaultdict(int)
    for task_type, keywords in _TASK_TYPE_PATTERNS:
        for kw in keywords:
            if kw in task_lower:
                scores[task_type] += 1
    if scores:
        return max(scores, key=scores.get)
    return "general"


# ---------------------------------------------------------------------------
# DAG Topology Classifier
# ---------------------------------------------------------------------------

def classify_dag_topology(nodes: list, edges: list) -> str:
    """Classify a DAG into a topology pattern.

    Returns one of:
        "single" - only one node
        "sequential_N" - N nodes in a linear chain
        "parallel_N" - N parallel nodes, fan-out/fan-in
        "hybrid_N" - mix of sequential and parallel
        "empty" - no nodes
    """
    n = len(nodes) if nodes else 0
    if n == 0:
        return "empty"
    if n == 1:
        return "single"

    # Build adjacency
    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    adj: dict[str, list[str]] = defaultdict(list)

    node_names = {node.get("name", "") for node in nodes}

    for edge in (edges or []):
        src, tgt = edge
        in_degree[tgt] += 1
        out_degree[src] += 1
        adj[src].append(tgt)

    # Check if perfectly sequential (each node has at most 1 in, 1 out, no branching)
    max_in = max(in_degree.values()) if in_degree else 0
    max_out = max(out_degree.values()) if out_degree else 0
    if max_in <= 1 and max_out <= 1:
        return f"sequential_{n}"

    # Check fan-out (one source → many) or fan-in (many → one sink)
    fan_out = sum(1 for v in out_degree.values() if v > 1)
    fan_in = sum(1 for v in in_degree.values() if v > 1)
    if fan_out > 0 or fan_in > 0:
        return f"parallel_{n}"

    return f"hybrid_{n}"


# ---------------------------------------------------------------------------
# Experience Database
# ---------------------------------------------------------------------------

class ExperienceDatabase:
    """Unified query layer over all execution experience dimensions.

    Reads from:
        - ModelHistory (~/.hermes/model_history.json)
        - SkillHistory (~/.hermes/skill_history.json)
    Writes/Manages:
        - DAG History (topology × task_type)
        - Agent Combo History (agent_set × task_type)

    Provides cross-dimensional query methods for the CEO planner.
    """

    def __init__(self):
        self._data: dict = {}
        self._model_history: Optional[dict] = None
        self._skill_history: Optional[dict] = None
        self._load()

    # --- Persistence ---

    def _load(self):
        """Load experience database from disk."""
        if _EXP_DB_PATH.exists():
            try:
                self._data = json.loads(_EXP_DB_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.warning("Failed to parse experience_db.json, starting fresh: %s", e)
                self._data = {}
        else:
            self._data = {}

        self._data.setdefault("dag_history", [])
        self._data.setdefault("agent_combo_history", [])
        self._data.setdefault("run_history", [])

        # Lazy-load model and skill history
        self._model_history = self._load_model_history()
        self._skill_history = self._load_skill_history()

    def _save(self):
        """Persist to disk."""
        _EXP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _EXP_DB_PATH.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _load_model_history() -> dict:
        """Load model history from its canonical path."""
        mh_path = Path.home() / ".hermes" / "model_history.json"
        if mh_path.exists():
            try:
                return json.loads(mh_path.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.warning("Failed to load model history: %s", e)
                return {}
        return {}

    @staticmethod
    def _load_skill_history() -> dict:
        """Load skill history from its canonical path."""
        sh_path = Path.home() / ".hermes" / "skill_history.json"
        if sh_path.exists():
            try:
                return json.loads(sh_path.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.warning("Failed to load skill history: %s", e)
                return {}
        return {}

    # --- Recording ---

    def record_dag_run(
        self,
        task: str,
        nodes: list,
        edges: list,
        skills_used: list[str],
        agent_roles: dict[str, str],
        model_assignments: dict[str, str],
        success: bool,
        wall_time_ms: float,
    ):
        """Record a full DAG execution experience.

        Args:
            task: The original task description
            nodes: DAG node definitions from the plan
            edges: DAG edges from the plan
            skills_used: All skill names used across the plan
            agent_roles: Mapping of agent_name → role
            model_assignments: Mapping of agent_name → model_id
            success: Whether the mission succeeded
            wall_time_ms: Total wall clock time in milliseconds
        """
        task_type = classify_task_type(task)
        topology = classify_dag_topology(nodes, edges)
        node_count = len(nodes) if nodes else 0
        edge_count = len(edges) if edges else 0

        # Compute max parallelism from the DAG
        in_degree: dict[str, int] = defaultdict(int)
        adj: dict[str, list[str]] = defaultdict(list)
        for edge in (edges or []):
            src, tgt = edge
            in_degree[tgt] += 1
            adj[src].append(tgt)
        max_parallelism = max(1, sum(1 for v in in_degree.values() if v == 0))

        # DAG History entry
        dag_entry = {
            "task_type": task_type,
            "topology": topology,
            "node_count": node_count,
            "edge_count": edge_count,
            "max_parallelism": max_parallelism,
            "success": success,
            "wall_time_ms": round(wall_time_ms, 1),
            "timestamp": time.time(),
        }
        self._data["dag_history"].append(dag_entry)

        # Agent Combo History entry
        agent_names = sorted(agent_roles.keys()) if agent_roles else []
        combo_entry = {
            "task_type": task_type,
            "agent_set": agent_names,
            "agent_roles": agent_roles,
            "skill_set": sorted(skills_used) if skills_used else [],
            "model_set": model_assignments,
            "topology": topology,
            "node_count": node_count,
            "success": success,
            "wall_time_ms": round(wall_time_ms, 1),
            "timestamp": time.time(),
        }
        self._data["agent_combo_history"].append(combo_entry)

        # Run History (comprehensive)
        run_entry = {
            "task_type": task_type,
            "topology": topology,
            "node_count": node_count,
            "skills": sorted(skills_used) if skills_used else [],
            "agents": agent_names,
            "roles": agent_roles,
            "models": model_assignments,
            "success": success,
            "wall_time_ms": round(wall_time_ms, 1),
            "timestamp": time.time(),
        }
        self._data["run_history"].append(run_entry)

        # Prune: keep last 500 entries for each history
        for key in ["dag_history", "agent_combo_history", "run_history"]:
            if len(self._data[key]) > 500:
                self._data[key] = self._data[key][-500:]

        self._save()
        _logger.info(
            "ExperienceDB: recorded DAG run (task_type=%s, topology=%s, nodes=%d, success=%s)",
            task_type, topology, node_count, success,
        )

    # --- Query Methods ---

    def get_best_topology_for_task_type(self, task_type: str, min_samples: int = 2) -> Optional[dict]:
        """Find the topology pattern with the highest success rate for a task type."""
        entries = [e for e in self._data["dag_history"] if e["task_type"] == task_type]
        if len(entries) < min_samples:
            return None

        by_topology: dict[str, dict] = defaultdict(lambda: {"success": 0, "fail": 0, "total_time": 0})
        for e in entries:
            key = e["topology"]
            if e["success"]:
                by_topology[key]["success"] += 1
            else:
                by_topology[key]["fail"] += 1
            by_topology[key]["total_time"] += e.get("wall_time_ms", 0)

        best = None
        best_rate = -1.0
        for topo, stats in by_topology.items():
            total = stats["success"] + stats["fail"]
            if total < min_samples:
                continue
            rate = stats["success"] / total if total > 0 else 0
            if rate > best_rate:
                best_rate = rate
                best = {
                    "topology": topo,
                    "success_rate": round(rate, 3),
                    "samples": total,
                    "avg_time_ms": round(stats["total_time"] / total, 1) if total > 0 else 0,
                }
        return best

    def get_best_agent_combo_for_task_type(self, task_type: str, min_samples: int = 2) -> Optional[dict]:
        """Find the agent/skill/model combination with the highest success rate."""
        entries = [e for e in self._data["agent_combo_history"] if e["task_type"] == task_type]
        if len(entries) < min_samples:
            return None

        # Group by normalized agent set (sorted role list)
        by_combo: dict[str, dict] = defaultdict(lambda: {"success": 0, "fail": 0, "total_time": 0, "entries": []})

        for e in entries:
            roles_key = ",".join(sorted(e.get("agent_roles", {}).values()))
            if e["success"]:
                by_combo[roles_key]["success"] += 1
            else:
                by_combo[roles_key]["fail"] += 1
            by_combo[roles_key]["total_time"] += e.get("wall_time_ms", 0)
            by_combo[roles_key]["entries"].append(e)

        best = None
        best_rate = -1.0
        for combo_key, stats in by_combo.items():
            total = stats["success"] + stats["fail"]
            if total < min_samples:
                continue
            rate = stats["success"] / total if total > 0 else 0
            if rate > best_rate:
                best_rate = rate
                latest = stats["entries"][-1]
                best = {
                    "agent_roles": latest.get("agent_roles", {}),
                    "skill_set": latest.get("skill_set", []),
                    "model_set": latest.get("model_set", {}),
                    "topology": latest.get("topology", ""),
                    "node_count": latest.get("node_count", 0),
                    "success_rate": round(rate, 3),
                    "samples": total,
                    "avg_time_ms": round(stats["total_time"] / total, 1) if total > 0 else 0,
                }
        return best

    def get_best_skill_combo_for_task_type(self, task_type: str, min_samples: int = 2) -> Optional[dict]:
        """Find the skill set with the highest success rate for a task type."""
        entries = [e for e in self._data["agent_combo_history"] if e["task_type"] == task_type]
        if len(entries) < min_samples:
            return None

        by_skills: dict[str, dict] = defaultdict(lambda: {"success": 0, "fail": 0})

        for e in entries:
            skills_key = ",".join(e.get("skill_set", []))
            if e["success"]:
                by_skills[skills_key]["success"] += 1
            else:
                by_skills[skills_key]["fail"] += 1

        best = None
        best_rate = -1.0
        for skills_key, stats in by_skills.items():
            total = stats["success"] + stats["fail"]
            if total < min_samples:
                continue
            rate = stats["success"] / total if total > 0 else 0
            if rate > best_rate:
                best_rate = rate
                best = {
                    "skill_set": skills_key.split(",") if skills_key else [],
                    "success_rate": round(rate, 3),
                    "samples": total,
                }
        return best

    def get_overall_stats(self) -> dict:
        """Return aggregate statistics across all dimensions."""
        runs = self._data["run_history"]
        total = len(runs)
        if total == 0:
            return {"total_runs": 0}

        success_count = sum(1 for r in runs if r["success"])
        task_types = defaultdict(lambda: {"total": 0, "success": 0})
        for r in runs:
            tt = r["task_type"]
            task_types[tt]["total"] += 1
            if r["success"]:
                task_types[tt]["success"] += 1

        return {
            "total_runs": total,
            "overall_success_rate": round(success_count / total, 3) if total > 0 else 0,
            "task_types": {
                tt: {
                    "total": stats["total"],
                    "success_rate": round(stats["success"] / stats["total"], 3) if stats["total"] > 0 else 0,
                }
                for tt, stats in sorted(task_types.items(), key=lambda x: -x[1]["total"])
            },
        }

    def query_best_for_task(self, task: str, min_samples: int = 1) -> dict:
        """Comprehensive query: given a task, return the best known combination.

        This is the primary query method used by the CEO planner.
        Returns insights across all dimensions.
        """
        task_type = classify_task_type(task)
        result = {
            "task_type": task_type,
            "best_topology": self.get_best_topology_for_task_type(task_type, min_samples),
            "best_agent_combo": self.get_best_agent_combo_for_task_type(task_type, min_samples),
            "best_skill_combo": self.get_best_skill_combo_for_task_type(task_type, min_samples),
            "overall_stats": self.get_overall_stats(),
        }
        return result

    def format_for_ceo(self, task: str, min_samples: int = 1) -> str:
        """Format experience insights for injection into the CEO planner prompt.

        Returns a markdown-formatted string ready for prompt injection.
        """
        insights = self.query_best_for_task(task, min_samples)
        overall = insights.get("overall_stats", {})

        if overall.get("total_runs", 0) == 0:
            return (
                "\n[EXPERIENCE DATABASE — No Prior Data]\n"
                "(This is the first execution. No historical patterns available yet.\n"
                "The system will learn from this run and improve future plans.)\n"
                "[End Experience Database]\n"
            )

        task_type = insights["task_type"]
        lines = [
            f"\n[EXPERIENCE DATABASE — Organizational Learning Insights]",
            f"Total runs across all task types: {overall.get('total_runs', 0)}",
            f"Overall success rate: {overall.get('overall_success_rate', 0):.1%}",
            "",
            f"### Insights for Task Type: '{task_type}'",
            "",
        ]

        # Task type breakdown
        task_stats = overall.get("task_types", {}).get(task_type)
        if task_stats:
            lines.append(
                f"**{task_type}**: {task_stats['total']} runs, "
                f"{task_stats['success_rate']:.1%} success rate"
            )
        else:
            lines.append(f"**{task_type}**: No prior runs for this specific task type.")

        # Best topology
        best_topo = insights.get("best_topology")
        if best_topo:
            lines.append("")
            lines.append("### Best DAG Topology")
            lines.append(
                f"- **{best_topo['topology']}**: {best_topo['success_rate']:.1%} success "
                f"({best_topo['samples']} runs, avg {best_topo['avg_time_ms']:.0f}ms)"
            )
            lines.append("- RECOMMENDATION: Prefer this topology pattern when planning the DAG.")

        # Best agent combo
        best_combo = insights.get("best_agent_combo")
        if best_combo:
            lines.append("")
            lines.append("### Best Agent Combination")
            lines.append(f"- Success rate: {best_combo['success_rate']:.1%} ({best_combo['samples']} runs)")
            lines.append(f"- Topology: {best_combo.get('topology', 'N/A')} ({best_combo.get('node_count', 0)} nodes)")
            if best_combo.get("agent_roles"):
                roles_str = ", ".join(f"{n}({r})" for n, r in best_combo["agent_roles"].items())
                lines.append(f"- Agent roles: {roles_str}")
            if best_combo.get("skill_set"):
                lines.append(f"- Skills used: {', '.join(best_combo['skill_set'])}")
            if best_combo.get("model_set"):
                models_str = ", ".join(f"{n}→{m}" for n, m in best_combo["model_set"].items())
                lines.append(f"- Models used: {models_str}")
            lines.append("- RECOMMENDATION: Consider this agent/skill/model configuration.")

        # Best skill combo
        best_skills = insights.get("best_skill_combo")
        if best_skills and best_skills.get("skill_set"):
            lines.append("")
            lines.append("### Best Skill Combination")
            lines.append(
                f"- **{', '.join(best_skills['skill_set'])}**: "
                f"{best_skills['success_rate']:.1%} success ({best_skills['samples']} runs)"
            )

        # General guidance
        lines.append("")
        lines.append("### How to Use This Data")
        lines.append("1. These are HISTORICAL PATTERNS, not deterministic rules.")
        lines.append("2. If a combination has a very low sample count (< 3), treat with caution.")
        lines.append("3. Balance experience data with the current task's unique requirements.")
        lines.append("4. The system learns continuously — each run improves future recommendations.")
        lines.append("[End Experience Database]\n")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_exp_db: Optional[ExperienceDatabase] = None


def get_experience_db() -> ExperienceDatabase:
    """Get or create the global ExperienceDatabase singleton."""
    global _exp_db
    if _exp_db is None:
        _exp_db = ExperienceDatabase()
        _logger.info("ExperienceDatabase initialized with %d runs",
                      len(_exp_db._data.get("run_history", [])))
    return _exp_db

"""
Plan schema validation and semantic checking.

Provides:
- validate_plan_schema(): structural validation of plan dict (nodes + edges)
- semantic_validate(): cycle detection, tool checks, skill conflict detection
"""

from collections import deque

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _validate_plan_nodes(nodes: list) -> list[str]:
    """Validate the nodes list: required fields and string-type constraints."""
    errors: list[str] = []
    if not isinstance(nodes, list):
        return ["'nodes' must be a list."]
    required_keys = ["name", "type", "role", "system_prompt", "subtask"]
    string_keys = ["name", "type", "role", "system_prompt", "subtask", "input", "output"]
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node at index {idx} is not an object.")
            continue
        label = node.get("name", "unnamed")
        errors.extend(
            f"Node at index {idx} ({label}) is missing required key '{k}'." for k in required_keys if k not in node
        )
        errors.extend(
            f"Node at index {idx} key '{k}' must be a string or null."
            for k in string_keys
            if k in node and node[k] is not None and not isinstance(node[k], str)
        )
    return errors


def _validate_plan_edges(edges: list, node_names: set[str]) -> list[str]:
    """Validate the edges list: format and referenced node name existence."""
    errors: list[str] = []
    if not isinstance(edges, list):
        return ["'edges' must be a list."]
    for idx, edge in enumerate(edges):
        if not isinstance(edge, (list, tuple)):
            errors.append(f"Edge at index {idx} is not a list/tuple.")
            continue
        if len(edge) < 2:
            errors.append(f"Edge at index {idx} must have at least 2 elements (source, target).")
            continue
        src = str(edge[0]).strip().lower().replace(" ", "_")
        dest = str(edge[1]).strip().lower().replace(" ", "_")
        if src not in node_names:
            errors.append(f"Edge at index {idx} refers to non-existent source node '{edge[0]}'.")
        if dest not in node_names:
            errors.append(f"Edge at index {idx} refers to non-existent target node '{edge[1]}'.")
    return errors


def _extract_node_names(plan: dict) -> set[str]:
    """Extract normalized snake_case node name set from a plan dict."""
    return {
        node["name"].strip().lower().replace(" ", "_")
        for node in plan.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }


def validate_plan_schema(plan: dict) -> list[str]:
    """Validate the top-level schema of a plan dict (nodes and edges)."""
    errors: list[str] = []
    if "nodes" not in plan:
        errors.append("Missing 'nodes' key in plan.")
    else:
        errors.extend(_validate_plan_nodes(plan["nodes"]))
    if "edges" not in plan:
        errors.append("Missing 'edges' key in plan.")
    else:
        errors.extend(_validate_plan_edges(plan["edges"], _extract_node_names(plan)))
    return errors


def semantic_validate(plan: dict) -> list[str]:
    """Perform semantic checks on the plan:
    - Circular dependency detection (cycle detection using Kahn's algorithm)
    - Tool availability check
    - Skill conflict detection
    """
    errors: list[str] = []
    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])

    # 1. Circle/Cycle Detection
    node_names = [n.get("name", "").strip().lower().replace(" ", "_") for n in nodes if isinstance(n, dict)]
    node_names = [name for name in node_names if name]

    in_degree = {name: 0 for name in node_names}
    adj_list = {name: [] for name in node_names}

    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            src = str(edge[0]).strip().lower().replace(" ", "_")
            dest = str(edge[1]).strip().lower().replace(" ", "_")
            if src in adj_list and dest in in_degree:
                adj_list[src].append(dest)
                in_degree[dest] += 1

    # Kahn's algorithm for cycle detection
    queue: deque[str] = deque(node for node, deg in in_degree.items() if deg == 0)
    visited_count = 0
    while queue:
        curr = queue.popleft()
        visited_count += 1
        for neighbor in adj_list[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count < len(node_names):
        errors.append("Circular dependency detected in agent edges. The execution path contains a cycle.")

    # 2. Tool availability check
    allowed_types = {"llm", "llm+web_search", "llm+image_tool", "llm+terminal"}
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        ntype = node.get("type", "").strip().lower()
        if ntype not in allowed_types:
            errors.append(
                f"Node at index {idx} ({node.get('name', 'unnamed')}) has invalid type '{ntype}'. "
                f"Allowed types: {sorted(allowed_types)}"
            )

    # 3. Skill conflict detection
    try:
        from api.skill_registry import get_skill_registry
        skill_registry = get_skill_registry()

        # Check plan-level skill conflicts
        plan_skills = plan.get("skills", [])
        plan_conflicts = skill_registry.detect_conflicts(plan_skills)
        for conflict in plan_conflicts:
            errors.append(f"[Plan Skills] {conflict}")

        # Check individual node-level skill conflicts
        for idx, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_skills = node.get("skills", [])
            node_conflicts = skill_registry.detect_conflicts(node_skills)
            for conflict in node_conflicts:
                node_label = node.get("name", f"index {idx}")
                errors.append(f"[Node '{node_label}' Skills] {conflict}")
    except Exception as e:
        _log.warning("Failed to run skill conflict detection: %s", e)

    return errors

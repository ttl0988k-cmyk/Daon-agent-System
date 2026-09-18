"""
Plan schema validation and semantic checking.

Provides:
- validate_plan_schema(): structural validation of plan dict (nodes + edges)
- semantic_validate(): cycle detection, tool checks, skill conflict detection
- validate_plan_structure(): NON-BLOCKING structure quality warnings (fail-open)
"""

from collections import defaultdict, deque

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


# 갭 B: 노드별 능력 축 필드 — MCP 서버 / 플러그인 / 실행환경.
# CEO가 계획 단계에서 "이 노드에는 어떤 MCP가 필요한가", "이 노드는 격리 환경에서
# 돌려야 하는가"를 선택할 수 있게 하는 스키마 필드다. 전부 OPTIONAL이며
# 미지정 시 기존 동작(연결된 MCP 전체 주입, 로컬 워크스페이스)이 유지된다.
ALLOWED_NODE_ENVIRONMENTS = {"local", "sandbox"}


def _validate_node_capability_fields(node: dict, idx: int, label: str) -> list[str]:
    """Validate optional per-node capability fields: mcp_servers / plugins / environment.

    - mcp_servers: null | list[str] — CEO가 이 노드에 바인딩할 MCP 서버 ID 목록.
      null/미지정 = 기존 동작(연결된 모든 MCP 주입), [] = MCP 주입 없음.
    - plugins: null | list[str] — 노드에 주입할 플러그인 식별자 목록 (확장용).
    - environment: null | "local" | "sandbox" — 실행환경 선택.
      "local" = 공유 run_dir 사용(기본), "sandbox" = 노드별 격리 임시 디렉터리.
    """
    errors: list[str] = []

    for list_key in ("mcp_servers", "plugins"):
        if list_key not in node or node[list_key] is None:
            continue
        value = node[list_key]
        if not isinstance(value, list):
            errors.append(f"Node at index {idx} ({label}) key '{list_key}' must be a list of strings or null.")
            continue
        for item in value:
            if not isinstance(item, str) or not item.strip():
                errors.append(
                    f"Node at index {idx} ({label}) key '{list_key}' entries must be non-empty strings."
                )
                break

    if "environment" in node and node["environment"] is not None:
        env_value = node["environment"]
        if not isinstance(env_value, str) or env_value.strip().lower() not in ALLOWED_NODE_ENVIRONMENTS:
            errors.append(
                f"Node at index {idx} ({label}) key 'environment' must be one of "
                f"{sorted(ALLOWED_NODE_ENVIRONMENTS)} (got {env_value!r})."
            )

    return errors


def _validate_plan_nodes(nodes: list) -> list[str]:
    """Validate the nodes list: required fields and string-type constraints.
    
    Supports two node formats:
    - Template-based: requires 'name', 'template_id', 'subtask' (type/role/system_prompt resolved from template)
    - Legacy inline: requires 'name', 'type', 'role', 'system_prompt', 'subtask'
    """
    errors: list[str] = []
    if not isinstance(nodes, list):
        return ["'nodes' must be a list."]
    legacy_required_keys = ["name", "type", "role", "system_prompt", "subtask"]
    template_required_keys = ["name", "template_id", "subtask"]
    string_keys = ["name", "type", "role", "system_prompt", "subtask", "input", "output", "template_id", "environment"]
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"Node at index {idx} is not an object.")
            continue
        label = node.get("name", "unnamed")
        # Determine which required keys apply
        if node.get("template_id"):
            required_keys = template_required_keys
        else:
            required_keys = legacy_required_keys
        errors.extend(
            f"Node at index {idx} ({label}) is missing required key '{k}'." for k in required_keys if k not in node
        )
        errors.extend(
            f"Node at index {idx} key '{k}' must be a string or null."
            for k in string_keys
            if k in node and node[k] is not None and not isinstance(node[k], str)
        )
        # 갭 B: 노드별 능력 축 필드 검증 (mcp_servers / plugins / environment)
        errors.extend(_validate_node_capability_fields(node, idx, label))
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
        # MULTI-AGENT ENFORCEMENT: CEO must delegate — at least 2 agents required.
        if isinstance(plan["nodes"], list) and len(plan["nodes"]) < 2:
            errors.append(
                "Plan must contain at least 2 nodes (agents). "
                "The CEO must delegate work to a multi-agent team — "
                "a single-node plan is not allowed. "
                "Add at least one implementation agent and one reviewer/QA agent."
            )
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

    # 2. Tool availability check (skip for template-based nodes — type resolved at compile time)
    allowed_types = {"llm", "llm+web_search", "llm+image_tool", "llm+terminal"}
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        if node.get("template_id"):
            continue  # Template nodes get their type from the template at compile time
        ntype = node.get("type", "").strip().lower()
        if ntype and ntype not in allowed_types:
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


# ---------------------------------------------------------------------------
# Structure quality warnings (NON-BLOCKING / fail-open)
# ---------------------------------------------------------------------------
#
# validate_plan_schema()/semantic_validate()는 "유효성"만 본다 — 필수 키, 최소
# 노드 수, 엣지 대상 존재, 사이클, 노드 타입, 스킬 충돌. 이들은 실행을 막는
# 하드 에러다.
#
# 반면 아래 validate_plan_structure()는 "품질"을 본다 — 고아 노드, 분리된
# 컴포넌트, 싱크 노드 부재, 중복 노드명, 완전 직렬 체인. 이들은 실행을 막지
# 않는다(fail-open). 호출측은 결과를 WARNING 로그로만 남기고 계획을 그대로
# 진행시킨다. 목적은 CEO가 만든 DAG의 구조적 결함을 관측 가능하게 만드는 것.

def _normalize_name(name) -> str:
    """Normalize a node name the same way the rest of the validator does."""
    return str(name).strip().lower().replace(" ", "_")


def validate_plan_structure(plan: dict) -> list[str]:
    """Return NON-BLOCKING structure quality warnings for a plan.

    Unlike validate_plan_schema()/semantic_validate(), the returned strings are
    advisory only — the caller must NOT block execution on them. Each warning is
    prefixed with ``[structure]`` so it is distinguishable from hard errors.

    Checks:
    - orphan nodes (no incoming and no outgoing edge)
    - disconnected components (more than one weakly-connected group)
    - missing sink node (no node with out-degree 0)
    - duplicate node names
    - fully-serial chain (max batch width == 1 while node count >= 3)
    """
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return warnings

    nodes = plan.get("nodes", [])
    edges = plan.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return warnings

    # Collect normalized names, preserving order, and detect duplicates.
    raw_names = [
        n.get("name")
        for n in nodes
        if isinstance(n, dict) and isinstance(n.get("name"), str) and n.get("name").strip()
    ]
    norm_names = [_normalize_name(n) for n in raw_names]
    nameset = set(norm_names)

    seen: set[str] = set()
    dupes: set[str] = set()
    for name in norm_names:
        if name in seen:
            dupes.add(name)
        seen.add(name)
    if dupes:
        warnings.append(f"[structure] Duplicate node name(s): {sorted(dupes)}")

    if not nameset:
        return warnings

    # Build adjacency (deduped) restricted to known node names.
    adj: dict[str, set[str]] = defaultdict(set)
    rev: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            src = _normalize_name(edge[0])
            tgt = _normalize_name(edge[1])
            if src in nameset and tgt in nameset and src != tgt:
                adj[src].add(tgt)
                rev[tgt].add(src)

    # Orphan nodes: no incoming and no outgoing edge.
    orphans = sorted(n for n in nameset if not adj.get(n) and not rev.get(n))
    if orphans:
        warnings.append(f"[structure] Orphan node(s) with no edges: {orphans}")

    # Missing sink: no node with out-degree 0 (every node has a successor).
    if not any(not adj.get(n) for n in nameset):
        warnings.append("[structure] No sink node (every node has an outgoing edge).")

    # Disconnected components (weakly connected, via union-find over undirected edges).
    parent: dict[str, str] = {n: n for n in nameset}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    for src, targets in adj.items():
        for tgt in targets:
            _union(src, tgt)
    components = {_find(n) for n in nameset}
    if len(components) > 1:
        warnings.append(
            f"[structure] Disconnected plan: {len(components)} separate component(s) "
            f"for {len(nameset)} node(s)."
        )

    # Fully-serial chain: max batch width == 1 while node count >= 3.
    if len(nameset) >= 3:
        deg: dict[str, int] = {n: 0 for n in nameset}
        for src, targets in adj.items():
            for tgt in targets:
                deg[tgt] += 1
        batches: list[list[str]] = []
        placed: set[str] = set()
        queue: deque[str] = deque(n for n, d in deg.items() if d == 0)
        while queue:
            batch = list(queue)
            batches.append(batch)
            placed.update(batch)
            queue.clear()
            for pnode in batch:
                for child in adj.get(pnode, ()):
                    deg[child] -= 1
            queue.extend(n for n, d in deg.items() if d == 0 and n not in placed)
        max_width = max((len(b) for b in batches), default=1)
        if max_width <= 1:
            warnings.append(
                f"[structure] Fully-serial chain: {len(nameset)} nodes but max "
                f"parallel width is 1 (no concurrency)."
            )

    return warnings

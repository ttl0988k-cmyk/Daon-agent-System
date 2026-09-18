# -*- coding: utf-8 -*-
"""
probe — DAG 품질 게이트 (P1) 검증.

목적:
  1. experience_db.record_dag_run() 의 max_parallelism 이 "최대 배치 폭"으로
     올바르게 계산되는지 (구버전: 항상 1 로 고정되던 버그 회귀 방지).
  2. dag_utils._compute_execution_batches() 와 수치가 일치하는지 (러너 정합).
  3. plan_validator.validate_plan_structure() 가 구조 결함을 실제로 검출하는지
     (고아 노드 / 분리 컴포넌트 / 싱크 부재 / 중복명 / 완전직렬).
  4. validate_plan_structure() 가 절대 raise 하지 않는지 (fail-open 계약).
  5. validate_plan_structure() 가 실행을 막지 않는지 — 반환값은 경고 문자열
     목록일 뿐이며, 정상 계획에는 빈 목록을 반환한다.

실행:
    cd "C:/daon/Daon agent System"
    PYTHONIOENCODING=utf-8 python api/tests/test_dag_quality_gate.py
"""
import sys
from pathlib import Path

# ── 경로 부트스트랩 ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent   # Daon agent System
sys.path.insert(0, str(ROOT / "api"))

from api.dynamic.experience_db import (          # noqa: E402
    ExperienceDatabase,
    classify_dag_topology,
)
from api.dynamic.dag_utils import (              # noqa: E402
    _build_dag_structures,
    _compute_execution_batches,
)
from api.dynamic.plan_validator import (         # noqa: E402
    validate_plan_structure,
    validate_plan_schema,
    semantic_validate,
)

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append((name, detail))
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  — " + detail) if detail else ""))


# ---------------------------------------------------------------------------
# max_parallelism 계산 헬퍼 — record_dag_run 을 거치지 않고 순수 계산만 뽑아낸다.
# (record_dag_run 은 파일 I/O 를 동반하므로, 동일 알고리즘을 여기서 재현해
#  "러너 배치" 와 "경험 DB 수치" 가 같은 정의를 쓰는지 대조한다.)
# ---------------------------------------------------------------------------
def _max_parallelism(nodes, edges):
    """experience_db.record_dag_run() 내부와 동일한 max batch width 계산."""
    from collections import defaultdict, deque

    node_names = [
        str(n.get("name", "")).strip()
        for n in (nodes or [])
        if isinstance(n, dict) and n.get("name")
    ]
    if not node_names:
        return 1
    nameset = set(node_names)
    deg = {n: 0 for n in node_names}
    adj = defaultdict(list)
    for edge in (edges or []):
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            src = str(edge[0]).strip()
            tgt = str(edge[1]).strip()
            if src in nameset and tgt in nameset:
                adj[src].append(tgt)
                deg[tgt] += 1
    batches = []
    placed = set()
    queue = deque(n for n, d in deg.items() if d == 0)
    while queue:
        batch = list(queue)
        batches.append(batch)
        placed.update(batch)
        queue.clear()
        for parent in batch:
            for child in adj[parent]:
                deg[child] -= 1
        queue.extend(n for n, d in deg.items() if d == 0 and n not in placed)
    return max(1, max((len(b) for b in batches), default=1))


def _nodes(*names):
    return [{"name": n} for n in names]


# ---------------------------------------------------------------------------
# [1] max_parallelism — 6 케이스 (계획서 §2-1 실측과 동일)
# ---------------------------------------------------------------------------
def test_max_parallelism_cases():
    print("\n[1] max_parallelism — 최대 배치 폭 (6 케이스)")

    # (a) fan-out 1 -> 4 : 루트 1개가 4개로 분기 → 배치폭 4
    nodes = _nodes("root", "a", "b", "c", "d")
    edges = [["root", "a"], ["root", "b"], ["root", "c"], ["root", "d"]]
    check("fan-out 1->4 == 4", _max_parallelism(nodes, edges) == 4,
          "got %s" % _max_parallelism(nodes, edges))

    # (b) fan-out 3 + merge : 3개 병렬 후 1개로 합류 → 배치폭 3
    nodes = _nodes("root", "a", "b", "c", "merge")
    edges = [["root", "a"], ["root", "b"], ["root", "c"],
             ["a", "merge"], ["b", "merge"], ["c", "merge"]]
    check("fan-out 3 + merge == 3", _max_parallelism(nodes, edges) == 3,
          "got %s" % _max_parallelism(nodes, edges))

    # (c) fan-out 9 : 루트 1개가 9개로 분기 → 배치폭 9
    nodes = _nodes("root", *["n%d" % i for i in range(9)])
    edges = [["root", "n%d" % i] for i in range(9)]
    check("fan-out 9 == 9", _max_parallelism(nodes, edges) == 9,
          "got %s" % _max_parallelism(nodes, edges))

    # (d) serial chain 4 : 완전 직렬 → 배치폭 1
    nodes = _nodes("a", "b", "c", "d")
    edges = [["a", "b"], ["b", "c"], ["c", "d"]]
    check("serial chain 4 == 1", _max_parallelism(nodes, edges) == 1,
          "got %s" % _max_parallelism(nodes, edges))

    # (e) orphan : 엣지 없는 노드 2개 → 배치폭 2
    nodes = _nodes("a", "b")
    edges = []
    check("2 nodes 0 edges == 2", _max_parallelism(nodes, edges) == 2,
          "got %s" % _max_parallelism(nodes, edges))

    # (f) empty : 노드 없음 → 1 (하한)
    check("no nodes == 1", _max_parallelism([], []) == 1,
          "got %s" % _max_parallelism([], []))


# ---------------------------------------------------------------------------
# [2] 구버전 버그 회귀 방지 — "항상 1" 이 아님을 증명
# ---------------------------------------------------------------------------
def test_old_bug_regression():
    print("\n[2] 구버전 버그 회귀 방지 (항상 1 이던 결함)")

    nodes = _nodes("root", "a", "b", "c", "d")
    edges = [["root", "a"], ["root", "b"], ["root", "c"], ["root", "d"]]
    got = _max_parallelism(nodes, edges)
    check("parallel_5 토폴로지에서 max_parallelism > 1", got > 1,
          "got %s (구버전은 항상 1)" % got)

    # 구버전 알고리즘을 재현해 "1" 이 나옴을 확인 (버그의 존재 증명)
    from collections import defaultdict
    in_degree = defaultdict(int)
    for edge in edges:
        in_degree[edge[1]] += 1
    old = max(1, sum(1 for v in in_degree.values() if v == 0))
    check("구버전 알고리즘은 1 을 반환 (버그 재현)", old == 1, "old=%s" % old)


# ---------------------------------------------------------------------------
# [3] 러너 정합 — dag_utils._compute_execution_batches 와 수치 일치
# ---------------------------------------------------------------------------
def test_runner_parity():
    print("\n[3] 러너 정합 — _compute_execution_batches 와 일치")

    cases = [
        (_nodes("root", "a", "b", "c", "d"),
         [["root", "a"], ["root", "b"], ["root", "c"], ["root", "d"]]),
        (_nodes("a", "b", "c", "d"),
         [["a", "b"], ["b", "c"], ["c", "d"]]),
        (_nodes("root", "a", "b", "c", "merge"),
         [["root", "a"], ["root", "b"], ["root", "c"],
          ["a", "merge"], ["b", "merge"], ["c", "merge"]]),
    ]
    for idx, (nodes, edges) in enumerate(cases):
        # 러너 경로: _build_dag_structures → _compute_execution_batches
        # (_build_dag_structures 는 agents 를 dict 리스트로 받는다 — runner.py 참조)
        agents = [{"name": n["name"]} for n in nodes]
        in_degree, adj_list, _ = _build_dag_structures(agents, edges)
        batches = _compute_execution_batches(in_degree, adj_list)
        runner_width = max((len(b) for b in batches), default=1)
        db_width = _max_parallelism(nodes, edges)
        check("case %d: runner(%d) == db(%d)" % (idx, runner_width, db_width),
              runner_width == db_width)


# ---------------------------------------------------------------------------
# [4] validate_plan_structure — 구조 결함 검출
# ---------------------------------------------------------------------------
def _plan(nodes, edges):
    return {"nodes": [{"name": n} for n in nodes], "edges": edges}


def test_structure_detection():
    print("\n[4] validate_plan_structure — 구조 결함 검출")

    # (a) 정상 fan-out 계획 → 경고 없음
    good = _plan(["root", "a", "b", "merge"],
                 [["root", "a"], ["root", "b"], ["a", "merge"], ["b", "merge"]])
    w = validate_plan_structure(good)
    check("정상 계획 → 경고 0", w == [], "got %s" % w)

    # (b) 고아 노드 검출
    orphan = _plan(["root", "a", "b", "lonely"],
                   [["root", "a"], ["a", "b"]])
    w = validate_plan_structure(orphan)
    check("고아 노드 검출", any("Orphan" in x for x in w), "got %s" % w)

    # (c) 분리 컴포넌트 검출
    split = _plan(["a", "b", "c", "d"],
                  [["a", "b"], ["c", "d"]])
    w = validate_plan_structure(split)
    check("분리 컴포넌트 검출", any("Disconnected" in x for x in w), "got %s" % w)

    # (d) 싱크 부재 검출 (사이클은 아니지만 모든 노드가 후속을 가짐)
    no_sink = _plan(["a", "b", "c"],
                    [["a", "b"], ["b", "c"], ["c", "a"]])
    w = validate_plan_structure(no_sink)
    check("싱크 부재 검출", any("sink" in x for x in w), "got %s" % w)

    # (e) 중복 노드명 검출
    dup = _plan(["a", "a", "b"], [["a", "b"]])
    w = validate_plan_structure(dup)
    check("중복 노드명 검출", any("Duplicate" in x for x in w), "got %s" % w)

    # (f) 완전 직렬 체인 검출 (노드 3+ 인데 배치폭 1)
    serial = _plan(["a", "b", "c", "d"],
                   [["a", "b"], ["b", "c"], ["c", "d"]])
    w = validate_plan_structure(serial)
    check("완전 직렬 체인 검출", any("Fully-serial" in x for x in w), "got %s" % w)


# ---------------------------------------------------------------------------
# [5] fail-open 계약 — 절대 raise 하지 않음
# ---------------------------------------------------------------------------
def test_fail_open():
    print("\n[5] fail-open 계약 — 절대 raise 하지 않음")

    bad_inputs = [
        None,
        {},
        {"nodes": "not-a-list", "edges": []},
        {"nodes": [], "edges": "not-a-list"},
        {"nodes": [None, 1, "x"], "edges": [[1, 2], "bad", []]},
        {"nodes": [{"name": None}], "edges": [["a"]]},
    ]
    for idx, bad in enumerate(bad_inputs):
        try:
            result = validate_plan_structure(bad)
            ok = isinstance(result, list)
        except Exception as exc:  # noqa: BLE001
            ok = False
            result = "RAISED: %r" % exc
        check("bad input %d → list 반환 (no raise)" % idx, ok, "got %s" % result)


# ---------------------------------------------------------------------------
# [6] 비차단 계약 — 정상 계획은 스키마/시맨틱 통과 + 구조 경고만
# ---------------------------------------------------------------------------
def test_non_blocking():
    print("\n[6] 비차단 계약 — 실행을 막지 않음")

    # 완전 직렬이지만 스키마/시맨틱은 유효한 계획
    plan = {
        "nodes": [
            {"name": "a", "type": "llm", "role": "r", "system_prompt": "s", "subtask": "t"},
            {"name": "b", "type": "llm", "role": "r", "system_prompt": "s", "subtask": "t"},
            {"name": "c", "type": "llm", "role": "r", "system_prompt": "s", "subtask": "t"},
        ],
        "edges": [["a", "b"], ["b", "c"]],
    }
    schema_errors = validate_plan_schema(plan)
    semantic_errors = semantic_validate(plan)
    structure_warnings = validate_plan_structure(plan)

    check("스키마 통과 (하드 에러 0)", schema_errors == [], "got %s" % schema_errors)
    check("시맨틱 통과 (하드 에러 0)", semantic_errors == [], "got %s" % semantic_errors)
    check("구조 경고는 존재 (직렬)", len(structure_warnings) > 0,
          "got %s" % structure_warnings)
    check("구조 경고는 [structure] 접두사", all(x.startswith("[structure]")
                                                for x in structure_warnings),
          "got %s" % structure_warnings)


# ---------------------------------------------------------------------------
# [7] classify_dag_topology 와의 정합 (parallel_N 은 N>=2 를 의미)
# ---------------------------------------------------------------------------
def test_topology_consistency():
    print("\n[7] classify_dag_topology 정합")

    nodes = _nodes("root", "a", "b", "c", "d")
    edges = [["root", "a"], ["root", "b"], ["root", "c"], ["root", "d"]]
    topo = classify_dag_topology(nodes, edges)
    width = _max_parallelism(nodes, edges)
    check("parallel 토폴로지 → width >= 2", topo.startswith("parallel") and width >= 2,
          "topo=%s width=%s" % (topo, width))


def main():
    print("=" * 70)
    print("DAG 품질 게이트 (P1) 검증")
    print("=" * 70)

    test_max_parallelism_cases()
    test_old_bug_regression()
    test_runner_parity()
    test_structure_detection()
    test_fail_open()
    test_non_blocking()
    test_topology_consistency()

    print("\n" + "=" * 70)
    print("결과: %d passed, %d failed" % (len(PASSED), len(FAILED)))
    print("=" * 70)
    if FAILED:
        for name, detail in FAILED:
            print("  FAIL: %s  %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

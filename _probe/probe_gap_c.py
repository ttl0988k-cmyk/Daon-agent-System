"""갭 C 기능 프로브: 수용 기준 추출/부착/파싱 + 검증 에이전트 + 재계획 + 루프 안전장치.

실제 LLM 호출 없이 _call_direct를 페이크로 교체해 다음을 검증한다:
1. attach/parse 수용 기준 라운드트립
2. ensure_acceptance_criteria 폴백 경로 (이미 부착/precomputed/LLM 추출/일반 폴백)
3. _verify_acceptance: 기준 없음 스킵, fail 판정 파싱, pass 판정, fail-open
4. _run_acceptance_replan: 결핍 능력 피드백 + 성공 결과 기반 재병합
5. limits 기본값 + run() 소스의 루프 탈출 조건 3종 존재 확인
"""
import re
import sys
import types

sys.path.insert(0, "api")

import api.dynamic.clarifier as clar  # noqa: E402
import api.dynamic.orchestrator as orch  # noqa: E402
from api.dynamic.clarifier import (  # noqa: E402
    attach_acceptance_criteria, parse_acceptance_criteria, ensure_acceptance_criteria,
    ACCEPTANCE_MARKER_START, ACCEPTANCE_MARKER_END,
)

# ── 1. attach/parse 라운드트립 ──
criteria = ["로그인 폼이 렌더링되어야 한다", "제출 시 유효성 검사가 동작해야 한다", "에러 메시지가 한국어로 표시되어야 한다"]
task_base = "로그인 페이지를 만들어줘.\n\n## 추가 컨텍스트\n프레임워크: Flask"
attached = attach_acceptance_criteria(task_base, criteria)
assert ACCEPTANCE_MARKER_START in attached and ACCEPTANCE_MARKER_END in attached
parsed = parse_acceptance_criteria(attached)
assert parsed == criteria, parsed
# 마커 없으면 빈 리스트
assert parse_acceptance_criteria(task_base) == []
assert parse_acceptance_criteria("") == []
assert parse_acceptance_criteria(None) == []
print("1. attach/parse round-trip OK")

# ── 2. ensure_acceptance_criteria 폴백 경로 ──
# (a) 이미 부착됨 → 변경 없음
assert ensure_acceptance_criteria(attached) == attached
# (b) precomputed 우선 사용 (LLM 호출 없이)
calls = []
clar._call_direct = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("LLM should not be called"))
ensured_b = ensure_acceptance_criteria(task_base, precomputed=["기준A", "기준B"])
assert parse_acceptance_criteria(ensured_b) == ["기준A", "기준B"]
assert calls == [], "precomputed 경로에서는 LLM 호출이 없어야 한다"
# (c) LLM 추출 성공 경로
clar._call_direct = lambda prompt, system=None, preferred_model=None, **k: '{"acceptance_criteria": ["추출기준1", "추출기준2"]}'
ensured_c = ensure_acceptance_criteria(task_base)
assert parse_acceptance_criteria(ensured_c) == ["추출기준1", "추출기준2"]
# (d) LLM 추출 실패 → 일반 폴백
clar._call_direct = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LLM down"))
ensured_d = ensure_acceptance_criteria(task_base)
assert parse_acceptance_criteria(ensured_d) == ["원본 요청이 산출물에 완전히 구현되어 있어야 한다."]
# (e) 빈 enriched_task → 그대로 반환
assert ensure_acceptance_criteria("") == ""
print("2. ensure_acceptance_criteria fallback paths OK")

# ── 3. _verify_acceptance ──
runner = orch.HermesDynamicRunner()
noop = lambda: None
task_with_criteria = attach_acceptance_criteria("테스트 작업", ["기준1", "기준2"])

# (a) 기준 없음 → 즉시 pass (LLM 호출 없이 스킵)
orch._call_direct = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call LLM"))
v = runner._verify_acceptance("결과물", "기준 없는 작업", noop)
assert v["verdict"] == "pass" and v["unmet_criteria"] == []
# (b) fail 판정 + unmet/caps 파싱
orch._call_direct = lambda prompt, system=None, preferred_model=None, **k: (
    '```json\n{"verdict": "fail", "unmet_criteria": ["기준2"], '
    '"missing_capabilities": ["에러 처리"], "reasoning": "증거 부족"}\n```'
)
v = runner._verify_acceptance("결과물", task_with_criteria, noop)
assert v["verdict"] == "fail" and v["unmet_criteria"] == ["기준2"]
assert v["missing_capabilities"] == ["에러 처리"], v
# (c) pass 판정 시 unmet 강제 비움
orch._call_direct = lambda prompt, system=None, preferred_model=None, **k: (
    '{"verdict": "pass", "unmet_criteria": ["잔재"], "missing_capabilities": [], "reasoning": "ok"}'
)
v = runner._verify_acceptance("결과물", task_with_criteria, noop)
assert v["verdict"] == "pass" and v["unmet_criteria"] == []
# (d) verdict 비정규값 + unmet 존재 → fail로 정규화
orch._call_direct = lambda prompt, system=None, preferred_model=None, **k: (
    '{"verdict": "weird", "unmet_criteria": ["기준1"], "missing_capabilities": [], "reasoning": ""}'
)
v = runner._verify_acceptance("결과물", task_with_criteria, noop)
assert v["verdict"] == "fail" and v["unmet_criteria"] == ["기준1"]
# (e) LLM 예외 → fail-open(pass)으로 파이프라인 비차단
orch._call_direct = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LLM down"))
v = runner._verify_acceptance("결과물", task_with_criteria, noop)
assert v["verdict"] == "pass" and v["unmet_criteria"] == []
print("3. _verify_acceptance semantics OK")

# ── 4. _run_acceptance_replan ──
captured = {}

class FakePlanner:
    def plan(self, task, mission_tracker=None, preferred_model=None, **k):
        captured["replan_prompt"] = task
        return {"nodes": [{"name": "fixer"}], "edges": []}

class FakeCompiler:
    def compile(self, plan):
        captured["compiled_plan"] = plan
        return [{"name": "fixer", "role": "fixer"}]

class FakeRunner:
    def run(self, agents, edges, task, **k):
        captured["runner_kwargs"] = k
        captured["runner_agents"] = agents
        return [{"name": "fixer", "role": "fixer", "status": "success",
                 "output": "보완된 산출물", "output_key": "fixer_out", "generation": k.get("generation", 0)}]

class FakeMerger:
    def merge(self, results, task, **k):
        captured["merge_results"] = results
        return f"MERGED({len(results)})"

runner.planner, runner.compiler, runner.runner, runner.merger = FakePlanner(), FakeCompiler(), FakeRunner(), FakeMerger()

prev_results = [
    {"name": "a1", "role": "dev", "status": "success", "output": "o1", "output_key": "k1", "generation": 1, "parents": []},
    {"name": "a2", "role": "dev", "status": "failed", "output": "boom", "output_key": "k2", "generation": 1, "parents": []},
]
new_final, merged_results, merged_plan, merged_agents = runner._run_acceptance_replan(
    ["기준2"], ["에러 처리"], "기존 산출물", task_with_criteria,
    state_manager=None, mission_tracker={}, plan={"nodes": []}, runner_results=prev_results,
    compiled_agents=[{"name": "a1"}], check_timeout=noop, generation=2)

prompt = captured["replan_prompt"]
assert "기준2" in prompt and "에러 처리" in prompt, "미충족 기준/결핍 능력이 재계획 프롬프트에 주입되어야 한다"
assert "기존 산출물" in prompt
io = captured["runner_kwargs"]["initial_outputs"]
assert [r["output_key"] for r in io] == ["k1"], "실패 노드는 initial_outputs에서 제외되어야 한다"
assert captured["runner_kwargs"]["generation"] == 2
assert [r["name"] for r in merged_results] == ["a1", "fixer"], "이전 성공 + 재계획 결과만 병합"
assert new_final == "MERGED(2)"
# E-L1/E-L2 시공 이후 merged_plan은 능력 판정/제작 큐/디스패치 키를 추가로 가질 수 있다.
# 핵심 계약: 원본 계획 + 재계획 키는 항상 존재하고, 추가 키는 E-L1/E-L2 산출물로 한정된다.
assert {"first_run_plan", "acceptance_replan"} <= set(merged_plan.keys())
assert set(merged_plan.keys()) <= {
    "first_run_plan", "acceptance_replan",
    "capability_resolutions", "builder_queue", "builder_dispatches",
}, f"예상 밖 merged_plan 키: {set(merged_plan.keys())}"
assert merged_agents == [{"name": "a1"}, {"name": "fixer", "role": "fixer"}]
print("4. _run_acceptance_replan OK")

# ── 5. limits 기본값 + run() 루프 탈출 조건 존재 확인 ──
from api.dynamic.limits import _load_harness_limits  # noqa: E402
limits = _load_harness_limits()
assert limits["mission"]["max_acceptance_retries"] == 2, limits["mission"]

src = open("api/api/dynamic/orchestrator.py", encoding="utf-8").read()
assert 'max_acceptance_retries' in src
assert 'acceptance_attempt >= max_acceptance' in src, "재시도 상한 탈출 조건 누락"
assert 'cur_unmet >= prev_unmet' in src, "개선 증거(미충족 집합 감소) 탈출 조건 누락"
assert '_verify_acceptance' in src and '_run_acceptance_replan' in src
print("5. limits + loop break conditions OK")

print("ALL GAP-C PROBES PASSED")

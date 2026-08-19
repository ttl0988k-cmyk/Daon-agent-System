"""갭 E-L1 기능 프로브: 결핍 능력 -> 결정 사슬(스킬/에이전트/Builder) + 오케스트레이터 배선.

실제 LLM/레지스트리/서버 없이 다음을 검증한다:
1. capability_resolver 상수/순서 값 (와이어 안정 문자열)
2. 결정 사슬: 순서 강제 + 단축 평가 (스킬 해결 시 에이전트/Builder 미호출)
3. 단계 예외 fail-safe (스킬 조회 실패 -> 체인 계속, 전체 실패 -> NEEDS_BUILDER 보고)
4. resolve() 입력 정규화 + builder_queue 수집 (E-L2 핸드오프)
5. 기본 단계 구현: 토큰 매칭(페이크 레지스트리/역할), Builder 요청 형태
6. 오케스트레이터 배선: 재계획 프롬프트 주입 + merged_plan 노출 + 리졸버 실패 폴백
7. 정적 배선 확인 (소스 내 임포트/속성/키 존재)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


from api.dynamic.capability_resolver import (  # noqa: E402
    CapabilityResolver, CapabilityResolutionError,
    RESOLVED_BY_SKILL, RESOLVED_BY_AGENT, NEEDS_BUILDER,
    STEP_SKILL, STEP_AGENT, STEP_BUILDER, STEP_ORDER,
    default_skill_searcher, default_agent_assigner, default_builder,
)

# ── 1. 상수/순서 값 ──
check(RESOLVED_BY_SKILL == "resolved_by_skill", "RESOLVED_BY_SKILL 와이어 값")
check(RESOLVED_BY_AGENT == "resolved_by_agent", "RESOLVED_BY_AGENT 와이어 값")
check(NEEDS_BUILDER == "needs_builder", "NEEDS_BUILDER 와이어 값")
check(STEP_ORDER == (STEP_SKILL, STEP_AGENT, STEP_BUILDER), "STEP_ORDER 순서 고정")
print("1. constants OK")

# ── 2. 순서 강제 + 단축 평가 ──
calls = []
r = CapabilityResolver(
    skill_searcher=lambda cap: calls.append("skill") or {"skill": "s1"},
    agent_assigner=lambda cap: calls.append("agent") or {"agent": "a1"},
    builder=lambda cap: calls.append("builder") or {"builder_request": {"capability": cap}},
)
rec = r.resolve_one("capA")
check(rec["outcome"] == RESOLVED_BY_SKILL, "스킬 해결 outcome")
check(rec["detail"] == {"skill": "s1"}, "스킬 해결 detail")
check(rec["steps_tried"] == [STEP_SKILL], "단축 평가: 스킬만 시도")
check(calls == ["skill"], "단축 평가: 에이전트/Builder 미호출")
check(rec["capability"] == "capA", "레코드에 capability 보존")

calls.clear()
r2 = CapabilityResolver(
    skill_searcher=lambda cap: calls.append("skill") or None,
    agent_assigner=lambda cap: calls.append("agent") or {"agent": "a1"},
    builder=lambda cap: calls.append("builder") or {"builder_request": {"capability": cap}},
)
rec = r2.resolve_one("capB")
check(rec["outcome"] == RESOLVED_BY_AGENT, "에이전트 해결 outcome")
check(rec["steps_tried"] == [STEP_SKILL, STEP_AGENT], "순서: 스킬->에이전트")
check(calls == ["skill", "agent"], "에이전트 해결 시 Builder 미호출")

calls.clear()
r3 = CapabilityResolver(
    skill_searcher=lambda cap: None,
    agent_assigner=lambda cap: None,
    builder=lambda cap: calls.append("builder") or {"builder_request": {"capability": cap}},
)
rec = r3.resolve_one("capC")
check(rec["outcome"] == NEEDS_BUILDER, "Builder 분기 outcome")
check(rec["detail"]["builder_request"]["capability"] == "capC", "Builder 요청에 capability")
check(rec["steps_tried"] == [STEP_SKILL, STEP_AGENT, STEP_BUILDER], "순서: 3단계 전부 시도")
check(calls == ["builder"], "Builder 정확히 1회 호출")

calls.clear()
r4 = CapabilityResolver(
    skill_searcher=lambda cap: None, agent_assigner=lambda cap: None,
    builder=lambda cap: calls.append("builder") or {"builder_request": {}},
    enable_builder=False)
rec = r4.resolve_one("capD")
check(rec["outcome"] == NEEDS_BUILDER, "라우터 모드에서도 NEEDS_BUILDER 보고")
check(rec["detail"] == {"builder_request": None}, "라우터 모드: 제작 요청 없음")
check(STEP_BUILDER not in rec["steps_tried"], "라우터 모드: Builder 단계 미시도")
check(calls == [], "라우터 모드: Builder 함수 미호출")
print("2. ordered chain + short-circuit OK")

# ── 3. 단계 예외 fail-safe ──
calls.clear()


def boom(cap):
    calls.append("skill-boom")
    raise RuntimeError("registry down")


r5 = CapabilityResolver(
    skill_searcher=boom,
    agent_assigner=lambda cap: calls.append("agent") or {"agent": "a1"},
    builder=lambda cap: calls.append("builder") or {"builder_request": {}})
rec = r5.resolve_one("capE")
check(rec["outcome"] == RESOLVED_BY_AGENT, "단계 예외: 체인이 에이전트로 계속")
check(calls == ["skill-boom", "agent"], "단계 예외: 순서 유지")

r6 = CapabilityResolver(
    skill_searcher=boom,
    agent_assigner=lambda cap: (_ for _ in ()).throw(RuntimeError("x")),
    builder=lambda cap: (_ for _ in ()).throw(RuntimeError("y")))
rec = r6.resolve_one("capF")
check(rec["outcome"] == NEEDS_BUILDER, "전 단계 실패: NEEDS_BUILDER 보고")
check(rec["detail"] == {"builder_request": None}, "전 단계 실패: builder_request=None")
check(rec["steps_tried"] == [STEP_SKILL, STEP_AGENT, STEP_BUILDER], "전 단계 실패: 3단계 시도 기록")
print("3. step exception fail-safe OK")

# ── 4. resolve() 정규화 + builder_queue ──
r7 = CapabilityResolver(skill_searcher=lambda cap: None, agent_assigner=lambda cap: None)
res, queue = r7.resolve(None)
check(res == [] and queue == [], "None 입력 -> 빈 결과")
res, queue = r7.resolve("single cap")
check(len(res) == 1 and res[0]["capability"] == "single cap", "문자열 입력 -> 단일 목록 정규화")
res, queue = r7.resolve(["  ", "", None, "real cap"])
check(len(res) == 1 and res[0]["capability"] == "real cap", "빈 cap 스킵")
check(len(queue) == 1 and queue[0]["capability"] == "real cap", "builder_queue 수집")
try:
    r7.resolve(123)
    check(False, "비-iterable 입력은 예외를 던져야 한다")
except CapabilityResolutionError:
    check(True, "비-iterable 입력 -> CapabilityResolutionError")

r8 = CapabilityResolver(
    skill_searcher=lambda cap: {"skill": "s"} if cap == "capSkill" else None,
    agent_assigner=lambda cap: {"agent": "a"} if cap == "capAgent" else None)
res, queue = r8.resolve(["capSkill", "capAgent", "capBuild"])
check([x["outcome"] for x in res] == [RESOLVED_BY_SKILL, RESOLVED_BY_AGENT, NEEDS_BUILDER],
      "혼합 outcome 입력 순서 보존")
check(len(queue) == 1 and queue[0]["capability"] == "capBuild",
      "builder_queue는 제작 대상만 수집")
print("4. resolve normalization + builder_queue OK")

# ── 5. 기본 단계 구현 ──


class FakeRegistry:
    def __init__(self, names):
        self.skills = {n: object() for n in names}


class BrokenRegistry:
    @property
    def skills(self):
        raise RuntimeError("boom")


hit = default_skill_searcher("pdf generation", registry=FakeRegistry(["pdf-generation", "web-scrape"]))
check(hit and hit["skill"] == "pdf-generation", "기본 스킬 검색: 토큰 매칭")
check(hit["score"] == 2, "기본 스킬 검색: 중복 토큰 점수")
check(default_skill_searcher("zzz qqq", registry=FakeRegistry(["pdf-generation"])) is None,
      "토큰 중복 없음 -> None")
check(default_skill_searcher("anything", registry=BrokenRegistry()) is None,
      "레지스트리 예외 -> None (fail-safe)")
check(default_skill_searcher("", registry=FakeRegistry(["x"])) is None, "빈 cap -> None")

check(default_agent_assigner("database tuning", known_roles=["database-engineer", "frontend"])
      == {"agent": "database-engineer"}, "기본 에이전트 배정: 역할 매칭")
check(default_agent_assigner("quantum flux", known_roles=["database-engineer"]) is None,
      "역할 매칭 없음 -> None")
check(default_agent_assigner("anything") is None, "known_roles 없음 -> None (보수적 기본)")

breq = default_builder("new cap")
check(breq["builder_request"]["capability"] == "new cap", "기본 Builder: capability 기록")
check(breq["builder_request"]["status"] == "pending", "기본 Builder: status=pending")
check(breq["builder_request"]["source"] == "capability_resolver", "기본 Builder: source 기록")
print("5. default step implementations OK")

# ── 6. 오케스트레이터 배선 ──
import api.dynamic.orchestrator as orch  # noqa: E402

runner = orch.HermesDynamicRunner()
check(runner.capability_resolver is None, "리졸버 지연 구성 (기본 None)")

captured = {}


class FakePlanner:
    def plan(self, task, mission_tracker=None, preferred_model=None, **k):
        captured["replan_prompt"] = task
        return {"nodes": [{"name": "fixer"}], "edges": []}


class FakeCompiler:
    def compile(self, plan):
        return [{"name": "fixer", "role": "fixer"}]


class FakeRunner:
    def run(self, agents, edges, task, **k):
        return [{"name": "fixer", "role": "fixer", "status": "success",
                 "output": "보완 산출물", "output_key": "fixer_out",
                 "generation": k.get("generation", 0)}]


class FakeMerger:
    def merge(self, results, task, **k):
        return "MERGED(%d)" % len(results)


runner.planner, runner.compiler, runner.runner, runner.merger = (
    FakePlanner(), FakeCompiler(), FakeRunner(), FakeMerger())

logs = []


def log_cb(agent, content, status="running"):
    logs.append((agent, content, status))


# 주입 리졸버: capSkill->스킬, capAgent->에이전트, capBuild->Builder(기본)
runner.capability_resolver = CapabilityResolver(
    skill_searcher=lambda cap: {"skill": "skill-x"} if cap == "capSkill" else None,
    agent_assigner=lambda cap: {"agent": "agent-y"} if cap == "capAgent" else None)

prev_results = [{"name": "a1", "role": "dev", "status": "success", "output": "o1",
                 "output_key": "k1", "generation": 1, "parents": []}]
new_final, merged_results, merged_plan, merged_agents = runner._run_acceptance_replan(
    ["기준2"], ["capSkill", "capAgent", "capBuild"], "이전 산출물", "테스트 작업",
    None, {}, {"nodes": []}, prev_results, [], lambda: None, log_callback=log_cb)

prompt = captured["replan_prompt"]
check("결핍 능력(missing capabilities)" in prompt, "caps_line 존재")
check("능력별 해결 판정" in prompt, "결정 사슬 판정 블록 주입")
check("capSkill -> 기존 스킬 'skill-x' 사용" in prompt, "스킬 판정 라인")
check("capAgent -> 전문 에이전트 'agent-y' 배정" in prompt, "에이전트 판정 라인")
check("capBuild" in prompt and "Builder 핸드오프" in prompt, "Builder 판정 라인")
check("capability_resolutions" in merged_plan, "merged_plan에 해결 기록 노출")
check(len(merged_plan["capability_resolutions"]) == 3, "해결 기록 3건")
check(merged_plan.get("builder_queue")
      and merged_plan["builder_queue"][0]["capability"] == "capBuild",
      "merged_plan에 builder_queue 노출 (E-L2 핸드오프)")
check(any(a == "Resolver" for a, c, s in logs), "Resolver 로그 방출")
check(any("제작 요청 1건" in c for a, c, s in logs if a == "Resolver"), "Resolver 로그 내용")
check(new_final == "MERGED(2)", "기존 성공 결과 + 재계획 결과 재병합")

# missing_caps 비어있음 -> 기존 경로 (해결 키 없음)
captured.clear()
new_final, _, merged_plan2, _ = runner._run_acceptance_replan(
    ["기준1"], [], "이전", "작업", None, {}, {"nodes": []}, prev_results, [], lambda: None)
check("결핍 능력(missing capabilities)" not in captured["replan_prompt"],
      "caps 비어있으면 caps_line 없음")
check("capability_resolutions" not in merged_plan2
      and "builder_queue" not in merged_plan2, "caps 비어있으면 해결 키 없음")

# 리졸버 예외 -> 우아한 폴백, 재계획은 계속
class BrokenResolver:
    def resolve(self, caps):
        raise RuntimeError("resolver crashed")


runner.capability_resolver = BrokenResolver()
captured.clear()
new_final, _, merged_plan3, _ = runner._run_acceptance_replan(
    ["기준1"], ["some cap"], "이전", "작업", None, {}, {"nodes": []}, prev_results, [], lambda: None)
check("결핍 능력(missing capabilities)" in captured["replan_prompt"],
      "리졸버 실패 시 기존 caps_line 폴백")
check("능력별 해결 판정" not in captured["replan_prompt"], "리졸버 실패 시 판정 블록 없음")
check("capability_resolutions" not in merged_plan3, "리졸버 실패 시 해결 키 없음")
check(new_final == "MERGED(2)", "리졸버 실패에도 재계획 완주")

# 빈 caps에서 _resolve_missing_capabilities 직접 호출 (기본 리졸버 지연 구성 경로)
runner.capability_resolver = None
res, queue, guidance = runner._resolve_missing_capabilities([])
check(res == [] and queue == [] and guidance == [], "빈 caps -> 빈 해결 결과")
print("6. orchestrator wiring OK")

# ── 7. 정적 배선 확인 ──
src = (ROOT / "api" / "api" / "dynamic" / "orchestrator.py").read_text(encoding="utf-8")
check("from api.dynamic.capability_resolver import" in src, "임포트 배선")
check("self.capability_resolver" in src, "리졸버 속성 배선")
check("_resolve_missing_capabilities" in src, "결정 사슬 메서드 배선")
check('"capability_resolutions"' in src, "capability_resolutions 키 배선")
check('"builder_queue"' in src, "builder_queue 키 배선")
print("7. static wiring OK")

print("ALL GAP-E-L1 PROBES PASSED (%d checks)" % CHECKS)

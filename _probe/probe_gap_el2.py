"""갭 E-L2 기능 프로브: Builder Agent 역할화 — builder_queue 소비 + 승인 게이트 + 서브팀 스폰.

실제 LLM/서버/위임 실행 없이 다음을 검증한다:
1. builder_agent 상수 값 (와이어 안정 문자열)
2. 제작 대상 분류 (스킬/플러그인/MCP 키워드 휴리스틱 + classifier 주입)
3. Builder 미션 구성 (draft 전용 제약 + 수용 기준 4개)
4. 승인 게이트 (리스크 5: approver 미등록 시 기본 거부, 예외 fail-safe)
5. dispatch_builder_requests 큐 소비 (spawned/denied/error 경로, 절대 raise 안 함)
6. 오케스트레이터 배선 (builder_queue -> _dispatch_builder_queue -> merged_plan)
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


from api.dynamic.builder_agent import (  # noqa: E402
    BUILD_TARGET_SKILL, BUILD_TARGET_PLUGIN, BUILD_TARGET_MCP,
    DISPATCH_SPAWNED, DISPATCH_DENIED, DISPATCH_ERROR,
    classify_build_target, build_builder_task, default_builder_gate,
    default_builder_spawner, dispatch_builder_requests,
)

# -- 1. 상수 값 --
check(BUILD_TARGET_SKILL == "skill", "BUILD_TARGET_SKILL 와이어 값")
check(BUILD_TARGET_PLUGIN == "plugin", "BUILD_TARGET_PLUGIN 와이어 값")
check(BUILD_TARGET_MCP == "mcp", "BUILD_TARGET_MCP 와이어 값")
check(DISPATCH_SPAWNED == "spawned", "DISPATCH_SPAWNED 와이어 값")
check(DISPATCH_DENIED == "denied", "DISPATCH_DENIED 와이어 값")
check(DISPATCH_ERROR == "error", "DISPATCH_ERROR 와이어 값")
print("1. constants OK")

# -- 2. 제작 대상 분류 --
check(classify_build_target("mcp server for weather") == BUILD_TARGET_MCP,
      "키워드 분류: mcp")
check(classify_build_target("Model Context Protocol bridge") == BUILD_TARGET_MCP,
      "키워드 분류: model context protocol (대소문자 무시)")
check(classify_build_target("plugin for git workflow") == BUILD_TARGET_PLUGIN,
      "키워드 분류: plugin")
check(classify_build_target("pre-commit hook integration") == BUILD_TARGET_PLUGIN,
      "키워드 분류: hook")
check(classify_build_target("tool registration helper") == BUILD_TARGET_PLUGIN,
      "키워드 분류: tool registration")
check(classify_build_target("pdf generation") == BUILD_TARGET_SKILL,
      "기본 분류: 스킬 (가장 가벼운 산출물)")
check(classify_build_target("") == BUILD_TARGET_SKILL, "빈 능력 -> 스킬 기본")
check(classify_build_target("anything", classifier=lambda cap: "plugin")
      == BUILD_TARGET_PLUGIN, "classifier 주입: 유효값 수용")
check(classify_build_target("anything", classifier=lambda cap: "banana")
      == BUILD_TARGET_SKILL, "classifier 무효값 -> 스킬 폴백")


def _boom_classifier(cap):
    raise RuntimeError("classifier down")


check(classify_build_target("anything", classifier=_boom_classifier)
      == BUILD_TARGET_SKILL, "classifier 예외 -> 스킬 폴백 (fail-safe)")
print("2. build target classification OK")

# -- 3. Builder 미션 구성 --
task_text, criteria = build_builder_task({"capability": "pdf generation"})
check("pdf generation" in task_text, "미션에 결핍 능력 포함")
check(BUILD_TARGET_SKILL in task_text, "미션에 제작 대상 포함")
check("draft" in task_text, "미션에 draft 전용 제약 명시")
check("promote" in task_text, "미션에 자체 promote 금지 명시")
check("프로브" in task_text, "미션에 프로브 작성 요구 명시")
check(len(criteria) == 4, "수용 기준 4개")
check("pdf generation" in criteria[0] and BUILD_TARGET_SKILL in criteria[0],
      "수용 기준 1번: 초안 산출물 존재")
task2, crit2 = build_builder_task("mcp weather server")
check(BUILD_TARGET_MCP in task2, "str 요청 + 자동 분류")
task3, _ = build_builder_task({"capability": "x"}, build_target=BUILD_TARGET_PLUGIN)
check(BUILD_TARGET_PLUGIN in task3, "build_target 재정의 우선")
print("3. builder mission composition OK")

# -- 4. 승인 게이트 (리스크 5) --
allowed, reason = default_builder_gate({"capability": "x"})
check(allowed is False, "approver 미등록 -> 기본 거부")
check("no approver" in reason, "기본 거부 사유 문구")
allowed, reason = default_builder_gate({"capability": "x"}, approver=lambda r: True)
check(allowed is True and reason == "approved", "approver 허용 -> 스폰 승인")
allowed, reason = default_builder_gate({"capability": "x"}, approver=lambda r: False)
check(allowed is False and reason == "denied by approver", "approver 거부 -> 거부")


def _approver_boom(r):
    raise RuntimeError("approver crashed")


allowed, reason = default_builder_gate({"capability": "x"}, approver=_approver_boom)
check(allowed is False and "approver error" in reason, "approver 예외 -> 거부 (fail-safe)")
allowed, _ = default_builder_gate({"capability": "x"}, approver=lambda r: {"ok": 1})
check(allowed is True, "truthy 판정 수용")
print("4. approval gate OK")

# -- 5. dispatch_builder_requests 큐 소비 --
queue = [{"capability": "pdf generation", "source": "capability_resolver",
          "status": "pending"}]

check(dispatch_builder_requests([]) == [], "빈 큐 -> 빈 레코드")
check(dispatch_builder_requests(None) == [], "None 큐 -> 빈 레코드")

spawn_calls = []


def fake_spawner(task, spawn_reason, criteria, preferred_model=None):
    spawn_calls.append({"task": task, "spawn_reason": spawn_reason,
                        "criteria": criteria, "preferred_model": preferred_model})
    return {"ok": True, "child_run_id": "dlg_abc", "final_output": "draft done",
            "error": None}


def fake_gate_allow(request, approver=None):
    return True, "approved"


def fake_gate_deny(request, approver=None):
    return False, "not allowed"


records = dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_allow)
check(len(records) == 1, "스폰 경로: 레코드 1건")
rec = records[0]
check(rec["capability"] == "pdf generation", "레코드: capability 보존")
check(rec["build_target"] == BUILD_TARGET_SKILL, "레코드: 제작 대상 기록")
check(rec["status"] == DISPATCH_SPAWNED, "레코드: spawned 상태")
check(rec["child_run_id"] == "dlg_abc", "레코드: 자식 실행 ID 인도")
check(rec["final_output"] == "draft done", "레코드: 제작 산출물 인도")
check(rec["spawn_reason"].startswith("Builder(E-L2)"), "레코드: 생성 사유 프리픽스")
check("pdf generation" in rec["spawn_reason"], "레코드: 생성 사유에 능력 포함")
check(len(spawn_calls) == 1, "스포너 정확히 1회 호출")
check("draft" in spawn_calls[0]["task"], "스포너에 전달된 미션에 draft 제약")
check(len(spawn_calls[0]["criteria"]) == 4, "스포너에 수용 기준 4개 전달")

spawn_calls.clear()
records = dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_deny)
check(records[0]["status"] == DISPATCH_DENIED, "게이트 거부 -> denied 상태")
check(records[0]["reason"] == "not allowed", "게이트 거부 사유 기록")
check(len(spawn_calls) == 0, "게이트 거부 시 스포너 미호출")


def spawner_fail(task, spawn_reason, criteria, preferred_model=None):
    return {"ok": False, "child_run_id": None, "final_output": "",
            "error": "budget exhausted"}


records = dispatch_builder_requests(queue, spawner=spawner_fail, gate=fake_gate_allow)
check(records[0]["status"] == DISPATCH_ERROR, "스폰 실패 -> error 상태")
check("budget exhausted" in records[0]["reason"], "스폰 실패 사유 기록")


def spawner_boom(task, spawn_reason, criteria, preferred_model=None):
    raise RuntimeError("spawn crashed")


records = dispatch_builder_requests(queue, spawner=spawner_boom, gate=fake_gate_allow)
check(records[0]["status"] == DISPATCH_ERROR, "스포너 예외 -> error (raise 안 함)")
check("spawn error" in records[0]["reason"], "스포너 예외 사유 기록")


def spawner_nondict(task, spawn_reason, criteria, preferred_model=None):
    return "weird"


records = dispatch_builder_requests(queue, spawner=spawner_nondict, gate=fake_gate_allow)
check(records[0]["status"] == DISPATCH_ERROR, "비-dict 스포너 반환 -> error")
check("non-dict" in records[0]["reason"], "비-dict 반환 사유 기록")


def gate_boom(request, approver=None):
    raise RuntimeError("gate crashed")


records = dispatch_builder_requests(queue, spawner=fake_spawner, gate=gate_boom)
check(records[0]["status"] == DISPATCH_DENIED, "게이트 예외 -> 거부 처리 (raise 안 함)")
check("gate error" in records[0]["reason"], "게이트 예외 사유 기록")

queue2 = [{"capability": "   "}, "", {"capability": "mcp bridge"},
          {"capability": "hook plugin"}]
records = dispatch_builder_requests(queue2, spawner=fake_spawner, gate=fake_gate_allow)
check(len(records) == 2, "빈 capability 스킵")
check([r["capability"] for r in records] == ["mcp bridge", "hook plugin"],
      "입력 순서 보존")
check(records[0]["build_target"] == BUILD_TARGET_MCP, "큐 항목별 분류: mcp")
check(records[1]["build_target"] == BUILD_TARGET_PLUGIN, "큐 항목별 분류: plugin")


def gate_one(request):
    return True, "ok"


def spawner_three(task, spawn_reason, criteria):
    return {"ok": True, "child_run_id": "dlg_3arg", "final_output": "x", "error": None}


records = dispatch_builder_requests(queue, spawner=spawner_three, gate=gate_one)
check(records[0]["status"] == DISPATCH_SPAWNED, "1인자 게이트 + 3인자 스포너 호환")
check(records[0]["child_run_id"] == "dlg_3arg", "3인자 스포너 결과 인도")

records = dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_allow,
                                    classifier=lambda cap: BUILD_TARGET_PLUGIN)
check(records[0]["build_target"] == BUILD_TARGET_PLUGIN, "dispatch classifier 주입")

spawn_calls.clear()
dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_allow,
                          preferred_model="model-x")
check(spawn_calls and spawn_calls[0]["preferred_model"] == "model-x",
      "preferred_model 스포너 전달")

# 기본 게이트(리스크 5): approver 미등록 큐 소비는 전부 거부
records = dispatch_builder_requests(queue, spawner=fake_spawner)
check(records[0]["status"] == DISPATCH_DENIED, "기본 게이트: approver 없으면 거부")
check("no approver" in records[0]["reason"], "기본 게이트 거부 사유")

logs2 = []


def log_cb2(agent, content, status="running"):
    logs2.append((agent, content, status))


dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_deny,
                          log_callback=log_cb2)
check(any(a == "Builder" and "게이트 거부" in c for a, c, s in logs2),
      "거부 로그 방출")
dispatch_builder_requests(queue, spawner=fake_spawner, gate=fake_gate_allow,
                          log_callback=log_cb2)
check(any(a == "Builder" and "spawned" in c for a, c, s in logs2),
      "스폰 로그 방출")

# 기본 스포너: delegate_team 미가용 환경에서도 우아한 실패 (서버/위임 불필요)
out = default_builder_spawner("task", "reason", ["c1"])
check(out["ok"] is False, "기본 스포너: 가용 환경 아니면 ok=False")
check("delegate_team unavailable" in str(out.get("error") or ""),
      "기본 스포너: 임포트 실패 사유 기록")
print("5. dispatch_builder_requests OK")

# -- 6. 오케스트레이터 배선 --
from api.dynamic.capability_resolver import CapabilityResolver  # noqa: E402
import api.dynamic.orchestrator as orch  # noqa: E402

runner = orch.HermesDynamicRunner()
check(runner.builder_approver is None, "builder_approver 지연 구성 (기본 None)")
check(runner.builder_spawner is None, "builder_spawner 지연 구성 (기본 None)")
check(runner._dispatch_builder_queue([]) == [], "빈 큐 디스패치 -> 빈 레코드")


class FakePlanner:
    def plan(self, task, mission_tracker=None, preferred_model=None, **k):
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

prev_results = [{"name": "a1", "role": "dev", "status": "success", "output": "o1",
                 "output_key": "k1", "generation": 1, "parents": []}]

# 모든 cap을 Builder로 보내는 리졸버 (스킬/에이전트 없음)
runner.capability_resolver = CapabilityResolver(
    skill_searcher=lambda cap: None, agent_assigner=lambda cap: None)

spawned_reasons = []


def orch_spawner(task, spawn_reason, criteria, preferred_model=None):
    spawned_reasons.append(spawn_reason)
    return {"ok": True, "child_run_id": "dlg_el2", "final_output": "draft: skills/pdf",
            "error": None}


runner.builder_approver = lambda req: True
runner.builder_spawner = orch_spawner

logs = []


def log_cb(agent, content, status="running"):
    logs.append((agent, content, status))


new_final, merged_results, merged_plan, merged_agents = runner._run_acceptance_replan(
    ["기준1"], ["pdf generation"], "이전 산출물", "테스트 작업",
    None, {}, {"nodes": []}, prev_results, [], lambda: None, log_callback=log_cb)

check(merged_plan.get("builder_queue")
      and merged_plan["builder_queue"][0]["capability"] == "pdf generation",
      "merged_plan에 builder_queue 노출 유지")
disp = merged_plan.get("builder_dispatches")
check(disp and len(disp) == 1, "merged_plan에 builder_dispatches 노출")
check(disp[0]["status"] == DISPATCH_SPAWNED, "배선: 승인 통과 -> 스폰")
check(disp[0]["child_run_id"] == "dlg_el2", "배선: 자식 실행 ID 인도")
check(disp[0]["final_output"] == "draft: skills/pdf", "배선: 제작 산출물 인도")
check(disp[0]["capability"] == "pdf generation", "배선: capability 보존")
check(disp[0]["build_target"] == BUILD_TARGET_SKILL, "배선: 제작 대상 기록")
check(len(spawned_reasons) == 1 and "Builder(E-L2)" in spawned_reasons[0],
      "배선: 생성 사유 부착")
check(any(a == "Builder" and "디스패치" in c for a, c, s in logs),
      "배선: 디스패치 요약 로그 방출")
check(new_final == "MERGED(2)", "배선: 재병합 경로 유지")

# 리스크 5 안전 기본값: approver 미등록 -> 게이트 거부, 스포너 미호출
runner.builder_approver = None


def must_not_be_called(*a, **k):
    raise AssertionError("approver 미등록 시 스포너가 호출되면 안 된다")


runner.builder_spawner = must_not_be_called
new_final, _, merged_plan2, _ = runner._run_acceptance_replan(
    ["기준1"], ["pdf generation"], "이전", "작업", None, {}, {"nodes": []},
    prev_results, [], lambda: None)
disp2 = merged_plan2.get("builder_dispatches")
check(disp2 and disp2[0]["status"] == DISPATCH_DENIED,
      "배선: approver 없으면 게이트 거부 기록")
check("no approver" in disp2[0]["reason"], "배선: 기본 거부 사유 기록")

# 스킬로 해결되는 cap -> builder_queue 없음 -> builder_dispatches 키 없음
runner.capability_resolver = CapabilityResolver(
    skill_searcher=lambda cap: {"skill": "s1"}, agent_assigner=lambda cap: None)
runner.builder_approver = lambda req: True
_, _, merged_plan3, _ = runner._run_acceptance_replan(
    ["기준1"], ["capSkill"], "이전", "작업", None, {}, {"nodes": []},
    prev_results, [], lambda: None)
check("builder_queue" not in merged_plan3, "스킬 해결: builder_queue 없음")
check("builder_dispatches" not in merged_plan3, "스킬 해결: builder_dispatches 없음")
print("6. orchestrator wiring OK")

# -- 7. 정적 배선 확인 --
orch_src = (ROOT / "api" / "api" / "dynamic" / "orchestrator.py").read_text(encoding="utf-8")
check("from api.dynamic.builder_agent import" in orch_src, "오케스트레이터: builder_agent 임포트")
check("dispatch_builder_requests" in orch_src, "오케스트레이터: 디스패치 호출")
check("builder_dispatches" in orch_src, "오케스트레이터: builder_dispatches 키")
check("self.builder_approver" in orch_src, "오케스트레이터: builder_approver 속성")
check("_dispatch_builder_queue" in orch_src, "오케스트레이터: _dispatch_builder_queue 메서드")
ba_src = (ROOT / "api" / "api" / "dynamic" / "builder_agent.py").read_text(encoding="utf-8")
check("from tools.delegate_team_tool import delegate_team" in ba_src,
      "builder_agent: delegate_team 경유 스폰")
print("7. static wiring OK")

print("ALL GAP-E-L2 PROBES PASSED (%d checks)" % CHECKS)

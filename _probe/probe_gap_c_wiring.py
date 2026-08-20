"""갭 C 배선 프로브: Builder 제작 -> 거버넌스 편입 프로덕션 배선 (시나리오 C).

실제 LLM/서버/위임 실행 없이 다음을 검증한다:
1. 핸드오프 마커 파싱 (builder_pipeline.parse_builder_handoff)
2. dispatch 레코드 -> E-L4 아티팩트 구성 (build_artifact_from_dispatch)
3. 편입 거버넌스 호출 (incorporate_builder_dispatches: 검증/승인/편입 순서 강제)
4. 세션 기반 승인자 (builder_approval.make_session_approver:
   승인 대기 등록/승인/거부/자동 타임아웃/SSE/잡 상태 연동)
5. 오케스트레이터 E-L4 배선 (_run_acceptance_replan -> builder_incorporations)
6. Builder 미션 핸드오프 마커 (회귀: 수용 기준 4개 유지)
7. 정적 배선 확인 (소스 내 임포트/속성/키 존재)
"""
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


from api.dynamic.builder_agent import (  # noqa: E402
    BUILD_TARGET_SKILL, BUILD_TARGET_PLUGIN,
    DISPATCH_SPAWNED, DISPATCH_DENIED,
    build_builder_task,
)
from api.dynamic.builder_pipeline import (  # noqa: E402
    HANDOFF_MARKER, parse_builder_handoff, build_artifact_from_dispatch,
    incorporate_builder_dispatches,
)
from api.dynamic.builder_approval import (  # noqa: E402
    APPROVAL_KIND_BUILDER_SPAWN, APPROVAL_KIND_INCORPORATION,
    make_session_approver, _resolve_auto_timeout,
)

# -- 1. 핸드오프 마커 파싱 --
marker_line = (
    HANDOFF_MARKER +
    ' {"name": "auto_pdf_skill", "probe_paths": ["probes/p1.py"], "status": "draft"}'
)
handoff = parse_builder_handoff("제작 완료\n사용법: ...\n" + marker_line)
check(handoff.get("name") == "auto_pdf_skill", "마커 파싱: name 추출")
check(handoff.get("probe_paths") == ["probes/p1.py"], "마커 파싱: probe_paths 추출")
check(handoff.get("status") == "draft", "마커 파싱: status 추출")

check(parse_builder_handoff("draft: skills/pdf") == {}, "마커 없음 -> 빈 dict")
check(parse_builder_handoff("") == {}, "빈 출력 -> 빈 dict")
check(parse_builder_handoff(None) == {}, "None 출력 -> 빈 dict")
check(parse_builder_handoff(HANDOFF_MARKER + " {broken json") == {},
      "깨진 JSON -> 빈 dict (raise 금지)")
check(parse_builder_handoff(HANDOFF_MARKER + ' ["a", "b"]') == {},
      "JSON 배열(객체 아님) -> 빈 dict")
nested = parse_builder_handoff(
    HANDOFF_MARKER + ' {"name": "s", "meta": {"a": 1}, "probe_paths": []} trailing')
check(nested.get("name") == "s" and nested.get("meta") == {"a": 1},
      "중첩 JSON 객체 파싱")
print("1. handoff parsing OK")

# -- 2. dispatch -> 아티팩트 구성 --
ws = tempfile.mkdtemp(prefix="probe_c_ws_")
dispatch_ok = {
    "capability": "pdf generation",
    "build_target": BUILD_TARGET_SKILL,
    "status": DISPATCH_SPAWNED,
    "reason": "spawned",
    "child_run_id": "child-1",
    "final_output": "초안 완료\n" + marker_line,
    "spawn_reason": "Builder(E-L2): pdf generation",
}
artifact = build_artifact_from_dispatch(dispatch_ok, workspace=ws)
check(artifact is not None, "아티팩트 구성: 스폰된 스킬은 편입 대상")
check(artifact["name"] == "auto_pdf_skill", "아티팩트: 마커 name 사용")
check(artifact["status"] == "draft", "아티팩트: draft 상태")
check(artifact["probe_paths"] == [str(Path(ws) / "probes/p1.py")],
      "아티팩트: 상대 probe_paths를 워크스페이스 기준으로 해석")
check(artifact["capability"] == "pdf generation", "아티팩트: capability 보존")
check(artifact["child_run_id"] == "child-1", "아티팩트: 자식 실행 ID 보존")

dispatch_nomarker = dict(dispatch_ok, final_output="draft: skills/pdf")
artifact2 = build_artifact_from_dispatch(dispatch_nomarker, workspace=ws)
check(artifact2 is not None and artifact2["name"] == "pdf_generation",
      "아티팩트: 마커 없으면 능력 이름에서 파생")
check(artifact2["probe_paths"] == [], "아티팩트: 마커 없으면 probe_paths 비어 있음")

check(build_artifact_from_dispatch(
    dict(dispatch_ok, status=DISPATCH_DENIED)) is None,
    "아티팩트: 거부된 디스패치는 편입 대상 아님")
check(build_artifact_from_dispatch(
    dict(dispatch_ok, build_target=BUILD_TARGET_PLUGIN)) is None,
    "아티팩트: 스킬 이외 제작 대상은 편입 대상 아님")
check(build_artifact_from_dispatch(
    {"capability": "", "build_target": BUILD_TARGET_SKILL,
     "status": DISPATCH_SPAWNED, "final_output": "no marker"}) is None,
    "아티팩트: 마커도 능력 이름도 없으면 None")
check(build_artifact_from_dispatch("garbage") is None, "아티팩트: 비-dict 입력 -> None")
print("2. artifact construction OK")

# -- 3. 편입 거버넌스 호출 (순서 강제) --
def make_probe_runner(ok=True, calls=None):
    def runner(probe_path):
        if calls is not None:
            calls.append(probe_path)
        return (ok, "probe output")
    return runner


probe_calls = []
approver_calls = []
promoter_calls = []
logs = []


def log_cb(agent, content, status="running"):
    logs.append((agent, content, status))


results = incorporate_builder_dispatches(
    [dispatch_ok], workspace=ws,
    approver=lambda a: approver_calls.append(a) or True,
    probe_runner=make_probe_runner(True, probe_calls),
    promoter=lambda a: promoter_calls.append(a) or True,
    log_callback=log_cb)
check(len(results) == 1, "편입: 고려된 dispatch당 결과 1건")
check(results[0]["ok"] is True and results[0]["status"] == "incorporated",
      "편입: 전 게이트 통과 -> incorporated")
check(results[0]["name"] == "auto_pdf_skill", "편입: 결과에 이름 노출")
check(results[0]["capability"] == "pdf generation", "편입: 결과에 capability 부착")
check(results[0]["child_run_id"] == "child-1", "편입: 결과에 자식 실행 ID 부착")
check(len(probe_calls) == 1 and len(approver_calls) == 1 and len(promoter_calls) == 1,
      "편입: 검증 -> 승인 -> 편입 순서로 각 단계 1회 호출")
check([s["stage"] for s in results[0]["stages"]] ==
      ["entry", "verify", "approve", "incorporate"],
      "편입: 단계 기록 순서 강제")
check(any(a == "Governance" for a, c, s in logs), "편입: Governance 로그 방출")

approver_calls2 = []
results = incorporate_builder_dispatches(
    [dispatch_ok], workspace=ws,
    approver=lambda a: approver_calls2.append(a) or True,
    probe_runner=make_probe_runner(False),
    promoter=lambda a: True)
check(results[0]["ok"] is False and results[0]["status"] == "rejected",
      "편입: 프로브 실패 -> rejected")
check("verification failed" in results[0]["reason"], "편입: 검증 실패 사유 기록")
check(approver_calls2 == [], "편입: 검증 실패 시 승인 단계 미호출 (순서 강제)")

promoter_calls2 = []
results = incorporate_builder_dispatches(
    [dispatch_ok], workspace=ws, approver=lambda a: False,
    probe_runner=make_probe_runner(True),
    promoter=lambda a: promoter_calls2.append(a) or True)
check(results[0]["status"] == "rejected"
      and "approval denied" in results[0]["reason"],
      "편입: 승인 거부 -> rejected")
check(promoter_calls2 == [], "편입: 승인 거부 시 편입 단계 미호출 (순서 강제)")

results = incorporate_builder_dispatches(
    [dispatch_ok], workspace=ws, approver=None,
    probe_runner=make_probe_runner(True), promoter=lambda a: True)
check(results[0]["status"] == "rejected"
      and "no approver registered" in results[0]["reason"],
      "편입: 승인자 미등록 -> 기본 거부 (리스크 5 안전 기본값)")

results = incorporate_builder_dispatches(
    [dispatch_nomarker], workspace=ws, approver=lambda a: True,
    probe_runner=make_probe_runner(True), promoter=lambda a: True)
check(results[0]["status"] == "rejected"
      and "no probe_paths" in results[0]["reason"],
      "편입: probe_paths 없음 -> 검증 단계 거부")
check(results[0]["name"] == "pdf_generation", "편입: 파생 이름으로 기록")

results = incorporate_builder_dispatches(
    [dispatch_ok], workspace=ws, approver=lambda a: True,
    probe_runner=lambda p: (_ for _ in ()).throw(RuntimeError("boom")),
    promoter=lambda a: True)
check(results[0]["status"] == "rejected" and "probe runner raised" in results[0]["reason"],
      "편입: 프로브 러너 예외 -> 거부 (fail-safe, raise 금지)")

skipped = incorporate_builder_dispatches(
    [dict(dispatch_ok, status=DISPATCH_DENIED),
     dict(dispatch_ok, build_target=BUILD_TARGET_PLUGIN),
     "garbage", {}],
    approver=lambda a: True, probe_runner=make_probe_runner(True),
    promoter=lambda a: True)
check(skipped == [], "편입: 대상 아닌 dispatch는 결과 없음")
check(incorporate_builder_dispatches([]) == [], "편입: 빈 큐 -> 빈 결과")
check(incorporate_builder_dispatches(None) == [], "편입: None 큐 -> 빈 결과")
print("3. incorporation governance OK")

# -- 4. 세션 기반 승인자 --
check(_resolve_auto_timeout(0) is None, "타임아웃 해석: 0 이하 -> 무한 대기")
check(_resolve_auto_timeout(5) == 5, "타임아웃 해석: 명시 인자 우선")
check(_resolve_auto_timeout("bad") == 45, "타임아웃 해석: 잘못된 값 -> 기본 45초")

no_session = make_session_approver(None)
check(no_session({"capability": "x"}) is False, "승인자: session_id 없음 -> 기본 거부")

from api.approval import (  # noqa: E402
    has_pending, get_pending, get_history,
    approve as do_approve, reject as do_reject,
)

# 4a. 사용자 승인 경로
sid = "probe-c-wiring-approve"
approver = make_session_approver(
    sid, kind=APPROVAL_KIND_BUILDER_SPAWN, auto_timeout=60, poll_interval=0.02)
result = {}


def run_approver():
    result["ok"] = approver({"capability": "pdf generation"})


t = threading.Thread(target=run_approver)
t.start()
deadline = time.time() + 5
while not has_pending(sid) and time.time() < deadline:
    time.sleep(0.01)
check(has_pending(sid), "승인자: set_pending로 승인 대기 등록")
pending = get_pending(sid)
check(pending.get("is_plan") is True, "승인 페이로드: is_plan=True (plan 카드 렌더)")
check(pending.get("preview_id") == "", "승인 페이로드: preview_id 빈 값 (apply-preview 스킵)")
check(pending.get("is_builder_approval") is True, "승인 페이로드: is_builder_approval 표시")
check(pending.get("approval_kind") == APPROVAL_KIND_BUILDER_SPAWN,
      "승인 페이로드: 승인 종류 기록")
check(bool(pending.get("approval_id")), "승인 페이로드: approval_id 상관 관계")
check(pending.get("status") == "pending", "승인 페이로드: pending 상태")
check("pdf generation" in str(pending.get("message")), "승인 페이로드: 메시지에 능력 포함")
do_approve(sid, reviewer="user")
t.join(timeout=5)
check(result.get("ok") is True, "승인자: 사용자 승인 -> True")
hist = get_history(sid, limit=10)
check(any(e.get("status") == "approved" and e.get("approval_id") == pending.get("approval_id")
          for e in hist), "승인자: 이력에서 approval_id 매칭")

# 4b. 사용자 거부 경로
sid2 = "probe-c-wiring-reject"
approver_r = make_session_approver(
    sid2, kind=APPROVAL_KIND_INCORPORATION, auto_timeout=60, poll_interval=0.02)
result_r = {}


def run_reject():
    result_r["ok"] = approver_r({"name": "auto_demo"})


t = threading.Thread(target=run_reject)
t.start()
deadline = time.time() + 5
while not has_pending(sid2) and time.time() < deadline:
    time.sleep(0.01)
check(has_pending(sid2), "승인자(거부 경로): 대기 등록")
pending2 = get_pending(sid2)
check(pending2.get("approval_kind") == APPROVAL_KIND_INCORPORATION,
      "승인자(거부 경로): 편입 종류 기록")
check(pending2.get("source_agent") == "Governance", "승인자(편입): source_agent=Governance")
check("auto_demo" in str(pending2.get("message")), "승인자(편입): 메시지에 초안 이름 포함")
do_reject(sid2, reason="not ready", reviewer="user")
t.join(timeout=5)
check(result_r.get("ok") is False, "승인자: 사용자 거부 -> False")

# 4c. SSE 알림 + 잡 상태 연동
from api.config import STREAMS  # noqa: E402
from api.dynamic_jobs import init_job, get_job  # noqa: E402

sid3 = "probe-c-wiring-sse"
rid3 = "probe-run-c-wiring"
init_job(rid3, session_id=sid3)
STREAMS[sid3] = queue.Queue()
approver_s = make_session_approver(
    sid3, kind=APPROVAL_KIND_BUILDER_SPAWN, run_id=rid3,
    auto_timeout=60, poll_interval=0.02)
result_s = {}


def run_sse():
    result_s["ok"] = approver_s({"capability": "sse cap"})


t = threading.Thread(target=run_sse)
t.start()
deadline = time.time() + 5
while not has_pending(sid3) and time.time() < deadline:
    time.sleep(0.01)
check(has_pending(sid3), "승인자(SSE): 대기 등록")
check(get_job(rid3)["status"] == "awaiting_approval",
      "승인자(SSE): 대기 중 잡 상태 awaiting_approval")
do_approve(sid3, reviewer="user")
t.join(timeout=5)
check(result_s.get("ok") is True, "승인자(SSE): 승인 -> True")
check(get_job(rid3)["status"] == "running", "승인자(SSE): 해결 후 잡 상태 running 복원")
events = []
while not STREAMS[sid3].empty():
    events.append(STREAMS[sid3].get_nowait())
del STREAMS[sid3]
check(any(kind == "approval" and data.get("status") == "pending"
          and data.get("approval_kind") == APPROVAL_KIND_BUILDER_SPAWN
          for kind, data in events),
      "승인자(SSE): approval 이벤트 방출")

# 4d. 자동 타임아웃 (UI 없는 투입 안전장치)
sid4 = "probe-c-wiring-auto"
approver_a = make_session_approver(
    sid4, kind=APPROVAL_KIND_INCORPORATION, auto_timeout=1, poll_interval=0.02)
started = time.time()
auto_ok = approver_a({"name": "auto_timeout_demo"})
elapsed = time.time() - started
check(auto_ok is True, "승인자(자동): 응답 없음 -> 타임아웃 자동 승인")
check(elapsed >= 0.9, "승인자(자동): 타임아웃까지 대기")
check(any(e.get("status") == "approved" for e in get_history(sid4, limit=10)),
      "승인자(자동): 이력에 자동 승인 기록")
print("4. session approver OK")

# -- 5. 오케스트레이터 E-L4 배선 (재계획 -> 편입) --
from api.dynamic.orchestrator import HermesDynamicRunner  # noqa: E402
from api.dynamic.capability_resolver import CapabilityResolver  # noqa: E402
import api.dynamic.incorporation as inc_mod  # noqa: E402

runner = HermesDynamicRunner()
check(runner.builder_incorporation_approver is None,
      "배선: 편입 승인자 기본 None (안전 기본값)")


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
runner.capability_resolver = CapabilityResolver(
    skill_searcher=lambda cap: None, agent_assigner=lambda cap: None)
runner.builder_approver = lambda req: True

prev_results = [{"name": "a1", "role": "dev", "status": "success", "output": "o1",
                 "output_key": "k1", "generation": 1, "parents": []}]

run_dir = Path(tempfile.mkdtemp(prefix="probe_c_run_"))
(run_dir / "probe_ok.py").write_text("print('probe ok')\n", encoding="utf-8")

handoff_line = (
    HANDOFF_MARKER +
    ' {"name": "auto_probe_demo", "probe_paths": ["probe_ok.py"], "status": "draft"}'
)


def spawner_with_marker(task, spawn_reason, criteria, preferred_model=None):
    return {"ok": True, "child_run_id": "child-c1",
            "final_output": "초안 완료\n" + handoff_line, "error": None}


runner.builder_spawner = spawner_with_marker
runner.builder_incorporation_approver = lambda artifact: True

# 기본 편입 촉진자(실제 레지스트리) 대신 결정적 페이크로 치환해 배선만 검증한다.
orig_promoter = inc_mod.default_skill_promoter
inc_mod.default_skill_promoter = lambda artifact: True
try:
    logs_w = []
    new_final, _, merged_plan, _ = runner._run_acceptance_replan(
        ["기준1"], ["demo capability"], "이전 산출물", "테스트 작업",
        None, {}, {"nodes": []}, prev_results, [], lambda: None,
        log_callback=lambda a, c, s="running": logs_w.append((a, c, s)),
        run_dir=run_dir)
finally:
    inc_mod.default_skill_promoter = orig_promoter

check(new_final == "MERGED(2)", "배선: 재병합 경로 유지")
check(merged_plan.get("builder_dispatches")
      and merged_plan["builder_dispatches"][0]["status"] == DISPATCH_SPAWNED,
      "배선: builder_dispatches 노출 유지 (회귀)")
incs = merged_plan.get("builder_incorporations")
check(incs and len(incs) == 1, "배선: builder_incorporations 키 노출")
check(incs[0]["ok"] is True and incs[0]["status"] == "incorporated",
      "배선: 마커 + 프로브 통과 + 승인 -> 편입 성공")
check(incs[0]["name"] == "auto_probe_demo", "배선: 편입 결과 이름")
check(incs[0]["capability"] == "demo capability", "배선: 편입 결과 capability")
check(incs[0]["child_run_id"] == "child-c1", "배선: 편입 결과 자식 실행 ID")
check([s["stage"] for s in incs[0]["stages"]] ==
      ["entry", "verify", "approve", "incorporate"],
      "배선: 거버넌스 전 단계 통과 기록")
check(any(a == "Governance" for a, c, s in logs_w), "배선: Governance 로그 방출")

# 마커 없는 산출물 -> probe_paths 비어 검증 단계 거부 (안전 기본값)
def spawner_no_marker(task, spawn_reason, criteria, preferred_model=None):
    return {"ok": True, "child_run_id": "child-c2",
            "final_output": "draft: skills/pdf", "error": None}


runner.builder_spawner = spawner_no_marker
_, _, merged_plan2, _ = runner._run_acceptance_replan(
    ["기준1"], ["demo capability"], "이전", "작업", None, {}, {"nodes": []},
    prev_results, [], lambda: None, run_dir=run_dir)
incs2 = merged_plan2.get("builder_incorporations")
check(incs2 and len(incs2) == 1, "배선(마커 없음): 편입 시도 기록")
check(incs2[0]["status"] == "rejected" and "no probe_paths" in incs2[0]["reason"],
      "배선(마커 없음): 검증 단계 거부")

# 편입 승인자 미등록 -> 승인 단계 거부 (리스크 5 안전 기본값)
runner.builder_spawner = spawner_with_marker
runner.builder_incorporation_approver = None
_, _, merged_plan3, _ = runner._run_acceptance_replan(
    ["기준1"], ["demo capability"], "이전", "작업", None, {}, {"nodes": []},
    prev_results, [], lambda: None, run_dir=run_dir)
incs3 = merged_plan3.get("builder_incorporations")
check(incs3 and incs3[0]["status"] == "rejected"
      and "no approver registered" in incs3[0]["reason"],
      "배선(승인자 없음): 편입 기본 거부")
print("5. orchestrator E-L4 wiring OK")

# -- 6. Builder 미션 핸드오프 마커 (회귀) --
task_text, criteria = build_builder_task({"capability": "pdf generation"})
check(len(criteria) == 4, "회귀: 수용 기준 4개 유지")
check(HANDOFF_MARKER in task_text, "미션: 핸드오프 마커 형식 지시")
check("probe_paths" in task_text, "미션: 마커 형식에 probe_paths 포함")
check(HANDOFF_MARKER in criteria[3], "미션: 4번 기준에 마커 포함")
check("draft" in task_text, "미션: draft 전용 제약 유지")
print("6. builder mission marker OK")

# -- 7. 정적 배선 확인 --
dj_src = (ROOT / "api" / "api" / "dynamic_jobs.py").read_text(encoding="utf-8")
check("make_session_approver" in dj_src, "dynamic_jobs: 승인자 팩토리 임포트")
check("runner.builder_approver" in dj_src, "dynamic_jobs: E-L2 승인자 주입")
check("runner.builder_incorporation_approver" in dj_src, "dynamic_jobs: E-L4 승인자 주입")
check("APPROVAL_KIND_BUILDER_SPAWN" in dj_src and "APPROVAL_KIND_INCORPORATION" in dj_src,
      "dynamic_jobs: 승인 종류 상수 사용")
orch_src = (ROOT / "api" / "api" / "dynamic" / "orchestrator.py").read_text(encoding="utf-8")
check("incorporate_builder_dispatches" in orch_src, "오케스트레이터: 편입 호출")
check("builder_incorporations" in orch_src, "오케스트레이터: builder_incorporations 키")
check("self.builder_incorporation_approver" in orch_src,
      "오케스트레이터: 편입 승인자 속성")
ba_src = (ROOT / "api" / "api" / "dynamic" / "builder_agent.py").read_text(encoding="utf-8")
check(HANDOFF_MARKER in ba_src, "builder_agent: 미션에 핸드오프 마커 포함")
print("7. static wiring OK")

print("ALL GAP-C-WIRING PROBES PASSED (%d checks)" % CHECKS)

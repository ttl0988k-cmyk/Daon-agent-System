"""갭 D 기능 프로브: 재귀적 위임(delegate_team) 통치 구조 검증.

실제 LLM/하네스 실행 없이 다음을 검증한다:
1. limits.py의 delegation 예산 블록 (max_depth / max_children_per_spawn / max_total_spawns)
2. delegation.py 가드 시맨틱: 컨텍스트 필수 / 깊이 상한 / spawn_reason 필수
3. 스폰 예산 카운터: 원자적 소비 / 고갈 / 리셋 / 스레드 로컬 격리
4. dynamic_jobs.py 혈통 레지스트리 + 부모 취소의 서브트리 연쇄 취소
5. delegate_team 도구: 스키마 / 거부 경로 / 자식 실행 해피패스 / 실패 폴백
6. orchestrator / runner / compiler 소스 배선 확인
"""
import shutil
import sys
import tempfile
import threading
import types
from pathlib import Path

sys.path.insert(0, "api")
sys.path.insert(0, "hermes-agent")

# tools.registry를 페이크로 주입해 무거운 의존성 격리
fake_registry_mod = types.ModuleType("tools.registry")
_registered = {}


class _FakeRegistry:
    def register(self, **kw):
        _registered[kw.get("name")] = kw


fake_registry_mod.registry = _FakeRegistry()
fake_registry_mod.tool_error = lambda msg, **extra: {"error": msg, **extra}
fake_registry_mod.tool_result = lambda data=None, **kw: (data if data is not None else kw)
sys.modules["tools.registry"] = fake_registry_mod

import api.dynamic_jobs as dj  # noqa: E402
import api.dynamic.limits as limits_mod  # noqa: E402
import api.dynamic.clarifier as clar  # noqa: E402
from api.dynamic import delegation  # noqa: E402
import tools.delegate_team_tool as dtt  # noqa: E402

# LLM 호출 시도 차단 (수용 기준 부착은 precomputed 경로만 사용)
clar._call_direct = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no LLM in probe"))

# ── 1. limits delegation 예산 블록 ──
limits = limits_mod._load_harness_limits()
dlg_limits = limits.get("delegation")
assert isinstance(dlg_limits, dict), "limits에 delegation 블록 누락"
assert int(dlg_limits.get("max_depth", -1)) >= 1, "max_depth 누락"
assert int(dlg_limits.get("max_children_per_spawn", -1)) >= 1, "max_children_per_spawn 누락"
assert int(dlg_limits.get("max_total_spawns", -1)) >= 1, "max_total_spawns 누락"
print("1. limits delegation block OK")

# ── 2. 가드 시맨틱 (순수 함수, 예산 소비 없음) ──
# (a) 하네스 밖(ctx 없음) → 거부
ok, msg = delegation.check_delegation_guard(None, limits, "이유")
assert ok is False and "only available inside" in msg, msg
# (b) spawn_reason 빈 값 → 거부
ok, msg = delegation.check_delegation_guard({"depth": 0}, limits, "   ")
assert ok is False and "spawn_reason" in msg, msg
# (c) 깊이 상한 도달 → 거부 (max_depth=1 일 때 depth=1 노드는 더 위임 불가)
max_depth = int(dlg_limits["max_depth"])
ok, msg = delegation.check_delegation_guard({"depth": max_depth}, limits, "이유")
assert ok is False and "depth" in msg, msg
# (d) 정상: depth=0 + 사유 있음 → 허용
ok, msg = delegation.check_delegation_guard({"depth": 0}, limits, "서브팀이 필요한 이유")
assert ok is True and msg == "ok", (ok, msg)
print("2. check_delegation_guard semantics OK")

# ── 3. 스폰 예산 카운터 + 스레드 로컬 격리 ──
delegation.reset_spawn_budget("probe_root_b")
assert delegation.try_consume_spawn_budget("probe_root_b", 2) == (True, 1)
assert delegation.try_consume_spawn_budget("probe_root_b", 2) == (True, 2)
assert delegation.try_consume_spawn_budget("probe_root_b", 2) == (False, 2), "고갈 후에도 소비되면 안 된다"
assert delegation.count_spawns("probe_root_b") == 2
delegation.reset_spawn_budget("probe_root_b")
assert delegation.count_spawns("probe_root_b") == 0
assert delegation.try_consume_spawn_budget("probe_root_b", 0) == (False, 0), "예산 0은 항상 거부"
assert delegation.try_consume_spawn_budget("", 5) == (False, 0), "root_run_id 빈 값은 거부"

delegation.set_current_delegation({"run_id": "t1", "depth": 0})
assert delegation.get_current_delegation()["run_id"] == "t1"
other_thread_ctx = {}


def _read_ctx_in_thread():
    other_thread_ctx["ctx"] = delegation.get_current_delegation()


_th = threading.Thread(target=_read_ctx_in_thread)
_th.start()
_th.join()
assert other_thread_ctx["ctx"] is None, "위임 컨텍스트는 스레드 로컬이어야 한다"
delegation.clear_current_delegation()
assert delegation.get_current_delegation() is None
print("3. spawn budget + thread-local isolation OK")

# ── 4. 혈통 레지스트리 + 취소 연쇄 전파 ──
dj.register_lineage("probe_c1", "probe_root_l", "probe_root_l", 1, "1단계 위임")
dj.register_lineage("probe_c2", "probe_c1", "probe_root_l", 2, "2단계 위임")
lin = dj.get_lineage("probe_c1")
assert lin and lin["parent_run_id"] == "probe_root_l" and lin["depth"] == 1
assert lin["spawn_reason"] == "1단계 위임"
desc = dj.get_descendants("probe_root_l")
assert set(desc) == {"probe_c1", "probe_c2"}, desc

# 부모 취소 → 서브트리 전체가 _CANCELLED_JOBS에 표시되어야 한다
with dj._DYNAMIC_JOBS_LOCK:
    dj._DYNAMIC_JOBS["probe_root_l"] = {"status": "running", "logs": []}
assert dj.cancel_job("probe_root_l") is True
assert dj.is_job_cancelled("probe_root_l")
assert dj.is_job_cancelled("probe_c1"), "자식 실행으로 취소가 전파되어야 한다"
assert dj.is_job_cancelled("probe_c2"), "손자 실행으로 취소가 전파되어야 한다"

dj.unregister_lineage_subtree("probe_c1")
assert dj.get_lineage("probe_c1") is None
assert dj.get_lineage("probe_c2") is None, "서브트리 정리 시 손자도 함께 제거되어야 한다"
print("4. lineage registry + cancel cascade OK")

# ── 5. delegate_team 도구 ──
entry = _registered.get("delegate_team")
assert entry is not None, "delegate_team 미등록"
assert entry.get("toolset") == "delegation"
assert entry.get("emoji") == "🤝"
assert callable(entry.get("check_fn"))
assert entry["schema"]["parameters"]["required"] == ["task", "spawn_reason"]

# (a) 빈 task → 즉시 오류
res = dtt.delegate_team("", "이유")
assert res.get("error") == "task is required."

# (b) 하네스 밖(ctx 없음) → 구조화 거부 (fail-open)
delegation.clear_current_delegation()
res = dtt.delegate_team("서브태스크", "이유")
assert res.get("delegated") is False and "only available inside" in res.get("reason", ""), res
assert "Handle it yourself" in res.get("instruction", "")

# (c) spawn_reason 빈 값 → 가드 거부 + 예산 미소비
delegation.set_current_delegation(
    {"run_id": "probe_parent", "root_run_id": "probe_root_guard", "depth": 0})
res = dtt.delegate_team("서브태스크", "")
assert res.get("delegated") is False and "spawn_reason" in res.get("reason", ""), res
assert delegation.count_spawns("probe_root_guard") == 0, "거부 시 예산이 소비되면 안 된다"

# (d) 깊이 상한 도달 → 가드 거부
delegation.set_current_delegation(
    {"run_id": "probe_parent", "root_run_id": "probe_root_guard", "depth": max_depth})
res = dtt.delegate_team("서브태스크", "이유")
assert res.get("delegated") is False and "depth" in res.get("reason", ""), res
assert delegation.count_spawns("probe_root_guard") == 0
delegation.clear_current_delegation()

# (e) 총 생성 예산 고갈 → 거부
max_total = int(dlg_limits["max_total_spawns"])
delegation.reset_spawn_budget("probe_root_dtb")
for _ in range(max_total):
    assert delegation.try_consume_spawn_budget("probe_root_dtb", max_total)[0] is True
delegation.set_current_delegation(
    {"run_id": "probe_parent", "root_run_id": "probe_root_dtb", "depth": 0})
res = dtt.delegate_team("서브태스크", "이유")
assert res.get("delegated") is False and "budget" in res.get("reason", ""), res
delegation.clear_current_delegation()
delegation.reset_spawn_budget("probe_root_dtb")

# (f) 해피패스: 페이크 러너로 자식 실행 동기 수행 검증
tmp_root = tempfile.mkdtemp(prefix="probe_gap_d_")
captured_run = {}


class _FakeRunner:
    def run(self, **kw):
        captured_run.update(kw)
        return {"final_output": "child done", "saved_paths": {"out": "x.md"}}


fake_dh = types.ModuleType("api.dynamic_hermes")
fake_dh.HermesDynamicRunner = _FakeRunner
sys.modules["api.dynamic_hermes"] = fake_dh

delegation.reset_spawn_budget("probe_root_happy")
delegation.set_current_delegation({
    "run_id": "probe_parent",
    "root_run_id": "probe_root_happy",
    "parent_run_id": None,
    "depth": 0,
    "run_dir": tmp_root,
})
res = dtt.delegate_team("서브태스크 본문", "혼자 하기엔 규모가 큰 독립 분기 작업",
                        acceptance_criteria=["조건A가 충족되어야 한다"])
assert res.get("delegated") is True, res
child_id = res.get("child_run_id", "")
assert child_id.startswith("dlg_"), child_id
assert res.get("depth") == 1
assert res.get("final_output") == "child done"
# 러너 전달 인자 검증
assert captured_run.get("run_id") == child_id
child_ctx = captured_run.get("delegation_context") or {}
assert child_ctx.get("depth") == 1 and child_ctx.get("parent_run_id") == "probe_parent"
assert child_ctx.get("root_run_id") == "probe_root_happy"
run_task = captured_run.get("task", "")
assert "[위임 지시문]" in run_task, "위임 지시문이 자식 task에 주입되어야 한다"
assert "혼자 하기엔 규모가 큰 독립 분기 작업" in run_task
assert f"최대 {int(dlg_limits['max_children_per_spawn'])}개" in run_task, "서브팀 규모 상한 지시문 누락"
assert "조건A가 충족되어야 한다" in run_task, "자식 실행에 수용 기준이 부착되어야 한다 (갭 C 프랙탈)"
# 자식 run_dir 생성 + 혈통 정리 + 예산 소비 검증
child_dir = captured_run.get("run_dir") or ""
assert "delegated" in child_dir and Path(child_dir).is_dir(), child_dir
assert dj.get_lineage(child_id) is None, "종료 후 혈통 기록이 정리되어야 한다"
assert delegation.count_spawns("probe_root_happy") == 1

# (g) 자식 실행 예외 → tool_error + 직접 처리 안내 (fail-open)
class _BoomRunner:
    def run(self, **kw):
        raise RuntimeError("boom")


fake_dh.HermesDynamicRunner = _BoomRunner
res = dtt.delegate_team("서브태스크", "이유 있음")
assert "error" in res and "Handle the subtask yourself" in res["error"], res
delegation.clear_current_delegation()
delegation.reset_spawn_budget("probe_root_happy")
shutil.rmtree(tmp_root, ignore_errors=True)

# (h) registry 핸들러 배선 + check_fn
captured_args = {}


def _fake_dt(**kw):
    captured_args.update(kw)
    return {"delegated": True}


saved_dt = dtt.delegate_team
dtt.delegate_team = _fake_dt
try:
    entry["handler"]({"task": "T", "spawn_reason": "R", "skills": [],
                      "acceptance_criteria": ["C1"]})
finally:
    dtt.delegate_team = saved_dt
assert captured_args.get("task") == "T" and captured_args.get("spawn_reason") == "R"
assert captured_args.get("skills") is None, "빈 skills 목록은 None으로 정규화"
assert captured_args.get("acceptance_criteria") == ["C1"]
assert dtt.check_delegate_team_requirements() is True
print("5. delegate_team tool OK")

# ── 6. 소스 배선 확인 (orchestrator / runner / compiler) ──
root = Path(__file__).resolve().parent.parent
compiler_src = (root / "api" / "api" / "dynamic" / "compiler.py").read_text(encoding="utf-8")
assert compiler_src.count('enabled_toolsets.append("delegation")') >= 2, \
    "compiler의 템플릿/레거시 두 경로 모두 delegation toolset을 주입해야 한다"
runner_src = (root / "api" / "api" / "dynamic" / "runner.py").read_text(encoding="utf-8")
assert '(mission_tracker or {}).get("delegation")' in runner_src
assert "set_current_delegation(" in runner_src and "clear_current_delegation()" in runner_src
orch_src = (root / "api" / "api" / "dynamic" / "orchestrator.py").read_text(encoding="utf-8")
assert "delegation_context" in orch_src
assert 'mission_tracker["delegation"]' in orch_src
assert "reset_spawn_budget" in orch_src
print("6. orchestrator/runner/compiler wiring OK")

print("ALL GAP-D PROBES PASSED")

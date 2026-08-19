"""갭 E-L4 기능 프로브: 편입 거버넌스 — draft -> 프로브 검증 -> 승인 -> 편입 순서 강제.

검증 대상: api/api/dynamic/incorporation.py
핵심 주장: 불변 순서(검증 통과 전 승인 불가, 승인 전 편입 불가)가 강제되며,
어떤 경로에서도 run_incorporation은 raise하지 않고 dict를 반환한다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0


def check(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        print("PROBE FAIL: %s" % msg)
        sys.exit(1)


from api.dynamic.incorporation import (  # noqa: E402
    IMMUTABLE_ORDER,
    INCORPORATION_ORDER,
    STAGE_ENTRY,
    STAGE_VERIFY,
    STAGE_APPROVE,
    STAGE_INCORPORATE,
    STATUS_INCORPORATED,
    STATUS_REJECTED,
    STATUS_ERROR,
    LIFECYCLE_DRAFT,
    artifact_name,
    artifact_lifecycle,
    check_entry_gate,
    verify_artifact,
    approve_artifact,
    default_skill_promoter,
    run_incorporation,
)


# --- 페이크 -----------------------------------------------------------------

class CallRecorder:
    """기록하는 콜렉션 — 호출 순서 추적을 위한 최소 헬퍼."""

    def __init__(self):
        self.calls = []


def make_probe_runner(results, recorder=None):
    """probe_path -> (ok, output) 매핑을 주는 페이크 러너."""

    def runner(probe_path):
        if recorder is not None:
            recorder.calls.append(str(probe_path))
        return results.get(str(probe_path), (False, "unknown probe"))

    return runner


def make_promoter(ok=True, recorder=None):
    def promoter(artifact):
        if recorder is not None:
            recorder.calls.append(artifact_name(artifact))
        return ok

    return promoter


def draft_artifact(name="auto_skill_demo", probes=None, status="draft"):
    return {
        "name": name,
        "status": status,
        "probe_paths": probes if probes is not None else ["probe_a.py"],
    }


# --- 그룹 1: 상수 + 정적 표면 -------------------------------------------------

check(IMMUTABLE_ORDER == ("create", "isolate", "verify", "approve", "incorporate", "use"),
      "IMMUTABLE_ORDER는 루프 전체 6단계여야 한다")
check(INCORPORATION_ORDER == ("entry", "verify", "approve", "incorporate"),
      "INCORPORATION_ORDER는 거버넌스 4단계여야 한다")
check(STATUS_INCORPORATED == "incorporated" and STATUS_REJECTED == "rejected"
      and STATUS_ERROR == "error", "상태 상수 값")
check(LIFECYCLE_DRAFT == "draft", "draft 상수")
check(STAGE_ENTRY == "entry" and STAGE_VERIFY == "verify"
      and STAGE_APPROVE == "approve" and STAGE_INCORPORATE == "incorporate",
      "단계 상수 값")
# 거버넌스 단계는 불변 순서의 부분 수열이어야 한다 (순서 정합성)
sub = [s for s in IMMUTABLE_ORDER if s in INCORPORATION_ORDER]
check(sub == ["verify", "approve", "incorporate"],
      "거버넌스 단계는 불변 순서 안에서 같은 상대 순서를 유지해야 한다")

# --- 그룹 2: artifact 접근자 --------------------------------------------------

check(artifact_name({"name": "abc"}) == "abc", "dict에서 이름 추출")
check(artifact_name("plain-name") == "plain-name", "문자열 아티팩트 이름")
check(artifact_name({"name": None}) == "", "이름 None은 빈 문자열")
check(artifact_name(None) == "", "None 아티팩트는 빈 이름")
check(artifact_name(123) == "123", "비문자열은 문자열화")
check(artifact_lifecycle({"status": "draft"}) == "draft", "dict lifecycle")
check(artifact_lifecycle({"status": "APPROVED"}) == "approved", "lifecycle 소문자 정규화")
check(artifact_lifecycle({"name": "x"}) == "draft", "status 누락은 draft 기본")
check(artifact_lifecycle("plain") == "draft", "문자열 아티팩트는 draft")
check(artifact_lifecycle(None) == "draft", "None 아티팩트는 draft")

# --- 그룹 3: 진입 게이트 ------------------------------------------------------

ok, reason = check_entry_gate(draft_artifact())
check(ok is True and "draft" in reason, "draft는 진입 허용")
ok, reason = check_entry_gate(draft_artifact(status="approved"))
check(ok is False and "approved" in reason, "approved는 진입 거부")
ok, reason = check_entry_gate(draft_artifact(status="rejected"))
check(ok is False and "rejected" in reason, "rejected는 진입 거부")
ok, reason = check_entry_gate(draft_artifact(status="incorporated"))
check(ok is False, "incorporated는 재편입 거부")
ok, reason = check_entry_gate({"status": "draft"})
check(ok is False and "name" in reason, "이름 없는 아티팩트 거부")
ok, reason = check_entry_gate("plain-draft-skill")
check(ok is True, "문자열 아티팩트(draft) 진입 허용")
ok, reason = check_entry_gate(None)
check(ok is False, "None 아티팩트 거부")

# --- 그룹 4: 검증 단계 --------------------------------------------------------

ok, detail = verify_artifact({"name": "x", "probe_paths": []})
check(ok is False and "probe" in detail, "프로브 목록 비어 있으면 검증 실패")
ok, detail = verify_artifact({"name": "x"})
check(ok is False, "probe_paths 키 자체가 없으면 검증 실패")
ok, detail = verify_artifact("plain")
check(ok is False, "비-dict 아티팩트는 프로브가 없어 검증 실패")

rec = CallRecorder()
runner = make_probe_runner({"p1.py": (True, "pass")}, recorder=rec)
ok, detail = verify_artifact(draft_artifact(probes=["p1.py"]), probe_runner=runner)
check(ok is True and "1 probe" in detail, "단일 프로브 통과")
check(rec.calls == ["p1.py"], "프로브 1회 호출")

rec = CallRecorder()
runner = make_probe_runner({"p1.py": (True, ""), "p2.py": (True, "")}, recorder=rec)
ok, detail = verify_artifact(draft_artifact(probes=["p1.py", "p2.py"]), probe_runner=runner)
check(ok is True and "2 probe" in detail, "다중 프로브 전부 통과")
check(rec.calls == ["p1.py", "p2.py"], "프로브 순서대로 호출")

rec = CallRecorder()
runner = make_probe_runner({"p1.py": (False, "boom"), "p2.py": (True, "")}, recorder=rec)
ok, detail = verify_artifact(draft_artifact(probes=["p1.py", "p2.py"]), probe_runner=runner)
check(ok is False and "p1.py" in detail and "boom" in detail, "첫 실패에서 검증 중단")
check(rec.calls == ["p1.py"], "실패 이후 프로브는 호출되지 않음")

def raising_runner(probe_path):
    raise RuntimeError("runner exploded")

ok, detail = verify_artifact(draft_artifact(probes=["p1.py"]), probe_runner=raising_runner)
check(ok is False and "raised" in detail, "프로브 러너 예외는 검증 실패(fail-safe)")

runner = make_probe_runner({"p1.py": True})
ok, detail = verify_artifact(draft_artifact(probes=["p1.py"]), probe_runner=runner)
check(ok is True, "러너가 bool만 반환해도 호환")

runner = make_probe_runner({"p1.py": (True, "")})
ok, detail = verify_artifact(draft_artifact(probes="p1.py"), probe_runner=runner)
check(ok is True, "probe_paths가 단일 문자열이어도 호환")

# --- 그룹 5: 승인 단계 --------------------------------------------------------

allowed, reason = approve_artifact(draft_artifact(), approver=None)
check(allowed is False and "no approver" in reason, "approver 미등록은 기본 거부(리스크 5)")
allowed, reason = approve_artifact(draft_artifact(), approver=lambda a: True)
check(allowed is True, "approver True는 허용")
allowed, reason = approve_artifact(draft_artifact(), approver=lambda a: False)
check(allowed is False and "denied" in reason, "approver False는 거부")
allowed, reason = approve_artifact(draft_artifact(),
                                   approver=lambda a: (True, "looks good"))
check(allowed is True and "looks good" in reason, "튜플 반환 approver 호환")

def raising_approver(a):
    raise ValueError("approver exploded")

allowed, reason = approve_artifact(draft_artifact(), approver=raising_approver)
check(allowed is False and "raised" in reason, "approver 예외는 거부(fail-safe)")

# --- 그룹 6: run_incorporation 순서 강제 (핵심) --------------------------------

# 6-1 해피패스: 전 단계 순서 실행 + 편입 완료
rec_approver = CallRecorder()
rec_promoter = CallRecorder()
result = run_incorporation(
    draft_artifact(name="skill_happy", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}),
    approver=lambda a: (rec_approver.calls.append(artifact_name(a)) or True),
    promoter=make_promoter(True, recorder=rec_promoter),
)
check(result["ok"] is True and result["status"] == STATUS_INCORPORATED, "해피패스 편입 완료")
check(result["name"] == "skill_happy", "결과에 이름 포함")
stage_seq = [s["stage"] for s in result["stages"]]
check(stage_seq == list(INCORPORATION_ORDER), "전 단계가 정확히 INCORPORATION_ORDER 순서로 실행")
check(all(s["ok"] for s in result["stages"]), "해피패스는 전 단계 ok")
check(rec_approver.calls == ["skill_happy"], "approver는 검증 통과 후 1회 호출")
check(rec_promoter.calls == ["skill_happy"], "promoter는 승인 후 1회 호출")

# 6-2 검증 실패 -> 승인/편입 절대 호출 안 됨 (순서 강제)
rec_approver = CallRecorder()
rec_promoter = CallRecorder()
result = run_incorporation(
    draft_artifact(name="skill_badprobe", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (False, "fail")}),
    approver=lambda a: (rec_approver.calls.append(1) or True),
    promoter=make_promoter(True, recorder=rec_promoter),
)
check(result["ok"] is False and result["status"] == STATUS_REJECTED, "검증 실패는 rejected")
check("verification failed" in result["reason"], "사유에 검증 실패 명시")
stage_seq = [s["stage"] for s in result["stages"]]
check(stage_seq == [STAGE_ENTRY, STAGE_VERIFY], "검증 실패 시 entry+verify까지만 기록")
check(rec_approver.calls == [], "검증 실패 시 approver 미호출 (순서 강제)")
check(rec_promoter.calls == [], "검증 실패 시 promoter 미호출 (순서 강제)")

# 6-3 승인 거부 -> 편입 절대 호출 안 됨 (순서 강제)
rec_promoter = CallRecorder()
result = run_incorporation(
    draft_artifact(name="skill_denied", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}),
    approver=lambda a: False,
    promoter=make_promoter(True, recorder=rec_promoter),
)
check(result["ok"] is False and result["status"] == STATUS_REJECTED, "승인 거부는 rejected")
check("approval denied" in result["reason"], "사유에 승인 거부 명시")
stage_seq = [s["stage"] for s in result["stages"]]
check(stage_seq == [STAGE_ENTRY, STAGE_VERIFY, STAGE_APPROVE], "거부 시 approve까지만 기록")
check(rec_promoter.calls == [], "승인 거부 시 promoter 미호출 (순서 강제)")

# 6-4 approver 미등록 -> 검증은 통과해도 기본 거부
result = run_incorporation(
    draft_artifact(name="skill_noapprover", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}),
    approver=None,
    promoter=make_promoter(True),
)
check(result["ok"] is False and result["status"] == STATUS_REJECTED,
      "approver 미등록 시 검증 통과해도 거부")
check("no approver" in result["reason"], "미등록 거부 사유")

# 6-5 진입 게이트 거부 (approved 아티팩트) -> 이후 단계 전부 미호출
rec_approver = CallRecorder()
rec_promoter = CallRecorder()
result = run_incorporation(
    draft_artifact(name="skill_already", status="approved", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}, recorder=CallRecorder()),
    approver=lambda a: (rec_approver.calls.append(1) or True),
    promoter=make_promoter(True, recorder=rec_promoter),
)
check(result["ok"] is False and result["status"] == STATUS_REJECTED, "approved 아티팩트 거부")
stage_seq = [s["stage"] for s in result["stages"]]
check(stage_seq == [STAGE_ENTRY], "진입 거부 시 entry만 기록")
check(rec_approver.calls == [] and rec_promoter.calls == [],
      "진입 거부 시 verify 이후 전부 미호출")

# 6-6 이름 없는 아티팩트 -> 진입 거부
result = run_incorporation({"status": "draft", "probe_paths": ["p1.py"]},
                           probe_runner=make_probe_runner({"p1.py": (True, "")}),
                           approver=lambda a: True, promoter=make_promoter(True))
check(result["ok"] is False and result["status"] == STATUS_REJECTED, "이름 없으면 진입 거부")
check(result["name"] == "", "이름 없는 결과")

# 6-7 promoter 실패(False) -> error
result = run_incorporation(
    draft_artifact(name="skill_promfail", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}),
    approver=lambda a: True,
    promoter=make_promoter(False),
)
check(result["ok"] is False and result["status"] == STATUS_ERROR, "promote 실패는 error")
check("promote failed" in result["reason"], "promote 실패 사유")

# 6-8 promoter 예외 -> error, 절대 raise 안 함
def raising_promoter(a):
    raise RuntimeError("promoter exploded")

result = run_incorporation(
    draft_artifact(name="skill_promraise", probes=["p1.py"]),
    probe_runner=make_probe_runner({"p1.py": (True, "")}),
    approver=lambda a: True,
    promoter=raising_promoter,
)
check(result["ok"] is False and result["status"] == STATUS_ERROR, "promoter 예외는 error")
check("raised" in result["reason"], "promoter 예외 사유 기록")
check([s["stage"] for s in result["stages"]][-1] == STAGE_INCORPORATE
      and result["stages"][-1]["ok"] is False, "예외도 incorporate 단계에 기록")

# 6-9 stages는 항상 INCORPORATION_ORDER의 접두사 (불변 순서 감사 추적)
for probes, approver, promoter, expect_prefix in [
    (["p1.py"], lambda a: True, make_promoter(True), 4),
    (["p1.py"], lambda a: False, make_promoter(True), 3),
    ([], lambda a: True, make_promoter(True), 2),
]:
    r = run_incorporation(draft_artifact(probes=probes),
                          probe_runner=make_probe_runner({"p1.py": (True, "")}),
                          approver=approver, promoter=promoter)
    seq = [s["stage"] for s in r["stages"]]
    check(seq == list(INCORPORATION_ORDER[:expect_prefix]),
          "stages는 INCORPORATION_ORDER의 접두사 (길이 %d)" % expect_prefix)

# 6-10 문자열 아티팩트 + 기본 promoter 미사용(주입) 경로
result = run_incorporation(
    "plain-draft-skill",
    probe_runner=make_probe_runner({"p.py": (True, "")}),
    approver=lambda a: True,
    promoter=make_promoter(True),
)
# probe_paths가 없으므로 검증 단계에서 거부되어야 한다 (문자열 아티팩트는 프로브 없음)
check(result["ok"] is False and result["status"] == STATUS_REJECTED,
      "문자열 아티팩트는 프로브가 없어 검증 거부")

# --- 그룹 7: default_skill_promoter 안전성 -------------------------------------

check(default_skill_promoter({"name": ""}) is False, "이름 없는 promote는 False")
check(default_skill_promoter(None) is False, "None 아티팩트 promote는 False")
check(default_skill_promoter("  ") is False, "공백 이름 promote는 False")

# --- 그룹 8: E-L2 핸드오프 정합성 ----------------------------------------------

from api.dynamic.builder_agent import build_builder_task  # noqa: E402

task_text, criteria = build_builder_task({"capability": "테스트 능력"})
check("E-L4" in task_text, "E-L2 미션은 편입을 E-L4 관할로 선언한다")
check(any("draft" in c for c in criteria), "E-L2 수용 기준에 draft 제약 포함")

print("ALL GAP-E-L4 PROBES PASSED (%d checks)" % CHECKS)

"""시나리오 D 기능 프로브: DiscoveryBoard — 수동적 인지(passive awareness) 계층.

실제 LLM/서버/하네스 실행 없이 다음을 검증한다:
1. 와이어 상수 값 (importance/tool 이름)
2. publish 신규/merge 시맨틱 (정규화 병합, confidence/중요도 승격, quota 비소비)
3. publish_limit (에이전트당 상한, merge는 상한 이후에도 허용)
4. max_total + LOW 퇴거 + _by_hash 유령 엔트리 없음 (SHA-1 키 일치)
5. board_full (LOW가 없을 때)
6. 입력 정규화/검증 (empty_summary, 잘못된 importance/confidence)
7. relevance_filter (affected_tasks/HIGH 항상/토큰 겹침 2개 이상)
8. compress (LOW 폐기, 자기 발견 제외, 헤더, 순서, 개수 상한, 빈 보드)
9. recent (최신순, exclude_agent, min_importance, since)
10. subscribe/_notify (publisher 제외, affected 타기팅, 콜백 예외 비전파)
11. disabled 보드 열화
12. limits 계약 (전체 limits dict의 discovery 섹션 읽기)
13. thread-local 노출 + 도구 핸들러 (no_board 포함)
14. 스키마 + register_discovery_tools 멱등 등록
15. 정적 배선 확인 (orchestrator/runner/limits 소스 내 핵심 문자열 존재)
"""
import hashlib
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


from api.dynamic.discovery_board import (  # noqa: E402
    IMPORTANCE_HIGH, IMPORTANCE_MEDIUM, IMPORTANCE_LOW,
    DISCOVERY_TOOLSET, TOOL_BROADCAST_DISCOVERY, TOOL_CHECK_TEAM_DISCOVERIES,
    DISCOVERY_TOOL_NAMES, DiscoveryBoard, Discovery,
    set_current_board, get_current_board, get_current_agent, clear_current_board,
    _handle_broadcast_discovery, _handle_check_team_discoveries,
    BROADCAST_SCHEMA, CHECK_SCHEMA, register_discovery_tools,
)

# -- 1. 와이어 상수 --
check(IMPORTANCE_HIGH == "high", "IMPORTANCE_HIGH 와이어 값")
check(IMPORTANCE_MEDIUM == "medium", "IMPORTANCE_MEDIUM 와이어 값")
check(IMPORTANCE_LOW == "low", "IMPORTANCE_LOW 와이어 값")
check(DISCOVERY_TOOLSET == "discovery", "DISCOVERY_TOOLSET 와이어 값")
check(TOOL_BROADCAST_DISCOVERY == "broadcast_discovery", "TOOL_BROADCAST_DISCOVERY 와이어 값")
check(TOOL_CHECK_TEAM_DISCOVERIES == "check_team_discoveries", "TOOL_CHECK_TEAM_DISCOVERIES 와이어 값")
check(set(DISCOVERY_TOOL_NAMES) == {"broadcast_discovery", "check_team_discoveries"},
      "DISCOVERY_TOOL_NAMES 구성")
print("1. constants OK")

# -- 2. publish 신규/merge 시맨틱 --
b2 = DiscoveryBoard("run_merge")
ok, reason = b2.publish("agent_a", "Database schema mismatch in users table",
                        importance="medium", confidence=0.5)
check(ok and reason == "published", "신규 publish reason=published")
ok, reason = b2.publish("agent_a", "database   schema MISMATCH in users table",
                        importance="high", confidence=0.8,
                        evidence="users.sql line 42", affected_tasks=["agent_b"])
check(ok and reason == "merged", "정규화(소문자/공백) 중복은 merge")
items = b2.recent(limit=5)
check(len(items) == 1, "merge 후에도 엔트리는 1건")
d = items[0]
check(d.count == 2, "merge 시 count 증가")
check(abs(d.confidence - 0.8) < 1e-9, "merge 시 confidence 승격")
check(d.importance == "high", "merge 시 중요도 승격")
check(d.evidence == "users.sql line 42", "merge 시 evidence 보충")
check(d.affected_tasks == ["agent_b"], "merge 시 affected_tasks 병합")
check(b2._publish_counts.get("agent_a") == 1, "merge는 quota를 소비하지 않음")
b2.publish("agent_a", "DATABASE schema mismatch in USERS table", confidence=0.3)
top = b2.recent(limit=1)[0]
check(abs(top.confidence - 0.8) < 1e-9, "낮은 confidence merge는 기존 최댓값 유지")
check(top.count == 3, "재병합 시 count 재증가")
st = b2.stats()
check(st["total"] == 1 and st["by_importance"].get("high") == 1, "stats 집계")
print("2. publish/merge semantics OK")

# -- 3. publish_limit + 상한 후 merge 허용 --
b3 = DiscoveryBoard("run_limit", {"discovery": {"max_publish_per_agent": 2}})
check(b3.publish("agent_x", "alpha one")[1] == "published", "상한 내 publish 1")
check(b3.publish("agent_x", "alpha two")[1] == "published", "상한 내 publish 2")
ok, reason = b3.publish("agent_x", "alpha three")
check(not ok and reason == "publish_limit", "상한 초과 신규는 publish_limit")
ok, reason = b3.publish("agent_x", "ALPHA   ONE")
check(ok and reason == "merged", "상한 도달 후에도 merge는 허용")
check(b3._publish_counts["agent_x"] == 2, "merge 후에도 quota 불변")
print("3. publish_limit OK")

# -- 4. max_total + LOW 퇴거 + _by_hash 무결성 --
b4 = DiscoveryBoard("run_evict", {"discovery": {"max_total": 3, "max_publish_per_agent": 10}})
b4.publish("agent_a", "low note one", importance="low")
b4.publish("agent_b", "medium fact two", importance="medium")
b4.publish("agent_c", "high alert three", importance="high")
ok, reason = b4.publish("agent_a", "medium fact four", importance="medium")
check(ok and reason == "published", "max_total 도달 시 LOW 퇴거 후 신규 게시")
summaries = {x.summary for x in b4.recent(limit=10)}
check("low note one" not in summaries, "가장 오래된 LOW 퇴거됨")
check("medium fact four" in summaries, "신규 발견 존재")
check(len(b4._items) == 3 and len(b4._by_hash) == 3, "퇴거 후 유령 엔트리 없음")
for x in b4._items:
    k = hashlib.sha1(" ".join(x.summary.lower().split()).encode("utf-8")).hexdigest()
    check(b4._by_hash.get(k) is x, "_by_hash 키가 SHA-1 해시와 일치")
print("4. eviction/by_hash OK")

# -- 5. board_full --
b5 = DiscoveryBoard("run_full", {"discovery": {"max_total": 2, "max_publish_per_agent": 10}})
b5.publish("agent_a", "fact one", importance="medium")
b5.publish("agent_b", "fact two", importance="high")
ok, reason = b5.publish("agent_c", "fact three")
check(not ok and reason == "board_full", "LOW 없이는 board_full")
print("5. board_full OK")

# -- 6. 입력 검증 --
b9 = DiscoveryBoard("run_valid")
ok, reason = b9.publish("agent_a", "   ")
check(not ok and reason == "empty_summary", "빈 summary 거부")
ok, _ = b9.publish("agent_a", "weird importance", importance="bogus")
check(ok and b9.recent(limit=1)[0].importance == "medium", "잘못된 importance는 medium 폴백")
b9.publish("agent_b", "bad confidence", confidence="abc")
dc = [x for x in b9.recent(limit=5) if x.summary == "bad confidence"][0]
check(abs(dc.confidence - 0.5) < 1e-9, "잘못된 confidence는 0.5 폴백")
b9.publish("agent_b", "clamped confidence", confidence=2.0)
dc = [x for x in b9.recent(limit=5) if x.summary == "clamped confidence"][0]
check(abs(dc.confidence - 1.0) < 1e-9, "confidence 1.0 클램프")
print("6. validation OK")

# -- 7. relevance_filter --
b6 = DiscoveryBoard("run_rel")
b6.publish("agent_a", "database migration needs index rebuild", importance="medium")
b6.publish("agent_c", "critical security hole in auth module", importance="high")
b6.publish("agent_a", "cache config tweak", importance="medium", affected_tasks=["agent_b"])
b6.publish("agent_c", "payment gateway timeout fixed", importance="medium")
rel = b6.relevance_filter("agent_b", subtask="optimize database migration")
rel_sum = {x.summary for x in rel}
check("cache config tweak" in rel_sum, "affected_tasks에 내 이름 포함시 관련")
check("critical security hole in auth module" in rel_sum, "HIGH는 항상 관련")
check("database migration needs index rebuild" in rel_sum, "토큰 겹침 2개 이상시 관련")
check("payment gateway timeout fixed" not in rel_sum, "무관한 발견 제외")
print("7. relevance_filter OK")

# -- 8. compress --
b7 = DiscoveryBoard("run_compress", {"discovery": {"digest_max_items": 3}})
b7.publish("agent_a", "my own finding alpha", importance="medium")
b7.publish("agent_b", "minor low note beta", importance="low")
b7.publish("agent_c", "critical high gamma", importance="high")
b7.publish("agent_c", "medium delta finding", importance="medium")
digest = b7.compress()
check(digest.startswith("[TEAM DISCOVERY BOARD]"), "digest 헤더")
check("critical high gamma" in digest, "HIGH digest 포함")
check("my own finding alpha" in digest, "MEDIUM digest 포함")
check("minor low note beta" not in digest, "LOW 기본 폐기")
check("check_team_discoveries" in digest, "footer 안내 포함")
check(digest.index("critical high gamma") < digest.index("my own finding alpha"),
      "중요도 내림차순 정렬")
digest_a = b7.compress(agent_name="agent_a", subtask="")
check("my own finding alpha" not in digest_a, "자기 발견 제외")
check("critical high gamma" in digest_a, "HIGH는 타 에이전트에게 노출")
check(DiscoveryBoard("run_empty").compress() == "", "빈 보드 digest는 빈 문자열")
b7b = DiscoveryBoard("run_cap", {"discovery": {"digest_max_items": 2}})
for i in range(5):
    b7b.publish("agent_%d" % i, "cap finding number %d" % i, importance="medium")
check(b7b.compress().count("- [") == 2, "digest_max_items 상한")
print("8. compress OK")

# -- 9. recent --
b8 = DiscoveryBoard("run_recent")
b8.publish("agent_a", "first finding", importance="medium")
b8.publish("agent_b", "second finding", importance="high")
b8.publish("agent_a", "third finding", importance="low")
b8._items[0].timestamp = 1000.0
b8._items[1].timestamp = 2000.0
b8._items[2].timestamp = 3000.0
r = b8.recent(limit=10)
check([x.summary for x in r] == ["third finding", "second finding", "first finding"],
      "recent 최신순")
r2 = b8.recent(limit=10, exclude_agent="agent_a")
check([x.summary for x in r2] == ["second finding"], "recent exclude_agent")
r3 = b8.recent(limit=10, min_importance="high")
check([x.summary for x in r3] == ["second finding"], "recent min_importance")
r4 = b8.recent(limit=10, since=1500.0)
check([x.summary for x in r4] == ["third finding", "second finding"], "recent since")
print("9. recent OK")

# -- 10. subscribe/_notify --
b10 = DiscoveryBoard("run_notify")
calls = []
b10.subscribe("agent_b", lambda p: calls.append(("b", p)))
b10.subscribe("agent_c", lambda p: calls.append(("c", p)))
b10.publish("agent_a", "broadcast no targets")
check(("b", "agent_a") in calls and ("c", "agent_a") in calls, "affected 없으면 전체 구독자 알림")
calls.clear()
b10.publish("agent_b", "self publish")
check(("b", "agent_b") not in calls and ("c", "agent_b") in calls, "publisher는 알림 대상 제외")
calls.clear()
b10.publish("agent_a", "targeted", affected_tasks=["agent_c"])
check(("c", "agent_a") in calls and ("b", "agent_a") not in calls, "affected_tasks 타기팅")


def _bad_cb(p):
    raise RuntimeError("boom")


b10.subscribe("agent_d", _bad_cb)
ok, reason = b10.publish("agent_a", "after bad callback")
check(ok and reason == "published", "콜백 예외는 publish에 전파되지 않음")
check(b10.subscribe("") is False, "빈 이름 구독 거부")
print("10. subscribe/notify OK")

# -- 11. disabled 보드 --
b11 = DiscoveryBoard("run_disabled", {"discovery": {"enabled": False}})
ok, reason = b11.publish("agent_a", "anything")
check(not ok and reason == "disabled", "disabled publish 거부")
check(b11.compress() == "", "disabled compress 빈 문자열")
check(b11.subscribe("agent_a") is True, "disabled여도 구독 등록은 허용")
check(b11.stats().get("enabled") is False, "stats enabled=False")
print("11. disabled OK")

# -- 12. limits 계약 (전체 limits dict의 discovery 섹션) --
b12 = DiscoveryBoard("run_cfg", {"discovery": {"max_publish_per_agent": 2, "max_total": 7}})
check(b12.max_publish_per_agent == 2, "limits discovery.max_publish_per_agent 반영")
check(b12.max_total == 7, "limits discovery.max_total 반영")
b13 = DiscoveryBoard("run_cfg2", {"max_publish_per_agent": 2})
check(b13.max_publish_per_agent == 5, "하위 dict 직접 전달 시 기본값 폴백(계약 확인)")
print("12. limits contract OK")

# -- 13. thread-local + 도구 핸들러 --
clear_current_board()
check(get_current_board() is None, "초기 보드 없음")
out = json.loads(_handle_broadcast_discovery({"summary": "x"}))
check(out == {"ok": False, "reason": "no_board"}, "broadcast no_board JSON")
out = json.loads(_handle_check_team_discoveries({}))
check(out.get("discoveries") == [] and out.get("reason") == "no_board", "check no_board JSON")

b14 = DiscoveryBoard("run_handler")
b14.publish("agent_z", "other agent finding", importance="high")
set_current_board(b14, "agent_a")
check(get_current_board() is b14 and get_current_agent() == "agent_a", "thread-local set")
out = json.loads(_handle_broadcast_discovery({"summary": "handler finding", "importance": "high"}))
check(out.get("ok") is True and out.get("reason") == "published", "핸들러 publish")
check(any(x.summary == "handler finding" and x.source_agent == "agent_a"
          for x in b14.recent(limit=10)), "핸들러가 thread-local 에이전트 이름 사용")
out = json.loads(_handle_check_team_discoveries({"min_importance": "low"}))
names = [x["summary"] for x in out["discoveries"]]
check("other agent finding" in names, "check는 타 에이전트 발견 반환")
check("handler finding" not in names, "check는 자기 발견 제외")
d_dict = b14.recent(limit=1)[0].to_dict()
for k in ("run_id", "source_agent", "type", "importance", "confidence",
          "summary", "evidence", "affected_tasks", "timestamp"):
    check(k in d_dict, "Discovery 스키마 필드: " + k)
seen = {}


def _worker():
    seen["b"] = get_current_board()
    seen["a"] = get_current_agent()


t = threading.Thread(target=_worker)
t.start()
t.join()
check(seen["b"] is None and seen["a"] == "", "thread-local은 스레드 격리")
clear_current_board()
check(get_current_board() is None and get_current_agent() == "", "thread-local clear")
print("13. thread-local/handlers OK")

# -- 14. 스키마 + register_discovery_tools 멱등 --
check(BROADCAST_SCHEMA["name"] == "broadcast_discovery", "BROADCAST_SCHEMA 이름")
check(BROADCAST_SCHEMA["parameters"]["required"] == ["summary"], "broadcast required=[summary]")
check(CHECK_SCHEMA["name"] == "check_team_discoveries", "CHECK_SCHEMA 이름")


class FakeRegistry:
    def __init__(self):
        self.names = set()
        self.calls = 0

    def get_all_tool_names(self):
        return list(self.names)

    def register(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.names.add(name)
        self.calls += 1


reg = FakeRegistry()
check(register_discovery_tools(reg) is True, "최초 등록 True")
check(reg.calls == 2 and reg.names == set(DISCOVERY_TOOL_NAMES), "도구 2개 등록")
check(register_discovery_tools(reg) is False, "재등록은 False (멱등)")
check(reg.calls == 2, "재등록 시 추가 register 호출 없음")
print("14. schema/registry OK")

# -- 15. 정적 배선 확인 --
orch_src = (ROOT / "api" / "api" / "dynamic" / "orchestrator.py").read_text(encoding="utf-8")
check('mission_tracker["discovery_board"] = DiscoveryBoard(run_id, limits)' in orch_src,
      "orchestrator: 전체 limits로 보드 생성")
check("compress(min_importance=IMPORTANCE_HIGH)" in orch_src,
      "orchestrator: replan HIGH digest 주입")
runner_src = (ROOT / "api" / "api" / "dynamic" / "runner.py").read_text(encoding="utf-8")
check("set_current_board(_discovery_board, agent_name)" in runner_src,
      "runner: thread-local 보드 노출")
check("clear_current_board()" in runner_src, "runner: thread-local 정리")
check("register_discovery_tools" in runner_src, "runner: 도구 등록")
check("_discovery_board.compress(" in runner_src, "runner: 노드 시작 digest 주입")
limits_src = (ROOT / "api" / "api" / "dynamic" / "limits.py").read_text(encoding="utf-8")
check('"discovery"' in limits_src, "limits: discovery 예산 존재")
print("15. static wiring OK")

print("probe_scenario_d: ALL PASS (%d checks)" % CHECKS)

# -*- coding: utf-8 -*-
"""
probe — template_validator.py (정적 에이전트 템플릿 스키마 집행기) 검증.

목적:
  1. 실 데이터(105개 템플릿)에서 위반 0건 (PASS) 인지 확인
  2. 검증기가 악성 케이스를 실제로 잡아내는지 (검출 능력) 확인
     — 단순히 "위반 없음"을 반환하는 무기능 모듈이 아님을 증명
  3. 절대 raise 하지 않는지 (fail-safe 계약) 확인
  4. 파일을 수정하지 않는지 (읽기 전용 계약) 확인
  5. template_loader 에 실제로 배선되었는지 확인

실행:
    cd "C:/daon/Daon agent System"
    PYTHONIOENCODING=utf-8 python api/tests/test_template_validator.py
"""
import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

# ── 경로 부트스트랩 ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent   # Daon agent System
sys.path.insert(0, str(ROOT / "api"))

from api.dynamic.template_validator import (          # noqa: E402
    validate_all_templates,
    _collect_existing_skills,
    PARENT_MAX_ITERATIONS,
)

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append((name, detail))
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  -- " + str(detail)) if detail else ""))


print("=" * 74)
print("PROBE: template_validator — 정적 에이전트 템플릿 스키마 집행기")
print("=" * 74)

# ══════════════════════════════════════════════════════════════════════
# 1. 실 데이터 검증 — 위반 0건
# ══════════════════════════════════════════════════════════════════════
print("\n[1] 실 데이터 (105개 템플릿) 검증")
real = validate_all_templates()
check("real run: no exception", "error" not in real, real.get("error", ""))
check("real run: schema loaded", real.get("schema_loaded") is True)
check("real run: no SCHEMA_LOAD_FAILED",
      "SCHEMA_LOAD_FAILED" not in real.get("by_code", {}))
check("real run: 105 templates", real.get("total") == 105, "got %s" % real.get("total"))
check("real run: skills indexed > 100", real.get("skills_indexed", 0) > 100,
      "got %s" % real.get("skills_indexed"))
check("real run: RESULT PASS (0 violations)", real.get("ok") is True,
      "violations=%s" % len(real.get("violations", [])))
if not real.get("ok"):
    for v in real.get("violations", [])[:10]:
        print("        %s %s %s: %s" % (v["code"], v["agent_id"], v["field"], v["detail"]))

# ══════════════════════════════════════════════════════════════════════
# 2. 검출 능력 — 가짜 agents 디렉토리로 악성 케이스 주입
# ══════════════════════════════════════════════════════════════════════
print("\n[2] 검출 능력 (악성 케이스 주입)")

tmp = tempfile.mkdtemp(prefix="tvprobe_")
try:
    # 최소 스키마. 주의: YAML flow context 에서 'list[string]' 의 '[' 는
    # flow sequence 시작으로 해석되어 파싱 오류를 낸다 → 'list' 만 쓴다.
    (Path(tmp) / "_schema.yaml").write_text("""
schema_version: "1.2"
fields:
  id:   {type: string, required: true}
  category: {type: string, required: true, enum: [developer, reviewer]}
  tools: {type: list, required: true, allowed_values: [file, terminal]}
  skills: {type: list, required: false}
  model_preference: {type: object, required: true}
  runtime:
    type: object
    required: true
    fields:
      max_iterations: {type: integer, default: 50}
  capability_score:
    type: object
    required: true
    fields:
      documentation: {type: integer, range: [1, 10]}
      architecture: {type: integer, range: [1, 10]}
  avoid_when: {type: list, required: false}
""", encoding="utf-8")

    # A. 미존재 스킬 + 미존재 related_agent + 허용값 밖 도구
    (Path(tmp) / "bad1.yaml").write_text("""
id: bad1
category: developer
tools: [file, terminal, excel]
skills: [definitely-not-a-real-skill-xyz]
related_agents: [not-a-real-agent-abc]
model_preference:
  coding: [deepseek-v3]
runtime:
  max_iterations: 50
capability_score:
  documentation: 5
  architecture: 5
""", encoding="utf-8")

    # B. 권한 불변식 위반 (max_iterations > 부모 상한)
    (Path(tmp) / "bad2.yaml").write_text("""
id: bad2
category: reviewer
tools: [file]
model_preference:
  reasoning: [claude-sonnet]
runtime:
  max_iterations: %d
capability_score:
  documentation: 5
  architecture: 5
""" % (PARENT_MAX_ITERATIONS + 50), encoding="utf-8")

    # C. 스키마 required 누락 (model_preference 없음)
    (Path(tmp) / "bad3.yaml").write_text("""
id: bad3
category: reviewer
tools: [file]
runtime:
  max_iterations: 20
capability_score:
  documentation: 5
  architecture: 5
""", encoding="utf-8")

    # D. enum 위반 (category)
    (Path(tmp) / "bad4.yaml").write_text("""
id: bad4
category: not-a-valid-category
tools: [file]
model_preference:
  reasoning: [claude-sonnet]
runtime:
  max_iterations: 20
capability_score:
  documentation: 5
  architecture: 5
""", encoding="utf-8")

    # E. range 위반 (capability_score.documentation = 99)
    (Path(tmp) / "bad5.yaml").write_text("""
id: bad5
category: developer
tools: [file]
model_preference:
  coding: [deepseek-v3]
runtime:
  max_iterations: 20
capability_score:
  documentation: 99
  architecture: 5
""", encoding="utf-8")

    # F. 자기모순 (avoid_when 문서작업 배제 + documentation=9)
    (Path(tmp) / "bad6.yaml").write_text("""
id: bad6
category: reviewer
tools: [file]
model_preference:
  reasoning: [claude-sonnet]
runtime:
  max_iterations: 20
avoid_when:
  - documentation_only
capability_score:
  documentation: 9
  architecture: 5
""", encoding="utf-8")

    # G. 정상 (위반 없는 템플릿)
    (Path(tmp) / "good.yaml").write_text("""
id: good
category: developer
tools: [file, terminal]
skills: []
model_preference:
  coding: [deepseek-v3]
runtime:
  max_iterations: 50
capability_score:
  documentation: 5
  architecture: 5
""", encoding="utf-8")

    rep = validate_all_templates(tmp)
    codes = {}
    for v in rep.get("violations", []):
        codes.setdefault(v["agent_id"], set()).add(v["code"])

    print("    주입 템플릿 7개 중 위반 검출: %d개 (good 제외 6개 기대)" % len(codes))

    check("detect: 미존재 스킬 (REF_SKILL_MISSING)",
          "REF_SKILL_MISSING" in codes.get("bad1", set()))
    check("detect: 미존재 related_agent (REF_AGENT_MISSING)",
          "REF_AGENT_MISSING" in codes.get("bad1", set()))
    check("detect: 허용값 밖 도구 (SCHEMA_ALLOWED)",
          "SCHEMA_ALLOWED" in codes.get("bad1", set()))
    check("detect: 권한 불변식 위반 (AUTHORITY_ITERATIONS)",
          "AUTHORITY_ITERATIONS" in codes.get("bad2", set()))
    check("detect: required 누락 (SCHEMA_REQUIRED)",
          "SCHEMA_REQUIRED" in codes.get("bad3", set()))
    check("detect: enum 위반 (SCHEMA_ENUM)",
          "SCHEMA_ENUM" in codes.get("bad4", set()))
    check("detect: range 위반 (SCHEMA_RANGE)",
          "SCHEMA_RANGE" in codes.get("bad5", set()))
    check("detect: 자기모순 (SELF_CONTRADICTION)",
          "SELF_CONTRADICTION" in codes.get("bad6", set()))
    check("detect: 정상 템플릿은 위반 없음",
          "good" not in codes, "good 의 위반=%s" % codes.get("good"))
    check("detect: 자기모순은 bad6 에서만",
          len([a for a, c in codes.items() if "SELF_CONTRADICTION" in c]) == 1)

    # ══════════════════════════════════════════════════════════════════
    # 3. fail-safe — 절대 raise 하지 않는다
    # ══════════════════════════════════════════════════════════════════
    print("\n[3] fail-safe 계약")
    r1 = validate_all_templates("/definitely/not/a/real/path/xyz")
    check("no raise: 존재하지 않는 경로",
          isinstance(r1, dict) and r1.get("ok") is False)
    check("detect: 존재하지 않는 경로 -> AGENTS_DIR_MISSING",
          "AGENTS_DIR_MISSING" in r1.get("by_code", {}))

    r2 = validate_all_templates(None)
    check("no raise: None 인자", isinstance(r2, dict))

    broken = tempfile.mkdtemp(prefix="tvbroken_")
    try:
        (Path(broken) / "_schema.yaml").write_text("::: not valid yaml :::\n\t- x\n",
                                                   encoding="utf-8")
        (Path(broken) / "x.yaml").write_text("id: x\n  bad indent: [\n", encoding="utf-8")
        r3 = validate_all_templates(broken)
        check("no raise: 깨진 YAML/스키마", isinstance(r3, dict))
        check("detect: 스키마 파싱 실패 -> SCHEMA_LOAD_FAILED",
              "SCHEMA_LOAD_FAILED" in r3.get("by_code", {}),
              "by_code=%s" % r3.get("by_code"))
    finally:
        shutil.rmtree(broken, ignore_errors=True)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════════════════
# 4. 읽기 전용 계약 — 템플릿 파일을 수정하지 않는다
# ══════════════════════════════════════════════════════════════════════
print("\n[4] 읽기 전용 계약")
agents_dir = ROOT / "api" / "agents"
before = {}
for p in sorted(agents_dir.rglob("*.yaml")):
    before[str(p)] = (p.stat().st_mtime_ns, p.stat().st_size)
validate_all_templates()
after = {}
for p in sorted(agents_dir.rglob("*.yaml")):
    after[str(p)] = (p.stat().st_mtime_ns, p.stat().st_size)
check("readonly: 파일 변경 없음", before == after,
      "changed=%s" % [k for k in before if before.get(k) != after.get(k)])

# ══════════════════════════════════════════════════════════════════════
# 5. 산출물 계약
# ══════════════════════════════════════════════════════════════════════
print("\n[5] 반환 스키마")
check("shape: 필수 키 존재",
      all(k in real for k in
          ("agents_dir", "schema_loaded", "total", "skills_indexed",
           "violations", "by_code", "by_agent", "ok")))
check("shape: violations 는 리스트", isinstance(real.get("violations"), list))
check("shape: JSON 직렬화 가능",
      isinstance(json.dumps(real, ensure_ascii=False), str))

# ══════════════════════════════════════════════════════════════════════
# 6. 배선 검증 — template_loader 가 실제로 검증기를 호출하는가
# ══════════════════════════════════════════════════════════════════════
print("\n[6] 배선 (template_loader 연동)")
try:
    from api.dynamic import template_loader as tl
    import api.dynamic.template_validator as tv

    check("wiring: _run_template_validation_once 존재",
          hasattr(tl, "_run_template_validation_once"))
    check("wiring: _VALIDATION_DONE 플래그 존재",
          hasattr(tl, "_VALIDATION_DONE"))

    # 1회 실행 + 멱등
    tl._VALIDATION_DONE = False
    tl.invalidate_cache()
    tl._run_template_validation_once()
    check("wiring: 1회 실행 후 플래그 True", tl._VALIDATION_DONE is True)

    tl._run_template_validation_once()
    check("wiring: 재호출 멱등 (예외 없음)", True)

    # load_all_templates 가 훅을 태운다
    tl._VALIDATION_DONE = False
    tl.invalidate_cache()
    tpls = tl.load_all_templates()
    check("wiring: load_all_templates 가 검증 훅 실행",
          tl._VALIDATION_DONE is True, "loaded=%d" % len(tpls))
    check("wiring: 105 템플릿 로드", len(tpls) == 105, "got %d" % len(tpls))

    # load_template 개별 경로도 커버
    tl._VALIDATION_DONE = False
    tl.invalidate_cache()
    one = tl.load_template("python-backend")
    check("wiring: load_template 개별 경로가 훅 실행",
          tl._VALIDATION_DONE is True,
          "id=%s" % (one.get("id") if one else None))

    # 검증된 템플릿의 skills 가 실재 스킬만 담고 있는지 (실 데이터 스팟체크)
    tl.invalidate_cache()
    ai = tl.load_template("ai-creator")
    real_skills = _collect_existing_skills()
    if ai:
        bad = [s for s in (ai.get("skills") or []) if s.lower() not in real_skills]
        check("wiring: ai-creator skills 전부 실재", not bad, "미존재=%s" % bad)

    # 부분 캐시 회귀 방지 (pre-existing 버그, 2026-09-12 수정)
    tl._VALIDATION_DONE = False
    tl.invalidate_cache()
    tl.load_template("ai-creator")
    partial = len(tl._TEMPLATE_CACHE)
    allt = tl.load_all_templates()
    check("wiring: 부분 캐시 후에도 load_all_templates 가 105개 반환",
          len(allt) == 105, "cache=%d, all=%d" % (partial, len(allt)))
    tl.invalidate_cache()

    # search_templates 이 실제로 템플릿을 찾는지 (위 버그의 증상)
    tl.invalidate_cache()
    tl.load_template("ai-creator")
    hits = tl.search_templates("ppt presentation", top_k=3)
    check("wiring: search_templates 가 부분 캐시 상태에서도 정상 검색",
          any(h.get("id") == "slide-master-expert" for h in hits),
          "hits=%s" % [h.get("id") for h in hits])
    tl.invalidate_cache()

    # fail-open: 검증기가 예외를 던져도 로더는 죽지 않는다
    orig_impl = tv.validate_all_templates

    def _boom(*a, **k):
        raise RuntimeError("boom")

    try:
        tv.validate_all_templates = _boom
        tl._VALIDATION_DONE = False
        tl._run_template_validation_once()
        check("wiring: fail-open (검증기 예외에도 raise 안 함)", True)
    except Exception as e:
        check("wiring: fail-open (검증기 예외에도 raise 안 함)", False, str(e))
    finally:
        tv.validate_all_templates = orig_impl
        tl._VALIDATION_DONE = False
        tl.invalidate_cache()

except Exception as exc:
    check("wiring: 모듈 import", False, str(exc))

# ══════════════════════════════════════════════════════════════════════
print()
print("=" * 74)
print("RESULT: %d passed, %d failed" % (len(PASSED), len(FAILED)))
if FAILED:
    print("\n실패 항목:")
    for n, d in FAILED:
        print("  - %s  %s" % (n, d))
    print("\nSOME PROBES FAILED")
else:
    print("\nALL PROBES PASSED")
print("=" * 74)
sys.exit(0 if not FAILED else 1)
